from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path

from app.services.code_graph.index_service import CodeGraphIndexService
from app.services.code_graph.java_tree_sitter_parser import JavaTreeSitterParser

from bench_java_review_cases import (
    DEFAULT_API_BASE,
    DEFAULT_CACHE_ROOT,
    DEFAULT_WORKSPACE_ROOT,
    load_cases,
    load_repositories,
    materialize_case,
    request_json,
    select_cases,
)


DEFAULT_CASE_ID = "java-ddd-composite-quality-regression"


def run_case(
    *,
    case_id: str,
    api_base: str,
    frontend_base: str,
    analysis_mode: str,
    wait_timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, object]:
    repositories = load_repositories()
    case = select_cases(load_cases(), [case_id])[0]
    materialized = materialize_case(
        case,
        repositories,
        workspace_root=DEFAULT_WORKSPACE_ROOT,
        cache_root=DEFAULT_CACHE_ROOT,
    )
    graph_result = CodeGraphIndexService(
        repo_root=materialized.workspace_repo,
        parser=JavaTreeSitterParser(),
    ).full_build(repository_id=materialized.repository.repo_key)
    payload = materialized.to_review_payload(analysis_mode=analysis_mode)
    payload["metadata"] = {
        **dict(payload.get("metadata") or {}),
        "workspace_repo_path": str(materialized.workspace_repo),
        "tree_sitter_flow_test": True,
        "code_graph_db_path": str(materialized.workspace_repo / ".code-review-graph" / "graph.db"),
    }
    created = request_json("POST", f"{api_base}/reviews", payload)
    review_id = str(created["review_id"])
    request_json("POST", f"{api_base}/reviews/{urllib.parse.quote(review_id)}/start")
    deadline = time.time() + wait_timeout_seconds
    latest_review: dict[str, object] = {}
    while time.time() < deadline:
        latest_review = request_json("GET", f"{api_base}/reviews/{urllib.parse.quote(review_id)}")
        status = str(latest_review.get("status") or "")
        phase = str(latest_review.get("phase") or "")
        print(json.dumps({"review_id": review_id, "status": status, "phase": phase}, ensure_ascii=False))
        if status in {"completed", "failed", "closed", "waiting_human"}:
            break
        time.sleep(poll_interval_seconds)
    report = request_json("GET", f"{api_base}/reviews/{urllib.parse.quote(review_id)}/report")
    replay = request_json("GET", f"{api_base}/reviews/{urllib.parse.quote(review_id)}/replay")
    events = request_json("GET", f"{api_base}/reviews/{urllib.parse.quote(review_id)}/events")
    event_items = events if isinstance(events, list) else list(events.get("events") or []) if isinstance(events, dict) else []
    tree_sitter_events = [
        item for item in event_items if str(item.get("event_type") or "").startswith("code_graph_")
        or str(item.get("event_type") or "") == "keyword_context_ready"
    ]
    issues = list(report.get("issues") or []) if isinstance(report, dict) else []
    findings = list(report.get("findings") or []) if isinstance(report, dict) else []
    llm_usage = dict((report.get("llm_usage") or {})) if isinstance(report, dict) else {}
    return {
        "case_id": case_id,
        "review_id": review_id,
        "review_url": f"{frontend_base.rstrip('/')}/review/{urllib.parse.quote(review_id)}",
        "impact_tab_url": f"{frontend_base.rstrip('/')}/review/{urllib.parse.quote(review_id)}?tab=impact",
        "process_tab_url": f"{frontend_base.rstrip('/')}/review/{urllib.parse.quote(review_id)}?tab=replay",
        "status": latest_review.get("status"),
        "phase": latest_review.get("phase"),
        "workspace_repo": str(materialized.workspace_repo),
        "changed_files": list(materialized.changed_files),
        "graph_result": graph_result,
        "tree_sitter_event_count": len(tree_sitter_events),
        "tree_sitter_events": tree_sitter_events[:12],
        "issue_count": len(issues),
        "finding_count": len(findings),
        "issue_titles": [str(item.get("title") or "") for item in issues if isinstance(item, dict)][:12],
        "finding_titles": [str(item.get("title") or "") for item in findings if isinstance(item, dict)][:12],
        "llm_total_calls": int(llm_usage.get("total_calls") or 0),
        "replay_message_count": len(list(replay.get("messages") or [])) if isinstance(replay, dict) else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a full frontend-friendly Java Tree-sitter review flow test.")
    parser.add_argument("--case", default=DEFAULT_CASE_ID)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--frontend-base", default="http://127.0.0.1:5174")
    parser.add_argument("--analysis-mode", default="light", choices=["light", "standard"])
    parser.add_argument("--wait-timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-interval-seconds", type=int, default=10)
    args = parser.parse_args()
    result = run_case(
        case_id=args.case,
        api_base=args.api_base,
        frontend_base=args.frontend_base,
        analysis_mode=args.analysis_mode,
        wait_timeout_seconds=args.wait_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
