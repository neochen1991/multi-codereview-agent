from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.event import ReviewEvent
from app.domain.models.feedback import FeedbackLabel
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.repositories.sqlite_event_repository import SqliteEventRepository
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository
from app.repositories.sqlite_finding_repository import SqliteFindingRepository
from app.repositories.sqlite_issue_repository import SqliteIssueRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository


def test_sqlite_event_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteEventRepository(db_path)
    event = ReviewEvent(
        review_id="rev_demo001",
        event_type="review_created",
        phase="pending",
        message="审核任务已创建",
        payload={"source": "test"},
    )

    repository.append(event)

    loaded = repository.list("rev_demo001")
    assert len(loaded) == 1
    assert loaded[0].payload["source"] == "test"


def test_sqlite_message_repository_round_trip_and_issue_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteMessageRepository(db_path)
    first = ConversationMessage(
        review_id="rev_demo001",
        issue_id="iss_1",
        expert_id="correctness_business",
        message_type="expert_ack",
        content="开始分析",
        metadata={"active_skills": ["design-consistency-check"]},
    )
    second = ConversationMessage(
        review_id="rev_demo001",
        issue_id="iss_2",
        expert_id="architecture_design",
        message_type="expert_ack",
        content="开始分析",
    )

    repository.append(first)
    repository.append(second)

    assert len(repository.list("rev_demo001")) == 2
    filtered = repository.list_by_issue("rev_demo001", "iss_1")
    assert len(filtered) == 1
    assert filtered[0].expert_id == "correctness_business"


def test_sqlite_finding_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteFindingRepository(db_path)
    finding = ReviewFinding(
        review_id="rev_demo001",
        expert_id="correctness_business",
        title="字段契约不一致",
        summary="返回字段新增但映射未同步",
        matched_design_points=["返回体包含 createdAt"],
        missing_design_points=["transformer 未填充 createdAt"],
    )

    repository.save("rev_demo001", finding)

    loaded = repository.list("rev_demo001")
    assert len(loaded) == 1
    assert loaded[0].title == "字段契约不一致"
    assert loaded[0].missing_design_points == ["transformer 未填充 createdAt"]


def test_sqlite_issue_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteIssueRepository(db_path)
    issue = DebateIssue(
        review_id="rev_demo001",
        title="需要人工确认的风险",
        summary="当前证据不足，需要人工裁决",
        finding_ids=["fdg_1", "fdg_2"],
        needs_human=True,
    )

    repository.save_all("rev_demo001", [issue])

    loaded = repository.list("rev_demo001")
    assert len(loaded) == 1
    assert loaded[0].finding_ids == ["fdg_1", "fdg_2"]
    assert loaded[0].needs_human is True


def test_sqlite_feedback_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteFeedbackRepository(db_path)
    feedback = FeedbackLabel(
        review_id="rev_demo001",
        issue_id="iss_1",
        label="false_positive",
        comment="这条是格式化误报",
    )

    repository.save(feedback)

    loaded = repository.list("rev_demo001")
    assert len(loaded) == 1
    assert loaded[0].label == "false_positive"
    assert loaded[0].comment == "这条是格式化误报"
