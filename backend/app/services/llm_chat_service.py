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
        """解析专家本轮应该使用的模型配置。"""
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
        """统一的文本补全入口，兼容 JSON 与 SSE 两类返回。"""
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
        client_timeout = self._build_http_timeout(safe_timeout)
        request_preview = self._build_request_preview(request_body, system_prompt=system_prompt, user_prompt=user_prompt)
        context_preview = self._stringify_context(log_context)
        total_started_at = time.perf_counter()
        for attempt in range(1, safe_attempts + 1):
            attempt_started_at = time.perf_counter()
            try:
                logger.info(
                    "llm request send context=%s attempt=%s/%s provider=%s model=%s endpoint=%s timeout_seconds=%s connect_timeout=%s read_timeout=%s write_timeout=%s pool_timeout=%s request=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    endpoint,
                    safe_timeout,
                    client_timeout.connect,
                    client_timeout.read,
                    client_timeout.write,
                    client_timeout.pool,
                    request_preview,
                )
                with HttpClientFactory.create(
                    timeout=client_timeout,
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
                    attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                    logger.info(
                        "llm response received context=%s attempt=%s/%s status=%s content_type=%s attempt_elapsed_ms=%s body=%s",
                        context_preview,
                        attempt,
                        safe_attempts,
                        getattr(response, "status_code", "unknown"),
                        content_type,
                        attempt_elapsed_ms,
                        response_preview,
                    )
                    try:
                        payload = self._decode_payload(
                            response_text=response_text,
                            content_type=content_type,
                        )
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
                timeout_kind = self._classify_timeout_exception(exc)
                attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
                logger.warning(
                    "llm request timeout context=%s attempt=%s/%s provider=%s model=%s timeout_kind=%s attempt_elapsed_ms=%s total_elapsed_ms=%s error=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    timeout_kind,
                    attempt_elapsed_ms,
                    total_elapsed_ms,
                    exc,
                )
                if attempt < safe_attempts:
                    time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
                    continue
            except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
                last_error = f"http_status:{exc.response.status_code}"
                attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                logger.warning(
                    "llm request status failure context=%s attempt=%s/%s provider=%s model=%s status=%s attempt_elapsed_ms=%s body=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    exc.response.status_code,
                    attempt_elapsed_ms,
                    self._truncate(getattr(exc.response, "text", "")),
                )
                if 500 <= exc.response.status_code < 600 and attempt < safe_attempts:
                    time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
                    continue
                break
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = f"request_failed:{exc}"
                attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                logger.exception(
                    "llm request failed context=%s attempt=%s/%s provider=%s model=%s attempt_elapsed_ms=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    attempt_elapsed_ms,
                )
                break
        if payload is None:
            total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
            logger.warning(
                "llm request exhausted context=%s provider=%s model=%s attempts=%s total_elapsed_ms=%s error=%s",
                context_preview,
                resolution.provider,
                resolution.model,
                safe_attempts,
                total_elapsed_ms,
                last_error or "request_failed:unknown",
            )
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
        content = self._extract_content(message.get("content")).strip()
        if not content:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error="empty_content",
                allow_fallback=allow_fallback,
            )
        total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
        logger.info(
            "llm response parsed context=%s provider=%s model=%s choices=%s total_elapsed_ms=%s content=%s",
            context_preview,
            resolution.provider,
            resolution.model,
            len(choices),
            total_elapsed_ms,
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

    def _build_http_timeout(self, timeout_seconds: float) -> httpx.Timeout:
        """针对内网和流式响应构造更稳妥的 httpx 超时参数。"""

        safe_timeout = max(10.0, float(timeout_seconds or 60.0))
        connect_timeout = min(45.0, max(15.0, round(safe_timeout * 0.3, 2)))
        read_timeout = round(safe_timeout + max(20.0, connect_timeout - 6.0), 2)
        write_timeout = connect_timeout
        pool_timeout = connect_timeout
        return httpx.Timeout(
            timeout=safe_timeout,
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )

    def _classify_timeout_exception(self, exc: httpx.TimeoutException) -> str:
        """把 httpx 超时异常归类成更易排查的日志标签。"""

        if isinstance(exc, httpx.ConnectTimeout):
            return "connect_timeout"
        if isinstance(exc, httpx.ReadTimeout):
            return "read_timeout"
        if isinstance(exc, httpx.WriteTimeout):
            return "write_timeout"
        if isinstance(exc, httpx.PoolTimeout):
            return "pool_timeout"
        return "timeout"

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

    def _decode_payload(self, *, response_text: str, content_type: str) -> dict[str, object]:
        cleaned = response_text.lstrip("\ufeff").strip()
        if not cleaned:
            raise ValueError("empty_response_body")
        lower_content_type = content_type.lower()
        if "text/event-stream" in lower_content_type or cleaned.startswith("data:"):
            logger.info("llm response parser selected parser=sse")
            return self._decode_sse_payload(cleaned)
        logger.info("llm response parser selected parser=json")
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("json_payload_not_object")
        return payload

    def _decode_sse_payload(self, response_text: str) -> dict[str, object]:
        chunks: list[dict[str, object]] = []
        accumulated_text_parts: list[str] = []
        for raw_line in response_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if isinstance(chunk, dict):
                chunks.append(chunk)
                chunk_text = self._extract_text_from_chunk(chunk)
                if chunk_text:
                    accumulated_text_parts.append(chunk_text)
        if not chunks:
            raise ValueError("sse_no_data_chunks")
        for chunk in reversed(chunks):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            message = choices[0].get("message") or {}
            content = self._extract_content(message.get("content")).strip()
            if content:
                return chunk
        accumulated_text = "".join(accumulated_text_parts).strip()
        if accumulated_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": accumulated_text,
                        }
                    }
                ]
            }
        raise ValueError("sse_no_message_content")

    def _extract_text_from_chunk(self, chunk: dict[str, object]) -> str:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        message_content = self._extract_content(message.get("content"))
        if message_content:
            return message_content
        delta = choice.get("delta") or {}
        return self._extract_content(delta.get("content"))

    def _extract_content(self, content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(str(item["text"]))
                        continue
                    if isinstance(item.get("content"), str):
                        parts.append(str(item["content"]))
                        continue
                    if isinstance(item.get("delta"), str):
                        parts.append(str(item["delta"]))
                        continue
            return "".join(parts)
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return str(content["text"])
            if isinstance(content.get("content"), str):
                return str(content["content"])
        return str(content)

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
