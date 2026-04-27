from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any


class McpStdioClient:
    """Lightweight JSON-RPC stdio client for MCP servers."""

    def __init__(self, command: list[str], *, cwd: str, timeout_seconds: int) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds

    def call_many(self, requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        executable = self.command[0] if self.command else ""
        resolved_executable = shutil.which(executable) if executable else None
        if executable and resolved_executable is None:
            raise RuntimeError(f"MCP 可执行命令不存在: {executable}")
        command = [resolved_executable or executable, *self.command[1:]]
        payload = b"".join(self._encode_message(request) for request in requests)
        try:
            completed = subprocess.run(
                command,
                input=payload,
                cwd=self.cwd,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"MCP 可执行命令启动失败: executable={command[0]} cwd={self.cwd} error={error}"
            ) from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="ignore")[-800:]
            raise RuntimeError(stderr or str(completed.returncode))
        return self._decode_messages(completed.stdout)

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
