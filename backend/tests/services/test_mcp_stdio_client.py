from app.services.mcp_stdio_client import McpStdioClient


def test_mcp_stdio_client_decodes_jsonrpc_stream() -> None:
    client = McpStdioClient(["echo"], cwd=".", timeout_seconds=5)
    body = b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}'
    encoded = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    responses = client._decode_messages(encoded)

    assert responses[7]["result"]["ok"] is True
