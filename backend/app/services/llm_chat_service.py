from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.runtime_settings import RuntimeSettings


@dataclass
class LLMResolution:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key: str | None = None


@dataclass
class LLMTextResult:
    text: str
    mode: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    error: str = ""


class LLMChatService:
    def resolve_expert(self, expert: ExpertProfile, runtime: RuntimeSettings) -> LLMResolution:
        return LLMResolution(
            provider=expert.provider or runtime.default_llm_provider or settings.DEFAULT_LLM_PROVIDER,
            model=expert.model or runtime.default_llm_model or settings.DEFAULT_LLM_MODEL,
            base_url=expert.api_base_url or runtime.default_llm_base_url or settings.DEFAULT_LLM_BASE_URL,
            api_key=expert.api_key or runtime.default_llm_api_key,
            api_key_env=expert.api_key_env
            or runtime.default_llm_api_key_env
            or settings.DEFAULT_LLM_API_KEY_ENV,
        )

    def resolve_main_agent(self, runtime: RuntimeSettings) -> LLMResolution:
        return LLMResolution(
            provider=runtime.default_llm_provider or settings.DEFAULT_LLM_PROVIDER,
            model=runtime.default_llm_model or settings.DEFAULT_LLM_MODEL,
            base_url=runtime.default_llm_base_url or settings.DEFAULT_LLM_BASE_URL,
            api_key_env=runtime.default_llm_api_key_env or settings.DEFAULT_LLM_API_KEY_ENV,
            api_key=runtime.default_llm_api_key,
        )

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        resolution: LLMResolution,
        fallback_text: str,
        temperature: float = 0.2,
        allow_fallback: bool = False,
    ) -> LLMTextResult:
        api_key = (resolution.api_key or "").strip() or os.getenv(resolution.api_key_env, "").strip()
        if not api_key:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error=f"missing_api_key:{resolution.api_key_env}",
                allow_fallback=allow_fallback,
            )

        request_body = {
            "model": resolution.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        endpoint = resolution.base_url.rstrip("/") + "/chat/completions"
        payload = None
        last_error = ""
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0, read=60.0)) as client:
                    response = client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    break
            except httpx.TimeoutException as exc:  # pragma: no cover - network dependent
                last_error = f"request_timeout:{exc}"
                if attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
            except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
                last_error = f"http_status:{exc.response.status_code}"
                if 500 <= exc.response.status_code < 600 and attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
                break
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = f"request_failed:{exc}"
                break
        if payload is None:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error=last_error or "request_failed:unknown",
                allow_fallback=allow_fallback,
            )

        choices = payload.get("choices") or []
        if not choices:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error="empty_choices",
                allow_fallback=allow_fallback,
            )
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error="empty_content",
                allow_fallback=allow_fallback,
            )
        return LLMTextResult(
            text=content,
            mode="live",
            provider=resolution.provider,
            model=resolution.model,
            base_url=resolution.base_url,
            api_key_env=resolution.api_key_env,
        )

    def _handle_failure(
        self,
        *,
        resolution: LLMResolution,
        fallback_text: str,
        error: str,
        allow_fallback: bool,
    ) -> LLMTextResult:
        if allow_fallback:
            return self._fallback(resolution, fallback_text, error)
        raise RuntimeError(
            f"LLM live call required but failed for model={resolution.model}, provider={resolution.provider}: {error}"
        )

    def _fallback(
        self,
        resolution: LLMResolution,
        fallback_text: str,
        error: str,
    ) -> LLMTextResult:
        return LLMTextResult(
            text=fallback_text,
            mode="fallback",
            provider=resolution.provider,
            model=resolution.model,
            base_url=resolution.base_url,
            api_key_env=resolution.api_key_env,
            error=error,
        )
