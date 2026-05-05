from io import BytesIO
import time
from unittest.mock import patch

import pytest

from app.services.mcp_stdio_client import McpStdioClient


def test_mcp_stdio_client_decodes_jsonrpc_stream() -> None:
    client = McpStdioClient(["echo"], cwd=".", timeout_seconds=5)
    body = b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}'
    encoded = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    responses = client._decode_messages(encoded)

    assert responses[7]["result"]["ok"] is True


def test_mcp_stdio_client_uses_resolved_executable_path() -> None:
    client = McpStdioClient(["gitnexus", "mcp"], cwd=".", timeout_seconds=5)
    body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    stdout = BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)

    with patch("shutil.which", lambda executable: "C:\\GitNexus\\gitnexus.exe" if executable == "gitnexus" else None):
        with patch("subprocess.Popen") as fake_popen:
            fake_process = fake_popen.return_value
            fake_process.stdin = BytesIO()
            fake_process.stdout = stdout
            fake_process.stderr = BytesIO()
            fake_process.communicate.return_value = (b"", b"")
            fake_process.returncode = 0

            responses = client.call_many([{"id": 1, "method": "tools/list", "params": {}}])

    assert responses[1]["result"]["ok"] is True
    called_command = fake_popen.call_args.args[0]
    assert called_command == ["C:\\GitNexus\\gitnexus.exe", "mcp"]


def test_mcp_stdio_client_forces_utf8_process_environment() -> None:
    client = McpStdioClient(
        ["gitnexus", "mcp"],
        cwd=".",
        timeout_seconds=5,
        env={"CUSTOM_FLAG": "1", "PYTHONIOENCODING": "gbk"},
    )
    body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    stdout = BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)

    with patch("shutil.which", lambda executable: "C:\\GitNexus\\gitnexus.exe" if executable == "gitnexus" else None):
        with patch("subprocess.Popen") as fake_popen:
            fake_process = fake_popen.return_value
            fake_process.stdin = BytesIO()
            fake_process.stdout = stdout
            fake_process.stderr = BytesIO()
            fake_process.returncode = 0

            responses = client.call_many([{"id": 1, "method": "tools/list", "params": {}}])

    assert responses[1]["result"]["ok"] is True
    process_env = fake_popen.call_args.kwargs["env"]
    assert process_env["CUSTOM_FLAG"] == "1"
    assert process_env["PYTHONUTF8"] == "1"
    assert process_env["PYTHONIOENCODING"] == "utf-8"
    assert process_env["NO_COLOR"] == "1"
    assert process_env["FORCE_COLOR"] == "0"


def test_mcp_stdio_session_times_out_when_server_stops_responding() -> None:
    client = McpStdioClient(["gitnexus", "mcp"], cwd=".", timeout_seconds=0.05)

    def slow_read(_stdout):
        time.sleep(0.2)
        return None

    with patch("shutil.which", lambda executable: "C:\\GitNexus\\gitnexus.exe" if executable == "gitnexus" else None):
        with patch("app.services.mcp_stdio_client._read_one_message", slow_read):
            with patch("subprocess.Popen") as fake_popen:
                fake_process = fake_popen.return_value
                fake_process.stdin = BytesIO()
                fake_process.stdout = BytesIO()
                fake_process.stderr = BytesIO()
                fake_process.poll.return_value = None
                fake_process.wait.return_value = 0
                fake_process.returncode = 0

                started = time.monotonic()
                with pytest.raises(RuntimeError, match="MCP 调用超时"):
                    client.call_many([{"id": 1, "method": "tools/list", "params": {}}])

    assert time.monotonic() - started < 0.18
    fake_process.kill.assert_called()
