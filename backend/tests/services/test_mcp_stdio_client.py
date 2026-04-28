from io import BytesIO
from unittest.mock import patch

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
