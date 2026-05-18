from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from bench_java_review_cases import (
    DEFAULT_API_BASE,
    DEFAULT_MANIFEST_PATH,
    load_cases,
    load_repositories,
    materialize_case,
    request_json,
    select_cases,
)


FRONTEND_URL = "http://127.0.0.1:5174/"
CASE_ID = "java-ddd-course-creator-bypasses-domain-events"
WAIT_TIMEOUT_SECONDS = 240
POLL_INTERVAL_SECONDS = 5


def request_text(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main() -> int:
    log("smoke: checking backend health")
    health = request_json("GET", "http://127.0.0.1:8011/health")
    assert isinstance(health, dict) and health.get("status") == "ok"

    log("smoke: checking frontend")
    index_html = request_text(FRONTEND_URL)
    assert "multi-code-review-frontend" in index_html or "<!doctype html" in index_html.lower()

    log(f"smoke: loading benchmark case {CASE_ID}")
    repositories = load_repositories(DEFAULT_MANIFEST_PATH)
    cases = select_cases(load_cases(DEFAULT_MANIFEST_PATH), [CASE_ID])
    materialized = materialize_case(cases[0], repositories)

    log("smoke: creating review")
    created = request_json("POST", f"{DEFAULT_API_BASE}/reviews", materialized.to_review_payload())
    assert isinstance(created, dict)
    review_id = str(created["review_id"])

    log(f"smoke: starting review {review_id}")
    started = request_json("POST", f"{DEFAULT_API_BASE}/reviews/{review_id}/start")
    assert isinstance(started, dict)
    assert started["review_id"] == review_id

    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    replay: dict[str, object] = {}
    report: dict[str, object] = {}
    artifacts: dict[str, object] = {}
    while time.time() < deadline:
        replay = request_json("GET", f"{DEFAULT_API_BASE}/reviews/{review_id}/replay")
        report = request_json("GET", f"{DEFAULT_API_BASE}/reviews/{review_id}/report")
        artifacts = request_json("GET", f"{DEFAULT_API_BASE}/reviews/{review_id}/artifacts")
        review_status = str((replay or {}).get("review", {}).get("status") or "")
        log(f"smoke: poll status={review_status or '-'}")
        findings = report.get("findings", []) if isinstance(report, dict) else []
        issues = report.get("issues", []) if isinstance(report, dict) else []
        filter_decisions = report.get("issue_filter_decisions", []) if isinstance(report, dict) else []
        if review_status in {"completed", "waiting_human"}:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    assert isinstance(replay, dict)
    assert isinstance(report, dict)
    assert isinstance(artifacts, dict)

    review = replay.get("review", {})
    assert isinstance(review, dict)
    assert review.get("status") in {"completed", "waiting_human"}
    assert report.get("status") in {"completed", "waiting_human"}
    llm_usage = report.get("llm_usage_summary") if isinstance(report.get("llm_usage_summary"), dict) else {}
    assert int(llm_usage.get("total_calls") or 0) > 0, "smoke review must use at least one LLM call"

    messages = replay.get("messages", [])
    assert isinstance(messages, list)
    assert any(
        isinstance(item, dict)
        and item.get("expert_id") == "main_agent"
        and item.get("message_type") == "main_agent_command"
        for item in messages
    )
    assert any(
        isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("mode") == "live"
        and item["metadata"].get("llm_call_id")
        for item in messages
    ), "smoke review must contain a live LLM message"
    assert any(
        isinstance(item, dict)
        and item.get("expert_id") == "ddd_architecture"
        and item.get("message_type") in {"expert_analysis", "expert_final"}
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("prompt_snapshot_summary")
        and item["metadata"].get("rule_check_results")
        and item["metadata"].get("candidate_findings") is not None
        for item in messages
    ), "smoke review must expose expert prompt and rule diagnostics"
    assert any(
        isinstance(item, dict)
        and item.get("expert_id") == "ddd_architecture"
        and item.get("message_type") in {"expert_ack", "expert_analysis", "expert_final", "expert_failed"}
        for item in messages
    )

    findings = report.get("findings", [])
    issues = report.get("issues", [])
    filter_decisions = report.get("issue_filter_decisions", [])
    assert isinstance(findings, list)
    assert isinstance(issues, list)
    assert isinstance(filter_decisions, list)
    assert findings or issues or filter_decisions

    representative = issues[0] if issues else findings[0]
    assert representative.get("title")
    assert representative.get("summary")
    if issues:
        assert representative.get("primary_expert_id") == "ddd_architecture"
        assert representative.get("participant_expert_ids")
    if findings:
        assert findings[0].get("expert_id") == "ddd_architecture"
        assert findings[0].get("remediation_suggestion")
        assert findings[0].get("code_excerpt")

    summary = {
        "case_id": CASE_ID,
        "review_id": review_id,
        "status": review.get("status"),
        "issue_count": len(issues),
        "finding_count": len(findings),
        "llm_total_calls": int(llm_usage.get("total_calls") or 0),
        "filtered_count": sum(
            len(item.get("finding_ids") or [])
            for item in filter_decisions
            if isinstance(item, dict)
        ),
        "primary_expert_ids": [
            item.get("primary_expert_id")
            for item in issues
            if isinstance(item, dict) and item.get("primary_expert_id")
        ],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, StopIteration, urllib.error.URLError) as error:
        print(f"smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
