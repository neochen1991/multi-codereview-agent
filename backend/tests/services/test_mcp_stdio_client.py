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
    stdout = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    with patch("shutil.which", lambda executable: "C:\\GitNexus\\gitnexus.exe" if executable == "gitnexus" else None):
        with patch("subprocess.run") as fake_run:
            fake_run.return_value.returncode = 0
            fake_run.return_value.stdout = stdout
            fake_run.return_value.stderr = b""

            responses = client.call_many([{"id": 1, "method": "tools/list", "params": {}}])

    assert responses[1]["result"]["ok"] is True
    called_command = fake_run.call_args.args[0]
    assert called_command == ["C:\\GitNexus\\gitnexus.exe", "mcp"]
