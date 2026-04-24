from __future__ import annotations

import json
import re

import httpx


def build_http_timeout(timeout_seconds: float) -> httpx.Timeout:
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


def classify_timeout_exception(exc: httpx.TimeoutException) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    return "timeout"


def classify_request_exception(exc: httpx.RequestError) -> str:
    text = collect_exception_text(exc).lower()
    if is_connection_aborted_text(text):
        return "connection_aborted"
    if is_connection_reset_text(text):
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


def classify_generic_transport_exception(exc: BaseException) -> str:
    text = collect_exception_text(exc).lower()
    if is_connection_aborted_text(text) or isinstance(exc, ConnectionAbortedError):
        return "connection_aborted"
    if is_connection_reset_text(text) or isinstance(exc, ConnectionResetError):
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


def is_connection_aborted_text(text: str) -> bool:
    return (
        "10053" in text
        or "software caused connection abort" in text
        or "aborted by the software in your host machine" in text
    )


def is_connection_reset_text(text: str) -> bool:
    return (
        "10054" in text
        or "connection reset by peer" in text
        or "existing connection was forcibly closed by the remote host" in text
    )


def collect_exception_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return " | ".join(part for part in parts if part)


def build_request_preview(
    request_body: dict[str, object],
    *,
    system_prompt: str,
    user_prompt: str,
    preview_limit: int | None,
) -> str:
    preview = {
        "model": request_body.get("model"),
        "temperature": request_body.get("temperature"),
        "system_prompt": truncate(system_prompt, preview_limit),
        "user_prompt": truncate(user_prompt, preview_limit),
    }
    return safe_json(preview, preview_limit)


def safe_json(value: object, limit: int | None = None, default_limit: int = 1600) -> str:
    try:
        return truncate(json.dumps(value, ensure_ascii=False), limit, default_limit)
    except Exception:
        return truncate(repr(value), limit, default_limit)


def stringify_context(
    log_context: dict[str, object] | None,
    *,
    preview_limit: int = 1600,
) -> str:
    if not log_context:
        return "-"
    return safe_json(log_context, preview_limit, preview_limit)


def truncate(value: object, limit: int | None = None, default_limit: int = 1600) -> str:
    text = str(value or "")
    safe_limit = default_limit if limit is None else int(limit)
    if safe_limit < 0:
        return text
    if safe_limit <= 0:
        return ""
    if len(text) <= safe_limit:
        return text
    return f"{text[:safe_limit].rstrip()}...<truncated>"


def decode_payload(*, response_text: str, content_type: str) -> dict[str, object]:
    cleaned = response_text.lstrip("\ufeff").strip()
    if not cleaned:
        raise ValueError("empty_response_body")
    lower_content_type = content_type.lower()
    sse_hint = "text/event-stream" in lower_content_type or cleaned.startswith("data:")
    if sse_hint:
      try:
          return decode_sse_payload(cleaned)
      except ValueError:
          pass
    return decode_json_payload(cleaned)


def decode_json_payload(cleaned: str) -> dict[str, object]:
    candidates: list[str] = [cleaned]
    stripped_fence = strip_markdown_json_fence(cleaned)
    if stripped_fence and stripped_fence != cleaned:
        candidates.append(stripped_fence)
    extracted = extract_first_json_object(cleaned)
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


def strip_markdown_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return stripped
    return str(match.group(1) or "").strip()


def extract_first_json_object(text: str) -> str:
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


def decode_sse_payload(response_text: str) -> dict[str, object]:
    chunks: list[dict[str, object]] = []
    accumulated_text_parts: list[str] = []
    latest_usage: dict[str, object] | None = None
    saw_choices = False
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
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
            chunk_text = extract_text_from_chunk(chunk)
            if chunk_text:
                accumulated_text_parts.append(chunk_text)
    if not chunks:
        raise ValueError("sse_no_data_chunks")
    accumulated_text = "".join(accumulated_text_parts).strip()
    if accumulated_text and not saw_choices:
        payload: dict[str, object] = {
            "choices": [{"message": {"content": accumulated_text}}],
        }
        if latest_usage:
            payload["usage"] = latest_usage
        return payload
    for chunk in reversed(chunks):
        top_level_text = extract_sse_top_level_text(chunk).strip()
        if top_level_text:
            payload = {
                "choices": [{"message": {"content": top_level_text}}],
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
        content = extract_content(message.get("content")).strip()
        if content:
            if latest_usage and not isinstance(chunk.get("usage"), dict):
                chunk = {**chunk, "usage": latest_usage}
            return chunk
    if accumulated_text:
        payload = {
            "choices": [{"message": {"content": accumulated_text}}],
        }
        if latest_usage:
            payload["usage"] = latest_usage
        return payload
    raise ValueError("sse_no_message_content")


def extract_usage(payload: dict[str, object]) -> tuple[int, int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0
    prompt_tokens = safe_int(usage.get("prompt_tokens"))
    completion_tokens = safe_int(usage.get("completion_tokens"))
    total_tokens = safe_int(usage.get("total_tokens"))
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def extract_payload_text(payload: dict[str, object]) -> str:
    text = extract_text_from_chunk(payload).strip()
    if text:
        return text
    choices = payload.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    content = extract_content((message or {}).get("content")).strip() if isinstance(message, dict) else ""
    if content:
        return content
    return ""


def extract_finish_reason(payload: dict[str, object]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    return str(choice.get("finish_reason") or "").strip()


def extract_text_from_chunk(chunk: dict[str, object]) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return extract_sse_top_level_text(chunk)
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    message_content = extract_content(message)
    if message_content:
        return message_content
    message_content = extract_content(choice.get("message"))
    if message_content:
        return message_content
    if isinstance(choice.get("text"), str):
        return str(choice.get("text") or "")
    if isinstance(choice.get("output_text"), str):
        return str(choice.get("output_text") or "")
    delta = choice.get("delta") or {}
    delta_content = extract_content(delta)
    if delta_content:
        return delta_content
    if isinstance(delta, dict):
        if isinstance(delta.get("text"), str):
            return str(delta.get("text") or "")
        if isinstance(delta.get("output_text"), str):
            return str(delta.get("output_text") or "")
    return extract_sse_top_level_text(chunk)


def extract_sse_top_level_text(chunk: dict[str, object]) -> str:
    for key in ("text", "output_text", "content"):
        value = chunk.get(key)
        extracted = extract_content(value).strip()
        if extracted:
            return extracted
    output = chunk.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            extracted = extract_content(item).strip()
            if extracted:
                parts.append(extracted)
        return "".join(parts)
    return ""


def extract_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            extracted = extract_content(item)
            if extracted:
                parts.append(extracted)
        return "".join(parts)
    if isinstance(content, dict):
        for key in ("text", "content", "delta", "output_text", "message", "output", "parts", "result"):
            extracted = extract_content(content.get(key))
            if extracted:
                return extracted
        return ""
    return str(content)
