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
    assert "attempt_elapsed_ms=" in caplog.text
    assert "total_elapsed_ms=" in caplog.text
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


def test_llm_chat_accepts_sse_chunks(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}
        text = (
            'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
            "data: [DONE]\n\n"
        )

        def raise_for_status(self) -> None:
            return None

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

    result = service.complete_text(
        system_prompt="system prompt",
        user_prompt="user prompt",
        resolution=service.resolve_main_agent(runtime),
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"review_id": "rev_sse"},
    )

    assert result.mode == "live"
    assert result.text == "hello world"


def test_llm_chat_accepts_json_with_list_content_blocks(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = (
            '{"choices":[{"message":{"content":['
            '{"type":"text","text":"first "},'
            '{"type":"text","text":"second"}'
            ']}}]}'
        )

        def raise_for_status(self) -> None:
            return None

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

    result = service.complete_text(
        system_prompt="system prompt",
        user_prompt="user prompt",
        resolution=service.resolve_main_agent(runtime),
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"review_id": "rev_blocks"},
    )

    assert result.mode == "live"
    assert result.text == "first second"


def test_llm_chat_extracts_usage_tokens_and_logs_them(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = (
            '{"choices":[{"message":{"content":"ok"}}],'
            '"usage":{"prompt_tokens":123,"completion_tokens":45,"total_tokens":168}}'
        )

        def raise_for_status(self) -> None:
            return None

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
            log_context={"review_id": "rev_usage"},
        )

    assert result.mode == "live"
    assert result.prompt_tokens == 123
    assert result.completion_tokens == 45
    assert result.total_tokens == 168
    assert result.call_id.startswith("llm_")
    assert "prompt_tokens=123" in caplog.text
    assert "completion_tokens=45" in caplog.text
    assert "total_tokens=168" in caplog.text


def test_llm_chat_uses_intranet_friendly_httpx_timeouts(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["timeout"] = kwargs.get("timeout")

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

    result = service.complete_text(
        system_prompt="system prompt",
        user_prompt="user prompt",
        resolution=service.resolve_main_agent(runtime),
        fallback_text="fallback",
        allow_fallback=False,
        timeout_seconds=120,
    )

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 40.0
    assert timeout.read == 180.0
    assert timeout.write == 40.0
    assert timeout.pool == 40.0
    assert result.mode == "live"


def test_llm_chat_logs_timeout_kind_and_elapsed(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            raise httpx.ReadTimeout("stream stalled")

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
            allow_fallback=True,
            max_attempts=1,
            log_context={"review_id": "rev_timeout"},
        )

    assert result.mode == "fallback"
    assert "timeout_kind=read_timeout" in caplog.text
    assert "attempt_elapsed_ms=" in caplog.text
    assert "total_elapsed_ms=" in caplog.text
    assert "llm request exhausted" in caplog.text


def test_llm_chat_retries_request_transport_errors_and_succeeds(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    attempts = {"count": 0}
    request = httpx.Request("POST", "https://coding.dashscope.aliyuncs.com/v1/chat/completions")

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok after retry"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError("temporary connect failure", request=request)
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
            max_attempts=2,
            log_context={"review_id": "rev_transport_retry"},
        )

    assert attempts["count"] == 2
    assert result.mode == "live"
    assert result.text == "ok after retry"
    assert "llm request transport failure" in caplog.text
    assert "request_error_kind=connect_error" in caplog.text


def test_llm_chat_classifies_windows_connection_abort_as_request_error(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    request = httpx.Request("POST", "https://coding.dashscope.aliyuncs.com/v1/chat/completions")

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            raise httpx.ReadError(
                "[WinError 10053] An established connection was aborted by the software in your host machine",
                request=request,
            )

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
            allow_fallback=True,
            max_attempts=1,
            log_context={"review_id": "rev_win_10053"},
        )

    assert result.mode == "fallback"
    assert result.error.startswith("request_transport_error:connection_aborted:")
    assert "request_error_kind=connection_aborted" in caplog.text
    assert "llm request exhausted" in caplog.text


def test_llm_chat_retries_windows_connection_aborted_os_error_and_succeeds(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    attempts = {"count": 0}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok after windows retry"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise ConnectionAbortedError(
                    "[WinError 10053] An established connection was aborted by the software in your host machine"
                )
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
            max_attempts=2,
            log_context={"review_id": "rev_win_abort_retry"},
        )

    assert attempts["count"] == 2
    assert result.mode == "live"
    assert result.text == "ok after windows retry"
    assert "request_error_kind=connection_aborted" in caplog.text
    assert "will_retry=True" in caplog.text


def test_llm_chat_retries_windows_connection_reset_os_error_and_succeeds(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    attempts = {"count": 0}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok after reset retry"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise ConnectionResetError(
                    "[WinError 10054] An existing connection was forcibly closed by the remote host"
                )
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
            max_attempts=2,
            log_context={"review_id": "rev_win_reset_retry"},
        )

    assert attempts["count"] == 2
    assert result.mode == "live"
    assert result.text == "ok after reset retry"
    assert "request_error_kind=connection_reset" in caplog.text
    assert "will_retry=True" in caplog.text


def test_llm_chat_compresses_prompt_in_light_mode(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    captured_payload: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            captured_payload.update(json)
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_analysis_mode": "light",
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
        }
    )

    long_system = "S" * 10000
    long_user = "中" * 160000
    result = service.complete_text(
        system_prompt=long_system,
        user_prompt=long_user,
        resolution=service.resolve_main_agent(runtime),
        runtime_settings=runtime,
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"analysis_mode": "light", "review_id": "rev_light_prompt_budget"},
    )

    assert result.mode == "live"
    assert isinstance(captured_payload.get("messages"), list)
    messages = captured_payload["messages"]  # type: ignore[index]
    assert isinstance(messages, list) and len(messages) == 2
    request_system = str((messages[0] or {}).get("content") or "")
    request_user = str((messages[1] or {}).get("content") or "")
    assert service._estimate_tokens(request_system) + service._estimate_tokens(request_user) <= 110000
    assert "[light prompt compressed]" in request_system or "[light prompt compressed]" in request_user


def test_llm_chat_uses_configured_light_prompt_budget(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    captured_payload: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            captured_payload.update(json)
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_analysis_mode": "light",
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
            "light_llm_max_input_tokens": 40000,
            "light_llm_max_prompt_chars": 30000,
        }
    )

    long_system = "S" * 12000
    long_user = "中" * 80000
    result = service.complete_text(
        system_prompt=long_system,
        user_prompt=long_user,
        resolution=service.resolve_main_agent(runtime),
        runtime_settings=runtime,
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"analysis_mode": "light", "review_id": "rev_light_prompt_budget_configured"},
    )

    assert result.mode == "live"
    messages = captured_payload["messages"]  # type: ignore[index]
    assert isinstance(messages, list) and len(messages) == 2
    request_system = str((messages[0] or {}).get("content") or "")
    request_user = str((messages[1] or {}).get("content") or "")
    assert service._estimate_tokens(request_system) + service._estimate_tokens(request_user) <= 40000
    assert len(request_system) + len(request_user) <= 30000


def test_light_prompt_compression_keeps_security_relevant_context():
    service = LLMChatService()
    user_prompt = (
        "目标 hunk:\n"
        "@@ -10,6 +10,20 @@\n"
        "+ public UserProfile loadUserProfile(HttpServletRequest request) {\n"
        '+   String jwtToken = request.getHeader("Authorization");\n'
        "+   String userId = tokenParser.parse(jwtToken);\n"
        '+   String sql = \"select * from users where user_id = ?\";\n'
        "+ }\n"
        "关键源码上下文:\n"
        + "\n".join(f"filler line {idx}" for idx in range(220))
        + "\npublic UserProfile loadUserProfile(HttpServletRequest request) {\n"
        + '  String jwtToken = request.getHeader("Authorization");\n'
        + "  validateJwtToken(jwtToken);\n"
        + '  String sql = \"select * from users where user_id = ?\";\n'
        + "}\n"
        + "\n".join(f"other filler {idx}" for idx in range(220))
        + "\npublic void runBatchSettlement() {\n"
        + "  for (Order item : orderList) {\n"
        + "    settlementService.process(item);\n"
        + "  }\n"
        + "}\n"
        "\n当前代码片段:\n"
        + "\n".join(f"snippet filler {idx}" for idx in range(180))
    )

    compressed = service._smart_compress_user_prompt(
        user_prompt,
        1200,
        log_context={"analysis_mode": "light", "expert_id": "security_compliance"},
    )

    assert "validateJwtToken" in compressed
    assert 'request.getHeader("Authorization")' in compressed
    assert 'select * from users where user_id = ?' in compressed


def test_light_prompt_compression_prioritizes_hunk_method_context():
    service = LLMChatService()
    user_prompt = (
        "目标 hunk:\n"
        "@@ -30,6 +30,18 @@\n"
        "+ public OrderResult processOrder(OrderCommand command) {\n"
        "+   validateCommand(command);\n"
        "+   return orderDomainService.processOrder(command);\n"
        "+ }\n"
        "代码仓上下文:\n"
        + "\n".join(f"context filler {idx}" for idx in range(260))
        + "\npublic OrderResult processOrder(OrderCommand command) {\n"
        + "  validateCommand(command);\n"
        + "  return orderDomainService.processOrder(command);\n"
        + "}\n"
        + "\n".join(f"noise line {idx}" for idx in range(260))
        + "\npublic void rebuildSearchIndex() {\n"
        + "  searchIndexer.rebuildAll();\n"
        + "}\n"
    )

    compressed = service._smart_compress_user_prompt(
        user_prompt,
        1000,
        log_context={"analysis_mode": "light", "expert_id": "architecture_design"},
    )

    assert "processOrder(OrderCommand command)" in compressed
    assert "validateCommand(command)" in compressed


def test_llm_chat_standard_mode_does_not_apply_light_summary(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    captured_payload: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            captured_payload.update(json)
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_analysis_mode": "standard",
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
        }
    )

    system_prompt = "《审视规范文档》开始\n" + ("规则A\n" * 40) + "《审视规范文档》结束"
    user_prompt = "规范提要:\n" + ("必须校验输入\n" * 60) + "JSON 字段要求:\n{}"
    result = service.complete_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        resolution=service.resolve_main_agent(runtime),
        runtime_settings=runtime,
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"analysis_mode": "standard", "review_id": "rev_standard_no_light_summary"},
    )

    assert result.mode == "live"
    messages = captured_payload.get("messages")
    assert isinstance(messages, list) and len(messages) == 2
    sent_system = str((messages[0] or {}).get("content") or "")
    sent_user = str((messages[1] or {}).get("content") or "")
    assert "[light summary: doc block compressed]" not in sent_system
    assert "[light summary: doc block compressed]" not in sent_user


def test_llm_chat_light_mode_summarizes_doc_block(monkeypatch, tmp_path: Path):
    service = LLMChatService()
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    captured_payload: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def raise_for_status(self) -> None:
            return None

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> DummyResponse:
            captured_payload.update(json)
            return DummyResponse()

    monkeypatch.setattr(httpx, "Client", DummyClient)

    runtime = RuntimeSettingsService(tmp_path / "storage").get().model_copy(
        update={
            "default_analysis_mode": "light",
            "default_llm_provider": "dashscope-openai-compatible",
            "default_llm_base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "default_llm_model": "kimi-k2.5",
            "default_llm_api_key_env": "DASHSCOPE_API_KEY",
            "default_llm_api_key": "sk-test",
        }
    )

    system_prompt = "《审视规范文档》开始\n" + ("规则A 必须校验输入\n" * 20000) + "《审视规范文档》结束"
    user_prompt = "规范提要:\n" + ("必须校验输入，禁止拼接SQL\n" * 8000) + "JSON 字段要求:\n{}"
    result = service.complete_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        resolution=service.resolve_main_agent(runtime),
        runtime_settings=runtime,
        fallback_text="fallback",
        allow_fallback=False,
        log_context={"analysis_mode": "light", "review_id": "rev_light_doc_summary"},
    )

    assert result.mode == "live"
    messages = captured_payload.get("messages")
    assert isinstance(messages, list) and len(messages) == 2
    sent_system = str((messages[0] or {}).get("content") or "")
    sent_user = str((messages[1] or {}).get("content") or "")
    assert "[light summary: doc block compressed]" in sent_system or "[light summary: doc block compressed]" in sent_user
