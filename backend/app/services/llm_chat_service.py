from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx

from app.config import settings
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.http_client_factory import HttpClientFactory
from app.services.memory_probe import MemoryProbe

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
    call_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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

        system_prompt, user_prompt = self._apply_prompt_budget_if_light_mode(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            runtime_settings=runtime_settings,
            log_context=log_context,
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
        call_id = f"llm_{uuid4().hex[:12]}"
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        preview_limit = self._effective_preview_limit(runtime_settings)
        request_preview = self._build_request_preview(
            request_body,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            preview_limit=preview_limit,
        )
        context_preview = self._stringify_context(log_context)
        MemoryProbe.log(
            "llm.complete_text.start",
            provider=resolution.provider,
            model=resolution.model,
            timeout_seconds=safe_timeout,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
        )
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
                    response_preview = self._truncate(response_text, preview_limit)
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
                    MemoryProbe.log(
                        "llm.complete_text.after_response",
                        provider=resolution.provider,
                        model=resolution.model,
                        status=getattr(response, "status_code", "unknown"),
                        response_len=len(response_text),
                    )
                    try:
                        payload = self._decode_payload(
                            response_text=response_text,
                            content_type=content_type,
                        )
                        prompt_tokens, completion_tokens, total_tokens = self._extract_usage(payload)
                        MemoryProbe.log(
                            "llm.complete_text.after_decode",
                            provider=resolution.provider,
                            model=resolution.model,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
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
                    "llm request timeout context=%s attempt=%s/%s provider=%s model=%s timeout_kind=%s will_retry=%s attempt_elapsed_ms=%s total_elapsed_ms=%s error=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    timeout_kind,
                    attempt < safe_attempts,
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
                    self._truncate(getattr(exc.response, "text", ""), preview_limit),
                )
                if 500 <= exc.response.status_code < 600 and attempt < safe_attempts:
                    time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
                    continue
                break
            except httpx.RequestError as exc:  # pragma: no cover - network dependent
                request_error_kind = self._classify_request_exception(exc)
                last_error = f"request_transport_error:{request_error_kind}:{exc}"
                attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
                logger.warning(
                    "llm request transport failure context=%s attempt=%s/%s provider=%s model=%s request_error_kind=%s will_retry=%s attempt_elapsed_ms=%s total_elapsed_ms=%s error=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    request_error_kind,
                    attempt < safe_attempts,
                    attempt_elapsed_ms,
                    total_elapsed_ms,
                    exc,
                )
                if attempt < safe_attempts:
                    time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
                    continue
                break
            except Exception as exc:  # pragma: no cover - network dependent
                generic_error_kind = self._classify_generic_transport_exception(exc)
                if generic_error_kind:
                    last_error = f"request_transport_error:{generic_error_kind}:{exc}"
                    attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                    total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
                    logger.warning(
                        "llm request transport failure context=%s attempt=%s/%s provider=%s model=%s request_error_kind=%s exc_type=%s will_retry=%s attempt_elapsed_ms=%s total_elapsed_ms=%s error=%s",
                        context_preview,
                        attempt,
                        safe_attempts,
                        resolution.provider,
                        resolution.model,
                        generic_error_kind,
                        type(exc).__name__,
                        attempt < safe_attempts,
                        attempt_elapsed_ms,
                        total_elapsed_ms,
                        exc,
                    )
                    if attempt < safe_attempts:
                        time.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))
                        continue
                    break
                last_error = f"request_failed:{exc}"
                attempt_elapsed_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                logger.exception(
                    "llm request failed context=%s attempt=%s/%s provider=%s model=%s exc_type=%s attempt_elapsed_ms=%s",
                    context_preview,
                    attempt,
                    safe_attempts,
                    resolution.provider,
                    resolution.model,
                    type(exc).__name__,
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
                call_id=call_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        choices = payload.get("choices") or []
        if not choices:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error="empty_choices",
                allow_fallback=allow_fallback,
            )
        content = self._extract_payload_text(payload).strip()
        if not content:
            return self._handle_failure(
                resolution=resolution,
                fallback_text=fallback_text,
                error=f"empty_content:finish_reason={self._extract_finish_reason(payload) or 'unknown'}",
                allow_fallback=allow_fallback,
            )
        total_elapsed_ms = round((time.perf_counter() - total_started_at) * 1000, 2)
        logger.info(
            "llm response parsed context=%s provider=%s model=%s call_id=%s choices=%s total_elapsed_ms=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s content=%s",
            context_preview,
            resolution.provider,
            resolution.model,
            call_id,
            len(choices),
            total_elapsed_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            self._truncate(content),
        )
        return LLMTextResult(
            text=content,
            mode="live",
            provider=resolution.provider,
            model=resolution.model,
            base_url=resolution.base_url,
            api_key_env=resolution.api_key_env,
            call_id=call_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    def _extract_payload_text(self, payload: dict[str, object]) -> str:
        text = self._extract_text_from_chunk(payload).strip()
        if text:
            return text
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = self._extract_content((message or {}).get("content")).strip() if isinstance(message, dict) else ""
        if content:
            return content
        return ""

    def _extract_finish_reason(self, payload: dict[str, object]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] if isinstance(choices[0], dict) else {}
        return str(choice.get("finish_reason") or "").strip()

    def _build_http_timeout(self, timeout_seconds: float) -> httpx.Timeout:
        """针对内网和流式响应构造更稳妥的 httpx 超时参数。"""

        safe_timeout = max(10.0, float(timeout_seconds or 60.0))
        connect_timeout = min(60.0, max(20.0, round(safe_timeout / 3, 2)))
        read_timeout = round(max(safe_timeout * 1.5, safe_timeout + connect_timeout + 15.0), 2)
        write_timeout = connect_timeout
        pool_timeout = connect_timeout
        return httpx.Timeout(
            timeout=safe_timeout,
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        )

    def _apply_prompt_budget_if_light_mode(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        runtime_settings: RuntimeSettings | None,
        log_context: dict[str, object] | None,
    ) -> tuple[str, str]:
        if not self._is_light_mode(runtime_settings, log_context):
            return system_prompt, user_prompt

        max_input_tokens = self._resolve_light_input_token_budget(runtime_settings)
        max_prompt_chars = self._resolve_light_prompt_char_budget(runtime_settings)
        if (
            self._estimate_tokens(system_prompt) + self._estimate_tokens(user_prompt) <= max_input_tokens
            and len(system_prompt) + len(user_prompt) <= max_prompt_chars
        ):
            return system_prompt, user_prompt

        # 智能裁剪：优先保留规则规范、问题信息、变更代码和关联上下文，再压缩次要区块。
        target_system_tokens = max(4000, int(max_input_tokens * 0.22))
        target_user_tokens = max(12000, max_input_tokens - target_system_tokens)
        trimmed_system = self._smart_compress_system_prompt(
            system_prompt,
            target_system_tokens,
            log_context=log_context,
        )
        trimmed_user = self._smart_compress_user_prompt(
            user_prompt,
            target_user_tokens,
            log_context=log_context,
        )

        # 二次兜底：仍超预算时，再做比例截断。
        total_tokens = self._estimate_tokens(trimmed_system) + self._estimate_tokens(trimmed_user)
        if total_tokens > max_input_tokens:
            overflow = total_tokens - max_input_tokens
            user_cut_tokens = int(overflow * 0.8)
            system_cut_tokens = max(0, overflow - user_cut_tokens)
            trimmed_user = self._truncate_to_tokens(trimmed_user, max(3000, self._estimate_tokens(trimmed_user) - user_cut_tokens))
            trimmed_system = self._truncate_to_tokens(trimmed_system, max(2000, self._estimate_tokens(trimmed_system) - system_cut_tokens))

        total_chars = len(trimmed_system) + len(trimmed_user)
        if total_chars > max_prompt_chars:
            trimmed_system, trimmed_user = self._fit_prompts_to_char_budget(
                trimmed_system,
                trimmed_user,
                max_prompt_chars,
            )

        if len(trimmed_system) != len(system_prompt) or len(trimmed_user) != len(user_prompt):
            logger.info(
                "llm light prompt compressed max_input_tokens=%s system_tokens=%s->%s user_tokens=%s->%s",
                max_input_tokens,
                self._estimate_tokens(system_prompt),
                self._estimate_tokens(trimmed_system),
                self._estimate_tokens(user_prompt),
                self._estimate_tokens(trimmed_user),
            )
            logger.info(
                "llm light prompt compressed max_chars_hint=%s system_len=%s->%s user_len=%s->%s",
                self._resolve_light_prompt_char_budget(runtime_settings),
                len(system_prompt),
                len(trimmed_system),
                len(user_prompt),
                len(trimmed_user),
            )
        return trimmed_system, trimmed_user

    def _is_light_mode(
        self,
        runtime_settings: RuntimeSettings | None,
        log_context: dict[str, object] | None,
    ) -> bool:
        mode_from_context = str((log_context or {}).get("analysis_mode") or "").strip().lower()
        if mode_from_context in {"light", "standard"}:
            return mode_from_context == "light"
        return str(getattr(runtime_settings, "default_analysis_mode", "") or "").strip().lower() == "light"

    def _resolve_light_prompt_char_budget(self, runtime_settings: RuntimeSettings | None) -> int:
        configured = int(getattr(runtime_settings, "light_llm_max_prompt_chars", 0) or 0) if runtime_settings else 0
        # 默认按 95k 字符控制，避免触发 131072 token 输入上限（尤其中文/混合文本场景）。
        budget = configured if configured > 0 else 95000
        return max(12000, budget)

    def _resolve_light_input_token_budget(self, runtime_settings: RuntimeSettings | None) -> int:
        configured = int(getattr(runtime_settings, "light_llm_max_input_tokens", 0) or 0) if runtime_settings else 0
        # 保守留出输出和协议冗余，默认输入上限 110k token。
        budget = configured if configured > 0 else 110000
        return max(16000, min(120000, budget))

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        ascii_count = sum(1 for ch in text if ord(ch) < 128)
        non_ascii_count = len(text) - ascii_count
        # 经验估算：英文约 4 chars/token，中文约 1 char/token。
        return int(non_ascii_count + ascii_count / 4) + 1

    def _truncate_to_tokens(self, text: str, token_budget: int) -> str:
        if token_budget <= 0:
            return ""
        if self._estimate_tokens(text) <= token_budget:
            return text
        # 用字符预算近似 token 预算，最后再回落校正。
        char_budget = max(200, int(token_budget * 3.2))
        trimmed = self._truncate_middle(text, char_budget)
        while self._estimate_tokens(trimmed) > token_budget and len(trimmed) > 200:
            char_budget = max(200, int(char_budget * 0.9))
            trimmed = self._truncate_middle(text, char_budget)
        return trimmed

    def _smart_compress_system_prompt(
        self,
        prompt: str,
        target_tokens: int,
        *,
        log_context: dict[str, object] | None = None,
    ) -> str:
        if self._estimate_tokens(prompt) <= target_tokens:
            return prompt
        # system prompt 重点保留规范和执行纪律。
        important_markers = [
            "《审视规范文档》开始",
            "《审视规范文档》结束",
            "《已激活 Skills》开始",
            "《已激活 Skills》结束",
            "《绑定参考文档》开始",
            "《绑定参考文档》结束",
            "《规则筛选结果》开始",
            "《规则筛选结果》结束",
            "执行纪律：",
        ]
        sections = self._split_by_markers(prompt, important_markers)
        if not sections:
            return self._truncate_to_tokens(prompt, target_tokens)
        weights: dict[str, float] = {
            "《审视规范文档》开始": 1.4,
            "《规则筛选结果》开始": 1.2,
            "执行纪律：": 1.3,
            "《绑定参考文档》开始": 1.0,
            "《已激活 Skills》开始": 0.8,
        }
        summarized = self._summarize_sections_for_light_mode(
            sections,
            headers={
                "《审视规范文档》开始",
                "《绑定参考文档》开始",
                "《已激活 Skills》开始",
            },
            focus_tokens=tuple(self._build_expert_compression_profile(log_context).get("focus_tokens") or ()),
        )
        return self._compress_sections_by_weight(summarized, target_tokens, weights)

    def _smart_compress_user_prompt(
        self,
        prompt: str,
        target_tokens: int,
        *,
        log_context: dict[str, object] | None = None,
    ) -> str:
        if self._estimate_tokens(prompt) <= target_tokens:
            return prompt
        section_headers = [
            "能力约束:",
            "规范提要:",
            "已激活技能:",
            "已绑定参考文档:",
            "规则遍历结果:",
            "输入完整性校验:",
            "语言通用规范提示:",
            "本次审核绑定的详细设计文档:",
            "目标 hunk:",
            "目标文件完整 diff:",
            "其他变更文件摘要:",
            "运行时工具调用结果:",
            "代码仓上下文:",
            "关键源码上下文:",
            "当前代码片段:",
            "必查项:",
            "禁止推断:",
            "JSON 字段要求:",
        ]
        sections = self._split_by_headers(prompt, section_headers)
        if not sections:
            return self._truncate_to_tokens(prompt, target_tokens)
        profile = self._build_expert_compression_profile(log_context)
        refined_sections = self._refine_sections_for_light_mode(
            sections,
            profile=profile,
            anchor_tokens=self._extract_anchor_tokens_from_sections(sections),
        )
        # 高权重 = 尽量保留；低权重 = 优先压缩。
        weights = self._apply_expert_header_boosts({
            "规范提要:": 1.2,
            "规则遍历结果:": 1.3,
            "输入完整性校验:": 1.2,
            "语言通用规范提示:": 1.1,
            "目标 hunk:": 1.3,
            "目标文件完整 diff:": 1.5,
            "关键源码上下文:": 1.5,
            "当前代码片段:": 1.4,
            "禁止推断:": 1.2,
            "JSON 字段要求:": 1.4,
            "已激活技能:": 0.6,
            "本次审核绑定的详细设计文档:": 0.8,
            "其他变更文件摘要:": 0.9,
            "运行时工具调用结果:": 0.8,
            "代码仓上下文:": 1.0,
            "能力约束:": 0.9,
            "已绑定参考文档:": 1.0,
            "必查项:": 1.0,
        }, profile)
        summarized = self._summarize_sections_for_light_mode(
            refined_sections,
            headers={
                "规范提要:",
                "已绑定参考文档:",
                "本次审核绑定的详细设计文档:",
                "已激活技能:",
            },
            focus_tokens=tuple(profile.get("focus_tokens") or ()),
        )
        return self._compress_sections_by_weight(summarized, target_tokens, weights)

    def _summarize_sections_for_light_mode(
        self,
        sections: list[tuple[str, str]],
        *,
        headers: set[str],
        focus_tokens: tuple[str, ...] = (),
    ) -> list[tuple[str, str]]:
        summarized: list[tuple[str, str]] = []
        for header, body in sections:
            if header not in headers:
                summarized.append((header, body))
                continue
            summarized.append((header, self._summarize_doc_block_for_light_mode(body, focus_tokens=focus_tokens)))
        return summarized

    def _summarize_doc_block_for_light_mode(self, text: str, *, focus_tokens: tuple[str, ...] = ()) -> str:
        if not text.strip():
            return text
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 18 and self._estimate_tokens(text) <= 1600:
            return text

        kept: list[str] = []
        # 保留最前面的说明，避免丢失上下文。
        kept.extend(lines[:6])

        # 保留包含规则/标题/关键词的信息行。
        effective_focus_tokens = (
            "rule", "规则", "title", "标题", "must", "should", "禁止", "必须", "风险", "安全", "性能", "事务",
            "aggregate", "domain", "sql", "缓存", "测试", "边界", "一致性"
        ) + tuple(focus_tokens)
        for line in lines[6:]:
            lower = line.lower()
            if any(token in lower for token in effective_focus_tokens):
                kept.append(line)
            if len(kept) >= 24:
                break

        # 保留最后几行，通常是执行纪律/输出要求。
        tail_candidates = lines[-4:]
        for line in tail_candidates:
            if line not in kept:
                kept.append(line)

        summary = "\n".join(kept[:30]).strip()
        if not summary:
            summary = "\n".join(lines[:12]).strip()
        return summary + "\n...[light summary: doc block compressed]...\n"

    def _build_expert_compression_profile(self, log_context: dict[str, object] | None) -> dict[str, object]:
        expert_id = str((log_context or {}).get("expert_id") or "").strip().lower()
        profiles: dict[str, dict[str, object]] = {
            "security_compliance": {
                "focus_tokens": (
                    "sql", "select", "update", "delete", "insert", "where", "auth", "token", "jwt", "permission",
                    "tenant", "header", "request", "input", "sanitize", "xss", "csrf", "secret", "password",
                ),
                "header_boosts": {
                    "目标文件完整 diff:": 0.15,
                    "关键源码上下文:": 0.2,
                    "运行时工具调用结果:": 0.12,
                    "代码仓上下文:": 0.12,
                },
            },
            "performance_reliability": {
                "focus_tokens": (
                    "for", "while", "stream", "parallel", "batch", "list", "map", "set", "cache", "redis",
                    "query", "join", "page", "limit", "loop", "pool", "thread",
                ),
                "header_boosts": {
                    "目标文件完整 diff:": 0.12,
                    "关键源码上下文:": 0.24,
                    "代码仓上下文:": 0.18,
                    "其他变更文件摘要:": 0.08,
                },
            },
            "database_analysis": {
                "focus_tokens": (
                    "sql", "select", "insert", "update", "delete", "join", "where", "index", "page", "limit",
                    "mapper", "mybatis", "jpa", "repository", "transaction",
                ),
                "header_boosts": {
                    "目标文件完整 diff:": 0.12,
                    "关键源码上下文:": 0.22,
                    "运行时工具调用结果:": 0.12,
                    "代码仓上下文:": 0.14,
                },
            },
            "ddd_specification": {
                "focus_tokens": (
                    "aggregate", "entity", "valueobject", "domain", "applicationservice", "repository",
                    "factory", "assembler", "command", "query", "domainservice", "acl",
                ),
                "header_boosts": {
                    "关键源码上下文:": 0.22,
                    "代码仓上下文:": 0.2,
                    "本次审核绑定的详细设计文档:": 0.16,
                    "目标 hunk:": 0.1,
                },
            },
            "architecture_design": {
                "focus_tokens": (
                    "service", "facade", "adapter", "controller", "gateway", "repository", "domain",
                    "dependency", "interface", "implementation", "boundary",
                ),
                "header_boosts": {
                    "关键源码上下文:": 0.2,
                    "代码仓上下文:": 0.18,
                    "其他变更文件摘要:": 0.12,
                },
            },
            "test_verification": {
                "focus_tokens": (
                    "test", "assert", "mock", "when", "then", "expect", "verify", "exception", "boundary",
                ),
                "header_boosts": {
                    "当前代码片段:": 0.1,
                    "关键源码上下文:": 0.12,
                    "代码仓上下文:": 0.12,
                    "其他变更文件摘要:": 0.08,
                },
            },
        }
        return profiles.get(expert_id, {"focus_tokens": (), "header_boosts": {}})

    def _apply_expert_header_boosts(
        self,
        weights: dict[str, float],
        profile: dict[str, object],
    ) -> dict[str, float]:
        merged = dict(weights)
        for header, boost in dict(profile.get("header_boosts") or {}).items():
            merged[str(header)] = merged.get(str(header), 1.0) + float(boost or 0.0)
        return merged

    def _refine_sections_for_light_mode(
        self,
        sections: list[tuple[str, str]],
        *,
        profile: dict[str, object],
        anchor_tokens: set[str],
    ) -> list[tuple[str, str]]:
        focus_tokens = tuple(str(token).lower() for token in tuple(profile.get("focus_tokens") or ()))
        context_headers = {
            "目标 hunk:",
            "目标文件完整 diff:",
            "其他变更文件摘要:",
            "运行时工具调用结果:",
            "代码仓上下文:",
            "关键源码上下文:",
            "当前代码片段:",
        }
        refined: list[tuple[str, str]] = []
        for header, body in sections:
            if header in context_headers:
                refined.append(
                    (
                        header,
                        self._summarize_context_block_for_light_mode(
                            header,
                            body,
                            anchor_tokens=anchor_tokens,
                            focus_tokens=focus_tokens,
                        ),
                    )
                )
            else:
                refined.append((header, body))
        return refined

    def _summarize_context_block_for_light_mode(
        self,
        header: str,
        text: str,
        *,
        anchor_tokens: set[str],
        focus_tokens: tuple[str, ...],
    ) -> str:
        if not text.strip() or self._estimate_tokens(text) <= 900:
            return text
        lines = text.splitlines()
        if len(lines) <= 36:
            return text

        chosen: set[int] = set(range(min(4, len(lines))))
        chosen.update(range(max(0, len(lines) - 4), len(lines)))
        structural_tokens = (
            "@@", "class ", "interface ", "enum ", "record ", "public ", "private ", "protected ", "void ",
            "return ", "if ", "for ", "while ", "catch ", "throws ", "select ", "update ", "delete ", "insert ",
        )
        for idx, raw_line in enumerate(lines):
            lower = raw_line.lower()
            score = 0
            if any(token and token in lower for token in anchor_tokens):
                score += 6
            if any(token and token in lower for token in focus_tokens):
                score += 4
            if any(token in lower for token in structural_tokens):
                score += 1
            if header == "目标文件完整 diff:" and raw_line.startswith(("@@", "+", "-")):
                score += 1
            if header == "运行时工具调用结果:" and any(token in lower for token in ("table", "index", "query", "call", "stack", "sql")):
                score += 2
            if score <= 0:
                continue
            for neighbor in range(max(0, idx - 2), min(len(lines), idx + 3)):
                chosen.add(neighbor)

        selected = [lines[idx] for idx in sorted(chosen)]
        if len(selected) >= len(lines):
            return text
        summary = "\n".join(selected).strip()
        if not summary:
            summary = "\n".join(lines[:24]).strip()
        return summary + "\n...[light summary: context block compressed]...\n"

    def _extract_anchor_tokens_from_sections(self, sections: list[tuple[str, str]]) -> set[str]:
        collected: list[str] = []
        for header, body in sections:
            if header not in {"目标 hunk:", "当前代码片段:", "目标文件完整 diff:"}:
                continue
            collected.extend(self._extract_identifier_tokens(body))
        return set(collected)

    def _extract_identifier_tokens(self, text: str) -> list[str]:
        raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
        stopwords = {
            "public", "private", "protected", "class", "void", "return", "string", "boolean", "integer",
            "static", "final", "null", "true", "false", "this", "that", "with", "from", "into", "while",
            "where", "select", "insert", "update", "delete", "value", "values", "table", "and", "the",
            "filler", "line", "lines", "snippet", "context", "other", "noise",
        }
        normalized: list[str] = []
        for token in raw_tokens:
            lowered = token.lower()
            if lowered in stopwords or len(lowered) < 3:
                continue
            normalized.append(lowered)
            parts = [part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+", token) if part]
            for part in parts:
                if part not in stopwords and len(part) >= 3:
                    normalized.append(part)
        return normalized[:80]

    def _split_by_headers(self, text: str, headers: list[str]) -> list[tuple[str, str]]:
        lines = text.splitlines(keepends=True)
        sections: list[tuple[str, str]] = []
        current_header = "preamble"
        current_lines: list[str] = []
        header_set = set(headers)
        for line in lines:
            stripped = line.strip()
            if stripped in header_set:
                if current_lines:
                    sections.append((current_header, "".join(current_lines)))
                current_header = stripped
                current_lines = []
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_header, "".join(current_lines)))
        return sections

    def _split_by_markers(self, text: str, markers: list[str]) -> list[tuple[str, str]]:
        positions: list[tuple[int, str]] = []
        for marker in markers:
            idx = text.find(marker)
            if idx >= 0:
                positions.append((idx, marker))
        if not positions:
            return []
        positions.sort(key=lambda item: item[0])
        sections: list[tuple[str, str]] = []
        start = 0
        if positions[0][0] > 0:
            sections.append(("preamble", text[: positions[0][0]]))
            start = positions[0][0]
        for index, (pos, marker) in enumerate(positions):
            next_pos = positions[index + 1][0] if index + 1 < len(positions) else len(text)
            block_start = pos + len(marker)
            body = text[block_start:next_pos]
            sections.append((marker, body))
        return sections

    def _compress_sections_by_weight(
        self,
        sections: list[tuple[str, str]],
        target_tokens: int,
        weights: dict[str, float],
    ) -> str:
        base_min_tokens = 320
        token_needs = [max(base_min_tokens, self._estimate_tokens(body)) for _, body in sections]
        total_need = sum(token_needs)
        if total_need <= target_tokens:
            return self._rebuild_sections(sections)

        weighted_total = sum(weights.get(header, 1.0) for header, _ in sections)
        allocated: list[int] = []
        for idx, (header, _) in enumerate(sections):
            ratio = weights.get(header, 1.0) / weighted_total if weighted_total > 0 else 1.0 / max(1, len(sections))
            budget = max(base_min_tokens, int(target_tokens * ratio))
            allocated.append(min(token_needs[idx], budget))

        shrink_order = sorted(
            range(len(sections)),
            key=lambda i: weights.get(sections[i][0], 1.0),
        )
        while sum(allocated) > target_tokens and shrink_order:
            changed = False
            for idx in shrink_order:
                if sum(allocated) <= target_tokens:
                    break
                if allocated[idx] <= 180:
                    continue
                allocated[idx] = max(180, allocated[idx] - 120)
                changed = True
            if not changed:
                break

        compressed_sections: list[tuple[str, str]] = []
        for (header, body), budget in zip(sections, allocated):
            compressed_sections.append((header, self._truncate_to_tokens(body, budget)))
        return self._rebuild_sections(compressed_sections)

    def _rebuild_sections(self, sections: list[tuple[str, str]]) -> str:
        chunks: list[str] = []
        for header, body in sections:
            if header != "preamble":
                chunks.append(f"{header}\n")
            chunks.append(body)
        return "".join(chunks)

    def _truncate_middle(self, text: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        if max_chars <= 80:
            return text[:max_chars]
        head_len = int(max_chars * 0.65)
        tail_len = max_chars - head_len - 32
        if tail_len < 0:
            tail_len = 0
        marker = "\n\n...[light prompt compressed]...\n\n"
        return text[:head_len].rstrip() + marker + (text[-tail_len:].lstrip() if tail_len > 0 else "")

    def _fit_prompts_to_char_budget(
        self,
        system_prompt: str,
        user_prompt: str,
        max_prompt_chars: int,
    ) -> tuple[str, str]:
        trimmed_system = system_prompt
        trimmed_user = user_prompt
        min_system_chars = min(800, max_prompt_chars)
        min_user_chars = min(1000, max_prompt_chars)

        while len(trimmed_system) + len(trimmed_user) > max_prompt_chars:
            overflow_chars = len(trimmed_system) + len(trimmed_user) - max_prompt_chars
            user_cut_chars = max(1, int(overflow_chars * 0.8))
            system_cut_chars = max(1, overflow_chars - user_cut_chars)

            next_user_budget = max(min_user_chars, len(trimmed_user) - user_cut_chars)
            next_system_budget = max(min_system_chars, len(trimmed_system) - system_cut_chars)

            next_user = self._truncate_middle(trimmed_user, next_user_budget)
            next_system = self._truncate_middle(trimmed_system, next_system_budget)

            # `_truncate_middle` 会插入 marker，极端情况下单轮裁剪后字符数可能几乎不变，
            # 这里再做一次强制切片，确保最终严格不超过配置预算。
            if len(next_user) >= len(trimmed_user) and len(trimmed_user) > min_user_chars:
                next_user = trimmed_user[: max(min_user_chars, len(trimmed_user) - max(user_cut_chars, 64))]
            if len(next_system) >= len(trimmed_system) and len(trimmed_system) > min_system_chars:
                next_system = trimmed_system[: max(min_system_chars, len(trimmed_system) - max(system_cut_chars, 32))]

            if next_user == trimmed_user and next_system == trimmed_system:
                remaining = max_prompt_chars - min(len(trimmed_system), min_system_chars)
                trimmed_system = trimmed_system[:min(len(trimmed_system), min_system_chars)]
                trimmed_user = trimmed_user[: max(0, min(len(trimmed_user), remaining))]
                break

            trimmed_user = next_user
            trimmed_system = next_system

        combined_len = len(trimmed_system) + len(trimmed_user)
        if combined_len > max_prompt_chars:
            user_allowance = max(0, max_prompt_chars - len(trimmed_system))
            trimmed_user = trimmed_user[:user_allowance]
            combined_len = len(trimmed_system) + len(trimmed_user)
        if combined_len > max_prompt_chars:
            trimmed_system = trimmed_system[: max(0, max_prompt_chars - len(trimmed_user))]
        return trimmed_system, trimmed_user

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

    def _classify_request_exception(self, exc: httpx.RequestError) -> str:
        """把 httpx 传输层异常归类成更易排查的日志标签。"""

        text = self._collect_exception_text(exc).lower()
        if self._is_connection_aborted_text(text):
            return "connection_aborted"
        if self._is_connection_reset_text(text):
            return "connection_reset"
        if isinstance(exc, httpx.ConnectError):
            return "connect_error"
        if isinstance(exc, httpx.ReadError):
            return "read_error"
        if isinstance(exc, httpx.WriteError):
            return "write_error"
        if isinstance(exc, httpx.CloseError):
            return "close_error"
        if isinstance(exc, httpx.RemoteProtocolError):
            return "remote_protocol_error"
        if isinstance(exc, httpx.LocalProtocolError):
            return "local_protocol_error"
        return "request_error"

    def _classify_generic_transport_exception(self, exc: BaseException) -> str:
        """识别未被 httpx 包装、但本质仍属于可重试传输层的异常。"""

        text = self._collect_exception_text(exc).lower()
        if self._is_connection_aborted_text(text) or isinstance(exc, ConnectionAbortedError):
            return "connection_aborted"
        if self._is_connection_reset_text(text) or isinstance(exc, ConnectionResetError):
            return "connection_reset"
        if isinstance(exc, BrokenPipeError):
            return "broken_pipe"
        if isinstance(exc, OSError):
            winerror = getattr(exc, "winerror", None)
            errno = getattr(exc, "errno", None)
            if winerror == 10053 or errno == 10053:
                return "connection_aborted"
            if winerror == 10054 or errno in {54, 104, 10054}:
                return "connection_reset"
        return ""

    def _is_connection_aborted_text(self, text: str) -> bool:
        return (
            "10053" in text
            or "software caused connection abort" in text
            or "aborted by the software in your host machine" in text
        )

    def _is_connection_reset_text(self, text: str) -> bool:
        return (
            "10054" in text
            or "connection reset by peer" in text
            or "existing connection was forcibly closed by the remote host" in text
        )

    def _collect_exception_text(self, exc: BaseException) -> str:
        parts: list[str] = []
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            parts.append(str(current))
            current = current.__cause__ or current.__context__
        return " | ".join(part for part in parts if part)

    def _handle_failure(
        self,
        *,
        resolution: LLMResolution,
        fallback_text: str,
        error: str,
        allow_fallback: bool,
        call_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> LLMTextResult:
        if allow_fallback:
            return self._fallback(
                resolution,
                fallback_text,
                error,
                call_id=call_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        raise RuntimeError(
            f"LLM live call required but failed for model={resolution.model}, provider={resolution.provider}: {error}"
        )

    def _build_request_preview(
        self,
        request_body: dict[str, object],
        *,
        system_prompt: str,
        user_prompt: str,
        preview_limit: int | None,
    ) -> str:
        preview = {
            "model": request_body.get("model"),
            "temperature": request_body.get("temperature"),
            "system_prompt": self._truncate(system_prompt, preview_limit),
            "user_prompt": self._truncate(user_prompt, preview_limit),
        }
        return self._safe_json(preview, preview_limit)

    def _safe_json(self, value: object, limit: int | None = None) -> str:
        try:
            return self._truncate(json.dumps(value, ensure_ascii=False), limit)
        except Exception:
            return self._truncate(repr(value), limit)

    def _stringify_context(self, log_context: dict[str, object] | None) -> str:
        if not log_context:
            return "-"
        return self._safe_json(log_context, self._PREVIEW_LIMIT)

    def _truncate(self, value: object, limit: int | None = None) -> str:
        text = str(value or "")
        if limit is None:
            safe_limit = self._PREVIEW_LIMIT
        else:
            safe_limit = int(limit)
        if safe_limit < 0:
            return text
        if safe_limit <= 0:
            return ""
        if len(text) <= safe_limit:
            return text
        return f"{text[:safe_limit].rstrip()}...<truncated>"

    def _effective_preview_limit(self, runtime_settings: RuntimeSettings | None) -> int | None:
        if runtime_settings is None:
            return self._PREVIEW_LIMIT
        if not bool(getattr(runtime_settings, "llm_log_truncate_enabled", True)):
            return -1
        return max(
            200,
            int(getattr(runtime_settings, "llm_log_preview_limit", self._PREVIEW_LIMIT) or self._PREVIEW_LIMIT),
        )

    def _decode_payload(self, *, response_text: str, content_type: str) -> dict[str, object]:
        cleaned = response_text.lstrip("\ufeff").strip()
        if not cleaned:
            raise ValueError("empty_response_body")
        lower_content_type = content_type.lower()
        sse_hint = "text/event-stream" in lower_content_type or cleaned.startswith("data:")
        if sse_hint:
            logger.info("llm response parser selected parser=sse")
            try:
                return self._decode_sse_payload(cleaned)
            except ValueError as exc:
                logger.warning("llm response sse decode failed, fallback to json parser error=%s", exc)
        logger.info("llm response parser selected parser=json")
        return self._decode_json_payload(cleaned)

    def _decode_json_payload(self, cleaned: str) -> dict[str, object]:
        candidates: list[str] = [cleaned]
        stripped_fence = self._strip_markdown_json_fence(cleaned)
        if stripped_fence and stripped_fence != cleaned:
            candidates.append(stripped_fence)
        extracted = self._extract_first_json_object(cleaned)
        if extracted and extracted not in candidates:
            candidates.append(extracted)

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        raise ValueError("json_payload_not_object")

    def _strip_markdown_json_fence(self, text: str) -> str:
        stripped = str(text or "").strip()
        if not stripped.startswith("```"):
            return stripped
        match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            return stripped
        return str(match.group(1) or "").strip()

    def _extract_first_json_object(self, text: str) -> str:
        source = str(text or "")
        start = source.find("{")
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(source)):
            char = source[index]
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1]
        return ""

    def _decode_sse_payload(self, response_text: str) -> dict[str, object]:
        chunks: list[dict[str, object]] = []
        accumulated_text_parts: list[str] = []
        latest_usage: dict[str, object] | None = None
        saw_choices = False
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
                if chunk.get("choices"):
                    saw_choices = True
                if isinstance(chunk.get("usage"), dict):
                    latest_usage = dict(chunk.get("usage") or {})
                chunk_text = self._extract_text_from_chunk(chunk)
                if chunk_text:
                    accumulated_text_parts.append(chunk_text)
        if not chunks:
            raise ValueError("sse_no_data_chunks")
        accumulated_text = "".join(accumulated_text_parts).strip()
        if accumulated_text and not saw_choices:
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": accumulated_text,
                        }
                    }
                ]
            }
            if latest_usage:
                payload["usage"] = latest_usage
            return payload
        for chunk in reversed(chunks):
            top_level_text = self._extract_sse_top_level_text(chunk).strip()
            if top_level_text:
                payload = {
                    "choices": [
                        {
                            "message": {
                                "content": top_level_text,
                            }
                        }
                    ]
                }
                if latest_usage and not isinstance(chunk.get("usage"), dict):
                    payload["usage"] = latest_usage
                elif isinstance(chunk.get("usage"), dict):
                    payload["usage"] = dict(chunk.get("usage") or {})
                return payload
            choices = chunk.get("choices") or []
            if not choices:
                continue
            message = choices[0].get("message") or {}
            content = self._extract_content(message.get("content")).strip()
            if content:
                if latest_usage and not isinstance(chunk.get("usage"), dict):
                    chunk = {**chunk, "usage": latest_usage}
                return chunk
        if accumulated_text:
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": accumulated_text,
                        }
                    }
                ]
            }
            if latest_usage:
                payload["usage"] = latest_usage
            return payload
        raise ValueError("sse_no_message_content")

    def _extract_usage(self, payload: dict[str, object]) -> tuple[int, int, int]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return 0, 0, 0
        prompt_tokens = self._safe_int(usage.get("prompt_tokens"))
        completion_tokens = self._safe_int(usage.get("completion_tokens"))
        total_tokens = self._safe_int(usage.get("total_tokens"))
        if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
            total_tokens = prompt_tokens + completion_tokens
        return prompt_tokens, completion_tokens, total_tokens

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _extract_text_from_chunk(self, chunk: dict[str, object]) -> str:
        choices = chunk.get("choices") or []
        if not choices:
            return self._extract_sse_top_level_text(chunk)
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        message_content = self._extract_content(message)
        if message_content:
            return message_content
        message_content = self._extract_content(choice.get("message"))
        if message_content:
            return message_content
        if isinstance(choice.get("text"), str):
            return str(choice.get("text") or "")
        if isinstance(choice.get("output_text"), str):
            return str(choice.get("output_text") or "")
        delta = choice.get("delta") or {}
        delta_content = self._extract_content(delta)
        if delta_content:
            return delta_content
        if isinstance(delta, dict):
            if isinstance(delta.get("text"), str):
                return str(delta.get("text") or "")
            if isinstance(delta.get("output_text"), str):
                return str(delta.get("output_text") or "")
        return self._extract_sse_top_level_text(chunk)

    def _extract_sse_top_level_text(self, chunk: dict[str, object]) -> str:
        for key in ("text", "output_text", "content"):
            value = chunk.get(key)
            extracted = self._extract_content(value).strip()
            if extracted:
                return extracted
        output = chunk.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                extracted = self._extract_content(item).strip()
                if extracted:
                    parts.append(extracted)
            return "".join(parts)
        return ""

    def _extract_content(self, content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                extracted = self._extract_content(item)
                if extracted:
                    parts.append(extracted)
            return "".join(parts)
        if isinstance(content, dict):
            for key in ("text", "content", "delta", "output_text", "message", "output", "parts", "result"):
                extracted = self._extract_content(content.get(key))
                if extracted:
                    return extracted
            return ""
        return str(content)

    def _fallback(
        self,
        resolution: LLMResolution,
        fallback_text: str,
        error: str,
        *,
        call_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> LLMTextResult:
        return LLMTextResult(
            text=fallback_text,
            mode="fallback",
            provider=resolution.provider,
            model=resolution.model,
            base_url=resolution.base_url,
            api_key_env=resolution.api_key_env,
            error=error,
            call_id=call_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
