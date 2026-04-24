from __future__ import annotations

import json
import re
from pathlib import Path


def build_llm_timeout_metrics(log_path: Path, *, tail_lines: int = 4000) -> dict[str, object]:
    """从后端日志中聚合最近一段时间的 LLM timeout 与耗时概览。"""

    empty_payload = {
        "timeout_count": 0,
        "connect_timeout_count": 0,
        "read_timeout_count": 0,
        "write_timeout_count": 0,
        "pool_timeout_count": 0,
        "other_timeout_count": 0,
        "success_count": 0,
        "avg_success_elapsed_ms": 0.0,
        "max_success_elapsed_ms": 0.0,
        "recent_timeouts": [],
    }
    if not log_path.exists():
        return empty_payload
    try:
        raw_text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return empty_payload
    entries = split_log_entries(raw_text)
    lines = entries[-max(100, tail_lines) :]

    timeout_counters = {
        "connect_timeout": 0,
        "read_timeout": 0,
        "write_timeout": 0,
        "pool_timeout": 0,
        "timeout": 0,
    }
    recent_timeouts: list[dict[str, object]] = []
    success_elapsed: list[float] = []
    for line in lines:
        if "llm request timeout" in line.lower():
            timeout_kind = extract_timeout_kind(line)
            counter_key = timeout_kind if timeout_kind in timeout_counters else "timeout"
            timeout_counters[counter_key] += 1
            recent_timeouts.append(
                {
                    "timestamp": extract_log_timestamp(line),
                    "timeout_kind": timeout_kind,
                    "provider": extract_log_field(line, "provider"),
                    "model": extract_log_field(line, "model"),
                    "phase": extract_context_field(line, "phase"),
                    "review_id": extract_context_field(line, "review_id"),
                    "expert_id": extract_context_field(line, "expert_id") or extract_context_field(line, "agent_id"),
                    "attempt_elapsed_ms": extract_float_log_field(line, "attempt_elapsed_ms"),
                    "total_elapsed_ms": extract_float_log_field(line, "total_elapsed_ms"),
                }
            )
        elif "llm response parsed " in line:
            elapsed = extract_float_log_field(line, "total_elapsed_ms")
            if elapsed > 0:
                success_elapsed.append(elapsed)

    timeout_count = sum(timeout_counters.values())
    avg_success = round(sum(success_elapsed) / len(success_elapsed), 2) if success_elapsed else 0.0
    max_success = round(max(success_elapsed), 2) if success_elapsed else 0.0
    return {
        "timeout_count": timeout_count,
        "connect_timeout_count": timeout_counters["connect_timeout"],
        "read_timeout_count": timeout_counters["read_timeout"],
        "write_timeout_count": timeout_counters["write_timeout"],
        "pool_timeout_count": timeout_counters["pool_timeout"],
        "other_timeout_count": timeout_counters["timeout"],
        "success_count": len(success_elapsed),
        "avg_success_elapsed_ms": avg_success,
        "max_success_elapsed_ms": max_success,
        "recent_timeouts": recent_timeouts[-10:],
    }


def split_log_entries(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    timestamp_pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"
    positions = [match.start() for match in re.finditer(timestamp_pattern, normalized)]
    if not positions:
        return [line for line in normalized.split("\n") if line.strip()]
    positions.append(len(normalized))
    entries: list[str] = []
    for index in range(len(positions) - 1):
        start = positions[index]
        end = positions[index + 1]
        chunk = normalized[start:end].strip()
        if chunk:
            entries.append(chunk)
    return entries


def extract_log_timestamp(line: str) -> str:
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
    return match.group(1) if match else ""


def extract_log_field(line: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}=([^\s]+)", line)
    return match.group(1).strip().strip(",") if match else ""


def extract_float_log_field(line: str, field: str) -> float:
    raw = extract_log_field(line, field)
    try:
        return float(raw)
    except Exception:
        return 0.0


def extract_timeout_kind(line: str) -> str:
    lowered = line.lower()
    if "timeout_kind=connect_timeout" in lowered:
        return "connect_timeout"
    if "timeout_kind=read_timeout" in lowered:
        return "read_timeout"
    if "timeout_kind=write_timeout" in lowered:
        return "write_timeout"
    if "timeout_kind=pool_timeout" in lowered:
        return "pool_timeout"
    if "connect timed out" in lowered or "connection timed out" in lowered:
        return "connect_timeout"
    if "read timed out" in lowered or "read timeout" in lowered or "stream stalled" in lowered:
        return "read_timeout"
    if "write timed out" in lowered or "write timeout" in lowered:
        return "write_timeout"
    if "pool timeout" in lowered:
        return "pool_timeout"
    return extract_log_field(line, "timeout_kind") or "timeout"


def extract_context_field(line: str, field: str) -> str:
    match = re.search(r"context=(\{.*?\})(?:\s+\w+=|$)", line)
    if not match:
        return ""
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return ""
    value = payload.get(field)
    return str(value).strip() if value is not None else ""
