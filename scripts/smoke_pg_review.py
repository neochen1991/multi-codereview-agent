from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_BASE = "http://127.0.0.1:8011/api"


def request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, Any] | list[object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def poll_review(review_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        replay = request_json("GET", f"{API_BASE}/reviews/{review_id}/replay")
        assert isinstance(replay, dict)
        review = replay.get("review")
        assert isinstance(review, dict)
        status = str(review.get("status") or "")
        if status in {"completed", "failed", "closed"}:
            return replay
        time.sleep(2)
    raise TimeoutError(f"等待审核完成超时：{review_id}")


def extract_pg_evidence(replay: dict[str, Any]) -> dict[str, Any]:
    messages = replay.get("messages")
    assert isinstance(messages, list)
    pg_tool_messages = [
        item
        for item in messages
        if isinstance(item, dict)
        and item.get("expert_id") == "database_analysis"
        and item.get("message_type") == "expert_tool_call"
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("tool_name") == "pg_schema_context"
    ]
    matched_tables: list[str] = []
    database = ""
    host = ""
    summary = ""
    for item in pg_tool_messages:
        metadata = item.get("metadata") or {}
        tool_result = metadata.get("tool_result")
        if not isinstance(tool_result, dict):
            continue
        summary = str(tool_result.get("summary") or summary)
        matched_tables.extend(str(value).strip() for value in list(tool_result.get("matched_tables") or []) if str(value).strip())
        data_source_summary = tool_result.get("data_source_summary")
        if isinstance(data_source_summary, dict):
            database = str(data_source_summary.get("database") or database)
            host = str(data_source_summary.get("host") or host)
    findings = replay.get("report", {}).get("findings", []) if isinstance(replay.get("report"), dict) else []
    db_findings = [
        item
        for item in findings
        if isinstance(item, dict) and item.get("expert_id") == "database_analysis"
    ]
    return {
        "pg_tool_message_count": len(pg_tool_messages),
        "data_source_database": database,
        "data_source_host": host,
        "matched_tables": list(dict.fromkeys(matched_tables)),
        "pg_summary": summary,
        "database_finding_count": len(db_findings),
        "database_finding_titles": [
            str(item.get("title") or "").strip() for item in db_findings if str(item.get("title") or "").strip()
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证数据库分析专家是否接入 PostgreSQL 元信息审查。")
    parser.add_argument("--mr-url", required=True, help="要审核的 MR/PR 链接")
    parser.add_argument("--title", default="PG datasource smoke review", help="审核任务标题")
    parser.add_argument("--analysis-mode", default="standard", choices=["standard", "light"], help="审核模式")
    parser.add_argument("--timeout-seconds", type=int, default=240, help="等待审核完成的最长时间")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    health = request_json("GET", "http://127.0.0.1:8011/health")
    assert isinstance(health, dict) and health.get("status") == "ok"

    created = request_json(
        "POST",
        f"{API_BASE}/reviews",
        {
            "subject_type": "mr",
            "analysis_mode": args.analysis_mode,
            "mr_url": args.mr_url,
            "title": args.title,
            "selected_experts": ["database_analysis"],
        },
    )
    assert isinstance(created, dict)
    review_id = str(created["review_id"])

    started = request_json("POST", f"{API_BASE}/reviews/{urllib.parse.quote(review_id)}/start")
    assert isinstance(started, dict)
    assert str(started.get("review_id") or "") == review_id

    replay = poll_review(review_id, args.timeout_seconds)
    report = replay.get("report")
    assert isinstance(report, dict)
    review = replay.get("review")
    assert isinstance(review, dict)
    evidence = extract_pg_evidence(replay)

    output = {
        "review_id": review_id,
        "status": review.get("status"),
        "phase": review.get("phase"),
        "summary": review.get("report_summary"),
        "issue_count": report.get("issue_count"),
        "finding_count": len(report.get("findings") or []),
        "pg_evidence": evidence,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, TimeoutError, KeyError, urllib.error.URLError) as error:
        print(f"pg smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
