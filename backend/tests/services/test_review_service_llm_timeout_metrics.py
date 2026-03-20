from pathlib import Path

from app.config import settings
from app.services.review_service import ReviewService


def test_review_service_builds_llm_timeout_metrics_from_backend_log(tmp_path: Path, monkeypatch) -> None:
    logs_root = tmp_path / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    (logs_root / "backend.log").write_text(
        "\n".join(
            [
                '2026-03-20 11:00:00,000 WARNING app.services.llm_chat_service llm request timeout context={"review_id": "rev_1", "expert_id": "performance_reliability", "phase": "expert_review"} attempt=1/2 provider=dashscope model=kimi timeout_kind=read_timeout attempt_elapsed_ms=120001 total_elapsed_ms=120001 error=stream stalled',
                '2026-03-20 11:01:00,000 WARNING app.services.llm_chat_service llm request timeout context={"review_id": "rev_2", "agent_id": "main_agent", "phase": "expert_selection"} attempt=1/1 provider=dashscope model=kimi timeout_kind=connect_timeout attempt_elapsed_ms=35000 total_elapsed_ms=35000 error=connect timed out',
                '2026-03-20 11:02:00,000 INFO app.services.llm_chat_service llm response parsed context={"review_id": "rev_ok", "expert_id": "correctness_business"} provider=dashscope model=kimi choices=1 total_elapsed_ms=8200 content=ok',
                '2026-03-20 11:03:00,000 INFO app.services.llm_chat_service llm response parsed context={"review_id": "rev_ok2", "expert_id": "architecture_design"} provider=dashscope model=kimi choices=1 total_elapsed_ms=14200 content=ok',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "LOGS_ROOT", logs_root)

    service = ReviewService(storage_root=tmp_path / "storage")
    metrics = service.build_llm_timeout_metrics()

    assert metrics["timeout_count"] == 2
    assert metrics["read_timeout_count"] == 1
    assert metrics["connect_timeout_count"] == 1
    assert metrics["success_count"] == 2
    assert metrics["avg_success_elapsed_ms"] == 11200.0
    assert metrics["max_success_elapsed_ms"] == 14200.0
    assert len(metrics["recent_timeouts"]) == 2
    assert metrics["recent_timeouts"][0]["review_id"] == "rev_1"
    assert metrics["recent_timeouts"][1]["expert_id"] == "main_agent"
