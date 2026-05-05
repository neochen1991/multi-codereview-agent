from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
from io import BufferedReader, BufferedWriter
from typing import Any

from app.services.command_resolver import resolve_executable

logger = logging.getLogger(__name__)


class McpStdioClient:
    """Lightweight JSON-RPC stdio client for MCP servers."""

    def __init__(self, command: list[str], *, cwd: str, timeout_seconds: float, env: dict[str, str] | None = None) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.env = dict(env or {})

    def call_many(self, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        with self.open_session() as session:
            return session.call_many(requests)

    def open_session(self) -> "McpStdioSession":
        executable = self.command[0] if self.command else ""
        resolved_executable = resolve_executable(executable) if executable else None
        if executable and not resolved_executable:
            raise RuntimeError(f"MCP 可执行命令不存在: {executable}")
        command = [resolved_executable or executable, *self.command[1:]]
        return McpStdioSession(command, cwd=self.cwd, timeout_seconds=self.timeout_seconds, env=self.env or None)

    def _exchange_messages(
        self,
        stdin: BufferedWriter,
        stdout: BufferedReader,
        requests: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        return _exchange_messages(stdin, stdout, requests)

    def _read_response_for(self, stdout: BufferedReader, expected_id: int) -> dict[str, Any] | None:
        return _read_response_for(stdout, expected_id)

    def _read_one_message(self, stdout: BufferedReader) -> dict[str, Any] | None:
        return _read_one_message(stdout)

    def _encode_message(self, message: dict[str, Any]) -> bytes:
        return _encode_message(message)

    def _decode_messages(self, data: bytes) -> dict[int, dict[str, Any]]:
        responses: dict[int, dict[str, Any]] = {}
        index = 0
        while index < len(data):
            header_end = data.find(b"\r\n\r\n", index)
            if header_end < 0:
                break
            headers = data[index:header_end].decode("ascii", errors="ignore")
            length = 0
            for line in headers.splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
                    break
            body_start = header_end + 4
            body_end = body_start + length
            if length <= 0 or body_end > len(data):
                break
            try:
                message = json.loads(data[body_start:body_end].decode("utf-8"))
            except json.JSONDecodeError:
                index = body_end
                continue
            message_id = message.get("id")
            if isinstance(message_id, int):
                responses[message_id] = message
            index = body_end
        return responses


class McpStdioSession:
    """Reusable JSON-RPC stdio session for multiple MCP batches."""

    def __init__(self, command: list[str], *, cwd: str, timeout_seconds: float, env: dict[str, str] | None = None) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.env = _build_process_env(env)
        self.process: subprocess.Popen[bytes] | None = None
        self.stdin: BufferedWriter | None = None
        self.stdout: BufferedReader | None = None
        self.stderr: BufferedReader | None = None
        self._stderr_tail = bytearray()
        self._stderr_thread: threading.Thread | None = None

    def __enter__(self) -> "McpStdioSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return
        logger.info(
            "mcp stdio call start executable=%s cwd=%s timeout_seconds=%s request_count=%s",
            self.command[0] if self.command else "",
            self.cwd,
            self.timeout_seconds,
            0,
        )
        try:
            process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            logger.exception(
                "mcp stdio executable start failed executable=%s cwd=%s",
                self.command[0] if self.command else "",
                self.cwd,
            )
            raise RuntimeError(
                f"MCP 可执行命令启动失败: executable={self.command[0]} cwd={self.cwd} error={error}"
            ) from error
        assert process.stdin is not None and process.stdout is not None
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr
        if process.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process.stderr,),
                name="mcp-stdio-stderr-drain",
                daemon=True,
            )
            self._stderr_thread.start()

    def call_many(self, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        self.start()
        assert self.stdin is not None and self.stdout is not None
        logger.info(
            "mcp stdio session batch executable=%s cwd=%s request_count=%s",
            self.command[0] if self.command else "",
            self.cwd,
            len(requests),
        )
        return self._exchange_messages_with_timeout(requests)

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            try:
                if self.stdin is not None:
                    self.stdin.close()
            except OSError:
                pass
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            stderr = self._stderr_text()
            logger.error(
                "mcp stdio call timeout executable=%s cwd=%s timeout_seconds=%s stderr=%s",
                self.command[0] if self.command else "",
                self.cwd,
                self.timeout_seconds,
                stderr,
            )
            raise RuntimeError(f"MCP 调用超时: timeout_seconds={self.timeout_seconds}") from error
        if process.returncode != 0:
            stderr = self._stderr_text()
            logger.error(
                "mcp stdio call failed executable=%s cwd=%s return_code=%s stderr=%s",
                self.command[0] if self.command else "",
                self.cwd,
                process.returncode,
                stderr,
            )
            raise RuntimeError(stderr or str(process.returncode))
        logger.info(
            "mcp stdio call finish executable=%s cwd=%s response_count=%s",
            self.command[0] if self.command else "",
            self.cwd,
            0,
        )
        self.process = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.2)
        self._stderr_thread = None

    def _exchange_messages_with_timeout(self, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                assert self.stdin is not None and self.stdout is not None
                result_queue.put(("result", _exchange_messages(self.stdin, self.stdout, requests)))
            except BaseException as error:  # noqa: BLE001 - propagate worker failures to caller.
                result_queue.put(("error", error))

        thread = threading.Thread(target=worker, name="mcp-stdio-response-reader", daemon=True)
        thread.start()
        thread.join(timeout=max(0.001, float(self.timeout_seconds or 0)))
        if thread.is_alive():
            process = self.process
            if process is not None:
                logger.error(
                    "mcp stdio response timeout executable=%s cwd=%s timeout_seconds=%s stderr_tail=%s",
                    self.command[0] if self.command else "",
                    self.cwd,
                    self.timeout_seconds,
                    self._stderr_text(),
                )
                try:
                    process.kill()
                except OSError:
                    pass
            raise RuntimeError(f"MCP 调用超时: timeout_seconds={self.timeout_seconds}")
        kind, payload = result_queue.get()
        if kind == "error":
            stderr = self._stderr_text()
            logger.error(
                "mcp stdio communication failed executable=%s cwd=%s error=%s stderr_tail=%s",
                self.command[0] if self.command else "",
                self.cwd,
                payload,
                stderr,
            )
            if isinstance(payload, BaseException):
                raise RuntimeError(f"MCP 通信失败: {payload}; stderr={stderr}") from payload
            raise RuntimeError(f"MCP 通信失败: {payload}; stderr={stderr}")
        return dict(payload) if isinstance(payload, dict) else {}

    def _drain_stderr(self, stderr: BufferedReader) -> None:
        try:
            while True:
                chunk = stderr.read(4096)
                if not chunk:
                    return
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-8192]
        except OSError:
            return

    def _stderr_text(self) -> str:
        return bytes(self._stderr_tail).decode("utf-8", errors="ignore")[-800:]


def _exchange_messages(
    stdin: BufferedWriter,
    stdout: BufferedReader,
    requests: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    responses: dict[int, dict[str, Any]] = {}
    for request in requests:
        stdin.write(_encode_message(request))
        stdin.flush()
        request_id = request.get("id")
        if not isinstance(request_id, int):
            continue
        response = _read_response_for(stdout, request_id)
        if response is not None:
            responses[request_id] = response
    return responses


def _read_response_for(stdout: BufferedReader, expected_id: int) -> dict[str, Any] | None:
    while True:
        message = _read_one_message(stdout)
        if message is None:
            return None
        message_id = message.get("id")
        if isinstance(message_id, int):
            return message


def _read_one_message(stdout: BufferedReader) -> dict[str, Any] | None:
    header_bytes = bytearray()
    while b"\r\n\r\n" not in header_bytes:
        chunk = stdout.read(1)
        if not chunk:
            return None
        header_bytes.extend(chunk)
    header_blob, _, _ = header_bytes.partition(b"\r\n\r\n")
    headers = header_blob.decode("ascii", errors="ignore")
    length = 0
    for line in headers.splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            break
    if length <= 0:
        return None
    body = stdout.read(length)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.warning("mcp stdio decode failed body=%s", body.decode("utf-8", errors="ignore")[:400])
        return None


def _encode_message(message: dict[str, Any]) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _build_process_env(overrides: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in (overrides or {}).items() if str(key)})

    # Windows may default Python subprocess text decoding to GBK. GitNexus MCP
    # can launch Python helpers, so force UTF-8 at the process boundary.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    return env
