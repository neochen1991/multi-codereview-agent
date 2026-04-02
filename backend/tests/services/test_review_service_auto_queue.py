from pathlib import Path

from app.domain.models.event import ReviewEvent
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewTask
from app.services.platform_adapter import OpenMergeRequest
from app.services.review_service import ReviewService


def test_enqueue_open_merge_requests_creates_pending_reviews_and_deduplicates(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    service.platform_adapter.normalize = lambda subject, runtime_settings=None: subject.model_copy(  # type: ignore[method-assign]
        update={
            "repo_id": subject.repo_id or "projectname",
            "project_id": subject.project_id or "FND",
            "source_ref": subject.source_ref or "mr/demo",
            "target_ref": subject.target_ref or "main",
            "title": subject.title or "Auto MR",
        }
    )
    service.platform_adapter.list_open_merge_requests = lambda repo_url, access_token, runtime_settings=None: [  # type: ignore[method-assign]
        OpenMergeRequest(
            mr_url="https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/101",
            title="MR 101",
            source_ref="feature/mr-101",
            target_ref="main",
            number="101",
            head_sha="abc101",
        ),
        OpenMergeRequest(
            mr_url="https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/102",
            title="MR 102",
            source_ref="feature/mr-102",
            target_ref="main",
            number="102",
            head_sha="abc102",
        ),
    ]

    created = service.enqueue_open_merge_requests("codehub-g.huawei.com/PIP/FND/projectname/merge_requests")
    assert len(created) == 2
    assert all(item.status == "pending" for item in created)

    duplicated = service.enqueue_open_merge_requests("codehub-g.huawei.com/PIP/FND/projectname/merge_requests")
    assert duplicated == []

    queue = service.list_pending_queue()
    assert len(queue) == 2
    assert queue[0].created_at <= queue[1].created_at


def test_start_next_pending_review_recovers_interrupted_running_review(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    service.platform_adapter.normalize = lambda subject, runtime_settings=None: subject.model_copy(  # type: ignore[method-assign]
        update={
            "repo_id": subject.repo_id or "projectname",
            "project_id": subject.project_id or "FND",
            "source_ref": subject.source_ref or "mr/demo",
            "target_ref": subject.target_ref or "main",
            "title": subject.title or "Auto MR",
        }
    )
    stale = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "projectname",
            "project_id": "FND",
            "source_ref": "feature/stale",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/10",
            "title": "stale running review",
        }
    )
    stale.status = "running"
    stale.phase = "expert_review"
    service.review_repo.save(stale)

    next_pending = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "projectname",
            "project_id": "FND",
            "source_ref": "feature/pending",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/11",
            "title": "next pending review",
        }
    )

    started_ids: list[str] = []

    def fake_start_review_async(review_id: str) -> ReviewTask:
        started_ids.append(review_id)
        review = service.get_review(review_id)
        assert review is not None
        review.status = "running"
        review.phase = "queued"
        service.review_repo.save(review)
        return review

    service.start_review_async = fake_start_review_async  # type: ignore[method-assign]

    started = service.start_next_pending_review()

    assert started is not None
    assert started.review_id == stale.review_id
    assert started_ids == [stale.review_id]
    recovered = service.get_review(stale.review_id)
    assert recovered is not None
    assert recovered.status == "running"
    events = service.list_events(stale.review_id)
    assert any(item.event_type == "review_recovered" for item in events)
    queue_ids = [item.review_id for item in service.list_pending_queue()]
    assert next_pending.review_id in queue_ids


def test_rerun_failed_review_clears_previous_runtime_outputs_before_restart(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_failed",
            "project_id": "proj_failed",
            "source_ref": "feature/failed",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/21",
            "title": "failed review",
        }
    )
    review.status = "failed"
    review.phase = "failed"
    review.failure_reason = "llm timeout"
    review.report_summary = "审核失败：llm timeout"
    service.review_repo.save(review)
    service.event_repo.append(
        ReviewEvent(review_id=review.review_id, event_type="review_failed", phase="failed", message="执行失败")
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="",
            expert_id="performance_reliability",
            message_type="expert_analysis",
            content="旧的失败消息",
        )
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            expert_id="performance_reliability",
            title="旧 finding",
            summary="旧结论",
        ),
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                title="旧 issue",
                summary="旧议题",
            )
        ],
    )
    review.report_summary = "历史产物"
    service.artifact_service.publish(review, [])

    def fake_queue_start(review_id: str) -> tuple[ReviewTask, str]:
        rerun = service.get_review(review_id)
        assert rerun is not None
        rerun.status = "running"
        rerun.phase = "queued"
        service.review_repo.save(rerun)
        return rerun, "任务已立即启动。"

    service.queue_start_review = fake_queue_start  # type: ignore[method-assign]

    updated, message = service.rerun_failed_review(review.review_id)

    assert updated.status == "running"
    assert updated.phase == "queued"
    assert message == "任务已立即启动。"

    refreshed = service.get_review(review.review_id)
    assert refreshed is not None
    assert refreshed.failure_reason == ""
    assert refreshed.completed_at is None
    assert refreshed.duration_seconds is None
    assert refreshed.human_review_status == "not_required"
    assert refreshed.pending_human_issue_ids == []
    assert refreshed.subject.metadata["rerun_count"] == 1
    assert service.list_findings(review.review_id) == []
    assert service.list_issues(review.review_id) == []
    assert service.list_all_messages(review.review_id) == []
    events = service.list_events(review.review_id)
    assert len(events) == 1
    assert events[0].event_type == "review_rerun_requested"
    assert service.get_artifacts(review.review_id) == {}


def test_build_report_aggregates_llm_calls_and_tokens_without_double_counting(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_usage",
            "project_id": "proj_usage",
            "source_ref": "feature/usage",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/99",
            "title": "llm usage review",
        }
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="main_agent_expert_selection",
            content="selection",
            metadata={
                "llm_call_id": "llm_call_main",
                "mode": "live",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        )
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="review_orchestration",
            expert_id="main_agent",
            message_type="main_agent_command",
            content="command",
            metadata={
                "llm_call_id": "llm_call_main",
                "mode": "live",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        )
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="finding_1",
            expert_id="performance_reliability",
            message_type="expert_analysis",
            content="analysis",
            metadata={
                "llm_call_id": "llm_call_expert",
                "mode": "fallback",
                "prompt_tokens": 300,
                "completion_tokens": 40,
                "total_tokens": 340,
            },
        )
    )

    report = service.build_report(review.review_id)

    assert report.llm_usage_summary.total_calls == 2
    assert report.llm_usage_summary.prompt_tokens == 400
    assert report.llm_usage_summary.completion_tokens == 60
    assert report.llm_usage_summary.total_tokens == 460


def test_build_report_uses_real_issue_count_not_finding_count(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_issue_count",
            "project_id": "proj_issue_count",
            "source_ref": "feature/issue-count",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/109",
            "title": "issue count review",
        }
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            expert_id="architecture_design",
            title="finding one",
            summary="first finding",
        ),
    )
    service.finding_repo.save(
        review.review_id,
        ReviewFinding(
            review_id=review.review_id,
            expert_id="performance_reliability",
            title="finding two",
            summary="second finding",
        ),
    )
    service.issue_repo.save_all(
        review.review_id,
        [
            DebateIssue(
                review_id=review.review_id,
                title="single issue",
                summary="only one issue should be counted",
                finding_ids=["fdg_1"],
            )
        ],
    )

    report = service.build_report(review.review_id)

    assert len(report.findings) == 2
    assert len(report.issues) == 1
    assert report.issue_count == 1


def test_build_replay_bundle_uses_snapshot_review_without_unified_diff(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_replay",
            "project_id": "proj_replay",
            "source_ref": "feature/replay",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/110",
            "title": "replay review",
            "unified_diff": "diff --git a/a.java b/a.java\n+class A {}",
            "design_docs": [
                {
                    "doc_id": "doc-1",
                    "title": "设计说明",
                    "filename": "design.md",
                    "content": "# very large design content",
                    "doc_type": "design_spec",
                }
            ],
        }
    )
    service.event_repo.append(
        ReviewEvent(review_id=review.review_id, event_type="review_started", phase="queued", message="queued")
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="",
            expert_id="main_agent",
            message_type="main_agent_expert_selection",
            content="selection details that should not be replayed in full",
            metadata={"file_path": "src/main/java/A.java", "rule_screening": {"matched_rule_count": 1}, "large": "x" * 200},
        )
    )

    bundle = service.build_replay_bundle(review.review_id)

    assert bundle["review"]["subject"]["unified_diff"] == ""
    assert bundle["review"]["subject"]["metadata"]["design_docs"] == [
        {
            "doc_id": "doc-1",
            "title": "设计说明",
            "filename": "design.md",
            "doc_type": "design_spec",
        }
    ]
    assert len(bundle["events"]) == 2
    assert len(bundle["messages"]) == 1
    assert bundle["messages"][0]["content"] == ""
    assert bundle["messages"][0]["metadata"] == {
        "file_path": "src/main/java/A.java",
        "rule_screening": {"matched_rule_count": 1},
    }


def test_build_process_messages_keeps_ui_fields_and_drops_unused_metadata(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_process",
            "project_id": "proj_process",
            "source_ref": "feature/process",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/111",
            "title": "process review",
        }
    )
    service.message_repo.append(
        ConversationMessage(
            review_id=review.review_id,
            issue_id="issue-1",
            expert_id="architecture_design",
            message_type="expert_analysis",
            content="full analysis content should remain available",
            metadata={
                "file_path": "src/main/java/A.java",
                "line_start": 42,
                "rule_screening": {"matched_rule_count": 2},
                "tool_result": {"summary": "compact"},
                "review_inputs": {"language_guidance_present": True},
                "large_unused_blob": "x" * 500,
            },
        )
    )

    messages = service.build_process_messages(review.review_id)

    assert len(messages) == 1
    assert messages[0]["content"] == "full analysis content should remain available"
    assert messages[0]["metadata"] == {
        "file_path": "src/main/java/A.java",
        "line_start": 42,
        "rule_screening": {"matched_rule_count": 2},
        "tool_result": {"summary": "compact"},
        "review_inputs": {"language_guidance_present": True},
    }


def test_list_review_summaries_returns_lightweight_subject_payload(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_home",
            "project_id": "proj_home",
            "source_ref": "feature/home",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/1",
            "title": "home review",
            "changed_files": ["src/Main.java"],
            "unified_diff": "x" * 1000,
            "metadata": {"trigger_source": "auto_scheduler", "queue_priority_at": "2026-04-02T10:00:00+00:00"},
        }
    )

    rows = service.list_review_summaries()

    row = next(item for item in rows if item["review_id"] == review.review_id)
    assert row["subject"]["title"] == "home review"
    assert row["subject"]["unified_diff"] == ""
    assert row["subject"]["changed_files"] == ["src/Main.java"]
    assert row["subject"]["metadata"] == {"trigger_source": "auto_scheduler"}


def test_list_pending_queue_light_with_diagnostics_preserves_queue_fields(tmp_path: Path):
    service = ReviewService(tmp_path / "storage")
    running = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_running",
            "project_id": "proj_running",
            "source_ref": "feature/running",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/2",
            "title": "running review",
        }
    )
    running.status = "running"
    running.phase = "expert_review"
    service.review_repo.save(running)
    pending = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_pending",
            "project_id": "proj_pending",
            "source_ref": "feature/pending",
            "target_ref": "main",
            "mr_url": "https://github.com/example/repo/pull/3",
            "title": "pending review",
        }
    )

    rows = service.list_pending_queue_light_with_diagnostics()

    row = next(item for item in rows if item["review_id"] == pending.review_id)
    assert row["queue_position"] == 1
    assert row["queue_blocker_code"] == "blocked_by_running_review"
    assert row["blocking_review_id"] == running.review_id
    assert row["subject"]["title"] == "pending review"
