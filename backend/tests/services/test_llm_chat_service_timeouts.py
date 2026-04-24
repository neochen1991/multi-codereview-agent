from app.services.llm_chat_service import LLMChatService


def test_build_http_timeout_keeps_read_timeout_within_business_deadline() -> None:
    service = LLMChatService()

    timeout = service._build_http_timeout(90.0)

    assert timeout.connect == 30.0
    assert timeout.read == 90.0
    assert timeout.write == 30.0
    assert timeout.pool == 30.0
