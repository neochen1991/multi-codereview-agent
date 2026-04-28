from __future__ import annotations

import json
import logging
import shutil
import subprocess
from io import BufferedReader, BufferedWriter
from typing import Any

logger = logging.getLogger(__name__)


class McpStdioClient:
    """Lightweight JSON-RPC stdio client for MCP servers."""

    def __init__(self, command: list[str], *, cwd: str, timeout_seconds: int, env: dict[str, str] | None = None) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.env = dict(env or {})

    def call_many(self, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        executable = self.command[0] if self.command else ""
        resolved_executable = shutil.which(executable) if executable else None
        if executable and resolved_executable is None:
            raise RuntimeError(f"MCP 可执行命令不存在: {executable}")
        command = [resolved_executable or executable, *self.command[1:]]
        logger.info(
            "mcp stdio call start executable=%s cwd=%s timeout_seconds=%s request_count=%s",
            command[0] if command else "",
            self.cwd,
            self.timeout_seconds,
            len(requests),
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=self.cwd,
                env=self.env or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            logger.exception(
                "mcp stdio executable start failed executable=%s cwd=%s",
                command[0] if command else "",
                self.cwd,
            )
            raise RuntimeError(
                f"MCP 可执行命令启动失败: executable={command[0]} cwd={self.cwd} error={error}"
            ) from error
        assert process.stdin is not None
        assert process.stdout is not None
        responses: dict[int, dict[str, Any]] = {}
        try:
            responses = self._exchange_messages(process.stdin, process.stdout, requests)
            try:
                process.stdin.close()
            except OSError:
                pass
            _, stderr_bytes = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            _, stderr_bytes = process.communicate()
            stderr = stderr_bytes.decode("utf-8", errors="ignore")[-800:]
            logger.error(
                "mcp stdio call timeout executable=%s cwd=%s timeout_seconds=%s stderr=%s",
                command[0] if command else "",
                self.cwd,
                self.timeout_seconds,
                stderr,
            )
            raise RuntimeError(f"MCP 调用超时: timeout_seconds={self.timeout_seconds}") from error
        if process.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="ignore")[-800:]
            logger.error(
                "mcp stdio call failed executable=%s cwd=%s return_code=%s stderr=%s",
                command[0] if command else "",
                self.cwd,
                process.returncode,
                stderr,
            )
            raise RuntimeError(stderr or str(process.returncode))
        logger.info(
            "mcp stdio call finish executable=%s cwd=%s response_count=%s",
            command[0] if command else "",
            self.cwd,
            len(responses),
        )
        return responses

    def _exchange_messages(
        self,
        stdin: BufferedWriter,
        stdout: BufferedReader,
        requests: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        responses: dict[int, dict[str, Any]] = {}
        for request in requests:
            stdin.write(self._encode_message(request))
            stdin.flush()
            request_id = request.get("id")
            if not isinstance(request_id, int):
                continue
            response = self._read_response_for(stdout, request_id)
            if response is not None:
                responses[request_id] = response
        return responses

    def _read_response_for(self, stdout: BufferedReader, expected_id: int) -> dict[str, Any] | None:
        while True:
            message = self._read_one_message(stdout)
            if message is None:
                return None
            message_id = message.get("id")
            if isinstance(message_id, int):
                return message

    def _read_one_message(self, stdout: BufferedReader) -> dict[str, Any] | None:
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

    def _encode_message(self, message: dict[str, Any]) -> bytes:
        body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode("utf-8")
        return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

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
