from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


API_BASE = "http://127.0.0.1:8011/api"
FRONTEND_URL = "http://127.0.0.1:5174/"


def request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, object] | list[object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def request_text(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def main() -> int:
    health = request_json("GET", "http://127.0.0.1:8011/health")
    assert isinstance(health, dict) and health.get("status") == "ok"

    index_html = request_text(FRONTEND_URL)
    assert "multi-code-review-frontend" in index_html or "<!doctype html" in index_html.lower()

    created = request_json(
        "POST",
        f"{API_BASE}/reviews",
        {
            "subject_type": "mr",
            "mr_url": "https://git.example.com/platform/payments/-/merge_requests/128",
            "title": "Smoke MR review",
        },
    )
    assert isinstance(created, dict)
    review_id = str(created["review_id"])

    started = request_json("POST", f"{API_BASE}/reviews/{review_id}/start")
    assert isinstance(started, dict)
    assert started["review_id"] == review_id

    replay_before = request_json("GET", f"{API_BASE}/reviews/{review_id}/replay")
    assert isinstance(replay_before, dict)
    messages = replay_before["messages"]
    assert isinstance(messages, list)
    assert any(
        isinstance(item, dict)
        and item.get("expert_id") == "main_agent"
        and item.get("message_type") == "main_agent_command"
        for item in messages
    )
    assert any(
        isinstance(item, dict)
        and item.get("expert_id") == "main_agent"
        and item.get("message_type") == "main_agent_summary"
        for item in messages
    )
    assert any(
        isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("file_path")
        and int(item["metadata"].get("line_start") or 0) >= 1
        for item in messages
    )
    assert any(
        isinstance(item, dict)
        and item.get("message_type") == "expert_ack"
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("model") == "kimi-k2.5"
        for item in messages
    )

    issues = replay_before["issues"]
    assert isinstance(issues, list) and issues
    human_issue = next((item for item in issues if isinstance(item, dict) and item.get("needs_human")), None)
    if human_issue:
        request_json(
            "POST",
            f"{API_BASE}/reviews/{review_id}/human-decisions",
            {
                "issue_id": human_issue["issue_id"],
                "decision": "approved",
                "comment": "smoke approved",
            },
        )

    replay_after = request_json("GET", f"{API_BASE}/reviews/{review_id}/replay")
    report = request_json("GET", f"{API_BASE}/reviews/{review_id}/report")
    artifacts = request_json("GET", f"{API_BASE}/reviews/{review_id}/artifacts")

    assert isinstance(replay_after, dict)
    assert isinstance(report, dict)
    assert isinstance(artifacts, dict)
    assert replay_after["review"]["status"] == "completed"
    assert "0 个待人工裁决" in replay_after["review"]["report_summary"]
    assert report["status"] == "completed"
    assert artifacts["check_run"]["status"] == "completed"
    assert report["findings"][0]["remediation_suggestion"]
    assert report["findings"][0]["code_excerpt"]

    print(
        json.dumps(
            {
                "review_id": review_id,
                "status": replay_after["review"]["status"],
                "issue_count": len(replay_after["issues"]),
                "message_count": len(replay_after["messages"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, StopIteration, urllib.error.URLError) as error:
        print(f"smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
