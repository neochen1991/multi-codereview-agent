from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.http_client_factory import HttpClientFactory

logger = logging.getLogger(__name__)


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
    _PREVIEW_LIMIT = 1600

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
        runtime_settings: RuntimeSettings | None = None,
        fallback_text: str,
        temperature: float = 0.2,
        allow_fallback: bool = False,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        log_context: dict[str, object] | None = None,
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
        safe_attempts = max(1, int(max_attempts or 1))
        safe_timeout = max(10.0, float(timeout_seconds or 60.0))
        request_preview = self._build_request_preview(request_body, system_prompt=system_prompt, user_prompt=user_prompt)
        context_preview = self._stringify_context(log_context)
        for attempt in range(1, safe_attempts + 1):
            try:
                logger.info(
                    "llm request send context=%s attempt=%s/%s provider=%s model=%s endpoint=%s timeout_seconds=%s request=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    endpoint,
                    safe_timeout,
                    request_preview,
                )
                with HttpClientFactory.create(
                    timeout=httpx.Timeout(safe_timeout, connect=min(10.0, safe_timeout), read=safe_timeout),
                    runtime_settings=runtime_settings,
                ) as client:
                    response = client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                    response.raise_for_status()
                    response_text = getattr(response, "text", "")
                    response_preview = self._truncate(response_text)
                    content_type = str((getattr(response, "headers", {}) or {}).get("Content-Type", ""))
                    logger.info(
                        "llm response received context=%s attempt=%s/%s status=%s content_type=%s body=%s",
                        context_preview,
                        attempt,
                        safe_attempts,
                        getattr(response, "status_code", "unknown"),
                        content_type,
                        response_preview,
                    )
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        last_error = (
                            "invalid_json_response:"
                            f"status={getattr(response, 'status_code', 'unknown')},"
                            f"content_type={content_type or 'unknown'},"
                            f"body={response_preview or '<empty>'}"
                        )
                        logger.exception(
                            "llm response json decode failed context=%s attempt=%s/%s error=%s",
                            context_preview,
                            attempt,
                            safe_attempts,
                            exc,
                        )
                        break
                    break
            except httpx.TimeoutException as exc:  # pragma: no cover - network dependent
                last_error = f"request_timeout:{exc}"
                logger.warning(
                    "llm request timeout context=%s attempt=%s/%s provider=%s model=%s error=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    exc,
                )
                if attempt < safe_attempts:
                    time.sleep(1.5 * attempt)
                    continue
            except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
                last_error = f"http_status:{exc.response.status_code}"
                logger.warning(
                    "llm request status failure context=%s attempt=%s/%s provider=%s model=%s status=%s body=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    exc.response.status_code,
                    self._truncate(getattr(exc.response, "text", "")),
                )
                if 500 <= exc.response.status_code < 600 and attempt < safe_attempts:
                    time.sleep(1.5 * attempt)
                    continue
                break
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = f"request_failed:{exc}"
                logger.exception(
                    "llm request failed context=%s attempt=%s/%s provider=%s model=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                )
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
        logger.info(
            "llm response parsed context=%s provider=%s model=%s choices=%s content=%s",
            context_preview,
            resolution.provider,
            resolution.model,
            len(choices),
            self._truncate(content),
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

    def _build_request_preview(
        self,
        request_body: dict[str, object],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        preview = {
            "model": request_body.get("model"),
            "temperature": request_body.get("temperature"),
            "system_prompt": self._truncate(system_prompt),
            "user_prompt": self._truncate(user_prompt),
        }
        return self._safe_json(preview)

    def _safe_json(self, value: object) -> str:
        try:
            return self._truncate(json.dumps(value, ensure_ascii=False))
        except Exception:
            return self._truncate(repr(value))

    def _stringify_context(self, log_context: dict[str, object] | None) -> str:
        if not log_context:
            return "-"
        return self._safe_json(log_context)

    def _truncate(self, value: object, limit: int | None = None) -> str:
        text = str(value or "")
        max_length = limit or self._PREVIEW_LIMIT
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}...<truncated>"

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
