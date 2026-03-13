from pathlib import Path
import logging
import json

import httpx
import pytest

from app.services.expert_registry import ExpertRegistry
from app.services.llm_chat_service import LLMChatService
from app.services.runtime_settings_service import RuntimeSettingsService


def test_custom_expert_without_llm_override_inherits_runtime_defaults(storage_root: Path):
    runtime = RuntimeSettingsService(storage_root)
    runtime.update(
        {
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-sp-18ef22cce0a24275a54eb6d97574c366",
        }
    )

    registry = ExpertRegistry(storage_root / "experts")
    expert = registry.create(
        {
            "expert_id": "custom_frontend",
            "name": "Custom Frontend Reviewer",
            "name_zh": "自定义前端专家",
            "role": "frontend",
            "provider": "",
            "api_base_url": "",
            "api_key_env": "",
            "model": "",
        }
    )

    assert expert.provider is None
    assert expert.api_base_url is None
    assert expert.api_key_env is None
    assert expert.model is None

    resolution = LLMChatService().resolve_expert(expert, runtime.get())
    assert resolution.provider == "dashscope-openai-compatible"
    assert resolution.base_url == "https://coding.dashscope.aliyuncs.com/v1"
    assert resolution.model == "kimi-k2.5"
    assert resolution.api_key_env == "DASHSCOPE_API_KEY"
    assert resolution.api_key == "sk-sp-18ef22cce0a24275a54eb6d97574c366"


def test_llm_chat_uses_runtime_api_key_when_env_is_missing(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    captured: dict[str, str] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "ok",
                        }
                    }
                ]
            }

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            captured["authorization"] = headers["Authorization"]
            captured["url"] = url
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-sp-18ef22cce0a24275a54eb6d97574c366",
        }
    )

    result = service.complete_text(
        system_prompt="sys",
        user_prompt="user",
        resolution=service.resolve_main_agent(runtime),
        fallback_text="fallback",
        allow_fallback=False,
    )

    assert result.mode == "live"
    assert captured["authorization"] == "Bearer sk-sp-18ef22cce0a24275a54eb6d97574c366"
    assert captured["url"] == "https://coding.dashscope.aliyuncs.com/v1/chat/completions"


def test_llm_chat_logs_request_and_response_previews(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok from llm"}}]}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "ok from llm"}}]}

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
        }
    )

    with caplog.at_level(logging.INFO):
        result = service.complete_text(
            system_prompt="system prompt",
            user_prompt="user prompt",
            resolution=service.resolve_main_agent(runtime),
            fallback_text="fallback",
            allow_fallback=False,
            log_context={"review_id": "rev_test", "expert_id": "correctness_business"},
        )

    assert result.mode == "live"
    assert "llm request send" in caplog.text
    assert "llm response received" in caplog.text
    assert "llm response parsed" in caplog.text
    assert '"review_id": "rev_test"' in caplog.text


def test_llm_chat_raises_clear_error_for_invalid_json_response(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "text/plain"}
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            raise json.JSONDecodeError("Expecting value", "", 0)

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
        }
    )

    with pytest.raises(RuntimeError) as exc_info:
        service.complete_text(
            system_prompt="system prompt",
            user_prompt="user prompt",
            resolution=service.resolve_main_agent(runtime),
            fallback_text="fallback",
            allow_fallback=False,
            log_context={"review_id": "rev_invalid_json"},
        )

    message = str(exc_info.value)
    assert "invalid_json_response" in message
    assert "content_type=text/plain" in message
