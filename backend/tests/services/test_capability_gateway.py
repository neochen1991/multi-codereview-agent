from app.services.capability_gateway import CapabilityGateway


def test_capability_gateway_invokes_registered_tool():
    gateway = CapabilityGateway()
    gateway.register_tool("echo", lambda payload: {"value": payload["value"]})
    result = gateway.invoke("echo", {"value": "ok"})
    assert result == {"value": "ok"}


def test_capability_gateway_rejects_unregistered_tool():
    gateway = CapabilityGateway()

    try:
        gateway.invoke("missing_tool", {})
    except KeyError as error:
        assert "missing_tool" in str(error)
    else:
        raise AssertionError("expected KeyError for unregistered tool")


def test_capability_gateway_invokes_registered_skill_binding():
    gateway = CapabilityGateway()
    gateway.register("summarize", "skill", lambda payload: {"summary": payload["text"][:5]})
    result = gateway.invoke_binding("summarize", {"text": "abcdef"})
    assert result == {"summary": "abcde"}
