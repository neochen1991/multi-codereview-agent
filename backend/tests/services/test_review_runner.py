from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.review_runner as review_runner_module
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.knowledge import KnowledgeDocument, KnowledgeDocumentSection
from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.message import ConversationMessage
from app.domain.models.report import ImpactReport, TestScopeRecommendation as ImpactTestScopeRecommendation
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_skill import ReviewSkillProfile
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_expert_repository import FileExpertRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository
from app.services.llm_chat_service import LLMResolution, LLMTextResult
from app.services.code_graph.storage import CodeGraphStorage
from app.services.review_learning_service import ReviewLearningService
from app.services.review_runner import ReviewRunner
from app.services.review_workspace_service import ReviewWorkspaceResult

PERFORMANCE_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-ultra-spec.md"
)


@pytest.fixture(autouse=True)
def _stub_live_llm_calls_for_review_runner_tests(monkeypatch):
    """Keep review-runner tests on the mandatory LLM path without real API keys."""

    def _result(text: str, phase: str) -> LLMTextResult:
        return LLMTextResult(
            text=text,
            mode="live",
            provider="test",
            model=f"test-{phase or 'llm'}",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"test-{phase or 'llm'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    def _fake_complete_text(
        _self,
        *,
        system_prompt: str,
        user_prompt: str,
        resolution: LLMResolution,
        runtime_settings=None,
        fallback_text: str,
        temperature: float = 0.2,
        allow_fallback: bool = False,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        log_context: dict[str, object] | None = None,
    ) -> LLMTextResult:
        phase = str((log_context or {}).get("phase") or "").strip()
        if phase in {"expert_selection", "routing_plan"}:
            return _result(fallback_text, phase)
        if phase == "expert_review":
            return _result(
                """
                {
                  "findings": [
                    {
                      "title": "新增分支缺少失败路径处理",
                      "claim": "新增订单创建分支在保存失败时没有返回明确错误，调用方可能误判为创建成功。",
                      "finding_type": "direct_defect",
                      "normalized_issue_type": "missing_failure_handling",
                      "severity": "high",
                      "confidence": 0.93,
                      "line_start": 18,
                      "line_end": 18,
                      "evidence": ["新增 create 调用没有处理 repository.save 失败或异常语义。", "当前 diff 命中订单创建主流程。"],
                      "cross_file_evidence": [],
                      "assumptions": [],
                      "context_files": [],
                      "matched_rules": ["失败路径必须显式处理"],
                      "violated_guidelines": ["业务失败语义不能被吞掉"],
                      "rule_based_reasoning": "新增业务分支改变成功/失败语义时，应明确处理失败路径。",
                      "fix_strategy": "在保存失败时返回明确错误或抛出领域异常。",
                      "suggested_fix": "补齐保存失败的错误处理，并用测试覆盖失败分支。",
                      "change_steps": ["捕获或判断保存失败结果", "返回明确错误语义", "补充失败路径单元测试"],
                      "suggested_code": "if (!repository.save(order)) { throw new OrderCreateException(\\\"create failed\\\"); }",
                      "verification_needed": false,
                      "verification_plan": ""
                    }
                  ]
                }
                """.strip(),
                phase,
            )
        if phase == "debate":
            return _result(
                "回应主Agent: 已复核。\n风险结论: 该问题有直接代码证据。\n修复建议: 补齐失败路径处理。",
                phase,
            )
        if phase == "final_summary":
            return _result("主Agent收敛完成：本轮已形成稳定检视结果，请优先处理高风险议题。", phase)
        return _result(fallback_text or "测试 LLM 响应", phase)

    monkeypatch.setattr(
        "app.services.llm_chat_service.LLMChatService.complete_text",
        _fake_complete_text,
    )


def test_review_runner_emits_finding_created_event(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    events = runner.list_events(review_id)
    assert any(event.event_type == "finding_created" for event in events)


def test_review_runner_records_code_graph_context_events_in_process_flow(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    bundle = {
        "source_summary": {
            "primary_source": "keyword_search",
            "fallback_used": True,
            "fallback_reason": "图谱未建",
        },
        "events": [
            review_runner_module.ReviewEvent(
                review_id=review_id,
                event_type="code_graph_context_started",
                phase="context",
                message="正在使用 Tree-sitter 代码图谱检索关联上下文",
                payload={"context_source": "tree_sitter"},
            ),
            review_runner_module.ReviewEvent(
                review_id=review_id,
                event_type="code_graph_context_fallback",
                phase="context",
                message="Tree-sitter 未命中有效关联上下文，已退化为关键词搜索：图谱未建",
                payload={"context_source": "tree_sitter", "fallback_source": "keyword_search"},
            ),
        ],
    }

    runner._record_code_graph_context_bundle(review_id, bundle)

    events = runner.list_events(review_id)
    messages = runner.message_repo.list(review_id)
    assert any(event.event_type == "code_graph_context_fallback" for event in events)
    assert any(message.message_type == "code_graph_context_fallback" for message in messages)
    assert any("Tree-sitter" in message.content and "关键词搜索" in message.content for message in messages)


def test_review_runner_prefers_workspace_code_graph_db_from_review_metadata(storage_root: Path, tmp_path: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    workspace_repo = tmp_path / "workspace-repo"
    configured_repo = tmp_path / "configured-repo"
    graph_db_path = workspace_repo / ".code-review-graph" / "graph.db"
    CodeGraphStorage(graph_db_path).initialize()
    configured_repo.mkdir(parents=True)
    review.subject.metadata = {
        **dict(review.subject.metadata or {}),
        "workspace_repo_path": str(workspace_repo),
    }

    class _RepositoryContext:
        local_path = configured_repo

    storage = runner._build_code_graph_storage_for_repository(_RepositoryContext(), review)

    assert storage is not None
    assert storage.db_path == graph_db_path


def test_review_runner_workspace_message_shows_snapshot_graph_status(storage_root: Path, tmp_path: Path):
    runner = ReviewRunner(storage_root=storage_root)
    workspace = tmp_path / "rw" / "repo" / "rev"
    result = SimpleNamespace(
        status="ready",
        base_repo_path=str(tmp_path / "repo"),
        workspace_path=str(workspace),
        snapshot_mode="diff_apply",
        snapshot_commit="1234567890abcdef",
        message="已基于目标分支应用 MR diff。",
        target_ref="dev",
        source_ref="feature/order",
    )

    content = runner._review_workspace_message_content(
        result,
        {"status": "ready", "graph_db_path": str(workspace / ".code-review-graph" / "graph.db")},
        {"status": "ready", "graph_dir": str(workspace / ".gitnexus")},
    )

    assert "Tree-sitter 快照图谱：ready" in content
    assert "GitNexus 快照图谱：ready" in content
    assert str(workspace / ".code-review-graph" / "graph.db") in content
    assert str(workspace / ".gitnexus") in content


def test_review_runner_records_workspace_graph_initialization_messages(storage_root: Path, tmp_path: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.subject.subject_type = "mr"
    review.subject.unified_diff = "diff --git a/src/App.java b/src/App.java\n@@ -1 +1,2 @@\n+class App {}\n"
    review.subject.metadata = {"repository_id": "repo-a"}
    result = ReviewWorkspaceResult(
        status="ready",
        workspace_path=str(tmp_path / "rw" / "repo-a" / review_id),
        base_repo_path=str(tmp_path / "repo-a"),
        repository_id="repo-a",
        review_id=review_id,
        target_ref="dev",
        source_ref="feature/a",
        base_sha="base",
        source_sha="source",
        snapshot_commit="1234567890abcdef",
        snapshot_mode="diff_apply",
        diff_hash="hash",
        message="已基于目标分支应用 MR diff。",
    )
    monkeypatch.setattr(runner.review_workspace_service, "prepare", lambda **_kwargs: result)
    monkeypatch.setattr(
        runner,
        "_build_review_workspace_code_graph",
        lambda *_args, **_kwargs: {"status": "ready", "graph_db_path": str(tmp_path / "rw" / "repo-a" / review_id / ".code-review-graph" / "graph.db")},
    )
    monkeypatch.setattr(
        runner,
        "_build_review_workspace_gitnexus_graph",
        lambda *_args, **_kwargs: {"status": "ready", "graph_dir": str(tmp_path / "rw" / "repo-a" / review_id / ".gitnexus")},
    )

    runner._prepare_review_workspace(review, RuntimeSettings(enable_review_workspace_realtime_graph=True))

    messages = runner.message_repo.list(review_id)
    message_types = [message.message_type for message in messages]
    assert "review_workspace_code_graph_started" in message_types
    assert "review_workspace_code_graph_completed" in message_types
    assert "review_workspace_gitnexus_graph_started" in message_types
    assert "review_workspace_gitnexus_graph_completed" in message_types
    assert any("Tree-sitter 快照图谱初始化完成" in message.content for message in messages)
    assert any("GitNexus 快照图谱初始化完成" in message.content for message in messages)


def test_review_runner_skips_worktree_when_realtime_workspace_graph_is_disabled(storage_root: Path, tmp_path: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.subject.subject_type = "mr"
    review.subject.unified_diff = "diff --git a/src/App.java b/src/App.java\n@@ -0,0 +1 @@\n+class App {}\n"
    configured_repo = tmp_path / "configured-repo"
    configured_repo.mkdir()
    runtime = RuntimeSettings(code_repo_local_path=str(configured_repo), enable_review_workspace_realtime_graph=False)

    def fail_prepare(**_kwargs):
        raise AssertionError("worktree prepare should not be called when realtime workspace graph is disabled")

    monkeypatch.setattr(runner.review_workspace_service, "prepare", fail_prepare)

    updated = runner._prepare_review_workspace(review, runtime)

    metadata = dict(updated.subject.metadata or {})
    assert metadata["review_workspace_status"] == "skipped"
    assert metadata["workspace_repo_path"] == str(configured_repo)
    assert "review_workspace_path" not in metadata
    assert any(message.message_type == "review_workspace" and "未创建 MR worktree" in message.content for message in runner.message_repo.list(review_id))


def test_review_runner_preserves_explicit_mr_workspace_when_realtime_graph_is_disabled(
    storage_root: Path, tmp_path: Path, monkeypatch
):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.subject.subject_type = "mr"
    review.subject.unified_diff = "diff --git a/src/App.java b/src/App.java\n@@ -0,0 +1 @@\n+class App {}\n"
    explicit_workspace = tmp_path / "mr-workspace"
    configured_repo = tmp_path / "configured-repo"
    graph_db_path = explicit_workspace / ".code-review-graph" / "graph.db"
    explicit_workspace.mkdir()
    configured_repo.mkdir()
    review.subject.metadata = {
        **dict(review.subject.metadata or {}),
        "workspace_repo_path": str(explicit_workspace),
        "repo_context_workspace_path": str(explicit_workspace),
        "code_graph_db_path": str(graph_db_path),
    }
    runtime = RuntimeSettings(code_repo_local_path=str(configured_repo), enable_review_workspace_realtime_graph=False)

    def fail_prepare(**_kwargs):
        raise AssertionError("worktree prepare should not be called when realtime workspace graph is disabled")

    monkeypatch.setattr(runner.review_workspace_service, "prepare", fail_prepare)

    updated = runner._prepare_review_workspace(review, runtime)

    metadata = dict(updated.subject.metadata or {})
    assert metadata["review_workspace_status"] == "skipped"
    assert metadata["workspace_repo_path"] == str(explicit_workspace)
    assert metadata["repo_context_workspace_path"] == str(explicit_workspace)
    assert metadata["configured_workspace_repo_path"] == str(configured_repo)
    assert metadata["code_graph_db_path"] == str(graph_db_path)


def test_review_runner_preheats_gitnexus_graph_even_when_runtime_tool_allowlist_is_missing_binding(storage_root: Path, tmp_path: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    repo = tmp_path / "repo"
    repo.mkdir()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-a",
        project_id="proj",
        source_ref="mr/12",
        target_ref="dev",
        changed_files=["src/App.java"],
        unified_diff="diff --git a/src/App.java b/src/App.java\n@@ -0,0 +1 @@\n+class App {}\n",
        metadata={
            "repository_id": "repo-a",
            "review_workspace_status": "ready",
            "review_workspace_path": str(repo),
            "workspace_repo_path": str(repo),
        },
    )
    runtime = RuntimeSettings(code_repo_local_path=str(repo), runtime_tool_allowlist=[])
    called: dict[str, object] = {}

    def fake_ensure_review_workspace_index(received_subject, received_runtime):
        called["subject"] = received_subject
        called["runtime"] = received_runtime
        return {"status": "ready", "graph_dir": str(repo / ".gitnexus")}

    monkeypatch.setattr(runner.gitnexus_impact_service, "ensure_review_workspace_index", fake_ensure_review_workspace_index)

    result = runner._build_review_workspace_gitnexus_graph(subject, runtime)

    assert result["status"] == "ready"
    assert called["subject"] is subject
    assert called["runtime"] is runtime


def test_change_impact_analysis_findings_are_always_suppressed(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        finding_id="finding-impact-1",
        review_id="review-impact-1",
        expert_id="change_impact_analysis",
        title="影响范围提示",
        summary="本次改动可能影响订单入口和库存联动。",
        finding_type="risk_hypothesis",
        severity="medium",
        confidence=0.92,
        file_path="src/main/java/com/example/OrderService.java",
        line_start=12,
        evidence=["GitNexus 返回了调用链影响。"],
        remediation_suggestion="补充回归测试。",
        code_excerpt="+ orderService.create();",
        created_at="2026-04-27T00:00:00Z",
    )

    assert runner._should_skip_finding("change_impact_analysis", finding) is True


def test_change_impact_analysis_runs_in_dedicated_flow(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["change_impact_analysis"]
    review.subject.metadata = {**dict(review.subject.metadata or {}), "manual_expert_selection": True}
    runner.review_repo.save(review)

    def _should_not_build_manual_routing(*_args, **_kwargs):
        raise AssertionError("只有关联影响分析专家时不应进入普通专家派工流程")

    monkeypatch.setattr(runner, "_build_manual_routing_plan", _should_not_build_manual_routing)
    monkeypatch.setattr(
        runner.gitnexus_impact_service,
        "analyze_with_trace",
        lambda subject, runtime: (
            ImpactReport(
                graph_status="ready",
                risk_level="medium",
                changed_files=list(subject.changed_files or []),
                impacted_files=[],
                impacted_modules=["order"],
                changed_symbols=[],
                impact_paths=[],
                external_entrypoints=["OrderController#create"],
                recommended_test_scope=[
                    ImpactTestScopeRecommendation(
                        scope="订单创建接口回归",
                        reason="入口调用链命中订单创建主流程",
                        paths=["OrderController -> OrderService -> OrderRepository"],
                        priority="high",
                    )
                ],
                must_run_tests=["OrderControllerTest#create"],
                manual_verification=["验证订单创建后库存联动"],
                limitations=[],
            ),
            {},
        ),
    )

    updated = runner.run_once(review_id)

    assert updated.status == "completed"
    assert dict(updated.subject.metadata or {}).get("impact_report")
    messages = runner.message_repo.list(review_id)
    assert any(item.message_type == "impact_analysis_started" for item in messages)
    assert any(item.message_type == "impact_report_generated" for item in messages)


def test_change_impact_analysis_failure_does_not_emit_fallback_report(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["change_impact_analysis"]
    review.subject.metadata = {**dict(review.subject.metadata or {}), "manual_expert_selection": True}
    runner.review_repo.save(review)

    monkeypatch.setattr(
        runner.gitnexus_impact_service,
        "analyze_with_trace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gitnexus mcp timeout")),
    )

    updated = runner.run_once(review_id)

    assert updated.status == "completed"
    metadata = dict(updated.subject.metadata or {})
    assert "impact_report" not in metadata
    progress = dict(metadata.get("impact_analysis_progress") or {})
    assert progress.get("state") == "failed"
    assert progress.get("graph_status") == "failed"
    assert "gitnexus mcp timeout" in str(progress.get("error_message") or "")
    messages = runner.message_repo.list(review_id)
    assert any(item.message_type == "impact_analysis_started" for item in messages)
    assert any(item.message_type == "impact_report_failed" for item in messages)
    assert not any(item.message_type == "impact_report_generated" for item in messages)


def test_review_runner_releases_large_expert_job_payload_after_execution(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    job = {
        "bound_documents": [{"title": "Doc"}],
        "knowledge_context": {"summary": "context"},
        "rule_screening": {"matched_rules_for_llm": [{"rule_id": "RULE-1"}]},
        "repository_context": {"summary": "repo"},
        "target_hunk": {"excerpt": "diff"},
        "related_files": ["src/main/java/com/example/OrderService.java"],
        "business_changed_files": ["src/main/java/com/example/OrderService.java"],
        "expected_checks": ["check"],
        "disallowed_inference": ["guess"],
        "keep_me": "value",
    }

    runner._release_expert_job_payload(job)

    assert job == {"keep_me": "value"}


def test_review_runner_route_hints_preserve_multi_line_hunk_metadata(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = runner.registry.list_all()[0]
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java"],
    )
    candidate_hunks = [
        {
            "file_path": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
            "line_start": 18,
            "start_line": 18,
            "end_line": 21,
            "changed_lines": [18, 21],
            "hunk_header": "@@ -15,9 +15,9 @@ public final class CourseCreator {",
            "excerpt": "18 | +        Course course = new Course(id, name, duration);\n21 | +        repository.save(course);",
            "repo_hits": {},
        }
    ]

    route_hints = runner._build_expert_route_hints(
        subject,
        expert,
        candidate_hunks,
        primary_route={"confidence": 0.9, "routing_reason": "test"},
    )

    assert len(route_hints) == 1
    assert route_hints[0]["target_hunk"]["start_line"] == 18
    assert route_hints[0]["target_hunk"]["end_line"] == 21
    assert route_hints[0]["target_hunk"]["changed_lines"] == [18, 21]


def test_review_runner_emits_main_agent_intake_message(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)

    intake = next(item for item in messages if item.message_type == "main_agent_intake")
    assert intake.expert_id == "main_agent"
    assert "changed_files" in intake.metadata


def test_review_runner_emits_main_agent_intake_before_routing_plan(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    original_build_routing_plan = runner.main_agent_service.build_routing_plan

    def _assert_intake_written_first(subject, experts, runtime_settings, analysis_mode="standard"):
        messages = runner.message_repo.list(review_id)
        intake_messages = [item for item in messages if item.message_type == "main_agent_intake"]
        assert intake_messages, "main_agent_intake 应该在路由规划前就已写入"
        return original_build_routing_plan(subject, experts, runtime_settings, analysis_mode=analysis_mode)

    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _assert_intake_written_first)

    runner.run_once(review_id)


def test_review_runner_emits_expert_selection_before_routing_plan(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    original_build_routing_plan = runner.main_agent_service.build_routing_plan

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        return {
            "requested_expert_ids": list(requested_expert_ids or []),
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": [experts[0].expert_id],
            "selected_experts": [
                {
                    "expert_id": experts[0].expert_id,
                    "expert_name": experts[0].name_zh,
                    "reason": "该 MR 主要命中正确性问题",
                    "confidence": 0.91,
                }
            ],
            "skipped_experts": [],
            "llm": {
                "provider": "test",
                "model": "test",
                "base_url": "http://llm.test",
                "api_key_env": "TEST_KEY",
                "mode": "live",
                "error": "",
            },
        }

    def _assert_selection_written_first(subject, experts, runtime_settings, analysis_mode="standard"):
        messages = runner.message_repo.list(review_id)
        selection_messages = [item for item in messages if item.message_type == "main_agent_expert_selection"]
        assert selection_messages, "main_agent_expert_selection 应该在路由规划前就已写入"
        return original_build_routing_plan(subject, experts, runtime_settings, analysis_mode=analysis_mode)

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _assert_selection_written_first)

    runner.run_once(review_id)


def test_review_runner_skips_llm_expert_selection_when_user_selected_experts(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["correctness_business"]
    review.subject.metadata = {**dict(review.subject.metadata or {}), "manual_expert_selection": True}
    runner.review_repo.save(review)

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("用户已手动选择专家时不应调用 LLM 进行专家判定")

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _should_not_be_called)

    runner.run_once(review_id)
    messages = runner.message_repo.list(review_id)
    selection = next(item for item in messages if item.message_type == "main_agent_expert_selection")
    assert selection.metadata.get("mode") == "user_selected_direct"


def test_review_runner_skips_routing_plan_llm_when_user_selected_experts(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["correctness_business"]
    review.subject.metadata = {**dict(review.subject.metadata or {}), "manual_expert_selection": True}
    runner.review_repo.save(review)

    def _should_not_select(*_args, **_kwargs):
        raise AssertionError("用户已手动选择专家时不应再次调用专家选择 LLM")

    def _should_not_route(*_args, **_kwargs):
        raise AssertionError("用户已手动选择专家且专家覆盖全部 hunk 时不应再调用 routing_plan LLM")

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _should_not_select)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _should_not_route)

    runner.run_once(review_id)
    messages = runner.message_repo.list(review_id)
    routing_ready = next(item for item in messages if item.message_type == "main_agent_routing_ready")
    assert routing_ready.metadata.get("selected_expert_ids") == ["correctness_business"]


def test_review_runner_still_calls_llm_selection_when_only_system_default_impact_expert_exists(
    storage_root: Path, monkeypatch
):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["change_impact_analysis"]
    review.subject.metadata = {**dict(review.subject.metadata or {}), "manual_expert_selection": False}
    runner.review_repo.save(review)

    llm_called = {"value": False}

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        llm_called["value"] = True
        assert requested_expert_ids == ["change_impact_analysis"]
        return {
            "requested_expert_ids": list(requested_expert_ids or []),
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": ["correctness_business", "change_impact_analysis"],
            "selected_experts": [
                {
                    "expert_id": "correctness_business",
                    "expert_name": "业务正确性专家",
                    "reason": "主Agent 判定该 MR 需要业务正确性检视",
                    "confidence": 0.92,
                },
                {
                    "expert_id": "change_impact_analysis",
                    "expert_name": "关联性影响分析专家",
                    "reason": "系统默认参与每一次 MR 检视",
                    "confidence": 1.0,
                },
            ],
            "skipped_experts": [],
            "llm": {
                "provider": "test",
                "model": "test",
                "base_url": "http://llm.test",
                "api_key_env": "TEST_KEY",
                "mode": "live",
                "error": "",
            },
        }

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)

    runner.run_once(review_id)

    assert llm_called["value"] is True
    messages = runner.message_repo.list(review_id)
    selection = next(item for item in messages if item.message_type == "main_agent_expert_selection")
    assert selection.metadata.get("mode") == "live"


def test_review_runner_forces_change_impact_expert_for_mr_even_when_llm_skips_it(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    enabled_experts = runner.registry.list_enabled()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="project",
        source_ref="feature/demo",
        target_ref="main",
        title="MR review",
    )

    plan = runner._ensure_required_mr_experts(
        subject=subject,
        enabled_experts=enabled_experts,
        selection_plan={
            "requested_expert_ids": ["change_impact_analysis"],
            "candidate_expert_ids": [expert.expert_id for expert in enabled_experts],
            "selected_expert_ids": ["correctness_business"],
            "selected_experts": [
                {
                    "expert_id": "correctness_business",
                    "expert_name": "业务正确性专家",
                    "reason": "LLM 判定业务专家参与",
                    "confidence": 0.9,
                }
            ],
            "skipped_experts": [
                {
                    "expert_id": "change_impact_analysis",
                    "reason": "LLM 误判无需参与",
                }
            ],
            "llm": {"mode": "live"},
        },
    )

    assert "change_impact_analysis" in plan["selected_expert_ids"]
    selected = {item["expert_id"]: item for item in plan["selected_experts"]}
    assert selected["change_impact_analysis"]["source"] == "system_required"
    assert all(item.get("expert_id") != "change_impact_analysis" for item in plan["skipped_experts"])


def test_review_runner_does_not_force_change_impact_expert_for_branch_review(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    enabled_experts = runner.registry.list_enabled()
    subject = ReviewSubject(
        subject_type="branch",
        repo_id="repo",
        project_id="project",
        source_ref="feature/demo",
        target_ref="main",
        title="Branch review",
    )
    selection_plan = {
        "requested_expert_ids": [],
        "candidate_expert_ids": [expert.expert_id for expert in enabled_experts],
        "selected_expert_ids": ["correctness_business"],
        "selected_experts": [{"expert_id": "correctness_business"}],
        "skipped_experts": [],
        "llm": {"mode": "live"},
    }

    plan = runner._ensure_required_mr_experts(
        subject=subject,
        enabled_experts=enabled_experts,
        selection_plan=selection_plan,
    )

    assert plan is selection_plan
    assert "change_impact_analysis" not in plan["selected_expert_ids"]


def test_review_runner_batches_rule_screening_once_per_expert(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["correctness_business"]
    runner.review_repo.save(review)

    route_hints = [
        {
            "file_path": "src/main/java/com/example/OrderService.java",
            "line_start": 18,
            "target_hunk": {
                "file_path": "src/main/java/com/example/OrderService.java",
                "start_line": 18,
                "changed_lines": [18],
                "excerpt": "+ create(order);",
            },
            "target_hunks": [
                {
                    "file_path": "src/main/java/com/example/OrderService.java",
                    "start_line": 18,
                    "changed_lines": [18],
                    "excerpt": "+ create(order);",
                }
            ],
            "repo_hits": {},
            "confidence": 0.9,
            "routing_reason": "批量覆盖业务文件",
        },
        {
            "file_path": "src/main/java/com/example/OrderRepository.java",
            "line_start": 33,
            "target_hunk": {
                "file_path": "src/main/java/com/example/OrderRepository.java",
                "start_line": 33,
                "changed_lines": [33],
                "excerpt": "+ findAll();",
            },
            "target_hunks": [
                {
                    "file_path": "src/main/java/com/example/OrderRepository.java",
                    "start_line": 33,
                    "changed_lines": [33],
                    "excerpt": "+ findAll();",
                }
            ],
            "repo_hits": {},
            "confidence": 0.9,
            "routing_reason": "批量覆盖仓储文件",
        },
    ]

    monkeypatch.setattr(runner, "_build_expert_route_hints", lambda *_args, **_kwargs: route_hints)
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_command",
        lambda _subject, _expert, _runtime_settings, route_hint=None: {
            "file_path": str((route_hint or {}).get("file_path") or ""),
            "line_start": int((route_hint or {}).get("line_start") or 1),
            "summary": "批量派工",
            "related_files": [str((route_hint or {}).get("file_path") or "")],
            "target_hunk": dict((route_hint or {}).get("target_hunk") or {}),
            "target_hunks": [dict(item) for item in list((route_hint or {}).get("target_hunks") or [])],
            "repository_context": {},
            "expected_checks": [],
            "disallowed_inference": [],
            "routing_reason": str((route_hint or {}).get("routing_reason") or ""),
            "routing_confidence": float((route_hint or {}).get("confidence") or 0.0),
        },
    )
    monkeypatch.setattr(runner.knowledge_service, "retrieve_for_expert", lambda *_args, **_kwargs: [])
    screening_calls: list[dict[str, object]] = []

    def _fake_screen_rules_for_expert(expert_id, review_context, **_kwargs):
        screening_calls.append(
            {
                "expert_id": expert_id,
                "changed_files": list(review_context.get("changed_files", []) or []),
                "query_terms": list(review_context.get("query_terms", []) or []),
            }
        )
        return {
            "total_rules": 2,
            "enabled_rules": 2,
            "must_review_count": 1,
            "possible_hit_count": 0,
            "matched_rule_count": 1,
            "screening_mode": "heuristic",
            "screening_fallback_used": False,
            "matched_rules_for_llm": [{"rule_id": "RULE-1", "title": "demo"}],
            "batch_summaries": [],
        }

    monkeypatch.setattr(runner.knowledge_service, "screen_rules_for_expert", _fake_screen_rules_for_expert)
    monkeypatch.setattr(runner, "_execute_expert_jobs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.graph, "invoke", lambda _state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda *_args, **_kwargs: ("批量筛选测试完成", {"provider": "test", "model": "test", "mode": "mock"}),
    )

    runner.run_once(review_id)

    assert len(screening_calls) == 1
    assert screening_calls[0]["expert_id"] == "correctness_business"
    assert "src/main/java/com/example/OrderService.java" in screening_calls[0]["query_terms"]
    assert "src/main/java/com/example/OrderRepository.java" in screening_calls[0]["query_terms"]


def test_review_runner_emits_routing_preparing_before_build_routing_plan(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    original_build_routing_plan = runner.main_agent_service.build_routing_plan

    def _assert_routing_prepare_written_first(subject, experts, runtime_settings, analysis_mode="standard"):
        messages = runner.message_repo.list(review_id)
        preparing_messages = [item for item in messages if item.message_type == "main_agent_routing_preparing"]
        assert preparing_messages, "main_agent_routing_preparing 应该在真正构建 routing_plan 前就已写入"
        return original_build_routing_plan(subject, experts, runtime_settings, analysis_mode=analysis_mode)

    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _assert_routing_prepare_written_first)

    runner.run_once(review_id)


def test_review_runner_emits_phase_timing_messages(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    selection_message = next(item for item in messages if item.message_type == "main_agent_expert_selection")
    routing_ready_message = next(item for item in messages if item.message_type == "main_agent_routing_ready")
    expert_execution_message = next(
        item for item in messages if item.message_type == "main_agent_expert_execution_completed"
    )

    assert isinstance(selection_message.metadata.get("selection_elapsed_ms"), (int, float))
    assert isinstance(routing_ready_message.metadata.get("routing_elapsed_ms"), (int, float))
    assert isinstance(expert_execution_message.metadata.get("expert_execution_elapsed_ms"), (int, float))


def test_review_runner_emits_rule_screening_batch_messages(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    monkeypatch.setattr(runner.knowledge_service, "retrieve_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.knowledge_service,
        "screen_rules_for_expert",
        lambda *_args, **_kwargs: {
            "total_rules": 4,
            "enabled_rules": 4,
            "must_review_count": 1,
            "possible_hit_count": 1,
            "matched_rule_count": 2,
            "screening_mode": "llm",
            "screening_fallback_used": False,
            "total_elapsed_ms": 321.45,
            "matched_rules_for_llm": [
                {
                    "rule_id": "PERF-SQL-001",
                    "title": "大结果集查询必须显式分页或限流",
                    "priority": "P1",
                    "scene_path": "数据库访问 / 查询性能 / 大结果集分页缺失",
                    "description": "查询接口缺少分页限制时必须带入深审。",
                    "language": "java",
                    "problem_code_example": "findAll();",
                    "problem_code_line": "findAll();",
                    "false_positive_code": "findAll(PageRequest.of(0, 50));",
                    "decision": "must_review",
                    "reason": "存在 LIMIT 和连续查询模式",
                    "matched_terms": ["limit"],
                },
                {
                    "rule_id": "PERF-BATCH-001",
                    "title": "批处理写入必须控制批大小与事务范围",
                    "priority": "P1",
                    "scene_path": "数据库访问 / 批处理 / 批处理事务范围过大",
                    "description": "批处理逻辑要关注单事务范围。",
                    "language": "java",
                    "problem_code_example": "flush(records);",
                    "problem_code_line": "flush(records);",
                    "false_positive_code": "flush(records.subList(0, 100));",
                    "decision": "possible_hit",
                    "reason": "存在 chunk 批处理信号",
                    "matched_terms": ["chunk"],
                },
            ],
            "batch_summaries": [
                {
                    "batch_index": 1,
                    "batch_count": 2,
                    "screening_mode": "llm",
                    "input_rule_count": 2,
                    "must_review_count": 1,
                    "possible_hit_count": 0,
                    "no_hit_count": 1,
                    "llm": {
                        "llm_call_id": "llm_rule_1",
                        "provider": "test",
                        "model": "demo",
                        "base_url": "http://llm.test",
                        "api_key_env": "TEST_KEY",
                        "mode": "live",
                        "llm_error": "",
                        "prompt_tokens": 120,
                        "completion_tokens": 20,
                        "total_tokens": 140,
                        "elapsed_ms": 111.1,
                    },
                    "input_rules": [
                        {"rule_id": "PERF-SQL-001", "title": "大结果集查询必须显式分页或限流", "priority": "P1"},
                        {"rule_id": "PERF-SQL-002", "title": "N+1 查询风险必须在服务层被识别", "priority": "P1"},
                    ],
                    "decisions": [
                        {
                            "rule_id": "PERF-SQL-001",
                            "title": "大结果集查询必须显式分页或限流",
                            "priority": "P1",
                            "decision": "must_review",
                            "reason": "存在 LIMIT 和连续查询模式",
                            "matched_terms": ["limit"],
                            "matched_signals": ["semantic:sql"],
                        },
                        {
                            "rule_id": "PERF-SQL-002",
                            "title": "N+1 查询风险必须在服务层被识别",
                            "priority": "P1",
                            "decision": "no_hit",
                            "reason": "当前改动未形成 N+1 信号",
                            "matched_terms": [],
                            "matched_signals": [],
                        },
                    ],
                },
                {
                    "batch_index": 2,
                    "batch_count": 2,
                    "screening_mode": "llm",
                    "input_rule_count": 2,
                    "must_review_count": 0,
                    "possible_hit_count": 1,
                    "no_hit_count": 1,
                    "llm": {
                        "llm_call_id": "llm_rule_2",
                        "provider": "test",
                        "model": "demo",
                        "base_url": "http://llm.test",
                        "api_key_env": "TEST_KEY",
                        "mode": "live",
                        "llm_error": "",
                        "prompt_tokens": 90,
                        "completion_tokens": 18,
                        "total_tokens": 108,
                        "elapsed_ms": 210.35,
                    },
                    "input_rules": [
                        {"rule_id": "PERF-BATCH-001", "title": "批处理写入必须控制批大小与事务范围", "priority": "P1"},
                        {"rule_id": "PERF-JSON-001", "title": "大型对象序列化路径必须避免重复拷贝", "priority": "P2"},
                    ],
                    "decisions": [
                        {
                            "rule_id": "PERF-BATCH-001",
                            "title": "批处理写入必须控制批大小与事务范围",
                            "priority": "P1",
                            "decision": "possible_hit",
                            "reason": "存在 chunk 批处理信号",
                            "matched_terms": ["chunk"],
                            "matched_signals": ["semantic:batch"],
                        },
                        {
                            "rule_id": "PERF-JSON-001",
                            "title": "大型对象序列化路径必须避免重复拷贝",
                            "priority": "P2",
                            "decision": "no_hit",
                            "reason": "当前改动未命中 JSON 热路径",
                            "matched_terms": [],
                            "matched_signals": [],
                        },
                    ],
                },
            ],
        },
    )

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    batch_messages = [item for item in messages if item.message_type == "expert_rule_screening_batch"]
    assert batch_messages
    assert len(batch_messages) >= 2
    first_batch = batch_messages[0]
    assert first_batch.expert_id
    assert "第 1/2 批" in first_batch.content
    batch_metadata = first_batch.metadata.get("rule_screening_batch", {})
    assert batch_metadata["batch_index"] == 1
    assert batch_metadata["input_rule_count"] == 2
    assert batch_metadata["elapsed_ms"] == 111.1
    assert first_batch.metadata["rule_screening"]["total_elapsed_ms"] == 321.45
    assert first_batch.metadata["rule_screening_total_elapsed_ms"] == 321.45


def test_review_runner_emits_issue_filter_message_when_findings_are_kept_as_findings(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        first = experts[0]
        return {
            "requested_expert_ids": list(requested_expert_ids or []),
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": [first.expert_id],
            "selected_experts": [{"expert_id": first.expert_id, "expert_name": first.name_zh, "reason": "演示治理过滤"}],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_build_routing_plan(subject, experts, runtime_settings, analysis_mode="standard"):
        first = experts[0]
        return {
            "jobs": [
                {
                    "expert": first,
                    "review": runner.review_repo.get(review_id),
                    "command_message": None,
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 42,
                    "runtime_settings": runtime_settings,
                    "analysis_mode": "standard",
                    "llm_request_options": {"timeout_seconds": 1, "max_attempts": 1},
                    "bound_documents": [],
                    "knowledge_context": {},
                    "finding_payloads": [],
                }
            ],
            "summary": {"effective_experts": [{"expert_id": first.expert_id, "expert_name": first.name_zh}]},
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        for job in expert_jobs:
            job["finding_payloads"].append(
                {
                    "finding_id": "fdg_hint_demo",
                    "expert_id": "maintainability_code_health",
                    "title": "建议统一日志补充方式",
                    "summary": "这是一个常见的提示性建议，主要影响可读性与排障体验，运行时风险较低。",
                    "finding_type": "risk_hypothesis",
                    "severity": "medium",
                    "confidence": 0.61,
                    "verification_needed": True,
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 42,
                    "evidence": ["日志模板风格不一致"],
                    "cross_file_evidence": [],
                    "context_files": [],
                    "matched_rules": ["日志补充"],
                    "violated_guidelines": ["统一写法"],
                    "assumptions": [],
                    "remediation_strategy": "统一日志输出模板",
                    "remediation_suggestion": "补齐统一日志模板",
                    "remediation_steps": [],
                    "code_excerpt": 'logger.info("...")',
                    "suggested_code": "",
                    "suggested_code_language": "java",
                }
            )

    def _fake_graph_invoke(state):
        assert state["findings"][0]["title"] == "建议统一日志补充方式"
        return {
            "issues": [],
            "issue_filter_decisions": [
                {
                    "topic": "src/app/service/OrderService.java::2",
                    "rule_code": "hint_like_medium",
                    "rule_label": "提示性中风险问题保留为 finding",
                    "reason": "当前问题更偏命名、注释、风格、日志补充等提示性建议，因此仅保留为 finding。",
                    "severity": "medium",
                    "finding_ids": ["fdg_hint_demo"],
                    "finding_titles": ["建议统一日志补充方式"],
                    "expert_ids": ["maintainability_code_health"],
                }
            ],
        }

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _fake_build_routing_plan)
    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", _fake_graph_invoke)
    def _fake_build_final_summary(*_args, **_kwargs):
        saved_review = runner.review_repo.get(review_id)
        assert saved_review is not None
        assert saved_review.status == "completed"
        assert runner.issue_repo.list(review_id) == []
        return "演示总结", {"provider": "test", "model": "test", "mode": "mock"}

    monkeypatch.setattr(runner.main_agent_service, "build_final_summary", _fake_build_final_summary)

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    issue_filter_message = next(item for item in messages if item.message_type == "issue_filter_applied")
    assert issue_filter_message.expert_id == "main_agent"
    assert "未升级为 issues" in issue_filter_message.content
    decisions = issue_filter_message.metadata.get("issue_filter_decisions", [])
    assert isinstance(decisions, list) and decisions
    assert decisions[0]["rule_code"] == "hint_like_medium"
    assert runner.issue_repo.list(review_id) == []


def test_review_runner_reads_issue_filter_settings_from_runtime(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    runner.runtime_settings_service.update(
        {
          "default_analysis_mode": "light",
          "issue_filter_enabled": True,
          "issue_min_priority_level": "P1",
          "issue_confidence_threshold_p0": 0.99,
          "issue_confidence_threshold_p1": 0.95,
          "issue_confidence_threshold_p2": 0.82,
          "issue_confidence_threshold_p3": 0.71,
          "suppress_low_risk_hint_issues": False,
          "hint_issue_confidence_threshold": 0.93,
          "hint_issue_evidence_cap": 5,
          "light_llm_timeout_seconds": 210,
          "light_llm_retry_count": 2,
          "light_max_parallel_experts": 1,
          "light_max_debate_rounds": 1,
        }
    )
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    review.analysis_mode = "light"
    runner.review_repo.save(review)

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        first = experts[0]
        return {
            "requested_expert_ids": list(requested_expert_ids or []),
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": [first.expert_id],
            "selected_experts": [{"expert_id": first.expert_id, "expert_name": first.name_zh, "reason": "校验设置联动"}],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_build_routing_plan(subject, experts, runtime_settings, analysis_mode="standard"):
        assert analysis_mode == "light"
        assert runtime_settings.light_llm_timeout_seconds == 210
        first = experts[0]
        return {
            "jobs": [
                {
                    "expert": first,
                    "review": runner.review_repo.get(review_id),
                    "command_message": None,
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 18,
                    "runtime_settings": runtime_settings,
                    "analysis_mode": "light",
                    "llm_request_options": {"timeout_seconds": 210, "max_attempts": 2},
                    "bound_documents": [],
                    "knowledge_context": {},
                    "finding_payloads": [],
                }
            ],
            "summary": {"effective_experts": [{"expert_id": first.expert_id, "expert_name": first.name_zh}]},
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        assert analysis_mode == "light"
        assert runtime_settings.light_llm_timeout_seconds == 210
        expert_jobs[0]["finding_payloads"].append(
            {
                "finding_id": "fdg_runtime_linked",
                "expert_id": "maintainability_code_health",
                "title": "重复逻辑应收敛",
                "summary": "当前实现存在重复分支，维护成本偏高。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.89,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 18,
                "evidence": ["重复判空逻辑"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["重复逻辑应收敛"],
                "violated_guidelines": ["维护性要求"],
                "assumptions": [],
                "remediation_strategy": "抽取公共函数",
                "remediation_suggestion": "收敛重复逻辑",
                "remediation_steps": [],
                "code_excerpt": "if (x == null) { ... }",
                "suggested_code": "",
                "suggested_code_language": "java",
            }
        )

    def _fake_graph_invoke(state):
        assert state["analysis_mode"] == "light"
        assert state["issue_filter_config"]["issue_filter_enabled"] is True
        assert state["issue_filter_config"]["issue_min_priority_level"] == "P1"
        assert state["issue_filter_config"]["issue_confidence_threshold_p0"] == 0.99
        assert state["issue_filter_config"]["issue_confidence_threshold_p1"] == 0.95
        assert state["issue_filter_config"]["issue_confidence_threshold_p2"] == 0.82
        assert state["issue_filter_config"]["issue_confidence_threshold_p3"] == 0.71
        assert state["issue_filter_config"]["suppress_low_risk_hint_issues"] is False
        assert state["issue_filter_config"]["hint_issue_confidence_threshold"] == 0.93
        assert state["issue_filter_config"]["hint_issue_evidence_cap"] == 5
        return {"issues": [], "issue_filter_decisions": []}

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _fake_build_routing_plan)
    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", _fake_graph_invoke)
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts: (
            "设置联动验证通过",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)


def test_review_runner_persists_primary_expert_issue_metadata(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.selected_experts = ["correctness_business"]
    runner.review_repo.save(review)

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        selected = next(expert for expert in experts if expert.expert_id == "correctness_business")
        return {
            "requested_expert_ids": list(requested_expert_ids or []),
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": [selected.expert_id],
            "selected_experts": [
                {"expert_id": selected.expert_id, "expert_name": selected.name_zh, "reason": "校验主责专家归因"}
            ],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_build_routing_plan(subject, experts, runtime_settings, analysis_mode="standard"):
        expert = next(item for item in experts if item.expert_id == "correctness_business")
        return {
            "jobs": [
                {
                    "expert": expert,
                    "review": runner.review_repo.get(review_id),
                    "command_message": None,
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 42,
                    "runtime_settings": runtime_settings,
                    "analysis_mode": analysis_mode,
                    "llm_request_options": {"timeout_seconds": 1, "max_attempts": 1},
                    "bound_documents": [],
                    "knowledge_context": {},
                    "finding_payloads": [],
                }
            ],
            "summary": {"effective_experts": [{"expert_id": expert.expert_id, "expert_name": expert.name_zh}]},
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        for job in expert_jobs:
            job["finding_payloads"].append(
                {
                    "finding_id": "fdg_primary_meta",
                    "expert_id": "correctness_business",
                    "title": "注释承诺与实现不一致",
                    "summary": "接口注释承诺了幂等行为，但实现中没有对应保护。",
                    "finding_type": "direct_defect",
                    "severity": "high",
                    "confidence": 0.92,
                    "verification_needed": False,
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 42,
                    "evidence": ["注释说明会忽略重复请求，但代码未判断 requestId"],
                    "cross_file_evidence": [],
                    "context_files": [],
                    "matched_rules": [],
                    "violated_guidelines": [],
                    "assumptions": [],
                    "remediation_strategy": "补齐幂等保护",
                    "remediation_suggestion": "基于 requestId 做重复请求判断",
                    "remediation_steps": [],
                    "code_excerpt": "public void createOrder(...)",
                    "suggested_code": "",
                    "suggested_code_language": "java",
                }
            )

    def _fake_graph_invoke(state):
        return {
            "issues": [
                {
                    "issue_id": "iss_primary_meta",
                    "title": "注释承诺与实现不一致",
                    "summary": "接口注释承诺了幂等行为，但实现中没有对应保护。",
                    "finding_type": "direct_defect",
                    "normalized_issue_type": "comment_promise_not_implemented",
                    "primary_expert_id": "correctness_business",
                    "file_path": "src/app/service/OrderService.java",
                    "line_start": 42,
                    "status": "open",
                    "severity": "high",
                    "confidence": 0.93,
                    "confidence_breakdown": {"source": "test"},
                    "finding_ids": ["fdg_primary_meta"],
                    "participant_expert_ids": ["correctness_business", "maintainability_code_health"],
                    "expert_views": [
                        {
                            "expert_id": "correctness_business",
                            "title": "注释承诺与实现不一致",
                            "summary": "接口注释承诺了幂等行为，但实现中没有对应保护。",
                            "severity": "high",
                            "confidence": 0.93,
                        }
                    ],
                    "aggregated_titles": ["注释承诺与实现不一致"],
                    "aggregated_summaries": ["接口注释承诺了幂等行为，但实现中没有对应保护。"],
                    "aggregated_remediation_strategies": ["补齐幂等保护"],
                    "aggregated_remediation_suggestions": ["基于 requestId 做重复请求判断"],
                    "aggregated_remediation_steps": [],
                    "remediation_strategy": "补齐幂等保护",
                    "remediation_suggestion": "基于 requestId 做重复请求判断",
                    "remediation_steps": [],
                    "evidence": ["注释说明会忽略重复请求，但代码未判断 requestId"],
                    "cross_file_evidence": [],
                    "assumptions": [],
                    "context_files": [],
                    "direct_evidence": True,
                    "needs_human": False,
                    "verified": True,
                    "needs_debate": False,
                    "tool_verified": False,
                    "resolution": "accepted",
                }
            ],
            "issue_filter_decisions": [],
        }

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _fake_build_routing_plan)
    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", _fake_graph_invoke)
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts: (
            "演示总结",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    persisted_issues = runner.issue_repo.list(review_id)
    assert len(persisted_issues) == 1
    assert persisted_issues[0].issue_id == "iss_primary_meta"
    assert persisted_issues[0].normalized_issue_type == "comment_promise_not_implemented"
    assert persisted_issues[0].primary_expert_id == "correctness_business"
    assert persisted_issues[0].participant_expert_ids == [
        "correctness_business",
        "maintainability_code_health",
    ]
    assert persisted_issues[0].expert_views[0]["expert_id"] == "correctness_business"


def test_review_runner_parse_expert_analysis_preserves_structured_fields(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = runner._parse_expert_analysis(
        """
        {
          "finding_type": "risk_hypothesis",
          "matched_rules": ["规则 1", "规则 2"],
          "violated_guidelines": ["规范 A"],
          "rule_based_reasoning": "字段变更后必须同步 transformer 与 DTO。",
          "context_files": ["packages/lib/schedules/getScheduleListItemData.ts"],
          "assumptions": ["当前只看到了局部 diff"],
          "claim": "存在跨文件语义漂移风险",
          "fix_strategy": "先统一 transformer 和输出 DTO",
          "change_steps": ["补字段映射", "补回归测试"],
          "suggested_code": "export function map() {}"
        }
        """,
        ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/x",
            target_ref="main",
        ),
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性",
            role="correctness",
            enabled=True,
            system_prompt="prompt",
        ),
        "apps/api/schedules/output.service.ts",
        12,
    )

    assert parsed["finding_type"] == "risk_hypothesis"
    assert parsed["matched_rules"] == ["规则 1", "规则 2"]
    assert parsed["violated_guidelines"] == ["规范 A"]
    assert parsed["rule_based_reasoning"] == "字段变更后必须同步 transformer 与 DTO。"
    assert parsed["context_files"] == ["packages/lib/schedules/getScheduleListItemData.ts"]
    assert parsed["assumptions"] == ["当前只看到了局部 diff"]
    assert parsed["fix_strategy"] == "先统一 transformer 和输出 DTO"
    assert parsed["change_steps"] == ["补字段映射", "补回归测试"]
    assert parsed["suggested_code"] == "export function map() {}"


def test_review_runner_parse_expert_analyses_supports_findings_array(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )
    parsed_items = runner._parse_expert_analyses(
        """
        {
          "findings": [
            {
              "title": "参数为空未校验",
              "claim": "入口参数 request 可能为空导致 NPE",
              "finding_type": "direct_defect",
              "severity": "high",
              "line_start": 18
            },
            {
              "title": "SQL 缺少分页",
              "claim": "查询未见 limit/page 保护，可能导致全表扫描",
              "finding_type": "risk_hypothesis",
              "severity": "medium",
              "line_start": 42
            }
          ]
        }
        """,
        subject,
        expert,
        "src/main/java/com/acme/FooService.java",
        18,
    )

    assert len(parsed_items) == 2
    assert parsed_items[0]["title"] == "参数为空未校验"
    assert parsed_items[0]["finding_type"] == "direct_defect"
    assert parsed_items[1]["title"] == "SQL 缺少分页"
    assert parsed_items[1]["finding_type"] == "risk_hypothesis"


def test_review_runner_saves_multiple_findings_from_single_expert_response(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性专家",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_multi_findings_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/multi",
            target_ref="main",
            changed_files=["src/main/java/com/acme/OrderService.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
                "--- a/src/main/java/com/acme/OrderService.java\n"
                "+++ b/src/main/java/com/acme/OrderService.java\n"
                "@@ -18,1 +18,2 @@\n"
                "- repository.save(entity);\n"
                "+ repository.save(entity);\n"
                "+ log.info(\"saved\");\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请审查本段变更",
        metadata={
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "target_hunk": {"hunk_header": "@@ -18,1 +18,2 @@", "excerpt": "+ repository.save(entity);"},
            "repository_context": {"routing_reason": "关键路径改动"},
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                '{"findings":['
                '{"title":"空参未校验","claim":"request 为空时会触发异常","finding_type":"direct_defect","severity":"high","line_start":18,'
                '"matched_rules":["CORR-001"],"violated_guidelines":["入参必须校验"],"rule_based_reasoning":"关键入口缺少空值保护。",'
                '"evidence":["未见 request 判空"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"入口增加非空校验","suggested_fix":"添加 Objects.requireNonNull","change_steps":["补判空"],'
                '"suggested_code":"Objects.requireNonNull(request);","confidence":0.9,"verification_needed":false,"verification_plan":""},'
                '{"title":"日志泄露业务标识","claim":"日志打印了敏感业务标识","finding_type":"risk_hypothesis","severity":"medium","line_start":19,'
                '"matched_rules":["CORR-LOG-001"],"violated_guidelines":["日志最小披露"],"rule_based_reasoning":"日志字段需脱敏。",'
                '"evidence":["新增 log.info 调用"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"收敛日志字段","suggested_fix":"去掉敏感字段","change_steps":["改日志模板"],'
                '"suggested_code":"log.info(\\"saved\\");","confidence":0.76,"verification_needed":true,"verification_plan":"核对日志规范"}'
                ']}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/OrderService.java",
        line_start=18,
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 2
    assert len(finding_payloads) == 2


def test_review_runner_saves_multiple_findings_from_single_expert_response_in_light_mode(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    monkeypatch.setenv("REVIEW_LIGHT_EXPERT_REQUEST_BUDGET_TOKENS", "1200")
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性专家",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_multi_findings_light_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/multi-light",
            target_ref="main",
            changed_files=["src/main/java/com/acme/OrderService.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
                "--- a/src/main/java/com/acme/OrderService.java\n"
                "+++ b/src/main/java/com/acme/OrderService.java\n"
                "@@ -18,1 +18,2 @@\n"
                "- repository.save(entity);\n"
                "+ repository.save(entity);\n"
                "+ log.info(\"saved\");\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请审查本段变更",
        metadata={
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "target_hunk": {"hunk_header": "@@ -18,1 +18,2 @@", "excerpt": "+ repository.save(entity);"},
            "repository_context": {"routing_reason": "关键路径改动"},
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    llm_log_context: dict[str, object] = {}

    def _fake_complete_text(**kwargs):
        llm_log_context.update(dict(kwargs.get("log_context") or {}))
        return LLMTextResult(
            text=(
                '{"findings":['
                '{"title":"空参未校验","claim":"request 为空时会触发异常","finding_type":"direct_defect","severity":"high","line_start":18,'
                '"matched_rules":["CORR-001"],"violated_guidelines":["入参必须校验"],"rule_based_reasoning":"关键入口缺少空值保护。",'
                '"evidence":["未见 request 判空"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"入口增加非空校验","suggested_fix":"添加 Objects.requireNonNull","change_steps":["补判空"],'
                '"suggested_code":"Objects.requireNonNull(request);","confidence":0.9,"verification_needed":false,"verification_plan":""},'
                '{"title":"日志泄露业务标识","claim":"日志打印了敏感业务标识","finding_type":"risk_hypothesis","severity":"medium","line_start":19,'
                '"matched_rules":["CORR-LOG-001"],"violated_guidelines":["日志最小披露"],"rule_based_reasoning":"日志字段需脱敏。",'
                '"evidence":["新增 log.info 调用"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"收敛日志字段","suggested_fix":"去掉敏感字段","change_steps":["改日志模板"],'
                '"suggested_code":"log.info(\\"saved\\");","confidence":0.76,"verification_needed":true,"verification_plan":"核对日志规范"}'
                ']}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        _fake_complete_text,
    )

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/OrderService.java",
        line_start=18,
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="light",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    messages = runner.message_repo.list(review.review_id)
    ack = next(item for item in messages if item.message_type == "expert_ack")
    analysis = next(item for item in messages if item.message_type == "expert_analysis")

    assert len(findings) == 2
    assert len(finding_payloads) == 2
    assert ack.metadata["prompt_budget"]["prompt_request_budget"]["total_budget"] == 1200
    assert ack.metadata["prompt_budget"]["retained_light_sections"]
    assert analysis.metadata["prompt_budget"]["prompt_request_budget"]["used_budget"] >= 0
    assert llm_log_context["prompt_budget"]["prompt_request_budget"]["total_budget"] == 1200


def test_review_runner_detects_uncovered_review_observations(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    candidates = [
        {
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "title": "已有问题",
            "claim": "已有结论",
            "observation_ids": ["obs_cov_001"],
        }
    ]
    observations = [
        {
            "observation_id": "obs_cov_001",
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "summary": "已覆盖 observation",
        },
        {
            "observation_id": "obs_miss_001",
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 36,
            "summary": "未覆盖 observation",
        },
    ]

    uncovered = runner._find_uncovered_review_observations(candidates, observations)

    assert len(uncovered) == 1
    assert uncovered[0]["observation_id"] == "obs_miss_001"


def test_review_runner_keeps_adjacent_different_kind_observation_uncovered(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    candidates = [
        {
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 19,
            "title": "循环内逐条远程调用",
            "claim": "paymentClient.sync(item) 位于循环体内，会放大网络往返。",
            "observation_ids": ["obs_loop_001"],
        }
    ]
    observations = [
        {
            "observation_id": "obs_loop_001",
            "kind": "control_flow_with_external_call",
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 19,
            "summary": "循环内存在外部调用",
        },
        {
            "observation_id": "obs_todo_001",
            "kind": "declared_intent_without_implementation",
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 20,
            "summary": "TODO 注释承诺的行为没有落地",
        },
    ]

    uncovered = runner._find_uncovered_review_observations(candidates, observations)

    assert len(uncovered) == 1
    assert uncovered[0]["observation_id"] == "obs_todo_001"


def test_review_runner_runs_observation_followup_when_first_pass_misses_observation(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_observation_followup_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/obs",
            target_ref="main",
            changed_files=["src/main/java/com/acme/OrderService.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
                "--- a/src/main/java/com/acme/OrderService.java\n"
                "+++ b/src/main/java/com/acme/OrderService.java\n"
                "@@ -18,2 +18,5 @@\n"
                "+ for (Order item : items) {\n"
                "+     paymentClient.sync(item);\n"
                "+ }\n"
                "+ // TODO publish event\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请审查本段变更",
        metadata={
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "target_hunk": {"hunk_header": "@@ -18,2 +18,5 @@", "excerpt": "+ for (Order item : items) {\n+     paymentClient.sync(item);\n+ }\n+ // TODO publish event"},
            "repository_context": {
                "routing_reason": "关键路径改动",
                "review_observations": [
                    {
                        "observation_id": "obs_loop_001",
                        "kind": "control_flow_with_external_call",
                        "file_path": "src/main/java/com/acme/OrderService.java",
                        "line_start": 19,
                        "line_end": 19,
                        "summary": "循环体内存在外部调用",
                        "evidence": ["paymentClient.sync(item) 位于循环体内"],
                        "risk_hints": ["可能导致逐条远程调用放大"],
                    }
                ],
            },
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    llm_calls: list[str] = []

    def _fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        llm_calls.append(phase)
        if phase == "expert_observation_followup":
            return LLMTextResult(
                text=(
                    '{"findings":['
                    '{"file_path":"src/main/java/com/acme/OrderService.java","title":"循环内逐条远程调用","claim":"paymentClient.sync(item) 位于循环体内，会放大网络往返与整体时延","finding_type":"direct_defect","severity":"high","line_start":19,"line_end":19,'
                    '"matched_rules":["PERF-001"],"violated_guidelines":["循环体内避免逐条外部调用"],"rule_based_reasoning":"循环体内逐条远程调用会造成线性放大。",'
                    '"evidence":["paymentClient.sync(item) 位于 for 循环体内"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                    '"observation_ids":["obs_loop_001"],"fix_strategy":"先聚合数据后批量同步","suggested_fix":"将逐条调用改为批量同步或异步批处理","change_steps":["提取批量入参","改成一次批量同步"],'
                    '"suggested_code":"paymentClient.syncBatch(items);","confidence":0.92,"verification_needed":false,"verification_plan":""}'
                    ']}'
                ),
                mode="mock",
                provider="test",
                model="test",
                base_url="http://llm.test",
                api_key_env="TEST_KEY",
            )
        return LLMTextResult(
            text=(
                '{"findings":['
                '{"file_path":"src/main/java/com/acme/OrderService.java","title":"注释承诺未落地","claim":"TODO 注释承诺的发布事件逻辑尚未实现","finding_type":"risk_hypothesis","severity":"medium","line_start":21,"line_end":21,'
                '"matched_rules":["CORR-001"],"violated_guidelines":["承诺行为必须落地"],"rule_based_reasoning":"注释与实现不一致。",'
                '"evidence":["存在 TODO publish event 注释"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"observation_ids":[],"fix_strategy":"补齐事件发布逻辑","suggested_fix":"在保存后补发领域事件","change_steps":["增加事件构造","补发事件"],'
                '"suggested_code":"domainEventPublisher.publish(new OrderCreatedEvent(orderId));","confidence":0.73,"verification_needed":false,"verification_plan":""}'
                ']}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", _fake_complete_text)

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/OrderService.java",
        line_start=18,
        repository_context=dict(command_message.metadata.get("repository_context") or {}),
        target_hunk=dict(command_message.metadata.get("target_hunk") or {}),
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    messages = runner.message_repo.list(review.review_id)

    assert len(findings) == 2
    followup_finding = next(
        item for item in findings if (item.code_context or {}).get("observation_ids") == ["obs_loop_001"]
    )
    assert followup_finding.file_path == "src/main/java/com/acme/OrderService.java"
    assert any(item.message_type == "expert_observation_followup" for item in messages)
    assert "expert_observation_followup" in llm_calls


def test_review_runner_adds_loop_risk_when_llm_misses_observation(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_observation_force_loop_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/obs-force",
            target_ref="main",
            changed_files=["src/main/java/com/acme/OrderService.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
                "--- a/src/main/java/com/acme/OrderService.java\n"
                "+++ b/src/main/java/com/acme/OrderService.java\n"
                "@@ -18,2 +18,4 @@\n"
                "+ items.forEach(item -> orderRepository.findByOrderNo(item.getOrderNo()));\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请审查本段变更",
        metadata={
            "file_path": "src/main/java/com/acme/OrderService.java",
            "line_start": 18,
            "target_hunk": {
                "hunk_header": "@@ -18,2 +18,4 @@",
                "excerpt": "+ items.forEach(item -> orderRepository.findByOrderNo(item.getOrderNo()));",
            },
            "repository_context": {
                "routing_reason": "关键路径改动",
                "review_observations": [
                    {
                        "observation_id": "obs_loop_001",
                        "kind": "control_flow_with_external_call",
                        "file_path": "src/main/java/com/acme/OrderService.java",
                        "line_start": 18,
                        "line_end": 18,
                        "summary": "循环体内存在外部调用",
                        "evidence": ["orderRepository.findByOrderNo(item.getOrderNo()) 位于 forEach 内"],
                        "risk_hints": ["可能导致逐条查库放大"],
                        "related_symbols": ["orderRepository.findByOrderNo", "forEach"],
                        "confidence": 0.86,
                    }
                ],
            },
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text='{"findings":[]}',
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/OrderService.java",
        line_start=18,
        repository_context=dict(command_message.metadata.get("repository_context") or {}),
        target_hunk=dict(command_message.metadata.get("target_hunk") or {}),
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 1
    assert findings[0].title == "循环调用放大"
    assert findings[0].expert_id == "performance_reliability"
    assert findings[0].finding_type == "risk_hypothesis"
    assert findings[0].verification_needed is True
    assert float(findings[0].confidence) <= 0.78


def test_review_runner_observation_followup_keeps_multiple_distinct_findings(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_observation_multi_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/obs-multi",
            target_ref="main",
            changed_files=["src/main/java/com/acme/OrderService.java"],
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)

    merged = runner._merge_expert_analysis_candidates(
        base_candidates=[
            {
                "file_path": "src/main/java/com/acme/OrderService.java",
                "title": "循环内逐条远程调用",
                "claim": "paymentClient.sync(item) 位于循环体内，会放大网络往返与整体时延",
                "finding_type": "direct_defect",
                "line_start": 19,
                "observation_ids": ["obs_loop_001"],
            }
        ],
        extra_candidates=[
            {
                "file_path": "src/main/java/com/acme/OrderService.java",
                "title": "注释承诺未落地",
                "claim": "TODO 注释承诺的发布事件逻辑尚未实现",
                "finding_type": "risk_hypothesis",
                "line_start": 21,
                "observation_ids": ["obs_todo_001"],
            }
        ],
        max_findings=8,
    )

    assert len(merged) == 2
    assert {item["title"] for item in merged} == {"循环内逐条远程调用", "注释承诺未落地"}


def test_review_runner_observation_merge_accepts_label_confidence(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    merged = runner._merge_expert_analysis_candidates(
        base_candidates=[
            {
                "file_path": "src/main/java/com/acme/CourseCreator.java",
                "title": "聚合工厂绕过",
                "claim": "直接 new 聚合根绕过工厂方法。",
                "finding_type": "direct_defect",
                "line_start": 20,
                "confidence": "high",
            }
        ],
        extra_candidates=[
            {
                "file_path": "src/main/java/com/acme/CourseCreator.java",
                "title": "领域事件丢失",
                "claim": "直接构造后没有记录领域事件。",
                "finding_type": "direct_defect",
                "line_start": 21,
                "confidence": "medium",
            }
        ],
        max_findings=8,
    )

    assert len(merged) == 2
    assert {item["title"] for item in merged} == {"聚合工厂绕过", "领域事件丢失"}


def test_review_runner_reanchors_semantically_distinct_findings_to_different_hunks(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_multi_hunk_anchor_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/multi-anchor",
            target_ref="main",
            changed_files=["src/main/java/com/acme/BatchConsumer.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/BatchConsumer.java b/src/main/java/com/acme/BatchConsumer.java\n"
                "--- a/src/main/java/com/acme/BatchConsumer.java\n"
                "+++ b/src/main/java/com/acme/BatchConsumer.java\n"
                "@@ -18,1 +18,1 @@\n"
                "- private final Integer CHUNKS = 200;\n"
                "+ private final Integer chunksTmp = 200;\n"
                "@@ -40,1 +40,1 @@\n"
                "- String sql = \"select * from events limit :chunk\";\n"
                "+ String sql = \"select * from events\";\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请审查这个文件里的多个问题",
        metadata={
            "file_path": "src/main/java/com/acme/BatchConsumer.java",
            "line_start": 18,
            "target_hunk": {
                "hunk_header": "@@ -18,1 +18,1 @@",
                "start_line": 18,
                "end_line": 18,
                "changed_lines": [18],
                "excerpt": "+ private final Integer chunksTmp = 200;",
            },
            "target_hunks": [
                {
                    "hunk_header": "@@ -18,1 +18,1 @@",
                    "start_line": 18,
                    "end_line": 18,
                    "changed_lines": [18],
                    "excerpt": "+ private final Integer chunksTmp = 200;",
                },
                {
                    "hunk_header": "@@ -40,1 +40,1 @@",
                    "start_line": 40,
                    "end_line": 40,
                    "changed_lines": [40],
                    "excerpt": '+ String sql = "select * from events";',
                },
            ],
            "repository_context": {"routing_reason": "同文件多 hunk 综合变更"},
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                '{"findings":['
                '{"title":"常量命名退化","claim":"chunksTmp 破坏常量命名规范，可读性下降","finding_type":"design_concern","severity":"medium","line_start":18,'
                '"matched_rules":["JAVA-NAMING-001"],"violated_guidelines":["常量必须使用全大写命名"],"rule_based_reasoning":"常量命名退化。",'
                '"evidence":["CHUNKS 改为 chunksTmp"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"恢复常量命名","suggested_fix":"改回 CHUNKS","change_steps":["恢复常量名"],'
                '"suggested_code":"private static final Integer CHUNKS = 200;","confidence":0.82,"verification_needed":false,"verification_plan":""},'
                '{"title":"SQL LIMIT 被移除","claim":"查询去掉 limit 后可能导致大结果集扫描","finding_type":"direct_defect","severity":"high","line_start":18,'
                '"matched_rules":["PERF-SQL-001"],"violated_guidelines":["查询必须有分页或 limit 保护"],"rule_based_reasoning":"limit 保护消失。",'
                '"evidence":["SQL 从 limit :chunk 变成无 limit"],"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"fix_strategy":"恢复分页保护","suggested_fix":"恢复 limit :chunk","change_steps":["把 SQL 改回带 limit"],'
                '"suggested_code":"select * from events limit :chunk","confidence":0.91,"verification_needed":false,"verification_plan":""}'
                ']}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/BatchConsumer.java",
        line_start=18,
        target_hunk=command_message.metadata["target_hunk"],
        target_hunks=command_message.metadata["target_hunks"],
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="light",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = sorted(runner.finding_repo.list(review.review_id), key=lambda item: item.title)
    assert len(findings) == 2
    assert findings[0].line_start != findings[1].line_start
    assert {item.line_start for item in findings} == {18, 40}


def test_review_runner_keeps_selected_security_expert_executable(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    review.subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        changed_files=[
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        ],
        unified_diff=(
            "diff --git a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java "
            "b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "--- a/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "+++ b/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java\n"
            "@@ -60,7 +60,7 @@ public final class HibernateCriteriaConverter<T> {\n"
            '-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n'
            '+        return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));\n'
        ),
    )
    review.selected_experts = ["security_compliance"]
    runner.review_repo.save(review)

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        security = next(expert for expert in experts if expert.expert_id == "security_compliance")
        return {
            "requested_expert_ids": ["security_compliance"],
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": ["security_compliance"],
            "selected_experts": [{"expert_id": "security_compliance", "expert_name": security.name_zh, "reason": "综合 MR 保守执行"}],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    recorded_jobs: list[dict[str, object]] = []

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        recorded_jobs.extend(expert_jobs)
        return []

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", lambda state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts, partial_failure_count=0: (
            "selected security expert executed",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    assert recorded_jobs, "security_compliance 应该进入 expert_jobs，而不是被 orchestration 阶段跳过"
    assert any(job["expert"].expert_id == "security_compliance" for job in recorded_jobs)
    skipped_messages = [
        message
        for message in runner.message_repo.list(review_id)
        if message.message_type == "expert_skipped" and message.expert_id == "security_compliance"
    ]
    assert not skipped_messages


def test_thorough_review_overrides_router_skip_and_scans_all_hunks(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    review.subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/router-skip",
        target_ref="main",
        changed_files=[
            "src/main/java/demo/UserController.java",
            "src/main/java/demo/UserRepository.java",
        ],
        unified_diff=(
            "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
            "--- a/src/main/java/demo/UserController.java\n"
            "+++ b/src/main/java/demo/UserController.java\n"
            "@@ -10,6 +10,7 @@ class UserController {\n"
            '+        log.info("token={}", token);\n'
            "diff --git a/src/main/java/demo/UserRepository.java b/src/main/java/demo/UserRepository.java\n"
            "--- a/src/main/java/demo/UserRepository.java\n"
            "+++ b/src/main/java/demo/UserRepository.java\n"
            "@@ -30,6 +30,7 @@ class UserRepository {\n"
            "+        jdbc.query(sql + name);\n"
        ),
    )
    runner.review_repo.save(review)

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        security = next(expert for expert in experts if expert.expert_id == "security_compliance")
        return {
            "requested_expert_ids": [],
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": ["security_compliance"],
            "selected_experts": [
                {
                    "expert_id": "security_compliance",
                    "expert_name": security.name_zh,
                    "reason": "初始选择安全专家",
                }
            ],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "weak-router", "mode": "mock"},
        }

    def _fake_build_routing_plan(subject, experts, runtime_settings, analysis_mode="standard"):
        return {
            "security_compliance": {
                "expert_id": "security_compliance",
                "routeable": False,
                "skip_reason": "路由模型没有命中安全关键词",
                "file_path": "src/main/java/demo/UserController.java",
                "line_start": 10,
                "routing_llm": {"provider": "test", "model": "weak-router", "mode": "mock"},
            }
        }

    def _fake_build_command(subject, expert, runtime_settings, route_hint=None):
        hint = dict(route_hint or {})
        return {
            **hint,
            "summary": "深度模式覆盖路由跳过并执行全量 hunk 审查",
            "repository_context": {},
            "target_hunk": dict(hint.get("target_hunk") or {}),
            "target_hunks": [dict(item) for item in list(hint.get("target_hunks") or []) if isinstance(item, dict)],
            "related_files": [],
            "expected_checks": ["安全通用规范"],
            "disallowed_inference": [],
            "routing_reason": str(hint.get("routing_reason") or ""),
            "routing_confidence": float(hint.get("confidence") or 0.0),
        }

    recorded_jobs: list[dict[str, object]] = []

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        recorded_jobs.extend(expert_jobs)
        return []

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", _fake_build_routing_plan)
    monkeypatch.setattr(runner.main_agent_service, "build_command", _fake_build_command)
    monkeypatch.setattr(runner, "_prepare_expert_batch_knowledge_inputs", lambda **_kwargs: ([], {}))
    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", lambda state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts, partial_failure_count=0: (
            "router skip overridden",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    security_jobs = [job for job in recorded_jobs if job["expert"].expert_id == "security_compliance"]
    assert security_jobs
    assert sum(len(job.get("target_hunks") or []) for job in security_jobs) == 2
    messages = runner.message_repo.list(review_id)
    assert not [
        message
        for message in messages
        if message.message_type == "expert_skipped" and message.expert_id == "security_compliance"
    ]
    command_messages = [
        message
        for message in messages
        if message.message_type == "main_agent_command"
        and message.metadata.get("target_expert_id") == "security_compliance"
    ]
    assert command_messages
    assert command_messages[0].metadata["routing_skip_overridden"] is True


def test_review_runner_batches_same_file_candidate_hunks_into_one_job(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    review.subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/all-hunks",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
            "--- a/src/main/java/com/acme/OrderService.java\n"
            "+++ b/src/main/java/com/acme/OrderService.java\n"
            "@@ -18,1 +18,1 @@\n"
            "- repository.save(entity);\n"
            "+ repository.save(entity);\n"
            "@@ -42,1 +42,1 @@\n"
            '- log.info(\"old\");\n'
            '+ log.info(\"new\");\n'
        ),
    )
    review.selected_experts = ["correctness_business"]
    runner.review_repo.save(review)

    def _fake_select_review_experts(subject, experts, runtime_settings, requested_expert_ids=None):
        target = next(expert for expert in experts if expert.expert_id == "correctness_business")
        return {
            "requested_expert_ids": ["correctness_business"],
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": ["correctness_business"],
            "selected_experts": [{"expert_id": "correctness_business", "expert_name": target.name_zh, "reason": "用户指定"}],
            "skipped_experts": [],
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        }

    monkeypatch.setattr(runner.main_agent_service, "select_review_experts", _fake_select_review_experts)
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_routing_plan",
        lambda *_args, **_kwargs: {
            "correctness_business": {
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 18,
                "routeable": True,
                "routing_reason": "主焦点 hunk",
                "confidence": 0.8,
                "routing_llm": {"provider": "test", "model": "test", "mode": "mock"},
            }
        },
    )
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_candidate_hunks",
        lambda *_args, **_kwargs: [
            {
                "candidate_id": "src/main/java/com/acme/OrderService.java:18:1",
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 18,
                "hunk_header": "@@ -18,1 +18,1 @@",
                "excerpt": "+ repository.save(entity);",
                "repo_hits": {},
            },
            {
                "candidate_id": "src/main/java/com/acme/OrderService.java:42:2",
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 42,
                "hunk_header": "@@ -42,1 +42,1 @@",
                "excerpt": '+ log.info("new");',
                "repo_hits": {},
            },
        ],
    )
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_command",
        lambda subject, expert, runtime_settings, route_hint=None: {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": str((route_hint or {}).get("file_path") or ""),
            "line_start": int((route_hint or {}).get("line_start") or 1),
            "related_files": [],
            "target_hunk": dict((route_hint or {}).get("target_hunk") or {}),
            "target_hunks": [dict(item) for item in list((route_hint or {}).get("target_hunks") or []) if isinstance(item, dict)],
            "repository_context": {},
            "expected_checks": [],
            "disallowed_inference": [],
            "routeable": True,
            "skip_reason": "",
            "routing_reason": str((route_hint or {}).get("routing_reason") or ""),
            "routing_confidence": float((route_hint or {}).get("confidence") or 0.0),
            "summary": "请审查当前 hunk",
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        },
    )
    monkeypatch.setattr(runner.knowledge_service, "retrieve_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.knowledge_service, "screen_rules_for_expert", lambda *_args, **_kwargs: {})

    recorded_jobs: list[dict[str, object]] = []

    def _fake_execute_expert_jobs(expert_jobs, runtime_settings, analysis_mode):
        recorded_jobs.extend(expert_jobs)
        return []

    monkeypatch.setattr(runner, "_execute_expert_jobs", _fake_execute_expert_jobs)
    monkeypatch.setattr(runner.graph, "invoke", lambda state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts, partial_failure_count=0: (
            "all hunks executed",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    assert len(recorded_jobs) == 1
    assert int(recorded_jobs[0]["line_start"]) == 18
    assert [int(item["start_line"]) for item in recorded_jobs[0]["target_hunks"]] == [18, 42]


def test_review_runner_reuses_knowledge_preparation_for_same_expert_file(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/cache",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n"
            "--- a/src/main/java/com/acme/OrderService.java\n"
            "+++ b/src/main/java/com/acme/OrderService.java\n"
            "@@ -18,1 +18,1 @@\n"
            "- repository.save(entity);\n"
            "+ repository.save(entity);\n"
            "@@ -42,1 +42,1 @@\n"
            '- log.info(\"old\");\n'
            '+ log.info(\"new\");\n'
        ),
    )
    review.selected_experts = ["correctness_business"]
    runner.review_repo.save(review)

    monkeypatch.setattr(
        runner.main_agent_service,
        "build_routing_plan",
        lambda *_args, **_kwargs: {
            "correctness_business": {
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 18,
                "routeable": True,
                "routing_reason": "主焦点 hunk",
                "confidence": 0.8,
                "routing_llm": {"provider": "test", "model": "test", "mode": "mock"},
            }
        },
    )
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_candidate_hunks",
        lambda *_args, **_kwargs: [
            {
                "candidate_id": "src/main/java/com/acme/OrderService.java:18:1",
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 18,
                "hunk_header": "@@ -18,1 +18,1 @@",
                "excerpt": "+ repository.save(entity);",
                "repo_hits": {},
            },
            {
                "candidate_id": "src/main/java/com/acme/OrderService.java:42:2",
                "file_path": "src/main/java/com/acme/OrderService.java",
                "line_start": 42,
                "hunk_header": "@@ -42,1 +42,1 @@",
                "excerpt": '+ log.info("new");',
                "repo_hits": {},
            },
        ],
    )
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_command",
        lambda subject, expert, runtime_settings, route_hint=None: {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": str((route_hint or {}).get("file_path") or ""),
            "line_start": int((route_hint or {}).get("line_start") or 1),
            "related_files": [],
            "target_hunk": dict((route_hint or {}).get("target_hunk") or {}),
            "target_hunks": [dict(item) for item in list((route_hint or {}).get("target_hunks") or []) if isinstance(item, dict)],
            "repository_context": {},
            "expected_checks": [],
            "disallowed_inference": [],
            "routeable": True,
            "skip_reason": "",
            "routing_reason": str((route_hint or {}).get("routing_reason") or ""),
            "routing_confidence": float((route_hint or {}).get("confidence") or 0.0),
            "summary": "请审查当前文件的全部 hunk",
            "llm": {"provider": "test", "model": "test", "mode": "mock"},
        },
    )

    retrieve_calls = {"count": 0}
    screening_calls = {"count": 0}

    def _fake_retrieve(*_args, **_kwargs):
        retrieve_calls["count"] += 1
        return []

    def _fake_screen(*_args, **_kwargs):
        screening_calls["count"] += 1
        return {}

    monkeypatch.setattr(runner.knowledge_service, "retrieve_for_expert", _fake_retrieve)
    monkeypatch.setattr(runner.knowledge_service, "screen_rules_for_expert", _fake_screen)
    monkeypatch.setattr(runner, "_execute_expert_jobs", lambda expert_jobs, *_args, **_kwargs: [])
    monkeypatch.setattr(runner.graph, "invoke", lambda state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts, partial_failure_count=0: (
            "knowledge cached",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    assert retrieve_calls["count"] == 1
    assert screening_calls["count"] == 1


def test_review_runner_expert_messages_include_rule_screening_metadata(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能与可靠性专家",
        role="performance",
        enabled=True,
        focus_areas=["连接池与容量规划"],
        system_prompt="你是性能专家",
    )
    review = ReviewTask(
        review_id="rev_rule_screening_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/perf",
            target_ref="main",
            title="连接池扩容",
            changed_files=["src/main/java/com/acme/HikariConfig.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/acme/HikariConfig.java b/src/main/java/com/acme/HikariConfig.java\n"
                "--- a/src/main/java/com/acme/HikariConfig.java\n"
                "+++ b/src/main/java/com/acme/HikariConfig.java\n"
                "@@ -10,1 +10,1 @@\n"
                "- config.setMaximumPoolSize(16);\n"
                "+ config.setMaximumPoolSize(256);\n"
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    runner.knowledge_service.create_document(
        {
            "title": "性能规则",
            "expert_id": expert.expert_id,
            "doc_type": "review_rule",
            "source_filename": "perf-rules.md",
            "content": (
                "## RULE: PERF-POOL-001 连接池扩容必须配套容量评估\n\n"
                "### 一级场景\n数据库访问\n\n"
                "### 二级场景\n连接池配置\n\n"
                "### 三级场景\n连接池扩容缺少容量评估\n\n"
                "### 描述\n检查连接池扩容是否同步评估下游容量。\n\n"
                "### 问题代码示例\n```java\nconfig.setMaximumPoolSize(256);\n```\n\n"
                "### 问题代码行\nconfig.setMaximumPoolSize(256);\n\n"
                "### 误报代码\n```java\nconfig.setMaximumPoolSize(32);\n```\n\n"
                "### 语言\njava\n\n"
                "### 问题级别\nP1\n"
            ),
        }
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请重点检查连接池扩容风险",
        metadata={
            "file_path": "src/main/java/com/acme/HikariConfig.java",
            "line_start": 10,
            "related_files": [],
            "business_changed_files": ["src/main/java/com/acme/HikariConfig.java"],
            "target_hunk": {
                "hunk_header": "@@ -10,1 +10,1 @@",
                "excerpt": "- config.setMaximumPoolSize(16);\n+ config.setMaximumPoolSize(256);",
            },
            "repository_context": {"routing_reason": "连接池参数变更"},
            "expected_checks": ["连接池容量评估"],
            "disallowed_inference": [],
        },
    )
    knowledge_context = runner._build_knowledge_review_context(
        review.subject,
        expert,
        "src/main/java/com/acme/HikariConfig.java",
        10,
        {"routing_reason": "连接池参数变更"},
        {"excerpt": "+ config.setMaximumPoolSize(256);"},
    )
    bound_documents = runner.knowledge_service.retrieve_for_expert(expert.expert_id, knowledge_context)
    rule_screening = runner.knowledge_service.screen_rules_for_expert(expert.expert_id, knowledge_context)

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                '{"ack":"收到","title":"连接池扩容缺少容量评估","finding_type":"risk_hypothesis",'
                '"claim":"连接池上限显著扩大，但当前 diff 未给出容量评估依据。","severity":"high",'
                '"line_start":10,"line_end":10,"matched_rules":["PERF-POOL-001"],'
                '"violated_guidelines":["连接池扩容必须配套容量评估"],'
                '"rule_based_reasoning":"规则要求扩容时同步说明容量依据。",'
                '"evidence":["maximumPoolSize 从 16 调整为 256","当前 diff 未看到容量评估说明"],'
                '"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"why_it_matters":"可能压垮数据库连接上限","fix_strategy":"补齐容量评估并渐进扩容",'
                '"suggested_fix":"补齐容量评估说明","change_steps":["补评估","分阶段扩容"],'
                '"suggested_code":"config.setMaximumPoolSize(32);","confidence":0.92,'
                '"verification_needed":true,"verification_plan":"核对数据库 max_connections"}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/com/acme/HikariConfig.java",
        line_start=10,
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=bound_documents,
        knowledge_context=knowledge_context,
        rule_screening=rule_screening,
        finding_payloads=[],
    )

    messages = runner.message_repo.list(review.review_id)
    ack = next(item for item in messages if item.message_type == "expert_ack")
    analysis = next(item for item in messages if item.message_type == "expert_analysis")

    assert ack.metadata["rule_screening"]["total_rules"] >= 1
    assert ack.metadata["rule_screening"]["matched_rule_count"] >= 0
    matched_rules = ack.metadata["rule_screening"]["matched_rules_for_llm"]
    batch_messages = [item for item in messages if item.message_type == "expert_rule_screening_batch"]
    batch_input_rules = [
        str(item.get("rule_id") or "")
        for message in batch_messages
        for item in list((message.metadata.get("rule_screening_batch") or {}).get("input_rules", []) or [])
    ]
    assert (matched_rules and matched_rules[0]["rule_id"] == "PERF-POOL-001") or "PERF-POOL-001" in batch_input_rules
    assert analysis.metadata["rule_screening"]["total_rules"] >= 1


def test_review_runner_database_expert_messages_include_pg_schema_context(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库分析专家",
        role="database",
        enabled=True,
        focus_areas=["SQL 与查询计划", "索引与性能"],
        system_prompt="你是数据库专家",
    )
    review = ReviewTask(
        review_id="rev_pg_schema_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            repo_url="https://github.com/example/repo.git",
            source_ref="feature/db",
            target_ref="main",
            title="orders 表新增 status 列",
            changed_files=["db/migration/V1__orders.sql"],
            unified_diff=(
                'diff --git a/db/migration/V1__orders.sql b/db/migration/V1__orders.sql\n'
                '--- a/db/migration/V1__orders.sql\n'
                '+++ b/db/migration/V1__orders.sql\n'
                '@@ -1,1 +1,1 @@\n'
                '-ALTER TABLE "orders" ADD COLUMN "legacy" text;\n'
                '+ALTER TABLE "orders" ADD COLUMN "status" varchar(32);\n'
            ),
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请重点检查 orders 表新增 status 列的兼容性与索引风险",
        metadata={
            "file_path": "db/migration/V1__orders.sql",
            "line_start": 1,
            "related_files": [],
            "business_changed_files": ["db/migration/V1__orders.sql"],
            "target_hunk": {
                "hunk_header": "@@ -1,1 +1,1 @@",
                "excerpt": '-ALTER TABLE "orders" ADD COLUMN "legacy" text;\n+ALTER TABLE "orders" ADD COLUMN "status" varchar(32);',
            },
            "repository_context": {"routing_reason": "表结构变更涉及数据库兼容性"},
            "expected_checks": ["新增列是否需要默认值、索引和回填策略"],
            "disallowed_inference": [],
        },
    )

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.review_tool_gateway,
        "invoke_for_expert",
        lambda *_args, **_kwargs: [
            {
                "tool_name": "pg_schema_context",
                "summary": "已从 PostgreSQL 数据源拉取 1 张表的结构与统计元信息。",
                "matched": True,
                "data_source_summary": {
                    "repo_url": "https://github.com/example/repo.git",
                    "provider": "postgres",
                    "host": "127.0.0.1",
                    "port": 5432,
                    "database": "review_db",
                    "user": "readonly",
                    "schema_allowlist": ["public"],
                    "ssl_mode": "prefer",
                },
                "matched_tables": ["orders"],
                "table_columns": [
                    {
                        "table_name": "orders",
                        "column_name": "status",
                        "data_type": "character varying",
                        "is_nullable": "YES",
                    }
                ],
                "constraints": [{"table_name": "orders", "constraint_type": "PRIMARY KEY", "columns": "id"}],
                "indexes": [{"table_name": "orders", "indexname": "idx_orders_created_at"}],
                "table_stats": [{"table_name": "orders", "estimated_rows": 1800000, "total_size": "512 MB"}],
            }
        ],
    )
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                '{"ack":"收到","title":"orders.status 新增列缺少默认值与回填策略","finding_type":"risk_hypothesis",'
                '"claim":"大表新增可空列且未说明回填策略，可能影响历史数据读取与查询条件稳定性。","severity":"high",'
                '"line_start":1,"line_end":1,"matched_rules":[],"violated_guidelines":[],'
                '"rule_based_reasoning":"结合表规模、现有主键与索引信息判断新增列需要明确回填与索引策略。",'
                '"evidence":["orders 预计行数较大","当前只看到新增 status 列，未看到默认值与索引联动"],'
                '"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"why_it_matters":"可能导致查询语义不稳定或上线回填成本过高","fix_strategy":"补齐默认值、回填脚本和索引评估",'
                '"suggested_fix":"明确 status 默认值并评估是否补索引","change_steps":["补默认值说明","制定回填计划"],'
                '"suggested_code":"ALTER TABLE orders ADD COLUMN status varchar(32) DEFAULT ''NEW'';","confidence":0.91,'
                '"verification_needed":true,"verification_plan":"核对历史查询条件与回填窗口"}'
            ),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )

    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="db/migration/V1__orders.sql",
        line_start=1,
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=[],
    )

    messages = runner.message_repo.list(review.review_id)
    tool_message = next(
        item for item in messages if item.message_type == "expert_tool_call" and item.metadata.get("tool_name") == "pg_schema_context"
    )
    ack = next(item for item in messages if item.message_type == "expert_ack")
    analysis = next(item for item in messages if item.message_type == "expert_analysis")

    assert "PostgreSQL 数据源" in tool_message.content
    assert tool_message.metadata["tool_result"]["matched_tables"] == ["orders"]
    runtime_tool_results = ack.metadata.get("runtime_tool_results", [])
    assert runtime_tool_results and runtime_tool_results[0]["tool_name"] == "pg_schema_context"
    assert analysis.metadata["runtime_tool_results"][0]["data_source_summary"]["database"] == "review_db"


def test_review_runner_rule_screening_fulltext_contains_full_rule_fields(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    text = runner._build_rule_screening_fulltext(
        {
            "total_rules": 1,
            "must_review_count": 1,
            "possible_hit_count": 0,
            "matched_rules_for_llm": [
                {
                    "rule_id": "PERF-POOL-001",
                    "title": "连接池扩容必须配套容量评估",
                    "priority": "P1",
                    "scene_path": "数据库访问 / 连接池配置 / 连接池扩容缺少容量评估",
                    "description": "检查连接池扩容是否同步评估下游容量。",
                    "language": "java",
                    "problem_code_example": "config.setMaximumPoolSize(256);",
                    "problem_code_line": "config.setMaximumPoolSize(256);",
                    "false_positive_code": "config.setMaximumPoolSize(32);",
                    "matched_terms": ["maximumPoolSize"],
                }
            ],
        }
    )

    assert "场景路径: 数据库访问 / 连接池配置 / 连接池扩容缺少容量评估" in text
    assert "规则描述: 检查连接池扩容是否同步评估下游容量。" in text
    assert "问题代码示例:" in text
    assert "config.setMaximumPoolSize(256);" in text
    assert "误报代码参考:" in text


def test_review_runner_parse_expert_analysis_omits_design_status_without_design_docs(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = runner._parse_expert_analysis(
        "普通文本回复",
        ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/x",
            target_ref="main",
        ),
        ExpertProfile(
            expert_id="correctness_business",
            name="Correctness",
            name_zh="正确性",
            role="correctness",
            enabled=True,
            system_prompt="prompt",
        ),
        "apps/api/schedules/output.service.ts",
        12,
    )

    assert parsed["design_alignment_status"] == ""


def test_review_runner_merge_context_files_uses_repo_context_and_skill_hits(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    merged = runner._merge_context_files(
        ["apps/api/schedules/output.service.ts"],
        {
            "context_files": [
                "apps/api/schedules/output.service.ts",
                "packages/lib/schedules/getScheduleListItemData.ts",
            ]
        },
        [
            {
                "tool_name": "repo_context_search",
                "context_files": [
                    "packages/lib/schedules/getScheduleListItemData.ts",
                    "packages/prisma/schema.prisma",
                ],
            }
        ],
    )

    assert merged == [
        "apps/api/schedules/output.service.ts",
        "packages/lib/schedules/getScheduleListItemData.ts",
        "packages/prisma/schema.prisma",
    ]


def test_review_runner_prompt_includes_related_source_snippets(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/order",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
            "--- a/src/main/java/com/example/OrderService.java\n"
            "+++ b/src/main/java/com/example/OrderService.java\n"
            "@@ -3,1 +3,1 @@\n"
            "-        audit(id);\n"
            "+        audit(id.trim());\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能",
        role="performance",
        enabled=True,
        focus_areas=["性能热点"],
        system_prompt="prompt",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/example/OrderService.java",
        3,
        tool_evidence=[],
        runtime_tool_results=[
            {
                "tool_name": "repo_context_search",
                "summary": "已按 1 个方法/类关键词检索目标分支代码仓，命中 1 个定义文件、1 个引用文件。",
                "related_source_snippets": [
                    {
                        "path": "src/main/java/com/example/OrderConsumer.java",
                        "symbol": "processOrder",
                        "kind": "reference",
                        "line_start": 5,
                        "snippet": "   4 | public void consume(String id) {\n   5 |     orderService.processOrder(id);\n   6 | }",
                    }
                ],
            }
        ],
        repository_context={
            "summary": "目标分支中存在 OrderConsumer 对 processOrder 的调用。",
            "routing_reason": "需要确认跨文件调用链上的性能影响",
        },
        target_hunk={"hunk_header": "@@ -3,1 +3,1 @@", "excerpt": "+        audit(id.trim());"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["证据不足时不要假定调用方一定安全"],
        expected_checks=["检查跨文件调用链上的性能与异常处理风险"],
        active_skills=[],
    )

    assert "关联源码片段" in prompt
    assert "src/main/java/com/example/OrderConsumer.java" in prompt
    assert "orderService.processOrder(id);" in prompt


def test_review_runner_prompt_includes_pg_schema_context(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/db",
        target_ref="main",
        changed_files=["db/migration/V1__orders.sql"],
        unified_diff='ALTER TABLE "orders" ADD COLUMN "status" varchar(32);',
    )
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库",
        role="database",
        enabled=True,
        focus_areas=["schema 变更", "索引与统计信息"],
        system_prompt="prompt",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "db/migration/V1__orders.sql",
        1,
        tool_evidence=[],
        runtime_tool_results=[
            {
                "tool_name": "pg_schema_context",
                "summary": "已从 PostgreSQL 数据源拉取 1 张表的结构与统计元信息。",
                "data_source_summary": {
                    "database": "review_db",
                    "host": "127.0.0.1",
                    "schema_allowlist": ["public"],
                },
                "matched_tables": ["orders"],
                "table_columns": [
                    {
                        "table_name": "orders",
                        "column_name": "status",
                        "data_type": "character varying",
                        "is_nullable": "YES",
                    }
                ],
                "constraints": [{"table_name": "orders", "constraint_type": "PRIMARY KEY", "columns": "id"}],
                "indexes": [{"table_name": "orders", "indexname": "idx_orders_status"}],
                "table_stats": [{"table_name": "orders", "estimated_rows": 1024, "total_size": "128 kB"}],
            }
        ],
        repository_context={},
        target_hunk={"hunk_header": "@@ -1,1 +1,1 @@", "excerpt": '+ALTER TABLE "orders" ADD COLUMN "status" varchar(32);'},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["检查新增列是否需要索引、默认值与约束联动"],
        active_skills=[],
    )

    assert "review_db @ 127.0.0.1" in prompt
    assert "命中表: orders" in prompt
    assert "orders.status(character varying / nullable=YES)" in prompt
    assert "orders:PRIMARY KEY(id)" in prompt


def test_review_runner_downgrades_import_only_dependency_guess(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "新增 UsersRepository 依赖但未在构造器中注入",
            "claim": "当前 diff 未显示 constructor 注入，若后续使用会导致依赖缺失。",
            "finding_type": "direct_defect",
            "severity": "high",
            "confidence": 0.8,
            "evidence": [
                "diff 只显示新增 import UsersRepository",
                "当前片段未显示 constructor 注入",
            ],
            "assumptions": [],
            "context_files": [],
            "verification_needed": False,
        },
        "maintainability_code_health",
        "apps/api/schedules/output.service.ts",
        4,
        {
            "excerpt": (
                "   2 | import { Schedule } from './types'\n"
                "   3 | +import { Injectable } from '@nestjs/common';\n"
                "   4 | +import { UsersRepository } from '@/modules/users/users.repository';\n"
            )
        },
    )

    assert stabilized["finding_type"] == "risk_hypothesis"
    assert stabilized["verification_needed"] is True
    assert stabilized["severity"] == "medium"
    assert float(stabilized["confidence"]) <= 0.45
    assert any("系统尚需补齐完整类定义与 constructor 信息" in item for item in stabilized["assumptions"])


def test_review_runner_downgrades_speculative_high_severity_claim(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "构造函数参数未同步修改，可能导致编译失败",
            "claim": "当前 diff 未显示完整构造函数，若参数未同步则会导致注入失败。",
            "finding_type": "direct_defect",
            "severity": "blocker",
            "confidence": 0.92,
            "evidence": [
                "字段声明已经改为 EventBus",
                "当前 diff 未看到构造函数完整实现",
            ],
            "assumptions": [],
            "context_files": [],
            "verification_needed": False,
        },
        "architecture_design",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        20,
        {
            "excerpt": (
                "  19 | public class MySqlDomainEventsConsumer {\n"
                "  20 | +    private final EventBus bus;\n"
                "  21 | +    private final Integer CHUNKS = 200;\n"
            )
        },
    )

    assert stabilized["finding_type"] == "risk_hypothesis"
    assert stabilized["verification_needed"] is True
    assert stabilized["direct_evidence"] is False
    assert stabilized["severity"] == "medium"
    assert float(stabilized["confidence"]) <= 0.4
    assert any("系统需要补齐完整方法/类定义" in item for item in stabilized["assumptions"])
    assert "回看完整 diff" not in str(stabilized["verification_plan"])
    assert "系统将补齐完整 diff" in str(stabilized["verification_plan"])


def test_review_runner_keeps_lock_risk_as_high_value_verifiable_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "批量回填事务范围过大可能放大锁竞争",
            "claim": "当前脚本如果在一次事务内回填 orders 并同步更新状态，可能导致锁持有时间过长。",
            "finding_type": "direct_defect",
            "severity": "high",
            "confidence": 0.86,
            "evidence": [
                "回填 SQL 未见分批提交",
                "同一事务内更新 orders 与 order_items",
            ],
            "cross_file_evidence": ["迁移脚本与 repository 调用链都指向批量更新"],
            "assumptions": [],
            "context_files": ["sql/migration/V42__backfill_orders.sql"],
            "verification_needed": False,
        },
        "performance_reliability",
        "sql/migration/V42__backfill_orders.sql",
        12,
        {
            "excerpt": (
                "12 | BEGIN;\n"
                "13 | UPDATE orders SET status = 'DONE' WHERE status = 'PENDING';\n"
                "14 | UPDATE order_items SET status = 'DONE' WHERE status = 'PENDING';\n"
                "15 | COMMIT;\n"
            )
        },
    )

    assert stabilized["finding_type"] == "risk_hypothesis"
    assert stabilized["verification_needed"] is True
    assert stabilized["severity"] == "high"
    assert float(stabilized["confidence"]) >= 0.75
    assert any("锁" in item or "事务" in item for item in stabilized["evidence"])


def test_review_runner_merge_context_files_filters_noise(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    merged = runner._merge_context_files(
        ["apps/api/schedules/output.service.ts", ".git/index", "yarn.lock"],
        {
            "context_files": [
                "packages/lib/schedules/getScheduleListItemData.ts",
                ".git/index",
            ]
        },
        [
            {
                "tool_name": "repo_context_search",
                "context_files": [
                    "packages/prisma/schema.prisma",
                    "node_modules/pkg/index.js",
                ],
            }
        ],
    )

    assert merged == [
        "apps/api/schedules/output.service.ts",
        "packages/lib/schedules/getScheduleListItemData.ts",
        "packages/prisma/schema.prisma",
    ]


def test_review_runner_downgrades_weak_performance_signal(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "DTO 可能需要继续观察",
            "claim": "当前 diff 只看到了 DTO 字段变化，但还没有明确的链路级证据。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.7,
            "evidence": ["ApiPropertyOptional 导入发生变化"],
            "cross_file_evidence": [],
            "assumptions": ["需要继续确认调用场景"],
            "context_files": ["packages/platform/types/schedules/output.ts"],
            "verification_needed": True,
        },
        "performance_reliability",
        "packages/platform/types/schedules/output.ts",
        1,
        {"excerpt": "1 | +import { ApiPropertyOptional } from '@nestjs/swagger'"},
    )

    assert stabilized["finding_type"] == "design_concern"
    assert stabilized["severity"] == "low"
    assert float(stabilized["confidence"]) <= 0.35


def test_review_runner_downgrades_finding_when_required_inputs_are_missing(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "控制器输入校验缺失",
            "claim": "当前入口缺少必要校验，会直接放过非法请求。",
            "finding_type": "direct_defect",
            "severity": "high",
            "confidence": 0.88,
            "evidence": ["diff 中删除了校验调用"],
            "assumptions": [],
            "context_files": [],
            "verification_needed": False,
        },
        "security_compliance",
        "src/main/java/com/example/UserController.java",
        18,
        {"excerpt": "18 | - validate(request)\n19 | + create(request)"},
        input_completeness={
            "missing_sections": ["语言通用规范提示", "关联源码上下文"],
        },
    )

    assert stabilized["finding_type"] == "risk_hypothesis"
    assert stabilized["verification_needed"] is True
    assert stabilized["direct_evidence"] is False
    assert stabilized["severity"] == "medium"
    assert float(stabilized["confidence"]) <= 0.35
    assert any("语言通用规范提示" in item for item in stabilized["assumptions"])
    assert "系统先补齐 语言通用规范提示 / 关联源码上下文" in str(stabilized["verification_plan"])


def test_review_runner_rewrites_user_confirmation_language(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "缓存失效策略存在遗漏",
            "claim": "建议用户先查看完整调用链再确认是否存在缓存穿透风险。",
            "finding_type": "design_concern",
            "severity": "medium",
            "confidence": 0.71,
            "evidence": ["本次改动移除了缓存键前缀"],
            "assumptions": ["需要人工确认是否有兜底缓存策略"],
            "change_steps": ["请用户核查 Redis 配置与应用参数是否一致"],
            "verification_plan": "请用户确认上下游接口实现后再决定是否修复",
            "verification_needed": True,
        },
        "performance_reliability",
        "src/main/java/com/example/CacheService.java",
        42,
        {"excerpt": "42 | -cache.put(prefix + id, value)\n43 | +cache.put(id, value)"},
    )

    blob = "\n".join(
        [
            str(stabilized.get("claim") or ""),
            str(stabilized.get("verification_plan") or ""),
            *[str(item) for item in list(stabilized.get("assumptions") or [])],
            *[str(item) for item in list(stabilized.get("change_steps") or [])],
        ]
    )
    assert "用户" not in blob
    assert "人工" not in blob
    assert "系统将自动补齐关联上下文并复核" in str(stabilized.get("verification_plan") or "")


def test_review_runner_builds_fallback_finding_when_expert_fails_with_matched_rules(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_test",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/ddd",
            target_ref="main",
            changed_files=["src/main/java/com/example/CourseCreator.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/example/CourseCreator.java b/src/main/java/com/example/CourseCreator.java\n"
                "--- a/src/main/java/com/example/CourseCreator.java\n"
                "+++ b/src/main/java/com/example/CourseCreator.java\n"
                "@@ -18,1 +18,1 @@\n"
                "-        Course course = Course.create(id, name, duration);\n"
                "+        Course course = new Course(id, name, duration);\n"
            ),
        ),
        selected_experts=["ddd_architecture"],
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD Architecture",
        name_zh="DDD架构专家",
        role="ddd architecture",
        enabled=True,
        focus_areas=["聚合边界"],
        system_prompt="prompt",
        review_spec="聚合必须在聚合根内守护不变量",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="command",
        metadata={
            "repository_context": {
                "primary_context": {
                    "path": "src/main/java/com/example/CourseCreator.java",
                    "snippet": "18 | Course course = new Course(id, name, duration);",
                },
                "context_files": ["src/main/java/com/example/CourseCreator.java"],
            },
            "target_hunk": {
                "file_path": "src/main/java/com/example/CourseCreator.java",
                "hunk_header": "@@ -18,1 +18,1 @@",
                "start_line": 18,
                "end_line": 18,
                "changed_lines": [18],
                "excerpt": "+        Course course = new Course(id, name, duration);",
            },
        },
    )

    finding = runner._build_failed_expert_fallback_finding(
        {
            "review": review,
            "expert": expert,
            "command_message": command_message,
            "file_path": "src/main/java/com/example/CourseCreator.java",
            "line_start": 18,
            "bound_documents": [],
            "rule_screening": {
                "enabled_rules": 2,
                "matched_rules_for_llm": [
                    {
                        "rule_id": "DDD-JDDD-001",
                        "title": "Aggregate 必须在聚合根内守护不变量，禁止外部裸改状态",
                        "priority": "P1",
                        "decision": "must_review",
                        "reason": "直接 new Course 可能绕过工厂和领域事件录制。",
                    }
                ],
                "must_review_count": 1,
                "possible_hit_count": 0,
            },
        },
        "request_timeout:The read operation timed out",
    )

    assert finding is not None
    assert finding.finding_type == "risk_hypothesis"
    assert finding.verification_needed is True
    assert "DDD-JDDD-001" in finding.matched_rules
    assert finding.confidence >= 0.28
    assert "专家执行失败" in finding.evidence[0]


def test_review_runner_uses_forced_ddd_observation_when_expert_fails(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_forced_ddd_fallback",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/ddd",
            target_ref="main",
            changed_files=["src/main/java/com/example/CourseCreator.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/example/CourseCreator.java b/src/main/java/com/example/CourseCreator.java\n"
                "--- a/src/main/java/com/example/CourseCreator.java\n"
                "+++ b/src/main/java/com/example/CourseCreator.java\n"
                "@@ -18,1 +18,1 @@\n"
                "-        Course course = Course.create(id, name, duration);\n"
                "+        Course course = new Course(id, name, duration);\n"
            ),
        ),
        selected_experts=["ddd_architecture"],
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD Architecture",
        name_zh="DDD架构专家",
        role="ddd architecture",
        enabled=True,
        focus_areas=["聚合边界"],
        system_prompt="prompt",
        review_spec="聚合必须在聚合根内守护不变量",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="command",
        metadata={
            "repository_context": {
                "review_observations": [
                    {
                        "observation_id": "obs_factory_001",
                        "kind": "construction_path_changed",
                        "file_path": "src/main/java/com/example/CourseCreator.java",
                        "line_start": 18,
                        "summary": "Course.create() 被替换为 new Course()",
                        "evidence": [
                            "- Course course = Course.create(id, name, duration);",
                            "+ Course course = new Course(id, name, duration);",
                            "eventBus.publish(course.pullDomainEvents())",
                        ],
                        "related_symbols": ["Course.create", "new Course"],
                        "confidence": 0.73,
                    }
                ],
                "primary_context": {
                    "path": "src/main/java/com/example/CourseCreator.java",
                    "snippet": "18 | Course course = new Course(id, name, duration);",
                },
            },
            "target_hunk": {
                "file_path": "src/main/java/com/example/CourseCreator.java",
                "hunk_header": "@@ -18,1 +18,1 @@",
                "start_line": 18,
                "end_line": 18,
                "changed_lines": [18],
                "excerpt": (
                    "-        Course course = Course.create(id, name, duration);\n"
                    "+        Course course = new Course(id, name, duration);"
                ),
            },
        },
    )

    finding = runner._build_failed_expert_fallback_finding(
        {
            "review": review,
            "expert": expert,
            "command_message": command_message,
            "file_path": "src/main/java/com/example/CourseCreator.java",
            "line_start": 18,
            "bound_documents": [],
            "rule_screening": {
                "enabled_rules": 2,
                "matched_rules_for_llm": [
                    {
                        "rule_id": "DDD-JDDD-001",
                        "title": "Aggregate 必须在聚合根内守护不变量，禁止外部裸改状态",
                        "priority": "P1",
                        "decision": "must_review",
                        "reason": "直接 new Course 可能绕过工厂和领域事件录制。",
                    }
                ],
                "must_review_count": 1,
                "possible_hit_count": 0,
            },
        },
        "request_timeout:The read operation timed out",
    )

    assert finding is not None
    assert finding.finding_type == "risk_hypothesis"
    assert finding.severity == "high"
    assert finding.verification_needed is True
    assert finding.confidence <= 0.78
    assert finding.assumptions == []
    assert "创建路径变更风险" in finding.title
    assert "DDD-JDDD-001" in finding.matched_rules
    assert (finding.code_context or {}).get("observation_ids") == ["obs_factory_001"]
    assert (finding.code_context or {}).get("direct_evidence") is False


def test_review_runner_builds_signal_aware_fallback_finding_when_expert_fails(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_demo",
        status="running",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_demo",
            project_id="proj_demo",
            source_ref="feature/query-risk",
            target_ref="main",
            title="query semantics regression",
            changed_files=["src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java"],
            unified_diff="",
        ),
        selected_experts=["database_analysis"],
    )
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库分析专家",
        role="database",
        enabled=True,
        focus_areas=["SQL"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="command",
        metadata={
            "repository_context": {},
            "target_hunk": {
                "file_path": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
                "hunk_header": "@@ -60,1 +60,1 @@",
                "start_line": 60,
                "end_line": 63,
                "changed_lines": [62],
                "excerpt": (
                    "-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n"
                    "+        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
                ),
            },
        },
    )

    finding = runner._build_failed_expert_fallback_finding(
        {
            "review": review,
            "expert": expert,
            "command_message": command_message,
            "file_path": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
            "line_start": 62,
            "bound_documents": [],
            "rule_screening": {
                "enabled_rules": 2,
                "matched_rules_for_llm": [
                    {
                        "rule_id": "PERF-SQL-001",
                        "title": "大结果集查询必须显式分页或限流",
                        "priority": "P1",
                        "decision": "must_review",
                        "reason": "精确匹配已放宽为模糊查询，存在查询语义退化风险。",
                    }
                ],
                "must_review_count": 1,
                "possible_hit_count": 0,
            },
        },
        "request_timeout:The read operation timed out",
    )

    assert finding is not None
    assert "查询语义" in finding.title
    assert "精确匹配" in finding.summary
    assert any("equal" in item.lower() and "like" in item.lower() for item in finding.evidence)


def test_review_runner_persists_deterministic_fallback_when_rule_guided_llm_times_out(
    storage_root: Path,
    monkeypatch,
):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_timeout_fallback",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_demo",
            project_id="proj_demo",
            source_ref="feature/swallow",
            target_ref="main",
            title="swallowed reflection exception",
            changed_files=["src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"],
            unified_diff=(
                "diff --git a/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java "
                "b/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
                "--- a/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
                "+++ b/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java\n"
                "@@ -29,7 +29,6 @@ public final class MySqlDomainEventsConsumer {\n"
                "         } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {\n"
                "-            e.printStackTrace();\n"
                "         }\n"
            ),
        ),
        selected_experts=["performance_reliability"],
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能与可靠性专家",
        role="performance",
        enabled=True,
        focus_areas=["可靠性"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
        tool_bindings=[],
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="command",
        metadata={},
    )

    def _timeout_on_main_review(_self, **kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        if phase == "expert_review":
            raise TimeoutError("request_timeout:The read operation timed out")
        return LLMTextResult(
            text=(
                '{"rule_check_results":[{"rule_id":"REL-JDDD-001","status":"violated",'
                '"evidence":["catch 删除 printStackTrace"],"missing_context":[],"reason":"异常被吞掉"}],'
                '"candidate_findings":[],"context_requests":[],'
                '"self_check":{"checked_all_rules":true,"used_context_files":[],"unverified_assumptions":[]}}'
            ),
            mode="live",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.llm_chat_service.LLMChatService.complete_text",
        _timeout_on_main_review,
    )
    finding_payloads: list[dict[str, object]] = []

    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        line_start=29,
        repository_context={},
        target_hunk={
            "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
            "hunk_header": "@@ -29,7 +29,6 @@ public final class MySqlDomainEventsConsumer {",
            "start_line": 29,
            "end_line": 31,
            "changed_lines": [30],
            "excerpt": (
                "29 |         } catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException e) {\n"
                "-30 |             e.printStackTrace();\n"
                "31 |         }"
            ),
        },
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="light",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={
            "enabled_rules": 1,
            "matched_rules_for_llm": [
                {
                    "rule_id": "REL-JDDD-001",
                    "title": "关键链路 fallback 不得掩盖真实异常",
                    "priority": "P1",
                    "decision": "must_review",
                    "reason": "catch 删除唯一异常处理语句，反射异常会被静默吞掉。",
                }
            ],
            "must_review_count": 1,
            "possible_hit_count": 0,
        },
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    messages = runner.message_repo.list(review.review_id)
    assert len(findings) == 1
    assert findings[0].finding_type == "direct_defect"
    assert findings[0].normalized_issue_type == "exception_swallowed"
    assert findings[0].verification_needed is False
    assert "REL-JDDD-001" in findings[0].matched_rules
    assert any("NoSuchMethodException" in item for item in findings[0].evidence)
    assert len(finding_payloads) == 1
    assert any(
        message.message_type
        in {"expert_timeout_deterministic_fallback", "expert_rule_prepass_deterministic_finding"}
        for message in messages
    )


def test_review_runner_enriches_ddd_finding_with_canonical_terms(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "Course创建绕过聚合工厂方法，破坏不变量守护与领域事件生成",
            "claim": "将 Course.create() 工厂方法改为 new Course() 直接构造，绕过了聚合根内部的不变量校验。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.4,
            "line_start": 18,
            "line_end": 18,
            "matched_rules": ["DDD-JDDD-001"],
            "violated_guidelines": [],
            "evidence": [],
            "assumptions": [],
            "context_files": [],
            "verification_needed": True,
        },
        "ddd_architecture",
        "src/main/java/com/example/CourseCreator.java",
        18,
        {
            "hunk_header": "@@ -18,1 +18,1 @@",
            "start_line": 18,
            "end_line": 18,
            "changed_lines": [18],
            "excerpt": "+        Course course = new Course(id, name, duration);",
        },
    )

    assert "创建路径变更风险" in str(stabilized["title"])
    claim = str(stabilized["claim"]).lower()
    assert "course.create" in claim
    assert "aggregate" in claim
    assert "factory" in claim
    assert "domain event" in claim


def test_review_runner_enriches_java_quality_signal_language(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "查询与消费逻辑存在退化风险",
            "claim": "当前改动可能导致查询与消费行为退化。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.4,
            "line_start": 40,
            "line_end": 40,
            "matched_rules": ["PERF-SQL-001"],
            "violated_guidelines": [],
            "evidence": [],
            "assumptions": [],
            "context_files": [],
            "verification_needed": True,
        },
        "database_analysis",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        40,
        {
            "hunk_header": "@@ -20,7 +20,7 @@",
            "start_line": 20,
            "end_line": 60,
            "changed_lines": [23, 40, 56],
            "excerpt": (
                "-\tprivate final Integer CHUNKS = 200;\n"
                "+\tprivate final Integer chunksTmp = 200;\n"
                "-\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk\"\n"
                "+\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on ASC\"\n"
                "-\t\t\t\te.printStackTrace();\n"
            ),
        },
        repository_context={},
    )

    assert "CHUNKS -> chunksTmp" in str(stabilized["claim"])
    assert "静默吞掉异常" in str(stabilized["claim"])


def test_review_runner_enriches_query_semantics_signal_language(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "共享查询语义退化",
            "claim": "当前改动会扩大查询结果范围。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.4,
            "line_start": 62,
            "line_end": 62,
            "matched_rules": ["PERF-SQL-001"],
            "violated_guidelines": [],
            "evidence": [],
            "assumptions": [],
            "context_files": [],
            "verification_needed": True,
        },
        "database_analysis",
        "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        62,
        {
            "hunk_header": "@@ -60,1 +60,1 @@",
            "start_line": 60,
            "end_line": 63,
            "changed_lines": [62],
            "excerpt": (
                "-        return builder.equal(root.get(filter.field().value()), filter.value().value());\n"
                "+        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));\n"
            ),
        },
        repository_context={},
    )

    assert "equal 精确匹配放宽成 like/contains 模糊匹配" in str(stabilized["claim"])
    assert "查询语义" in str(stabilized["title"])
    assert "精确匹配" in str(stabilized["summary"])
    assert any("equal" in str(item).lower() and "like" in str(item).lower() for item in list(stabilized["evidence"]))


def test_review_runner_enriches_naming_and_exception_signals_into_summary(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "事件消费逻辑存在退化风险",
            "summary": "当前改动会让消费逻辑更难维护。",
            "claim": "当前改动可能导致事件消费行为退化。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.4,
            "line_start": 40,
            "line_end": 40,
            "matched_rules": ["PERF-SQL-001"],
            "violated_guidelines": [],
            "evidence": [],
            "assumptions": [],
            "context_files": [],
            "verification_needed": True,
        },
        "correctness_business",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        40,
        {
            "hunk_header": "@@ -20,7 +20,7 @@",
            "start_line": 20,
            "end_line": 60,
            "changed_lines": [23, 40, 56],
            "excerpt": (
                "-\tprivate final Integer CHUNKS = 200;\n"
                "+\tprivate final Integer chunksTmp = 200;\n"
                "-\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk\"\n"
                "+\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on ASC\"\n"
                "-\t\t\t\te.printStackTrace();\n"
            ),
        },
        repository_context={},
    )

    assert "命名规范" in str(stabilized["title"])
    assert "CHUNKS" in str(stabilized["summary"])
    assert "chunksTmp" in str(stabilized["summary"])
    assert "静默吞掉异常" in str(stabilized["summary"])


def test_review_runner_stabilize_expert_analysis_reanchors_line_start_to_target_hunk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "创建订单前缺少币种校验",
            "claim": "当前 createOrder 在写入前没有完成必要的业务校验。",
            "finding_type": "direct_defect",
            "severity": "high",
            "confidence": 0.86,
            "line_start": 1,
            "line_end": 1,
            "evidence": ["validateCurrency 调用缺失"],
            "assumptions": [],
            "context_files": [],
            "verification_needed": False,
        },
        "correctness_business",
        "apps/api/order/order.service.ts",
        22,
        {
            "hunk_header": "@@ -20,2 +22,4 @@",
            "start_line": 22,
            "end_line": 24,
            "changed_lines": [22, 23],
            "excerpt": (
                "  22 | +  const payload = { amount, currency };\n"
                "  23 | +  return client.post('/api/orders', payload);\n"
            ),
        },
    )

    assert stabilized["line_start"] == 22
    assert stabilized["line_end"] >= 22


def test_review_runner_refines_same_hunk_findings_to_semantic_changed_lines(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "hunk_header": "@@ -20,7 +20,7 @@",
        "start_line": 20,
        "end_line": 60,
        "changed_lines": [23, 40, 56],
        "excerpt": (
            "-\tprivate final Integer CHUNKS = 200;\n"
            "+\tprivate final Integer chunksTmp = 200;\n"
            '-\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"\n'
            '+\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC"\n'
            "-\t\t\t\te.printStackTrace();\n"
        ),
    }

    naming_line = runner._refine_line_start_within_hunk(
        {
            "title": "常量命名退化",
            "claim": "chunksTmp 破坏常量命名规范，可读性下降",
            "evidence": ["CHUNKS 改为 chunksTmp"],
        },
        target_hunk,
        20,
    )
    sql_line = runner._refine_line_start_within_hunk(
        {
            "title": "SQL LIMIT 被移除",
            "claim": "查询去掉 limit 后可能导致大结果集扫描",
            "evidence": ["SQL 从 limit :chunk 变成无 limit"],
        },
        target_hunk,
        20,
    )
    exception_line = runner._refine_line_start_within_hunk(
        {
            "title": "异常被静默吞掉",
            "claim": "catch 中的 printStackTrace 被移除后，问题会更难定位",
            "evidence": ["printStackTrace 被删除"],
        },
        target_hunk,
        20,
    )

    assert naming_line == 23
    assert sql_line == 40
    assert exception_line == 56


def test_review_runner_prefers_explicit_line_number_mentioned_in_problem_description(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stabilized = runner._stabilize_expert_analysis(
        {
            "title": "异常被静默吞掉",
            "summary": "问题出现在第56行的 catch 处理。",
            "claim": "第 56 行把 printStackTrace 删除后，异常会被静默吞掉。",
            "finding_type": "risk_hypothesis",
            "severity": "medium",
            "confidence": 0.72,
            "line_start": 20,
            "line_end": 20,
            "evidence": ["56 | } catch (...) {", "printStackTrace 被删除"],
            "assumptions": [],
            "context_files": [],
            "verification_needed": False,
        },
        "correctness_business",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        20,
        {
            "hunk_header": "@@ -20,7 +20,7 @@",
            "start_line": 20,
            "end_line": 60,
            "changed_lines": [23, 40, 56],
            "excerpt": (
                "-\tprivate final Integer CHUNKS = 200;\n"
                "+\tprivate final Integer chunksTmp = 200;\n"
                '-\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"\n'
                '+\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC"\n'
                "-\t\t\t\te.printStackTrace();\n"
            ),
        },
        repository_context={},
    )

    assert stabilized["line_start"] == 56


def test_review_runner_refine_line_start_ignores_suggested_code_semantics(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "hunk_header": "@@ -20,7 +20,7 @@",
        "start_line": 20,
        "end_line": 60,
        "changed_lines": [23, 40, 56],
        "excerpt": (
            "-\tprivate final Integer CHUNKS = 200;\n"
            "+\tprivate final Integer chunksTmp = 200;\n"
            '-\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"\n'
            '+\t\t\t\t"SELECT * FROM domain_events ORDER BY occurred_on ASC"\n'
            "-\t\t\t\te.printStackTrace();\n"
        ),
    }

    line = runner._refine_line_start_within_hunk(
        {
            "title": "命名规范退化",
            "claim": "常量命名从 CHUNKS 退化为 chunksTmp。",
            "summary": "问题在命名，不在 SQL。",
            "suggested_code": 'select * from domain_events order by occurred_on asc limit :chunk',
            "evidence": ["chunksTmp"],
        },
        target_hunk,
        20,
    )

    assert line == 23


def test_review_runner_suppresses_weak_performance_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        review_id="rev_demo",
        expert_id="performance_reliability",
        title="DTO 字段变更可能有风险",
        summary="当前只看到 DTO 变化，尚无明确性能证据。",
        finding_type="risk_hypothesis",
        severity="medium",
        confidence=0.4,
        file_path="packages/platform/types/schedules/output.ts",
        line_start=1,
        evidence=["ApiPropertyOptional 导入变化"],
        cross_file_evidence=[],
        context_files=["packages/platform/types/schedules/output.ts"],
        remediation_strategy="观察",
        remediation_suggestion="补充验证",
        remediation_steps=[],
        code_excerpt="1 | +import { ApiPropertyOptional } from '@nestjs/swagger'",
        suggested_code="",
        suggested_code_language="typescript",
    )

    assert runner._should_skip_finding("performance_reliability", finding) is True


def test_review_runner_keeps_loop_amplification_performance_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        review_id="rev_demo",
        expert_id="performance_reliability",
        title="循环内逐条查库会放大调用成本",
        summary="当前变更把 repository 查询放进 for 循环，批量场景会放大数据库往返和超时风险。",
        finding_type="risk_hypothesis",
        severity="medium",
        confidence=0.46,
        file_path="src/main/java/com/example/OrderBatchService.java",
        line_start=41,
        evidence=["for 循环内调用 orderRepository.findByOrderNo"],
        cross_file_evidence=[],
        context_files=["src/main/java/com/example/OrderBatchService.java"],
        remediation_strategy="批量化",
        remediation_suggestion="把逐条查库改成批量查询或预加载。",
        remediation_steps=[],
        code_excerpt="41 | for (OrderItem item : items) {\n42 |   orderRepository.findByOrderNo(item.getOrderNo());\n43 | }",
        suggested_code="",
        suggested_code_language="java",
    )

    assert runner._should_skip_finding("performance_reliability", finding) is False


def test_review_runner_keeps_loop_amplification_finding_without_timeout_keywords(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        review_id="rev_demo",
        expert_id="performance_reliability",
        title="循环调用放大",
        summary="检测到循环内调用，批量路径存在放大风险。",
        finding_type="risk_hypothesis",
        severity="medium",
        confidence=0.42,
        file_path="src/main/java/com/example/OrderBatchService.java",
        line_start=41,
        evidence=["forEach 内调用 orderService.process(item)"],
        cross_file_evidence=[],
        context_files=["src/main/java/com/example/OrderBatchService.java"],
        remediation_strategy="批处理化",
        remediation_suggestion="把循环内逐条调用改为批量接口。",
        remediation_steps=[],
        code_excerpt="41 | items.forEach(item -> orderService.process(item));",
        suggested_code="",
        suggested_code_language="java",
    )

    assert runner._should_skip_finding("performance_reliability", finding) is False


def test_review_runner_marks_comment_contract_signal_as_verification_risk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    stabilized = runner._enrich_java_quality_signal_language(
        {
            "title": "订单创建逻辑",
            "summary": "create 逻辑存在实现缺口。",
            "claim": "当前实现与承诺不一致。",
            "evidence": [],
            "signal_terms": {"comment_contract_unimplemented": ["// TODO: 创建订单后自动扣减库存并发送事件"]},
        },
        "correctness_business",
        "src/main/java/com/example/OrderService.java",
        {"excerpt": "+    // TODO: 创建订单后自动扣减库存并发送事件\n+    return orderRepository.save(order);"},
        {},
    )

    assert "承诺未落地" in str(stabilized["title"])
    assert "create 逻辑存在实现缺口" in str(stabilized["summary"])
    assert any("注释/待办承诺未实现" in item for item in list(stabilized["evidence"]))
    assert stabilized["finding_type"] == "risk_hypothesis"
    assert stabilized["verification_needed"] is True
    assert stabilized["direct_evidence"] is False
    assert stabilized["severity"] == "high"
    assert float(stabilized["confidence"]) <= 0.78


def test_review_runner_keeps_comment_contract_signal_as_risk_hypothesis(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "TODO 未实现",
            "claim": "注释说明需要扣减库存，但实现中没有对应动作，属于承诺未落地。",
            "summary": "注释与实现不一致。",
            "evidence": ["检测到注释/待办承诺未实现：// TODO: 扣减库存并发送事件"],
            "assumptions": [],
            "confidence": 0.78,
            "severity": "high",
            "finding_type": "risk_hypothesis",
            "verification_needed": True,
        },
        "correctness_business",
        "src/main/java/com/example/OrderService.java",
        24,
        {"excerpt": "+    // TODO: 扣减库存并发送事件\n+    return orderRepository.save(order);"},
        repository_context={},
        input_completeness={},
    )

    assert result["verification_needed"] is True
    assert result["finding_type"] == "risk_hypothesis"
    assert result["direct_evidence"] is False
    assert result["severity"] == "high"
    assert float(result["confidence"]) <= 0.78
    assert "承诺未落地" in str(result["title"])
    assert any("注释/待办承诺未实现" in item for item in list(result["evidence"]))


def test_review_runner_keeps_loop_amplification_as_risk_hypothesis(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "批量处理性能风险",
            "claim": "当前实现可能在循环里逐条调用仓储，批量场景会被放大。",
            "summary": "存在循环内外部调用。",
            "evidence": [],
            "signal_terms": {"loop_call_amplification": ["orderRepository.findByOrderNo", "forEach"]},
            "confidence": 0.41,
            "severity": "medium",
            "finding_type": "risk_hypothesis",
            "verification_needed": True,
        },
        "performance_reliability",
        "src/main/java/com/example/OrderBatchService.java",
        41,
        {"excerpt": "+    items.forEach(item -> orderRepository.findByOrderNo(item.getOrderNo()));"},
        repository_context={},
        input_completeness={},
    )

    assert result["finding_type"] == "risk_hypothesis"
    assert result["verification_needed"] is True
    assert result["direct_evidence"] is False
    assert result["severity"] == "high"
    assert float(result["confidence"]) <= 0.78
    assert "循环调用放大" in str(result["title"])
    assert any("检测到循环内调用放大" in item for item in list(result["evidence"]))


def test_review_runner_keeps_exception_swallowed_as_risk_hypothesis(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "订单消费异常处理退化",
            "claim": "当前 catch 里的错误处理被移除，后续失败会更难定位。",
            "summary": "异常处理被削弱。",
            "evidence": [],
            "signal_terms": {"exception_swallowed": ["catch", "printStackTrace", "logger"]},
            "confidence": 0.42,
            "severity": "medium",
            "finding_type": "risk_hypothesis",
            "verification_needed": True,
        },
        "correctness_business",
        "src/main/java/com/example/OrderEventConsumer.java",
        56,
        {
            "excerpt": (
                "-        } catch (Exception ex) {\n"
                "-            ex.printStackTrace();\n"
                "+        } catch (Exception ex) {\n"
                "+        }\n"
            )
        },
        repository_context={},
        input_completeness={},
    )

    assert result["finding_type"] == "risk_hypothesis"
    assert result["verification_needed"] is True
    assert result["direct_evidence"] is False
    assert result["severity"] == "high"
    assert float(result["confidence"]) <= 0.8
    assert "静默吞掉异常" in str(result["title"])
    assert any("静默吞掉异常" in item for item in list(result["evidence"]))


def test_review_runner_promotes_rule_backed_empty_catch_to_direct_defect(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "事件消费链路的反射异常处理被删除",
            "claim": "catch 删除 printStackTrace 后没有替代日志、失败标记或补偿。",
            "summary": "NoSuchMethodException 等反射异常会被静默吞掉。",
            "evidence": [
                "@@ -28,7 +28,6 @@\n} catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException | InstantiationException e) {\n-    e.printStackTrace();\n}",
                "目标文件完整内容已加载。",
            ],
            "matched_rules": ["CORR-JDDD-002"],
            "violated_guidelines": ["CORR-JDDD-002"],
            "context_files": [
                "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
            ],
            "confidence": 0.72,
            "severity": "medium",
            "finding_type": "risk_hypothesis",
            "verification_needed": True,
        },
        "correctness_business",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        29,
        {
            "excerpt": (
                "-\t\t\t\te.printStackTrace();\n"
                "+\t\t\t}\n"
            )
        },
        repository_context={},
        input_completeness={},
    )

    assert result["finding_type"] == "direct_defect"
    assert result["verification_needed"] is False
    assert result["direct_evidence"] is True
    assert result["severity"] == "high"
    assert result["normalized_issue_type"] == "exception_swallowed"
    assert float(result["confidence"]) >= 0.86


def test_review_runner_keeps_exception_semantics_weakened_as_risk_hypothesis(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "订单处理异常语义退化",
            "claim": "当前异常分支返回默认值，调用方可能误以为处理成功。",
            "summary": "异常后的返回语义被弱化。",
            "evidence": [],
            "signal_terms": {"exception_semantics_weakened": ["catch", "null"]},
            "confidence": 0.46,
            "severity": "medium",
            "finding_type": "risk_hypothesis",
            "verification_needed": True,
        },
        "correctness_business",
        "src/main/java/com/example/OrderFacade.java",
        72,
        {
            "excerpt": (
                " try {\n"
                "   return orderGateway.submit(command);\n"
                " } catch (Exception ex) {\n"
                "   return null;\n"
                " }\n"
            )
        },
        repository_context={},
        input_completeness={},
    )

    assert result["finding_type"] == "risk_hypothesis"
    assert result["verification_needed"] is True
    assert result["direct_evidence"] is False
    assert result["severity"] == "high"
    assert float(result["confidence"]) <= 0.8
    assert "异常返回语义被弱化" in str(result["title"])
    assert any("返回语义被弱化" in item for item in list(result["evidence"]))


def test_review_runner_builds_forced_loop_observation_candidate(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        system_prompt="prompt",
    )

    forced = runner._build_forced_observation_candidates(
        expert=expert,
        uncovered_observations=[
            {
                "observation_id": "obs_loop_001",
                "kind": "control_flow_with_external_call",
                "file_path": "src/main/java/com/example/OrderBatchService.java",
                "line_start": 41,
                "summary": "循环体内存在外部调用",
                "evidence": ["orderRepository.findByOrderNo(item.getOrderNo()) 位于 forEach 内"],
                "related_symbols": ["orderRepository.findByOrderNo", "forEach"],
                "confidence": 0.86,
            }
        ],
        max_findings=4,
    )

    assert len(forced) == 1
    assert forced[0]["title"] == "循环调用放大"
    assert forced[0]["finding_type"] == "risk_hypothesis"
    assert forced[0]["verification_needed"] is True
    assert forced[0]["direct_evidence"] is False
    assert forced[0]["evidence_source"] == "observation_signal"
    assert forced[0]["severity"] == "high"
    assert float(forced[0]["confidence"]) <= 0.78


def test_review_runner_builds_forced_comment_contract_candidate(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性专家",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )

    forced = runner._build_forced_observation_candidates(
        expert=expert,
        uncovered_observations=[
            {
                "observation_id": "obs_todo_001",
                "kind": "declared_intent_without_implementation",
                "file_path": "src/main/java/com/example/OrderService.java",
                "line_start": 21,
                "summary": "TODO 注释承诺的行为没有落地",
                "evidence": ["// TODO: 创建订单后自动扣减库存并发送事件"],
                "related_symbols": ["// TODO: 创建订单后自动扣减库存并发送事件"],
                "confidence": 0.84,
            }
        ],
        max_findings=4,
    )

    assert len(forced) == 1
    assert forced[0]["title"] == "承诺未落地"
    assert forced[0]["finding_type"] == "risk_hypothesis"
    assert forced[0]["verification_needed"] is True
    assert forced[0]["direct_evidence"] is False
    assert forced[0]["evidence_source"] == "observation_signal"
    assert forced[0]["severity"] == "high"
    assert float(forced[0]["confidence"]) <= 0.78


def test_review_runner_builds_forced_ddd_factory_bypass_candidate(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD架构专家",
        role="architecture",
        enabled=True,
        system_prompt="prompt",
    )

    forced = runner._build_forced_observation_candidates(
        expert=expert,
        uncovered_observations=[
            {
                "observation_id": "obs_factory_001",
                "kind": "construction_path_changed",
                "file_path": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
                "line_start": 18,
                "summary": "Course.create() 被替换为 new Course()",
                "evidence": [
                    "- Course course = Course.create(id, name, duration);",
                    "+ Course course = new Course(id, name, duration);",
                    "eventBus.publish(course.pullDomainEvents())",
                ],
                "related_symbols": ["Course.create", "new Course"],
                "confidence": 0.73,
            }
        ],
        max_findings=4,
    )

    assert len(forced) == 1
    assert forced[0]["title"] == "创建路径变更风险"
    assert forced[0]["finding_type"] == "risk_hypothesis"
    assert forced[0]["verification_needed"] is True
    assert forced[0]["direct_evidence"] is False
    assert forced[0]["evidence_source"] == "observation_signal"
    assert forced[0]["severity"] == "high"
    assert float(forced[0]["confidence"]) <= 0.78
    assert "DDD-JDDD-001" in forced[0]["matched_rules"]
    assert forced[0]["observation_ids"] == ["obs_factory_001"]


def test_review_runner_builds_forced_security_guard_candidate_as_verification_risk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全合规专家",
        role="security",
        enabled=True,
        system_prompt="prompt",
    )

    forced = runner._build_forced_observation_candidates(
        expert=expert,
        uncovered_observations=[
            {
                "observation_id": "obs_guard_001",
                "kind": "security_guard_removed",
                "file_path": "src/main/java/com/example/OrderController.java",
                "line_start": 31,
                "summary": "@PreAuthorize 被移除",
                "evidence": ["- @PreAuthorize(\"hasRole('ADMIN')\")", "+ public Order create(...)"],
                "related_symbols": ["@PreAuthorize", "create"],
                "confidence": 0.77,
            }
        ],
        max_findings=4,
    )

    assert len(forced) == 1
    assert forced[0]["title"] == "入口保护变更风险"
    assert forced[0]["finding_type"] == "risk_hypothesis"
    assert forced[0]["verification_needed"] is True
    assert forced[0]["direct_evidence"] is False
    assert forced[0]["evidence_source"] == "observation_signal"
    assert float(forced[0]["confidence"]) <= 0.8


def test_review_runner_stabilize_expert_analysis_preserves_observation_ids(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    result = runner._stabilize_expert_analysis(
        {
            "title": "循环调用放大",
            "claim": "在循环中调用下游依赖，批量路径会被逐条放大。",
            "summary": "存在循环内外部调用。",
            "evidence": ["for (OrderItem item : items)", "orderRepository.findByOrderNo(item.getOrderNo())"],
            "observation_ids": ["obs_loop_001", "obs_loop_001", ""],
        },
        "performance_reliability",
        "src/main/java/com/example/OrderBatchService.java",
        41,
        {
            "excerpt": "+    for (OrderItem item : items) {\n+        orderRepository.findByOrderNo(item.getOrderNo());\n+    }",
            "changed_lines": [41, 42, 43],
            "start_line": 41,
            "end_line": 43,
        },
        repository_context={},
        input_completeness={},
    )

    assert result["observation_ids"] == ["obs_loop_001"]


def test_review_runner_schema_gate_downgrades_unstructured_direct_defect(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    result = runner._stabilize_expert_analysis(
        {
            "title": "疑似风险",
            "claim": "这里有问题",
            "finding_type": "direct_defect",
            "severity": "urgent",
            "confidence": 0.95,
            "evidence": [],
            "matched_rules": [],
        },
        "correctness_business",
        "src/main/java/com/example/OrderService.java",
        12,
        {
            "excerpt": "+    return order;",
            "changed_lines": [12],
            "start_line": 12,
            "end_line": 12,
        },
        repository_context={},
        input_completeness={},
    )

    assert result["finding_type"] == "risk_hypothesis"
    assert result["severity"] == "medium"
    assert result["confidence"] <= 0.69
    assert result["verification_needed"] is True
    assert "direct_defect_without_evidence" in result["schema_validation_errors"]
    assert result["normalized_issue_type"]
    assert result["schema_payload_valid"] is True


def test_review_runner_schema_gate_hard_rejects_empty_expert_payload(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    result = runner._stabilize_expert_analysis(
        {
            "finding_type": "direct_defect",
            "severity": "blocker",
            "confidence": 0.96,
            "evidence": [],
            "matched_rules": [],
        },
        "correctness_business",
        "src/main/java/com/example/OrderService.java",
        12,
        {
            "excerpt": "",
            "changed_lines": [],
            "start_line": 12,
            "end_line": 12,
        },
        repository_context={},
        input_completeness={},
    )

    assert result["schema_rejected"] is True
    assert result["finding_type"] == "risk_hypothesis"
    assert result["confidence"] <= 0.35
    assert "irrecoverable_empty_payload" in result["schema_validation_errors"]
    assert result["schema_payload_valid"] is True


def test_review_runner_normalize_review_observations_preserves_language_and_tags(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    normalized = runner._normalize_review_observations(
        [
            {
                "observation_id": "obs_generic_001",
                "kind": "cross_layer_dependency",
                "signal": "cross_layer_dependency",
                "language": "java",
                "file_path": "src/main/java/com/example/OrderController.java",
                "line_start": 24,
                "line_end": 24,
                "summary": "Controller 直接依赖 repository",
                "risk_hints": ["分层耦合"],
                "evidence": ["orderRepository.save(request)"],
                "related_symbols": ["OrderController", "orderRepository"],
                "tags": ["layering", "architecture"],
                "confidence": 0.81,
            }
        ]
    )

    assert normalized[0]["language"] == "java"
    assert normalized[0]["tags"] == ["layering", "architecture"]


def test_review_runner_does_not_fallback_to_default_file_when_multifile_path_missing(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    batch_items = [
        {
            "file_path": "src/main/java/com/example/OrderService.java",
            "target_hunk": {"hunk_header": "@@ -10,2 +10,2 @@", "excerpt": "+ orderRepository.save(order);"},
        },
        {
            "file_path": "src/main/java/com/example/InventoryService.java",
            "target_hunk": {"hunk_header": "@@ -20,2 +20,2 @@", "excerpt": "+ inventoryRepository.deduct(sku);"},
        },
    ]
    resolved = runner._resolve_finding_file_path(
        {"title": "可能存在性能风险", "claim": "建议关注批量路径"},
        fallback_file_path="src/main/java/com/example/OrderService.java",
        batch_items=batch_items,
    )
    assert resolved == ""


def test_review_runner_resolves_multifile_path_from_semantics(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    batch_items = [
        {
            "file_path": "src/main/java/com/example/OrderService.java",
            "target_hunk": {"hunk_header": "@@ -10,2 +10,2 @@", "excerpt": "+ orderRepository.save(order);"},
        },
        {
            "file_path": "src/main/java/com/example/InventoryService.java",
            "target_hunk": {"hunk_header": "@@ -20,4 +20,6 @@", "excerpt": "+ for (Item item : items) {\n+   inventoryRepository.findBySku(item.getSku());\n+ }"},
        },
    ]
    resolved = runner._resolve_finding_file_path(
        {
            "title": "循环调用放大",
            "claim": "for 循环内调用 inventoryRepository.findBySku，批量场景会放大数据库往返。",
            "evidence": ["inventoryRepository.findBySku 出现在循环中"],
        },
        fallback_file_path="src/main/java/com/example/OrderService.java",
        batch_items=batch_items,
    )
    assert resolved == "src/main/java/com/example/InventoryService.java"


def test_review_runner_recognizes_generic_suggested_code_as_invalid(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    assert (
        runner._looks_like_concrete_suggested_code(
            "# Suggested rewrite for src/main/java/com/example/OrderService.java\n# 1. Separate validation from execution",
            file_path="src/main/java/com/example/OrderService.java",
        )
        is False
    )
    assert (
        runner._looks_like_concrete_suggested_code(
            "// TODO: 将循环内逐条外部调用改为批量处理，避免调用放大",
            file_path="src/main/java/com/example/OrderService.java",
        )
        is False
    )
    assert (
        runner._looks_like_concrete_suggested_code(
            "// 先批量查询订单\n// 再组装返回结果",
            file_path="src/main/java/com/example/OrderService.java",
        )
        is False
    )
    assert (
        runner._looks_like_concrete_suggested_code(
            "public void save(Order request) {\n    Objects.requireNonNull(request);\n    repository.save(request);\n}",
            file_path="src/main/java/com/example/OrderService.java",
        )
        is True
    )


def test_review_runner_repairs_missing_suggested_code(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性专家",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_repair_suggested_code",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/repair",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderService.java"],
        ),
        selected_experts=[expert.expert_id],
    )

    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text='{"suggested_code":"public void save(Order request) {\\n    Objects.requireNonNull(request);\\n    repository.save(request);\\n}"}',
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_load_repository_problem_context",
        lambda *_args, **_kwargs: {"snippet": "18 | public void save(Order request) {\n19 |     repository.save(request);\n20 | }"},
    )

    repaired = runner._repair_missing_suggested_code(
        review=review,
        expert=expert,
        runtime_settings=runner.runtime_settings_service.get().model_copy(update={"review_quality_mode": "standard"}),
        llm_request_options={"timeout_seconds": 30, "max_attempts": 1},
        file_path="src/main/java/com/example/OrderService.java",
        line_start=18,
        parsed={
            "title": "空参未校验",
            "claim": "request 为空时会触发异常",
            "fix_strategy": "入口增加非空校验",
            "suggested_fix": "添加 Objects.requireNonNull",
            "change_steps": ["补判空"],
        },
        target_hunk={"excerpt": "+ repository.save(request);"},
    )

    assert "Objects.requireNonNull" in repaired


def test_review_runner_skips_suggested_code_llm_repair_in_thorough_review(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="通用编码规范专家",
        role="architecture",
        enabled=True,
        system_prompt="prompt",
    )
    review = ReviewTask(
        review_id="rev_skip_repair_suggested_code",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/repair",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderService.java"],
        ),
        selected_experts=[expert.expert_id],
    )
    calls = {"count": 0}

    def _complete_text(**_kwargs):  # noqa: ANN001
        calls["count"] += 1
        return LLMTextResult(
            text='{"suggested_code":"return true;"}',
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", _complete_text)

    repaired = runner._repair_missing_suggested_code(
        review=review,
        expert=expert,
        runtime_settings=runner.runtime_settings_service.get().model_copy(
            update={"review_quality_mode": "thorough_review"}
        ),
        llm_request_options={"timeout_seconds": 30, "max_attempts": 1},
        file_path="src/main/java/com/example/OrderService.java",
        line_start=18,
        parsed={"title": "标题", "claim": "问题结论混入了无关描述"},
        target_hunk={"excerpt": "+ repository.save(request);"},
    )

    assert repaired == ""
    assert calls["count"] == 0


def test_review_runner_suppresses_no_risk_formatting_findings(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        review_id="rev_demo",
        expert_id="architecture_design",
        title="代码格式化变更无架构风险",
        summary="当前变更仅涉及缩进调整，无架构问题。",
        finding_type="design_concern",
        severity="low",
        confidence=0.7,
        file_path="sentinel-cluster/server/NettyTransportServer.java",
        line_start=12,
        evidence=["本次改动只调整了空格和换行"],
        cross_file_evidence=[],
        context_files=["sentinel-cluster/server/NettyTransportServer.java"],
        remediation_strategy="无需处理",
        remediation_suggestion="保持现状",
        remediation_steps=[],
        code_excerpt="12 |     // formatting only",
        suggested_code="",
        suggested_code_language="java",
    )

    assert runner._should_skip_finding("architecture_design", finding) is True


def test_review_runner_build_evidence_scopes_domain_hints_by_file(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="cal.com",
        project_id="calcom",
        source_ref="mr/28378",
        target_ref="main",
        changed_files=[
            "packages/prisma/migrations/20260311195632_add_availability_timestamps/migration.sql",
            "packages/lib/schedules/transformers/getScheduleListItemData.ts",
        ],
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
    )

    evidence = runner._build_evidence(
        subject,
        expert,
        "packages/lib/schedules/transformers/getScheduleListItemData.ts",
        [],
        {"evidence": ["transformer 未同步更新"]},
    )

    assert "database_migration" not in evidence
    assert "test_surface" not in evidence
    assert "transformer 未同步更新" in evidence


def test_review_runner_fails_when_no_enabled_experts(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    repository = FileExpertRepository(storage_root / "experts")
    for expert in repository.list():
        repository.save(expert.model_copy(update={"enabled": False}))

    review = runner.run_once(review_id)

    assert review.status == "failed"
    assert review.phase == "failed"


def test_review_runner_preserves_results_when_single_expert_fails(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    architecture = ExpertProfile(
        expert_id="architecture_design",
        name="Coding Standards",
        name_zh="通用编码规范",
        role="coding standards",
        enabled=True,
        focus_areas=["命名与表达"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )
    correctness = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性",
        role="correctness",
        enabled=True,
        focus_areas=["业务规则"],
        system_prompt="prompt",
        runtime_tool_bindings=[],
    )

    monkeypatch.setattr(runner.registry, "list_enabled", lambda: [architecture, correctness])
    monkeypatch.setattr(
        runner.main_agent_service,
        "select_review_experts",
        lambda *_args, **_kwargs: {
            "requested_expert_ids": ["architecture_design", "correctness_business"],
            "candidate_expert_ids": ["architecture_design", "correctness_business"],
            "selected_expert_ids": ["architecture_design", "correctness_business"],
            "selected_experts": [
                {"expert_id": "architecture_design", "expert_name": "通用编码规范", "reason": "命中编码规范风险"},
                {"expert_id": "correctness_business", "expert_name": "正确性", "reason": "命中正确性风险"},
            ],
            "skipped_experts": [],
            "llm": {},
        },
    )
    monkeypatch.setattr(runner.main_agent_service, "build_routing_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_command",
        lambda _subject, expert, _runtime, route_hint=None: {
            "file_path": "backend/app/main.py",
            "line_start": 2,
            "summary": f"{expert.name_zh} 审查 backend/app/main.py",
            "routeable": True,
            "related_files": [],
            "target_hunk": {},
            "repository_context": {},
            "expected_checks": [],
            "disallowed_inference": [],
            "routing_reason": "test",
            "routing_confidence": 0.9,
            "llm": {},
        },
    )
    monkeypatch.setattr(runner.knowledge_service, "retrieve_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.knowledge_service,
        "screen_rules_for_expert",
        lambda *_args, **_kwargs: {
            "total_rules": 0,
            "enabled_rules": 0,
            "must_review_count": 0,
            "possible_hit_count": 0,
            "matched_rule_count": 0,
            "matched_rules_for_llm": [],
            "batch_summaries": [],
        },
    )
    monkeypatch.setattr(runner.graph, "invoke", lambda _state: {"issues": [], "issue_filter_decisions": []})
    monkeypatch.setattr(runner.main_agent_service, "build_final_summary", lambda *_args, **_kwargs: ("final", {}))

    def _fake_run_expert_from_command(**kwargs):
        expert = kwargs["expert"]
        if expert.expert_id == "architecture_design":
            raise TimeoutError("expert timed out")
        kwargs["finding_payloads"].append(
            {
                "finding_id": "fdg_demo",
                "expert_id": expert.expert_id,
                "title": "发现问题",
                "summary": "summary",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.61,
                "file_path": "backend/app/main.py",
                "line_start": 2,
                "evidence": ["evidence"],
                "cross_file_evidence": [],
                "assumptions": [],
                "context_files": ["backend/app/main.py"],
            }
        )

    monkeypatch.setattr(runner, "_run_expert_from_command", _fake_run_expert_from_command)

    runner.run_once(review_id)
    review = runner.review_repo.get(review_id)
    assert review is not None

    assert review.status == "completed"
    assert review.subject.metadata["expert_execution"]["partial_failure_count"] == 1
    progress = review.subject.metadata["expert_review_progress"]
    assert progress["total_expert_jobs"] == 2
    assert progress["started_count"] == 2
    assert progress["completed_count"] == 1
    assert progress["failed_count"] == 1
    assert progress["last_event"] in {"completed", "failed"}
    assert "1 个专家任务执行失败" in review.report_summary
    assert any(event.event_type == "expert_failed" for event in runner.event_repo.list(review_id))
    assert any(event.event_type == "review_completed" for event in runner.event_repo.list(review_id))


def test_review_runner_build_expert_prompt_includes_skill_tool_and_design_doc_context(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/design-check",
        target_ref="main",
        title="Design consistency review",
        changed_files=["apps/api/order/order.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/order/order.service.ts b/apps/api/order/order.service.ts\n"
            "--- a/apps/api/order/order.service.ts\n"
            "+++ b/apps/api/order/order.service.ts\n"
            "@@ -8,6 +8,8 @@\n"
            " export async function createOrder() {\n"
            "+  const payload = { amount, currency };\n"
            "+  return client.post('/api/orders', payload);\n"
            " }\n"
        ),
        metadata={
            "design_docs": [
                {
                    "doc_id": "doc_design",
                    "title": "订单创建详细设计",
                    "filename": "order-design.md",
                    "content": "# API 定义\nPOST /api/orders\n\n# 入参字段\namount: number\ncurrency: string",
                    "doc_type": "design_spec",
                }
            ]
        },
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性"],
        system_prompt="prompt",
        review_spec="规则一\n规则二",
    )
    skill = ReviewSkillProfile(
        skill_id="design-consistency-check",
        name="详细设计一致性检查",
        description="检查实现是否符合详细设计文档",
        required_tools=["diff_inspector", "design_spec_alignment"],
        prompt_body="必须检查设计文档中的 API、字段和业务流程是否一致。",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "apps/api/order/order.service.ts",
        10,
        tool_evidence=[],
        runtime_tool_results=[
            {
                "tool_name": "design_spec_alignment",
                "summary": "已解析 1 份详细设计文档，并发现 1 条缺失设计点。",
                "design_alignment_status": "partially_aligned",
            }
        ],
        repository_context={"summary": "目标分支中存在 order controller 和 dto 实现。", "routing_reason": "字段契约变化更适合正确性专家"},
        target_hunk={"hunk_header": "@@ -8,6 +8,8 @@", "excerpt": "+ return client.post('/api/orders', payload);"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["证据不足时不要假定接口已经完全打通"],
        expected_checks=["校验 API 和字段定义是否一致"],
        active_skills=[skill],
    )

    assert "已激活技能" in prompt
    assert "design-consistency-check" in prompt
    assert "运行时工具调用结果" in prompt
    assert "design_spec_alignment" in prompt
    assert "本次审核绑定的详细设计文档" in prompt
    assert "订单创建详细设计" in prompt
    assert "POST /api/orders" in prompt
    assert "主Agent派工理由" in prompt


def test_review_runner_build_expert_prompt_includes_target_file_full_diff(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/full-diff",
        target_ref="main",
        title="Full diff should reach expert",
        changed_files=[
            "apps/api/order/order.service.ts",
            "apps/api/order/order.controller.ts",
        ],
        unified_diff=(
            "diff --git a/apps/api/order/order.service.ts b/apps/api/order/order.service.ts\n"
            "--- a/apps/api/order/order.service.ts\n"
            "+++ b/apps/api/order/order.service.ts\n"
            "@@ -8,6 +8,8 @@\n"
            " export async function createOrder(amount, currency) {\n"
            "+  validateCurrency(currency);\n"
            "+  const payload = { amount, currency };\n"
            "   return client.post('/api/orders', payload);\n"
            " }\n"
            "@@ -20,4 +22,6 @@\n"
            " export async function cancelOrder(id) {\n"
            "-  return client.delete(`/api/orders/${id}`);\n"
            "+  auditCancel(id);\n"
            "+  return client.delete(`/api/orders/${id.trim()}`);\n"
            " }\n"
            "diff --git a/apps/api/order/order.controller.ts b/apps/api/order/order.controller.ts\n"
            "--- a/apps/api/order/order.controller.ts\n"
            "+++ b/apps/api/order/order.controller.ts\n"
            "@@ -3,1 +3,2 @@\n"
            "-router.post('/orders', createOrderHandler);\n"
            "+router.post('/orders', authGuard, createOrderHandler);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性"],
        system_prompt="prompt",
        review_spec="规则一\n规则二",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "apps/api/order/order.service.ts",
        9,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={"summary": "目标分支中存在 controller 和 service 配合改动。", "routing_reason": "service 存在字段和调用链变化"},
        target_hunk={"hunk_header": "@@ -8,6 +8,8 @@", "excerpt": "+  const payload = { amount, currency };"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["证据不足时不要假定 controller 已完成全部校验"],
        expected_checks=["检查同文件内其他变更是否影响业务一致性"],
        active_skills=[],
    )

    assert "目标文件完整 diff" in prompt
    assert "validateCurrency(currency);" in prompt
    assert "auditCancel(id);" in prompt
    assert "其他变更文件摘要" in prompt
    assert "authGuard" in prompt


def test_review_runner_build_expert_prompt_includes_complete_repository_context_snippets(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/context",
        target_ref="main",
        changed_files=["apps/api/order/order.service.ts", "apps/api/order/order.controller.ts"],
        unified_diff=(
            "diff --git a/apps/api/order/order.service.ts b/apps/api/order/order.service.ts\n"
            "--- a/apps/api/order/order.service.ts\n"
            "+++ b/apps/api/order/order.service.ts\n"
            "@@ -4,2 +4,4 @@\n"
            "   async createOrder(payload) {\n"
            "+    validateOrder(payload);\n"
            "+    auditCreate(payload.id);\n"
            "   }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性"],
        system_prompt="prompt",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "apps/api/order/order.service.ts",
        5,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={
            "summary": "service 与 controller、dto 存在联动",
            "routing_reason": "需要检查 service 到 controller 的契约是否一致",
            "primary_context": {
                "path": "apps/api/order/order.service.ts",
                "line_start": 5,
                "snippet": (
                    "   3 | export class OrderService {\n"
                    "   4 |   async createOrder(payload) {\n"
                    "   5 |     validateOrder(payload);\n"
                    "   6 |     auditCreate(payload.id);\n"
                    "   7 |   }\n"
                ),
            },
            "related_contexts": [
                {
                    "path": "apps/api/order/order.controller.ts",
                    "line_start": 12,
                    "snippet": (
                        "  10 | export class OrderController {\n"
                        "  11 |   async create(req) {\n"
                        "  12 |     return this.orderService.createOrder(req.body);\n"
                        "  13 |   }\n"
                    ),
                }
            ],
            "symbol_contexts": [
                {
                    "symbol": "createOrder",
                    "definitions": [
                        {
                            "path": "apps/api/order/order.service.ts",
                            "line_number": 4,
                            "snippet": "4: async createOrder(payload) {",
                        }
                    ],
                    "references": [
                        {
                            "path": "apps/api/order/order.controller.ts",
                            "line_number": 12,
                            "snippet": "12: return this.orderService.createOrder(req.body);",
                        }
                    ],
                }
            ],
        },
        target_hunk={"hunk_header": "@@ -4,2 +4,4 @@", "excerpt": "+    validateOrder(payload);"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["检查跨文件调用链上的输入校验与业务约束"],
        active_skills=[],
    )

    assert "validateOrder(payload);" in prompt
    assert "auditCreate(payload.id);" in prompt
    assert "return this.orderService.createOrder(req.body);" in prompt
    assert "createOrder" in prompt


def test_review_runner_build_code_excerpt_prefers_diff_anchor_over_repository_source_context(storage_root: Path):
    repo_root = storage_root / "repo"
    target_file = repo_root / "apps" / "api" / "order" / "order.service.ts"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        "\n".join(
            [
                "export class OrderService {",
                "  constructor(private readonly client: HttpClient) {}",
                "",
                "  async createOrder(payload: CreateOrderInput) {",
                "    validateOrder(payload);",
                "    auditCreate(payload.id);",
                "    return this.client.post('/orders', payload);",
                "  }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    runner = ReviewRunner(storage_root=storage_root)
    runner.runtime_settings_service.update(
        {
            "code_repo_clone_url": "https://example.com/repo.git",
            "code_repo_local_path": str(repo_root),
            "code_repo_default_branch": "main",
        }
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/source-snippet",
        target_ref="main",
        changed_files=["apps/api/order/order.service.ts"],
        unified_diff=(
            "diff --git a/apps/api/order/order.service.ts b/apps/api/order/order.service.ts\n"
            "--- a/apps/api/order/order.service.ts\n"
            "+++ b/apps/api/order/order.service.ts\n"
            "@@ -4,2 +4,4 @@\n"
            "   async createOrder(payload: CreateOrderInput) {\n"
            "+    validateOrder(payload);\n"
            "+    auditCreate(payload.id);\n"
            "     return this.client.post('/orders', payload);\n"
            "   }\n"
        ),
    )

    excerpt = runner._build_code_excerpt(subject, "apps/api/order/order.service.ts", 6, "correctness_business")

    assert "+    auditCreate(payload.id);" in excerpt
    assert "auditCreate(payload.id);" in excerpt
    assert "return this.client.post('/orders', payload);" in excerpt
    assert "constructor(private readonly client: HttpClient)" not in excerpt


def test_review_runner_rejects_formal_finding_anchor_outside_post_change_diff_lines(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/diff-anchor",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
            "--- a/src/main/java/com/example/OrderService.java\n"
            "+++ b/src/main/java/com/example/OrderService.java\n"
            "@@ -10,3 +10,4 @@\n"
            " public Order create(Command command) {\n"
            "+    validate(command);\n"
            "     return repository.save(command);\n"
            " }\n"
        ),
    )
    target_hunk = {
        "file_path": "src/main/java/com/example/OrderService.java",
        "start_line": 10,
        "end_line": 13,
        "changed_lines": [11],
        "excerpt": "# src/main/java/com/example/OrderService.java\n  10 |  public Order create(Command command) {\n  11 | +    validate(command);\n  12 |      return repository.save(command);\n  13 |  }",
    }

    assert runner._finding_has_valid_diff_anchor(subject, "src/main/java/com/example/OrderService.java", 11, target_hunk)
    assert not runner._finding_has_valid_diff_anchor(subject, "src/main/java/com/example/OrderService.java", 12, target_hunk)
    assert not runner._finding_has_valid_diff_anchor(subject, "src/main/java/com/example/Other.java", 11, target_hunk)


def test_review_runner_rejects_finding_that_only_matches_removed_code(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/main/java/com/example/OrderService.java",
        "start_line": 20,
        "end_line": 23,
        "changed_lines": [21],
        "excerpt": (
            "# src/main/java/com/example/OrderService.java\n"
            "  20 |  public Order create(Command command) {\n"
            "   - |     legacyValidate(command);\n"
            "  21 | +    validate(command);\n"
            "  22 |      return repository.save(command);\n"
            "  23 |  }"
        ),
    }
    parsed = {
        "title": "legacyValidate 缺少幂等保护",
        "claim": "legacyValidate(command) 没有校验重复提交，可能导致重复创建订单。",
        "finding_type": "direct_defect",
        "evidence": ["旧校验函数 legacyValidate 仍在当前路径中执行。"],
    }

    result = runner._finding_matches_current_diff_code(parsed, target_hunk)

    assert result["matched"] is False
    assert "legacyvalidate" in result["removed_token_overlap"]


def test_review_runner_accepts_finding_that_matches_added_code(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/main/java/com/example/OrderService.java",
        "start_line": 20,
        "end_line": 23,
        "changed_lines": [21],
        "excerpt": (
            "# src/main/java/com/example/OrderService.java\n"
            "  20 |  public Order create(Command command) {\n"
            "   - |     legacyValidate(command);\n"
            "  21 | +    validate(command);\n"
            "  22 |      return repository.save(command);\n"
            "  23 |  }"
        ),
    }
    parsed = {
        "title": "validate 缺少幂等保护",
        "claim": "validate(command) 没有校验重复提交，可能导致重复创建订单。",
        "finding_type": "direct_defect",
        "evidence": ["新增 validate 仍没有检查 command.requestId。"],
    }

    result = runner._finding_matches_current_diff_code(parsed, target_hunk)

    assert result["matched"] is True
    assert "validate" in result["added_token_overlap"]


def test_review_runner_keeps_general_rule_attribution_without_custom_rules(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "订单总价计算错误",
        "claim": "price.add(quantity) 会把单价和数量相加，导致订单金额错误。",
        "finding_type": "direct_defect",
        "matched_rules": ["正确性专家通用规范：金额计算必须保持业务语义"],
        "evidence": ["diff 显示 multiply 被替换为 add", "price、quantity、total 表明这是订单金额计算"],
    }

    attribution = runner._normalize_finding_rule_attribution(
        parsed,
        rule_screening={"matched_rules_for_llm": []},
        expert_id="correctness_business",
    )

    assert attribution["sources"] == ["expert_general"]
    assert attribution["normalized_matched_rules"] == ["正确性专家通用规范：金额计算必须保持业务语义"]
    assert attribution["valid_custom_rule_ids"] == []
    assert attribution["invalid_custom_rule_ids"] == []


def test_review_runner_tracks_valid_additive_custom_rule_ids(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "订单导出明文输出手机号",
        "claim": "新增导出字段直接写入 user.getPhone()，违反订单导出脱敏要求。",
        "finding_type": "direct_defect",
        "matched_rules": ["ORDER-SEC-001"],
        "evidence": ["新增 row.put(\"phone\", user.getPhone())", "该路径属于订单导出"],
    }
    rule_screening = {
        "matched_rules_for_llm": [
            {
                "rule_id": "ORDER-SEC-001",
                "title": "订单导出敏感字段必须脱敏",
                "priority": "P1",
                "scene_path": "订单 / 导出 / 安全",
                "reason": "命中 phone 导出字段",
            }
        ]
    }

    attribution = runner._normalize_finding_rule_attribution(
        parsed,
        rule_screening=rule_screening,
        expert_id="security_compliance",
    )

    assert attribution["sources"] == ["product_or_repo_custom"]
    assert attribution["valid_custom_rule_ids"] == ["ORDER-SEC-001"]
    assert attribution["custom_rule_details"][0]["title"] == "订单导出敏感字段必须脱敏"
    assert runner._apply_additive_rule_priority_to_severity(
        "medium",
        finding_type="direct_defect",
        rule_attribution=attribution,
    ) == "high"


def test_review_runner_keeps_general_text_when_valid_custom_rule_is_embedded(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "订单导出明文输出手机号",
        "claim": "新增导出字段直接写入 user.getPhone()，违反订单导出脱敏要求。",
        "finding_type": "direct_defect",
        "matched_rules": ["ORDER-SEC-001 安全专家通用规范：敏感信息输出必须脱敏"],
        "evidence": ["新增 row.put(\"phone\", user.getPhone())", "该路径属于订单导出"],
    }
    rule_screening = {
        "matched_rules_for_llm": [
            {"rule_id": "ORDER-SEC-001", "title": "订单导出敏感字段必须脱敏", "priority": "P1"}
        ]
    }

    attribution = runner._normalize_finding_rule_attribution(
        parsed,
        rule_screening=rule_screening,
        expert_id="security_compliance",
    )

    assert attribution["valid_custom_rule_ids"] == ["ORDER-SEC-001"]
    assert attribution["general_rules"] == ["安全专家通用规范：敏感信息输出必须脱敏"]
    assert attribution["sources"] == ["expert_general", "product_or_repo_custom"]


def test_review_runner_removes_fabricated_custom_rule_id_but_keeps_general_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "订单导出明文输出手机号",
        "claim": "新增导出字段直接写入 user.getPhone()，违反安全专家通用敏感信息保护要求。",
        "finding_type": "direct_defect",
        "matched_rules": ["ORDER-SEC-999", "安全专家通用规范：敏感信息输出必须脱敏"],
        "evidence": ["新增 row.put(\"phone\", user.getPhone())", "phone 属于敏感联系方式"],
    }
    rule_screening = {
        "matched_rules_for_llm": [
            {"rule_id": "ORDER-SEC-001", "title": "订单导出敏感字段必须脱敏", "priority": "P1"}
        ]
    }

    attribution = runner._normalize_finding_rule_attribution(
        parsed,
        rule_screening=rule_screening,
        expert_id="security_compliance",
    )

    assert attribution["valid_custom_rule_ids"] == []
    assert attribution["invalid_custom_rule_ids"] == ["ORDER-SEC-999"]
    assert attribution["normalized_matched_rules"] == ["安全专家通用规范：敏感信息输出必须脱敏"]
    assert attribution["sources"] == ["expert_general"]
    assert attribution["assumptions"]


def test_review_runner_removes_fabricated_custom_rule_id_even_without_available_rules(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "订单导出明文输出手机号",
        "claim": "新增导出字段直接写入 user.getPhone()，违反安全专家通用敏感信息保护要求。",
        "finding_type": "direct_defect",
        "matched_rules": ["ORDER-SEC-999", "安全专家通用规范：敏感信息输出必须脱敏"],
        "evidence": ["新增 row.put(\"phone\", user.getPhone())", "phone 属于敏感联系方式"],
    }

    attribution = runner._normalize_finding_rule_attribution(
        parsed,
        rule_screening={"matched_rules_for_llm": []},
        expert_id="security_compliance",
    )

    assert attribution["valid_custom_rule_ids"] == []
    assert attribution["invalid_custom_rule_ids"] == ["ORDER-SEC-999"]
    assert attribution["normalized_matched_rules"] == ["安全专家通用规范：敏感信息输出必须脱敏"]
    assert attribution["sources"] == ["expert_general"]


def test_review_runner_full_flow_with_additive_product_rule_document(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全与合规专家",
        role="security",
        enabled=True,
        focus_areas=["敏感信息保护", "导出接口安全"],
        system_prompt="你是安全与合规专家",
        review_spec="安全专家通用规范：敏感信息输出必须脱敏，访问令牌不得写入日志。",
    )
    product_rule_doc = (
        "# 订单导出附加产品规则\n\n"
        "## RULE: ORDER-SEC-001 订单导出敏感字段必须脱敏\n\n"
        "### 元数据\n"
        "- 触发关键词: order export, phone, address, getPhone, getAddress\n"
        "- 风险类型: sensitive_export\n\n"
        "### 一级场景\n订单\n\n"
        "### 二级场景\n导出\n\n"
        "### 三级场景\n敏感字段明文输出\n\n"
        "### 描述\n"
        "订单导出接口新增手机号、身份证号、收货地址等字段时，必须使用脱敏工具输出，不能直接写入原始字段。\n\n"
        "### 问题代码示例\n"
        "```java\n"
        "row.put(\"phone\", user.getPhone());\n"
        "row.put(\"address\", order.getAddress());\n"
        "```\n\n"
        "### 问题代码行\n"
        "row.put(\"phone\", user.getPhone());\n\n"
        "### 误报代码\n"
        "```java\n"
        "row.put(\"phone\", Masking.maskPhone(user.getPhone()));\n"
        "```\n\n"
        "### 语言\njava\n\n"
        "### 问题级别\nP1\n"
    )
    runner.knowledge_service.create_document(
        {
            "title": "订单导出附加产品规则",
            "expert_id": expert.expert_id,
            "doc_type": "review_rule",
            "source_filename": "order-export-security-rules.md",
            "content": product_rule_doc,
        }
    )
    file_path = "src/main/java/com/acme/order/OrderExportController.java"
    unified_diff = (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        "@@ -18,2 +18,5 @@\n"
        " public Map<String, Object> export(Order order, User user, String token) {\n"
        "+    Map<String, Object> row = new LinkedHashMap<>();\n"
        "+    row.put(\"phone\", user.getPhone());\n"
        "+    row.put(\"address\", order.getAddress());\n"
        "+    log.info(\"export token={}\", token);\n"
        "     return row;\n"
        " }\n"
    )
    target_hunk = {
        "file_path": file_path,
        "hunk_header": "@@ -18,2 +18,5 @@",
        "start_line": 18,
        "end_line": 24,
        "changed_lines": [19, 20, 21, 22],
        "excerpt": (
            f"# {file_path}\n"
            "  18 |  public Map<String, Object> export(Order order, User user, String token) {\n"
            "  19 | +    Map<String, Object> row = new LinkedHashMap<>();\n"
            "  20 | +    row.put(\"phone\", user.getPhone());\n"
            "  21 | +    row.put(\"address\", order.getAddress());\n"
            "  22 | +    log.info(\"export token={}\", token);\n"
            "  23 |      return row;\n"
            "  24 |  }"
        ),
    }
    review = ReviewTask(
        review_id="rev_additive_rule_full_flow",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_additive_rule",
            project_id="proj",
            source_ref="feature/order-export",
            target_ref="main",
            title="订单导出新增字段",
            changed_files=[file_path],
            unified_diff=unified_diff,
        ),
        selected_experts=[expert.expert_id],
    )
    runner.review_repo.save(review)
    repository_context = {"routing_reason": "订单导出新增手机号、地址和 token 日志输出", "target_hunk": target_hunk}
    knowledge_context = runner._build_knowledge_review_context(
        review.subject,
        expert,
        file_path,
        20,
        repository_context,
        target_hunk,
    )
    bound_documents = runner.knowledge_service.retrieve_for_expert(expert.expert_id, knowledge_context)
    rule_screening = runner.knowledge_service.screen_rules_for_expert(expert.expert_id, knowledge_context)

    assert any(item.title == "订单导出附加产品规则" for item in bound_documents)
    assert rule_screening["total_rules"] >= 1
    matched_rule_ids = [str(item.get("rule_id") or "") for item in list(rule_screening["matched_rules_for_llm"])]
    assert "ORDER-SEC-001" in matched_rule_ids
    assert rule_screening["must_review_count"] >= 1
    system_prompt = runner._build_expert_system_prompt(
        expert,
        bound_documents,
        active_skills=[],
        rule_screening=rule_screening,
        analysis_mode="standard",
    )
    user_prompt = runner._build_expert_prompt(
        review.subject,
        expert,
        file_path,
        20,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context=repository_context,
        target_hunk=target_hunk,
        target_hunks=[target_hunk],
        bound_documents=bound_documents,
        disallowed_inference=[],
        expected_checks=["订单导出敏感字段脱敏"],
        active_skills=[],
        rule_screening=rule_screening,
        analysis_mode="standard",
    )
    assert "附加产品/仓库规则" in system_prompt
    assert "ORDER-SEC-001" in system_prompt
    assert "附加产品/仓库规则遍历结果" in user_prompt

    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.llm_chat_service,
        "complete_text",
        lambda **_kwargs: LLMTextResult(
            text=(
                '{"findings":['
                '{"ack":"收到","file_path":"%s","title":"订单导出明文输出手机号和地址",'
                '"finding_type":"direct_defect","normalized_issue_type":"sensitive_export_plaintext",'
                '"claim":"订单导出新增手机号和地址字段时直接输出原始值，违反附加产品规则和安全通用规范。",'
                '"severity":"medium","line_start":20,"line_end":21,'
                '"matched_rules":["ORDER-SEC-001 安全专家通用规范：敏感信息输出必须脱敏"],'
                '"violated_guidelines":["订单导出接口必须对手机号和地址做脱敏"],'
                '"rule_based_reasoning":"ORDER-SEC-001 要求订单导出敏感字段必须脱敏，当前 diff 直接写入 user.getPhone() 和 order.getAddress()。",'
                '"evidence":["新增 row.put(\\"phone\\", user.getPhone())","新增 row.put(\\"address\\", order.getAddress())"],'
                '"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"why_it_matters":"导出文件会暴露用户联系方式和地址","fix_strategy":"导出前统一调用脱敏工具",'
                '"suggested_fix":"使用 Masking.maskPhone 和 Masking.maskAddress 包装导出字段",'
                '"change_steps":["手机号脱敏","地址脱敏","补充导出断言"],'
                '"suggested_code":"row.put(\\"phone\\", Masking.maskPhone(user.getPhone()));\\nrow.put(\\"address\\", Masking.maskAddress(order.getAddress()));",'
                '"confidence":0.93,"verification_needed":false,"verification_plan":""},'
                '{"ack":"收到","file_path":"%s","title":"导出日志明文输出访问令牌",'
                '"finding_type":"direct_defect","normalized_issue_type":"sensitive_token_logged",'
                '"claim":"新增日志直接打印 token，违反安全专家通用规范。",'
                '"severity":"high","line_start":22,"line_end":22,'
                '"matched_rules":["SEC-FAKE-999","安全专家通用规范：访问令牌不得写入日志"],'
                '"violated_guidelines":["访问令牌不得进入应用日志"],'
                '"rule_based_reasoning":"安全通用规范要求 token 不得写入日志，当前 diff 新增 log.info 输出 token。",'
                '"evidence":["新增 log.info(\\"export token={}\\", token)","token 是访问令牌形态的敏感凭证"],'
                '"cross_file_evidence":[],"assumptions":[],"context_files":[],'
                '"why_it_matters":"日志可能被更多运维和系统读取，导致凭证泄露","fix_strategy":"删除 token 日志或只记录请求 ID",'
                '"suggested_fix":"不要打印 token，改为记录 requestId",'
                '"change_steps":["删除 token 日志","补充日志脱敏测试"],'
                '"suggested_code":"log.info(\\"export requestId={}\\", requestId);",'
                '"confidence":0.91,"verification_needed":false,"verification_plan":""}'
                ']}'
            )
            % (file_path, file_path),
            mode="mock",
            provider="test",
            model="test",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查订单导出新增字段的安全风险",
        metadata={"file_path": file_path, "line_start": 20, "target_hunk": target_hunk},
    )

    finding_payloads: list[dict[str, object]] = []
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path=file_path,
        line_start=20,
        repository_context=repository_context,
        target_hunk=target_hunk,
        target_hunks=[target_hunk],
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=bound_documents,
        knowledge_context=knowledge_context,
        rule_screening=rule_screening,
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 2
    export_finding = next(item for item in findings if "手机号" in item.title)
    token_finding = next(item for item in findings if "访问令牌" in item.title)
    export_attr = export_finding.code_context["rule_attribution"]
    token_attr = token_finding.code_context["rule_attribution"]

    assert export_finding.severity == "high"
    assert export_attr["valid_custom_rule_ids"] == ["ORDER-SEC-001"]
    assert export_attr["general_rules"] == ["安全专家通用规范：敏感信息输出必须脱敏"]
    assert export_attr["sources"] == ["expert_general", "product_or_repo_custom"]
    assert "ORDER-SEC-001" in export_finding.matched_rules
    assert "安全专家通用规范：敏感信息输出必须脱敏" in export_finding.matched_rules

    assert token_attr["valid_custom_rule_ids"] == []
    assert token_attr["invalid_custom_rule_ids"] == ["SEC-FAKE-999"]
    assert token_attr["normalized_matched_rules"] == ["安全专家通用规范：访问令牌不得写入日志"]
    assert token_finding.matched_rules == ["安全专家通用规范：访问令牌不得写入日志"]
    assert token_finding.assumptions

    messages = runner.message_repo.list(review.review_id)
    analysis_messages = [item for item in messages if item.message_type == "expert_analysis"]
    assert len(analysis_messages) == 2
    assert analysis_messages[0].metadata["rule_attribution"]
    assert analysis_messages[0].metadata["rule_screening"]["matched_rules_for_llm"][0]["rule_id"] == "ORDER-SEC-001"
    correction_events = [
        item for item in runner.event_repo.list(review.review_id)
        if item.event_type == "finding_rule_attribution_corrected"
    ]
    assert correction_events and correction_events[0].payload["invalid_custom_rule_ids"] == ["SEC-FAKE-999"]


def test_review_runner_semantic_line_candidates_parse_formatted_target_hunk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/main/java/com/example/OrderService.java",
        "start_line": 30,
        "end_line": 35,
        "changed_lines": [31, 33],
        "excerpt": (
            "# src/main/java/com/example/OrderService.java\n"
            "  30 |  public Order create(Command command) {\n"
            "  31 | +    validate(command);\n"
            "  32 |      Order order = mapper.toOrder(command);\n"
            "  33 | +    repository.saveWithoutTransaction(order);\n"
            "  34 |      return order;\n"
            "  35 |  }"
        ),
    }

    candidates = runner._extract_semantic_line_candidates(target_hunk)

    assert candidates == {
        31: ["validate(command);"],
        33: ["repository.saveWithoutTransaction(order);"],
    }


def test_issue_current_code_prefers_target_hunk_over_repository_source_context(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        finding_id="fdg_anchor",
        review_id="rev_anchor",
        expert_id="correctness_business",
        title="校验逻辑错误",
        summary="新增校验逻辑使用了错误条件。",
        finding_type="direct_defect",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=11,
        code_excerpt="# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
        code_context={
            "target_hunk": {
                "excerpt": "# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
            },
            "problem_source_context": {
                "snippet": "public Order create(Command command) {\n    legacyValidate(command);\n    return repository.save(command);\n}",
            },
        },
    )

    current_code = runner._extract_issue_current_code(finding)

    assert "+    validate(command);" in current_code
    assert "legacyValidate" not in current_code


def test_issue_current_code_prefers_focused_code_excerpt_over_large_hunk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    finding = ReviewFinding(
        finding_id="fdg_focus",
        review_id="rev_focus",
        expert_id="performance_reliability",
        title="批量查询存在循环内逐条调用风险",
        summary="listOrders 在批量输入下循环逐条调用 orderRepository.findById。",
        finding_type="direct_defect",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=22,
        code_excerpt="# src/main/java/com/example/OrderService.java\n  22 | +        orderRepository.findById(orderId);",
        code_context={
            "target_hunk": {
                "excerpt": "\n".join(
                    [
                        "# src/main/java/com/example/OrderService.java",
                        "  10 | + public List<OrderDTO> listOrders(List<Long> orderIds) {",
                        "  11 | +     List<OrderDTO> result = new ArrayList<>();",
                        "  12 | +     // TODO: 只返回当前登录用户有权限的订单",
                        "  13 | +     for (Long orderId : orderIds) {",
                        "  14 | +         Order order = orderRepository.findById(orderId);",
                        "  15 | +         result.add(toDTO(order));",
                        "  16 | +     }",
                        "  17 | +     return result;",
                        "  18 | + }",
                    ]
                ),
            },
        },
    )

    current_code = runner._extract_issue_current_code(finding)

    assert current_code == "# src/main/java/com/example/OrderService.java\n  22 | +        orderRepository.findById(orderId);"
    assert "TODO" not in current_code


def test_issue_consistency_baseline_prefers_finding_hunk_over_stale_issue_code(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_anchor",
        issue_id="iss_anchor",
        title="校验逻辑错误",
        summary="新增校验逻辑使用了错误条件。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=11,
        current_code="public class OrderService {\n    void oldCreate() { legacyValidate(command); }\n}",
        finding_ids=["fdg_anchor"],
    )
    finding = ReviewFinding(
        finding_id="fdg_anchor",
        review_id="rev_anchor",
        expert_id="correctness_business",
        title="校验逻辑错误",
        summary="新增校验逻辑使用了错误条件。",
        finding_type="direct_defect",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=11,
        code_excerpt="# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
        code_context={
            "target_hunk": {
                "excerpt": "# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
            },
        },
    )

    baseline = runner._build_issue_consistency_baseline(issue, [finding])

    assert "+    validate(command);" in baseline["current_code"]
    assert "legacyValidate" not in baseline["current_code"]


def test_issue_consistency_validation_keeps_diff_anchor_when_judge_returns_full_old_class(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_anchor",
        issue_id="iss_anchor",
        title="校验逻辑错误",
        summary="新增校验逻辑使用了错误条件。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=11,
        current_code="# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
        finding_ids=["fdg_anchor"],
    )
    baseline = {
        "title": issue.title,
        "summary": issue.summary,
        "normalized_issue_type": "validation_bug",
        "file_path": "src/main/java/com/example/OrderService.java",
        "line_start": 11,
        "remediation_strategy": "修正校验条件。",
        "remediation_suggestion": "使用新代码中的 validate(command)。",
        "remediation_steps": ["修正校验"],
        "current_code": "# src/main/java/com/example/OrderService.java\n  11 | +    validate(command);",
        "suggested_code": "",
    }
    payload = {
        "status": "repaired",
        "file_path": "src/main/java/com/example/OrderService.java",
        "line_start": 99,
        "current_code": (
            "public class OrderService {\n"
            "    public void oldCreate() { legacyValidate(command); }\n"
            "    public void helperA() {}\n"
            "    public void helperB() {}\n"
            "    public void helperC() {}\n"
            "}"
        ),
        "reason": "Judge 试图写回旧代码。",
    }

    updated, _summary = runner._apply_issue_consistency_validation(
        issue=issue,
        baseline=baseline,
        payload=payload,
    )

    assert updated.line_start == 11
    assert "+    validate(command);" in updated.current_code
    assert "legacyValidate" not in updated.current_code


def test_review_runner_build_finding_code_context_contains_diff_and_related_context(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    workspace_repo = storage_root / "workspace-repo"
    target_file = workspace_repo / "apps" / "api" / "order" / "order.service.ts"
    related_file = workspace_repo / "apps" / "api" / "order" / "order.controller.ts"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        "\n".join(
            [
                "export class OrderService {",
                "  async createOrder(payload) {",
                "    validateOrder(payload);",
                "    auditCreate(payload.id);",
                "    return payload;",
                "  }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    related_file.write_text(
        "\n".join(
            [
                "export class OrderController {",
                "  async create(req) {",
                "    return this.orderService.createOrder(req.body);",
                "  }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/context-payload",
        target_ref="main",
        changed_files=["apps/api/order/order.service.ts", "apps/api/order/order.controller.ts"],
        unified_diff=(
            "diff --git a/apps/api/order/order.service.ts b/apps/api/order/order.service.ts\n"
            "--- a/apps/api/order/order.service.ts\n"
            "+++ b/apps/api/order/order.service.ts\n"
            "@@ -4,2 +4,4 @@\n"
            "   async createOrder(payload) {\n"
            "+    validateOrder(payload);\n"
            "+    auditCreate(payload.id);\n"
            "   }\n"
            "diff --git a/apps/api/order/order.controller.ts b/apps/api/order/order.controller.ts\n"
            "--- a/apps/api/order/order.controller.ts\n"
            "+++ b/apps/api/order/order.controller.ts\n"
            "@@ -10,1 +10,1 @@\n"
            "-return this.orderService.createOrder(req.body)\n"
            "+return this.orderService.createOrder(req.body)\n"
        ),
        metadata={"trigger_source": "manual_real_case_test", "workspace_repo_path": str(workspace_repo)},
    )

    context = runner._build_finding_code_context(
        subject,
        "apps/api/order/order.service.ts",
        5,
        {
            "file_path": "apps/api/order/order.service.ts",
            "hunk_header": "@@ -4,2 +4,4 @@",
            "start_line": 4,
            "end_line": 6,
            "changed_lines": [5, 6],
            "excerpt": "+    validateOrder(payload);",
        },
        {
            "routing_reason": "需要检查 service 到 controller 的契约是否一致",
            "java_review_mode": "general",
            "java_context_signals": ["controller_entry", "transaction_boundary", "repository_dependency"],
            "primary_context": {
                "path": "apps/api/order/order.service.ts",
                "snippet": "   4 | async createOrder(payload) {\n   5 |   validateOrder(payload);\n   6 | }",
            },
            "related_contexts": [
                {
                    "path": "apps/api/order/order.controller.ts",
                    "snippet": "  10 | return this.orderService.createOrder(req.body);",
                }
            ],
            "symbol_contexts": [{"symbol": "createOrder", "definitions": [], "references": []}],
            "context_files": ["apps/api/order/order.service.ts", "apps/api/order/order.controller.ts"],
            "code_graph_context_source_summary": {"primary_source": "tree_sitter", "context_count": 2},
            "code_graph_minimal_context": {
                "summary": "Tree-sitter 图谱识别订单创建入口会影响控制器调用链。",
                "risk_level": "high",
                "risk_score": 0.82,
                "review_priorities": [{"reason": "入口调用链命中"}],
                "affected_flows": [
                    {
                        "entrypoint": "OrderController.create",
                        "changed_node": "OrderService.createOrder",
                        "criticality": 0.7,
                    }
                ],
            },
            "code_graph_impact_analysis": {
                "changed_nodes": [
                    {
                        "qualified_name": "OrderService.createOrder",
                        "file_path": "apps/api/order/order.service.ts",
                        "line_start": 4,
                    }
                ],
                "relationship_count": 1,
            },
            "code_graph_related_contexts": [
                {
                    "path": "apps/api/order/order.controller.ts",
                    "relationship": "calls",
                    "source_qualified_name": "OrderController.create",
                    "snippet": "return this.orderService.createOrder(req.body);",
                }
            ],
        },
    )

    assert "validateOrder(payload);" in str(context["target_file_full_diff"])
    assert "order.controller.ts" in str(context["related_diff_summary"])
    assert context["target_hunk"]["changed_lines"] == [5, 6]
    assert context["code_graph_source_summary"]["primary_source"] == "tree_sitter"
    assert context["code_graph_minimal_context"]["risk_level"] == "high"
    assert context["code_graph_evidence_chain"]
    assert {step["step"] for step in context["code_graph_evidence_chain"]} >= {
        "context_source",
        "minimal_context",
        "affected_flows",
        "graph_relationship",
    }


def test_review_runner_sorts_expert_jobs_by_code_graph_priority(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    jobs = [
        {"expert_id": "architecture_design", "file_path": "B.java", "line_start": 20, "code_graph_priority_score": 0.2},
        {"expert_id": "correctness_business", "file_path": "A.java", "line_start": 10, "code_graph_priority_score": 0.9},
        {"expert_id": "security", "file_path": "C.java", "line_start": 30, "code_graph_priority_score": 0.0},
    ]

    sorted_jobs = runner._sort_expert_jobs_by_code_graph_priority(jobs)

    assert [job["expert_id"] for job in sorted_jobs] == [
        "correctness_business",
        "architecture_design",
        "security",
    ]


def test_review_runner_enriches_issues_with_graph_evidence_chain(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_graph",
        title="订单创建入口缺少失败路径处理",
        summary="Tree-sitter 图谱命中入口调用链和变更节点。",
        finding_type="direct_defect",
        file_path="src/main/java/com/acme/OrderService.java",
        line_start=18,
        status="needs_human",
        severity="high",
        confidence=0.9,
        needs_human=True,
        finding_ids=["fdg_graph"],
    )
    finding_payloads = [
        {
            "finding_id": "fdg_graph",
            "context_source": "tree_sitter",
            "evidence_chain": [
                {"step": "minimal_context", "summary": "高风险入口变更"},
                {"step": "graph_relationship", "relationship": "calls"},
                {"step": "affected_flows", "flows": [{"entrypoint": "OrderController.submit"}]},
                {"step": "ignored_step", "summary": "不会透传"},
            ],
        }
    ]

    runner._enrich_issues_with_finding_evidence_chains([issue], finding_payloads)

    assert {step["step"] for step in issue.evidence_chain} == {"minimal_context", "graph_relationship", "affected_flows"}
    assert issue.confidence_breakdown["tree_sitter_context"] is True
    assert issue.tool_name == "tree_sitter_code_graph"
    assert issue.tool_verified is True


def test_review_runner_caches_repository_source_excerpt(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/cache-source",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff="diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n",
    )

    class _FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def is_ready(self) -> bool:
            return True

        def load_file_context(self, file_path: str, line_start: int, radius: int = 8) -> dict[str, object]:
            self.calls += 1
            return {"snippet": f"# {file_path}\n{line_start} | cached snippet"}

    fake_service = _FakeService()
    monkeypatch.setattr(
        review_runner_module.RepositoryContextService,
        "from_review_context",
        lambda **_kwargs: fake_service,
    )

    excerpt1 = runner._load_repository_source_excerpt(subject, "src/main/java/com/acme/OrderService.java", 18)
    excerpt2 = runner._load_repository_source_excerpt(subject, "src/main/java/com/acme/OrderService.java", 18)

    assert excerpt1 == excerpt2
    assert fake_service.calls == 1


def test_review_runner_caches_repository_problem_context(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/cache-problem",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff="diff --git a/src/main/java/com/acme/OrderService.java b/src/main/java/com/acme/OrderService.java\n",
    )

    class _FakeService:
        def __init__(self) -> None:
            self.calls = 0

        def is_ready(self) -> bool:
            return True

        def load_file_range(
            self,
            file_path: str,
            start_line: int,
            end_line: int,
            *,
            padding: int,
            expand_to_block: bool,
        ) -> dict[str, object]:
            self.calls += 1
            return {
                "path": file_path,
                "line_start": start_line,
                "line_end": end_line,
                "padding": padding,
                "expand_to_block": expand_to_block,
                "snippet": "cached block",
            }

    fake_service = _FakeService()
    monkeypatch.setattr(
        review_runner_module.RepositoryContextService,
        "from_review_context",
        lambda **_kwargs: fake_service,
    )

    hunk = {"start_line": 18, "end_line": 20, "changed_lines": [18, 19, 20], "excerpt": "+ repository.save(entity);"}
    context1 = runner._load_repository_problem_context(subject, "src/main/java/com/acme/OrderService.java", 18, hunk)
    context2 = runner._load_repository_problem_context(subject, "src/main/java/com/acme/OrderService.java", 18, hunk)

    assert context1 == context2
    assert fake_service.calls == 1


def test_review_runner_caches_repository_source_excerpt(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/cache-source",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff="",
    )

    calls = {"count": 0}

    class _FakeRepoService:
        def is_ready(self) -> bool:
            return True

        def load_file_context(self, file_path: str, line_start: int, radius: int = 8):
            calls["count"] += 1
            return {"snippet": f"{file_path}:{line_start}:{radius}"}

    monkeypatch.setattr(
        "app.services.review_runner.RepositoryContextService.from_review_context",
        lambda **_kwargs: _FakeRepoService(),
    )

    first = runner._load_repository_source_excerpt(subject, "src/main/java/com/acme/OrderService.java", 18, radius=8)
    second = runner._load_repository_source_excerpt(subject, "src/main/java/com/acme/OrderService.java", 18, radius=8)

    assert first == second == "src/main/java/com/acme/OrderService.java:18:8"
    assert calls["count"] == 1


def test_review_runner_caches_repository_problem_context(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/cache-problem",
        target_ref="main",
        changed_files=["src/main/java/com/acme/OrderService.java"],
        unified_diff="",
    )

    calls = {"count": 0}

    class _FakeRepoService:
        def is_ready(self) -> bool:
            return True

        def load_file_range(self, file_path: str, start_line: int, end_line: int, padding: int = 0, expand_to_block: bool = False):
            calls["count"] += 1
            return {
                "path": file_path,
                "line_start": start_line,
                "line_end": end_line,
                "padding": padding,
                "expand_to_block": expand_to_block,
                "snippet": f"{file_path}:{start_line}-{end_line}:{padding}",
            }

    monkeypatch.setattr(
        "app.services.review_runner.RepositoryContextService.from_review_context",
        lambda **_kwargs: _FakeRepoService(),
    )

    target_hunk = {
        "start_line": 18,
        "end_line": 19,
        "changed_lines": [18, 19],
    }
    first = runner._load_repository_problem_context(subject, "src/main/java/com/acme/OrderService.java", 18, target_hunk)
    second = runner._load_repository_problem_context(subject, "src/main/java/com/acme/OrderService.java", 18, target_hunk)

    assert first["snippet"] == second["snippet"]
    assert calls["count"] == 1


def test_review_runner_merge_repository_context_for_batch_keeps_observations_and_signals(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    merged = runner._merge_repository_context_for_batch(
        {
            "java_quality_signals": ["comment_contract_unimplemented"],
            "review_observations": [
                {
                    "observation_id": "obs_comment_001",
                    "kind": "declared_intent_without_implementation",
                    "signal": "comment_contract_unimplemented",
                    "file_path": "src/main/java/com/example/CourseCreator.java",
                    "line_start": 13,
                    "summary": "TODO 承诺未落地",
                    "evidence": ["// TODO 持久化后同步发送审计事件"],
                    "confidence": 0.9,
                }
            ],
        },
        {
            "java_quality_signals": ["loop_call_amplification"],
            "review_observations": [
                {
                    "observation_id": "obs_loop_001",
                    "kind": "control_flow_with_external_call",
                    "signal": "loop_call_amplification",
                    "file_path": "src/main/java/com/example/OrderBatchService.java",
                    "line_start": 16,
                    "summary": "循环体中的外部依赖调用",
                    "evidence": ["orderRepository.findByOrderNo(item.getOrderNo())"],
                    "confidence": 0.87,
                }
            ],
        },
    )

    assert set(merged["java_quality_signals"]) == {
        "comment_contract_unimplemented",
        "loop_call_amplification",
    }
    observations = merged["review_observations"]
    assert len(observations) == 2
    assert {item["observation_id"] for item in observations} == {"obs_comment_001", "obs_loop_001"}


def test_review_runner_runtime_repo_context_overrides_stale_command_context(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    merged = runner._merge_runtime_repository_context(
        {
            "related_contexts": [
                {
                    "path": "src/main/java/com/example/PetController.java",
                    "line_start": 1,
                    "snippet": "   1 | /*\n   2 |  * Copyright header",
                }
            ],
            "related_source_snippets": [],
            "symbol_contexts": [],
        },
        [
            {
                "tool_name": "repo_context_search",
                "related_contexts": [
                    {
                        "path": "src/main/java/com/example/PetController.java",
                        "line_start": 106,
                        "snippet": " 106 | public String processCreationForm(Owner owner, @Valid Pet pet, BindingResult result,",
                    }
                ],
                "related_source_snippets": [
                    {
                        "path": "src/main/java/com/example/PetController.java",
                        "symbol": "processCreationForm",
                        "kind": "reference",
                        "line_start": 106,
                        "snippet": " 106 | public String processCreationForm(Owner owner, @Valid Pet pet, BindingResult result,",
                    }
                ],
                "symbol_contexts": [
                    {
                        "symbol": "processCreationForm",
                        "references": [
                            {
                                "path": "src/main/java/com/example/PetController.java",
                                "line_number": 106,
                                "snippet": "public String processCreationForm(Owner owner, @Valid Pet pet, BindingResult result,",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert merged["related_contexts"][0]["line_start"] == 106
    assert merged["related_source_snippets"][0]["line_start"] == 106
    assert merged["symbol_contexts"][0]["symbol"] == "processCreationForm"


def test_review_runner_build_java_review_focus_switches_between_general_and_ddd(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    general_focus = runner._build_java_ddd_review_focus(
        "java",
        "architecture_design",
        {
            "java_review_mode": "general",
            "java_context_signals": ["controller_entry", "transaction_boundary", "repository_dependency"],
            "current_class_context": {"snippet": "public void create() {}", "path": "src/main/java/com/acme/UserService.java"},
            "caller_contexts": [{"path": "src/main/java/com/acme/UserController.java", "snippet": "userService.create();"}],
            "callee_contexts": [{"path": "src/main/java/com/acme/UserRepository.java", "snippet": "userRepository.insert();"}],
            "transaction_context": {"transactional_method": "create"},
            "persistence_contexts": [{"path": "src/main/resources/mapper/UserMapper.xml", "snippet": "<select />"}],
        },
    )
    ddd_focus = runner._build_java_ddd_review_focus(
        "java",
        "ddd_architecture",
        {
            "java_review_mode": "ddd_enhanced",
            "java_context_signals": ["ddd_package_layout", "domain_model_context", "domain_aggregate"],
            "current_class_context": {"snippet": "order.setStatus(CLOSED);", "path": "src/main/java/com/acme/order/app/OrderApplicationService.java"},
            "domain_model_contexts": [{"path": "src/main/java/com/acme/order/domain/OrderAggregate.java", "snippet": "class OrderAggregate {}", "symbol": "OrderAggregate"}],
        },
    )

    assert "Java 通用审查要求" in general_focus
    assert "Java 通用模式" in general_focus
    assert "命名是否表达真实业务语义" in general_focus
    assert "Java DDD 增强模式" in ddd_focus
    assert "Java DDD 增强要求" in ddd_focus


def test_review_runner_extract_design_alignment_returns_tool_payload(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    payload = runner._extract_design_alignment(
        [
            {"tool_name": "repo_context_search", "summary": "repo context ready"},
            {
                "tool_name": "design_spec_alignment",
                "design_alignment_status": "misaligned",
                "design_doc_titles": ["订单创建详细设计"],
                "matched_implementation_points": ["已实现 create order API"],
                "missing_implementation_points": ["缺少 currency 字段校验"],
                "extra_implementation_points": ["新增 debug 字段"],
                "conflicting_implementation_points": ["接口路径与设计文档不一致"],
            },
        ]
    )

    assert payload["design_alignment_status"] == "misaligned"
    assert payload["design_doc_titles"] == ["订单创建详细设计"]
    assert payload["missing_implementation_points"] == ["缺少 currency 字段校验"]


def test_review_runner_emits_design_skill_summary_message(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_design_summary",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/design-check",
            target_ref="main",
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        system_prompt="prompt",
    )
    runner._emit_skill_summary_messages(
        review=review,
        expert=expert,
        file_path="apps/api/order/order.service.ts",
        line_start=18,
        active_skills=[
            ReviewSkillProfile(
                skill_id="design-consistency-check",
                name="详细设计一致性检查",
                description="desc",
                required_tools=["design_spec_alignment"],
            )
        ],
        runtime_tool_results=[
            {
                "tool_name": "design_spec_alignment",
                "design_doc_titles": ["订单创建详细设计"],
                "design_alignment_status": "partially_aligned",
                "structured_design": {
                    "api_definitions": ["POST /api/orders"],
                    "response_fields": ["createdAt", "updatedAt"],
                    "table_definitions": ["orders(id, created_at, updated_at)"],
                    "business_sequences": ["创建订单后返回时间戳字段"],
                },
                "matched_implementation_points": ["已新增 createdAt 字段"],
                "missing_implementation_points": ["未补 updatedAt 映射"],
                "conflicting_implementation_points": ["transformer 未从源对象取值"],
                "uncertain_points": ["性能要求待专项验证"],
            }
        ],
        target_hunk={"hunk_header": "@@ -1,4 +1,8 @@"},
        target_hunks=[],
        runtime_settings=runner.runtime_settings_service.get(),
    )

    messages = SqliteMessageRepository(storage_root / "app.db").list("rev_design_summary")
    summary_message = next(item for item in messages if item.message_type == "expert_skill_call")
    assert summary_message.expert_id == "correctness_business"
    assert summary_message.metadata["skill_name"] == "design-consistency-check"
    assert summary_message.metadata["design_alignment_status"] == "partially_aligned"
    assert "已完成详细设计解析" in summary_message.content
    assert "POST /api/orders" in str(summary_message.metadata["skill_result"])


def test_review_runner_builds_routing_summary_with_system_fallback(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    enabled_experts = runner.registry.list_enabled()
    experts_by_id = {expert.expert_id: expert for expert in enabled_experts}

    summary = runner._build_routing_summary(
        selected_ids=["ddd_architecture"],
        experts_by_id=experts_by_id,
        skipped_experts=[
            {
                "expert_id": "ddd_architecture",
                "expert_name": "DDD架构专家",
                "reason": "当前 hunk 仅为 import 级调整",
            }
        ],
        effective_experts=[
            {
                "expert_id": "architecture_design",
                "expert_name": "通用编码规范专家",
                "source": "system_fallback",
            }
        ],
        system_added_experts=[
            {
                "expert_id": "architecture_design",
                "expert_name": "通用编码规范专家",
                "reason": "系统已自动补入通用编码规范专家作为兜底审查者",
            }
        ],
    )

    assert summary["fallback_expert_added"] is True
    assert summary["user_selected_experts"][0]["expert_id"] == "ddd_architecture"
    assert summary["system_added_experts"][0]["expert_id"] == "architecture_design"
    assert "自动补入" in runner._build_routing_summary_message(summary)


def test_review_runner_adds_architecture_fallback_job_when_all_selected_experts_skipped(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_fallback_demo",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_demo",
            project_id="proj_demo",
            source_ref="feature/demo",
            target_ref="main",
            title="Fallback review",
            changed_files=["src/demo.ts"],
            unified_diff=(
                "diff --git a/src/demo.ts b/src/demo.ts\n"
                "--- a/src/demo.ts\n"
                "+++ b/src/demo.ts\n"
                "@@ -1,2 +1,3 @@\n"
                " import { A } from './a'\n"
                "+import { B } from './b'\n"
                " export const demo = true\n"
            ),
        ),
        selected_experts=["ddd_architecture"],
    )

    job = runner._maybe_build_fallback_job(
        review=review,
        enabled_experts=runner.registry.list_enabled(),
        existing_jobs=[],
        selected_ids=["ddd_architecture"],
        skipped_experts=[
            {
                "expert_id": "ddd_architecture",
                "expert_name": "DDD架构专家",
                "reason": "当前 hunk 仅为 import 级调整",
            }
        ],
        effective_runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60.0, "max_attempts": 1},
        finding_payloads=[],
    )

    assert job is not None
    assert job["expert"].expert_id == "architecture_design"
    assert job["file_path"] == "src/demo.ts"


def test_review_runner_fails_when_remote_diff_is_missing(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.subject = review.subject.model_copy(update={"changed_files": [], "unified_diff": ""})
    runner.review_repo.save(review)

    updated = runner.run_once(review_id)

    assert updated.status == "failed"
    assert updated.phase == "failed"
    assert "无法继续审核" in (updated.failure_reason or "")


def test_review_runner_uses_light_mode_runtime_strategy(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    review = runner.review_repo.get(review_id)
    assert review is not None
    review.analysis_mode = "light"
    runner.review_repo.save(review)

    runtime = runner.runtime_settings_service.get().model_copy(
        update={
            "default_max_debate_rounds": 3,
            "light_max_debate_rounds": 1,
            "standard_max_parallel_experts": 4,
            "light_max_parallel_experts": 1,
            "review_quality_mode": "standard",
        }
    )

    effective = runner._effective_runtime_settings(runtime, "light")
    llm_options = runner._build_llm_request_options(runtime, "light")

    assert effective.default_max_debate_rounds == 1
    assert llm_options["timeout_seconds"] <= 90
    assert llm_options["max_attempts"] == 1
    assert runner._max_parallel_experts(runtime, "light") == 1


def test_thorough_review_light_mode_prioritizes_stable_llm_calls(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    runtime = runner.runtime_settings_service.get().model_copy(
        update={
            "review_quality_mode": "thorough_review",
            "light_llm_retry_count": 1,
            "light_max_parallel_experts": 1,
        }
    )

    llm_options = runner._build_llm_request_options(runtime, "light")

    assert llm_options["max_attempts"] >= 2
    assert runner._max_parallel_experts(runtime, "light") == 2


def test_review_runner_system_prompt_includes_review_spec(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库分析专家",
        role="database",
        enabled=True,
        system_prompt="你是数据库分析专家。",
        review_spec="# 数据库分析审视规范\n\n必须检查索引与 migration 风险。",
    )
    bound_docs = [
        KnowledgeDocument(
            title="数据库迁移补充规范",
            expert_id="database_analysis",
            doc_type="review_rule",
            content="补充要求：涉及 DDL 变更时必须评估锁表、回填和回滚路径。",
            source_filename="database-review.md",
        )
    ]

    prompt = runner._build_expert_system_prompt(expert, bound_docs)

    assert "《审视规范文档》开始" in prompt
    assert "数据库分析审视规范" in prompt
    assert "必须检查索引与 migration 风险" in prompt
    assert "《专家绑定参考文档》开始" in prompt
    assert "数据库迁移补充规范" in prompt
    assert "结构化审查步骤" in prompt
    assert "只针对已删除代码、历史旧代码或未变更代码下结论的 finding 必须丢弃" in prompt
    assert "置信度口径" in prompt
    assert "normalized_issue_type" in prompt


def test_review_runner_infers_normalized_issue_type(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    issue_type = runner._normalize_issue_type(
        {
            "title": "循环内逐条调用仓储导致放大",
            "claim": "for 循环里每个元素都执行 repository 查询，存在 N+1 风险",
            "matched_rules": ["PERF-LOOP-001"],
            "evidence": ["for (Order item : orders) { repository.findById(item.id()); }"],
        },
        "performance_reliability",
    )

    assert issue_type == "loop_call_amplification"


def test_review_runner_deterministic_loop_finding_keeps_call_symbol_in_summary(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_loop_symbol_summary",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/payment",
            target_ref="main",
            changed_files=["src/main/java/com/example/PaymentSettlementService.java"],
            unified_diff=(
                "diff --git a/src/main/java/com/example/PaymentSettlementService.java "
                "b/src/main/java/com/example/PaymentSettlementService.java\n"
            ),
        ),
        status="running",
        phase="expert_review",
    )
    monkeypatch.setattr(
        runner.diff_excerpt_service,
        "list_hunks",
        lambda *_args, **_kwargs: [
            {
                "start_line": 20,
                "excerpt": (
                    "+        for (Payment payment : payments) {\n"
                    "+            gateway.capture(payment);\n"
                    "+            paymentRepository.save(payment);\n"
                    "+        }"
                ),
            }
        ],
    )
    monkeypatch.setattr(
        runner.java_quality_signal_extractor,
        "extract",
        lambda **_kwargs: {
            "signals": ["loop_call_amplification"],
            "signal_terms": {
                "loop_call_amplification": [
                    "for (Payment payment : payments) {",
                    "paymentRepository.save",
                    "gateway.capture",
                ]
            },
        },
    )

    finding_payloads: list[dict[str, object]] = []
    runner._append_deterministic_java_quality_findings(review, finding_payloads)

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 1
    assert "paymentRepository.save" in findings[0].summary
    assert "循环" in findings[0].summary
    assert "paymentRepository.save" in findings[0].remediation_suggestion


def test_review_runner_system_prompt_prefers_matched_sections_over_full_document(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构专家",
        role="architecture",
        enabled=True,
        system_prompt="你是架构专家。",
        review_spec="# 架构规范\n\n必须关注依赖方向。",
    )
    bound_docs = [
        KnowledgeDocument(
            title="架构补充规范",
            expert_id="architecture_design",
            doc_type="review_rule",
            content="很长的原始全文，不应该整体注入。",
            source_filename="architecture-review.md",
            indexed_outline=["总则", "服务层", "仓储层"],
            matched_sections=[
                KnowledgeDocumentSection(
                    node_id="node-1",
                    doc_id="doc-1",
                    title="服务层",
                    path="总则 / 服务层",
                    summary="服务层禁止直接依赖基础设施实现。",
                    content="Service 不得直接 new 基础设施实现类。",
                )
            ],
        )
    ]

    prompt = runner._build_expert_system_prompt(expert, bound_docs)

    assert "总则 / 服务层" in prompt
    assert "Service 不得直接 new 基础设施实现类" in prompt


def test_review_runner_bound_document_metadata_prefers_matched_sections(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    metadata = runner._build_bound_document_metadata(
        [
            KnowledgeDocument(
                title="架构补充规范",
                expert_id="architecture_design",
                doc_type="review_rule",
                content="原始全文",
                source_filename="architecture-review.md",
                indexed_outline=["总则", "总则 / 服务层"],
                matched_sections=[
                    KnowledgeDocumentSection(
                        node_id="node-1",
                        doc_id="doc-1",
                        title="服务层",
                        path="总则 / 服务层",
                        summary="服务层禁止直接依赖基础设施实现。",
                        content="Service 不得直接 new 基础设施实现类。",
                        score=8.6,
                        matched_terms=["service", "基础设施"],
                        matched_signals=["query_terms:service", "query_terms:基础设施"],
                    )
                ],
            )
        ]
    )

    assert metadata
    assert metadata[0]["indexed_outline"] == ["总则", "总则 / 服务层"]
    assert metadata[0]["matched_sections"]
    assert metadata[0]["matched_sections"][0]["path"] == "总则 / 服务层"
    assert metadata[0]["matched_sections"][0]["matched_terms"] == ["service", "基础设施"]
    assert metadata[0]["matched_sections"][0]["matched_signals"] == ["query_terms:service", "query_terms:基础设施"]


def test_review_runner_system_prompt_uses_matched_sections_from_large_performance_doc(storage_root: Path):
    assert PERFORMANCE_SPEC_PATH.exists(), "长版性能规范文档尚未生成"
    raw_content = PERFORMANCE_SPEC_PATH.read_text(encoding="utf-8")
    assert len(raw_content.splitlines()) > 10000

    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能与可靠性专家",
        role="performance",
        enabled=True,
        system_prompt="你是性能与可靠性专家。",
        review_spec="# 性能规范\n\n必须关注超时、连接池和批处理。",
    )
    ingestion = runner.knowledge_service._ingestion
    retrieval = runner.knowledge_service._retrieval
    ingestion.ingest(
        KnowledgeDocument(
            title="性能与可靠性超长规范",
            expert_id="performance_reliability",
            doc_type="review_rule",
            content=raw_content,
            tags=["performance", "java", "jvm", "db", "cache"],
            source_filename=PERFORMANCE_SPEC_PATH.name,
        )
    )
    bound_docs = retrieval.retrieve(
        "performance_reliability",
        {
            "changed_files": ["infra/pool/hikari-pool-tuning.conf"],
            "query_terms": ["hikaricp", "maxpoolsize", "connectiontimeout", "validationtimeout"],
            "focus_file": "infra/pool/hikari-pool-tuning.conf",
            "focus_line": 88,
        },
    )

    prompt = runner._build_expert_system_prompt(expert, bound_docs)

    assert "HikariCP 连接池容量规划" in prompt
    assert "虚拟线程 pinning 风险正反例" not in prompt
    assert len(prompt) < len(raw_content) // 4


def test_review_runner_light_system_prompt_uses_summary_not_fulltext(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构专家",
        role="architecture",
        enabled=True,
        system_prompt="你是架构专家。",
        review_spec="# 架构规范\n\n必须关注依赖方向。",
    )
    bound_docs = [
        KnowledgeDocument(
            title="架构补充规范",
            expert_id="architecture_design",
            doc_type="review_rule",
            content="原始全文第一段。\n原始全文第二段。\n原始全文第三段。",
            source_filename="architecture-review.md",
            indexed_outline=["总则", "总则 / 服务层"],
            matched_sections=[
                KnowledgeDocumentSection(
                    node_id="node-1",
                    doc_id="doc-1",
                    title="服务层",
                    path="总则 / 服务层",
                    summary="服务层禁止直接依赖基础设施实现。",
                    content="Service 不得直接 new 基础设施实现类。",
                )
            ],
        )
    ]

    prompt = runner._build_expert_system_prompt(
        expert,
        bound_docs,
        [],
        {"total_rules": 2, "matched_rules_for_llm": [{"title": "应用层不得承载领域规则", "priority": "P1", "scene_path": "应用层/领域层", "reason": "命中服务边界变更"}]},
        analysis_mode="light",
    )

    assert "《专家绑定参考文档摘要》开始" in prompt
    assert "《规则遍历结果摘要》开始" in prompt
    assert "Service 不得直接 new 基础设施实现类" not in prompt
    assert "架构补充规范" in prompt


def test_review_runner_light_multi_file_prompt_deduplicates_repository_blocks(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构专家",
        role="architecture",
        enabled=True,
        system_prompt="你是架构专家。",
        review_spec="# 架构规范\n\n必须关注依赖方向。",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        title="test",
        changed_files=[
            "src/main/java/com/example/A.java",
            "src/main/java/com/example/B.java",
        ],
        unified_diff="diff --git a/src/main/java/com/example/A.java b/src/main/java/com/example/A.java",
    )
    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/example/A.java",
        10,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={
            "summary": "已补充大量仓库上下文",
            "primary_context": {"path": "src/main/java/com/example/A.java", "snippet": "line1\nline2\nline3"},
        },
        target_hunk={"excerpt": "10 | + change", "changed_lines": [10], "start_line": 10, "end_line": 10},
        target_hunks=[{"excerpt": "10 | + change", "changed_lines": [10], "start_line": 10, "end_line": 10}],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=[],
        active_skills=[],
        rule_screening={},
        analysis_mode="light",
        include_target_file_full_diff=False,
        include_related_diff_summary=False,
    )

    assert "详细源码片段已在“多文件联合审查补充”逐文件提供" in prompt


def test_review_runner_repository_source_blocks_follow_expert_context_priority(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    base_context = {
        "prompt_context_section_order": [
            "persistence_contexts",
            "transaction_context",
            "caller_contexts",
            "current_class_context",
        ],
        "primary_context": {
            "path": "src/main/java/com/example/OrderService.java",
            "snippet": "line1\nline2",
        },
        "current_class_context": {
            "path": "src/main/java/com/example/OrderService.java",
            "snippet": "class snippet",
        },
        "caller_contexts": [
            {
                "path": "src/main/java/com/example/OrderController.java",
                "symbol": "OrderController#create",
                "snippet": "caller snippet",
            }
        ],
        "persistence_contexts": [
            {
                "path": "src/main/java/com/example/OrderRepository.java",
                "symbol": "OrderRepository#save",
                "snippet": "persistence snippet",
            }
        ],
        "transaction_context": {
            "transactional_path": "src/main/java/com/example/OrderService.java",
            "transactional_method": "createOrder",
            "transaction_boundary_snippet": "@Transactional\npublic void createOrder() {}",
            "call_chain": ["OrderController#create", "OrderService#createOrder"],
        },
    }

    source_blocks = runner._build_repository_source_blocks(base_context, [])

    assert source_blocks.index("# 持久化上下文") < source_blocks.index("# 事务边界")
    assert source_blocks.index("# 事务边界") < source_blocks.index("# 调用方")
    assert source_blocks.index("# 调用方") < source_blocks.index("# 当前类问题片段")


def test_review_runner_light_prompt_context_trim_prioritizes_top_sections(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    repository_context = {
        "primary_context": {"path": "src/Main.java", "snippet": "main snippet"},
        "related_contexts": [{"path": f"src/A{i}.java", "snippet": "related"} for i in range(4)],
        "caller_contexts": [{"path": f"src/C{i}.java", "snippet": "caller"} for i in range(4)],
        "callee_contexts": [{"path": f"src/D{i}.java", "snippet": "callee"} for i in range(4)],
        "domain_model_contexts": [{"path": f"src/M{i}.java", "snippet": "domain"} for i in range(4)],
        "persistence_contexts": [{"path": f"src/P{i}.java", "snippet": "persistence"} for i in range(4)],
        "symbol_contexts": [{"symbol": f"S{i}", "definitions": [], "references": []} for i in range(4)],
        "review_observations": [
            {"observation_id": f"obs-{i}", "kind": "query_plan_risk", "summary": "observation", "confidence": 0.9}
            for i in range(3)
        ],
    }

    trimmed = runner._trim_prompt_repository_context_for_light(
        repository_context,
        ["persistence_contexts", "callee_contexts", "caller_contexts", "domain_model_contexts"],
        expert_id="performance_reliability",
        rule_screening={"matched_rules_for_llm": [{"rule_id": "PERF-SQL-001", "title": "SQL risk"}]},
    )

    assert len(trimmed["persistence_contexts"]) >= len(trimmed["domain_model_contexts"])
    assert len(trimmed["callee_contexts"]) >= len(trimmed["domain_model_contexts"])
    assert "primary_context" in trimmed
    assert "prompt_context_budget" in trimmed
    assert trimmed["prompt_context_budget"]["must_keep_blocks"] == ["primary_context:0"]
    assert "persistence_contexts:0" in trimmed["prompt_context_budget"]["kept_blocks"]


def test_review_runner_builds_prompt_repository_context_blocks(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    repository_context = {
        "primary_context": {
            "path": "src/main/java/com/example/OrderService.java",
            "snippet": "for (Long id : ids) { repository.findById(id); }",
        },
        "current_class_context": {
            "path": "src/main/java/com/example/OrderService.java",
            "snippet": "class OrderService { ... }",
        },
        "persistence_contexts": [
            {
                "path": "src/main/java/com/example/OrderRepository.java",
                "symbol": "OrderRepository#findById",
                "snippet": "Order findById(Long id);",
            }
        ],
        "review_observations": [
            {
                "observation_id": "obs-loop-1",
                "kind": "control_flow_with_external_call",
                "summary": "loop inside repository call",
                "confidence": 0.92,
            }
        ],
    }

    blocks = runner._build_prompt_repository_context_blocks(
        repository_context=repository_context,
        section_order=["persistence_contexts"],
        expert_id="performance_reliability",
        rule_screening={"matched_rules_for_llm": [{"rule_id": "PERF-001", "title": "Loop query"}]},
    )

    block_ids = {block.block_id for block in blocks}
    assert "primary_context:0" in block_ids
    assert "current_class_context:0" in block_ids
    assert "persistence_contexts:0" in block_ids
    assert "review_observations:0" in block_ids
    primary = next(block for block in blocks if block.block_id == "primary_context:0")
    observation = next(block for block in blocks if block.block_id == "review_observations:0")
    assert primary.must_keep is True
    assert primary.priority == "P0"
    assert observation.related_observation_ids == ["obs-loop-1"]


def test_review_runner_light_prompt_request_budget_keeps_core_sections(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    monkeypatch.setenv("REVIEW_LIGHT_EXPERT_REQUEST_BUDGET_TOKENS", "800")

    rendered, metadata = runner._apply_light_prompt_request_budget(
        expert_id="performance_reliability",
        include_target_file_full_diff=True,
        sections={
            "review_spec_summary": "review spec " * 30,
            "active_skill_summary": "active skill " * 20,
            "bound_documents_summary": "bound docs " * 20,
            "rule_screening_summary": "matched rules " * 30,
            "input_completeness_summary": "input completeness " * 30,
            "language_general_guidance": "java naming and transaction guidance " * 20,
            "design_doc_summary": "design summary " * 20,
            "hunk_summary": "target hunk " * 40,
            "hunk_batch_summary": "same file other hunks " * 30,
            "target_file_full_diff": "full diff " * 80,
            "related_diff_summary": "related diff " * 50,
            "runtime_tool_summary": "runtime tool " * 60,
            "repository_context_summary": "repo summary " * 60,
            "repository_source_blocks": "repo source " * 80,
            "code_excerpt": "current code " * 50,
            "observation_review_summary": "observation " * 40,
        },
    )

    assert rendered["hunk_summary"].strip()
    assert rendered["code_excerpt"].strip()
    assert rendered["review_spec_summary"].strip()
    assert rendered["rule_screening_summary"].strip()
    assert rendered["language_general_guidance"].strip()
    assert "hunk_summary" in metadata["must_keep_blocks"]
    assert "code_excerpt" in metadata["must_keep_blocks"]
    assert "review_spec_summary" in metadata["must_keep_blocks"]
    assert "rule_screening_summary" in metadata["must_keep_blocks"]
    assert "language_general_guidance" in metadata["must_keep_blocks"]
    assert metadata["used_budget"] >= 0
    assert metadata["total_budget"] == 800
    assert metadata["compressed_blocks"] or metadata["dropped_blocks"]


def test_review_runner_builds_knowledge_context_metadata(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    metadata = runner._build_knowledge_context_metadata(
        {
            "focus_file": "src/app/service/order_service.py",
            "focus_line": 42,
            "changed_files": ["src/app/service/order_service.py", "src/app/repository/order_repository.py"],
            "query_terms": ["order_service", "symbol_query", "routing_reason"],
            "knowledge_sources": ["knowledge_search", "repo_context_search"],
        }
    )

    assert metadata["focus_file"] == "src/app/service/order_service.py"
    assert metadata["focus_line"] == 42
    assert metadata["changed_files"] == [
        "src/app/service/order_service.py",
        "src/app/repository/order_repository.py",
    ]
    assert metadata["query_terms"] == ["order_service", "symbol_query", "routing_reason"]
    assert metadata["knowledge_sources"] == ["knowledge_search", "repo_context_search"]


def test_review_runner_prompt_includes_input_completeness_summary(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/input-contract",
        target_ref="main",
        changed_files=["src/main/java/com/example/UserController.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/UserController.java b/src/main/java/com/example/UserController.java\n"
            "--- a/src/main/java/com/example/UserController.java\n"
            "+++ b/src/main/java/com/example/UserController.java\n"
            "@@ -8,1 +8,1 @@\n"
            "-    create(request);\n"
            "+    create(request);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        enabled=True,
        focus_areas=["输入校验", "权限边界"],
        system_prompt="prompt",
        review_spec="入口必须完成参数校验与权限校验",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/example/UserController.java",
        8,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={
            "routing_reason": "入口参数校验变化",
            "primary_context": {"path": "src/main/java/com/example/UserController.java", "snippet": "8 | create(request);"},
            "related_contexts": [{"path": "src/main/java/com/example/UserService.java", "snippet": "12 | userService.create(request);"}],
        },
        target_hunk={"hunk_header": "@@ -8,1 +8,1 @@", "excerpt": "+    create(request);"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["不要假定隐藏的统一校验链路一定存在"],
        expected_checks=["检查入口校验与权限边界"],
        active_skills=[],
        rule_screening={
            "enabled_rules": 2,
            "matched_rules_for_llm": [{"rule_id": "SEC-JAVA-001", "title": "Java 入口必须保留显式校验"}],
        },
    )

    assert "输入完整性校验" in prompt
    assert "专家规范: 已提供" in prompt
    assert "语言通用规范提示: 已提供" in prompt
    assert "绑定规则: 1 条命中 / 2 条启用" in prompt
    assert "关联源码上下文: 1 段" in prompt
    assert "遵循 Java / Spring 通用代码规范" in prompt


def test_review_runner_build_expert_prompt_requests_comment_and_implementation_consistency_check(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/comment-contract",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
            "--- a/src/main/java/com/example/OrderService.java\n"
            "+++ b/src/main/java/com/example/OrderService.java\n"
            "@@ -20,1 +20,2 @@\n"
            "- // 创建订单后自动扣减库存\n"
            "+ // 创建订单后自动扣减库存\n"
            "+ return orderRepository.save(order);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性", "边界条件"],
        system_prompt="prompt",
        review_spec="关注业务行为与实现是否一致",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/example/OrderService.java",
        20,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={
            "routing_reason": "注释承诺了库存扣减行为，需要核对是否真正落地",
            "primary_context": {
                "path": "src/main/java/com/example/OrderService.java",
                "snippet": "20 | // 创建订单后自动扣减库存\n21 | return orderRepository.save(order);",
            },
            "related_contexts": [
                {
                    "path": "src/main/java/com/example/InventoryService.java",
                    "snippet": "33 | inventoryService.reserve(order.getSku(), order.getQuantity());",
                }
            ],
        },
        target_hunk={
            "hunk_header": "@@ -20,1 +20,2 @@",
            "excerpt": "+ // 创建订单后自动扣减库存\n+ return orderRepository.save(order);",
        },
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["证据不足时不要假定隐藏调用链已经实现"],
        expected_checks=["核对注释、方法意图与真实实现是否一致"],
        active_skills=[],
    )

    assert "注释、方法名、接口说明或 TODO 明确承诺了某个行为" in prompt
    assert "实现缺失或与承诺不一致" in prompt
    assert "阿里巴巴 Java 开发手册" in prompt
    assert "结构化观察点" in prompt
    assert "observation_ids" in prompt
    assert "跨文件影响提示" in prompt
    assert "审查阶段说明" in prompt
    assert "规则阶段" in prompt


def test_review_runner_prompt_includes_compact_review_learning_hints(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_learning",
        project_id="proj",
        source_ref="feature/comment-contract",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderRepository.java"],
    )
    ReviewLearningService(storage_root).record_issue_decision_case(
        review=ReviewTask(review_id="rev_old", subject=subject, status="completed"),
        issue=DebateIssue(
            review_id="rev_old",
            issue_id="iss_old",
            title="接口声明的方法未在实现类落地",
            summary="OrderRepository 接口定义方法但实现类没有实现。",
            normalized_issue_type="comment_contract_unimplemented",
            file_path="src/main/java/com/example/OrderRepository.java",
            line_start=12,
            evidence=[
                "JdbcOrderRepository implements OrderRepository。",
                "JdbcOrderRepository 中存在 @Override public List<Order> findActive() { return jdbc.query(...); }。",
            ],
            context_files=["src/main/java/com/example/JdbcOrderRepository.java"],
        ),
        decision="rejected",
        comment="误报：实现类已 implements 接口，并且 @Override 了同名方法。",
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        focus_areas=["业务正确性"],
        system_prompt="prompt",
        review_spec="关注业务行为与实现是否一致",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/example/OrderRepository.java",
        12,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={},
        target_hunk={
            "hunk_header": "@@ -12,1 +12,2 @@",
            "excerpt": "+ List<Order> findActive();",
        },
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=[],
        active_skills=[],
    )

    assert "历史人工反馈" in prompt
    assert "[误报样本]" in prompt
    assert "comment_contract_unimplemented" in prompt
    assert "不要报承诺未落地" in prompt


def test_review_runner_filters_issue_by_review_learning_case_judgement(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_learning",
        project_id="proj",
        source_ref="feature/comment-contract",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderRepository.java"],
    )
    learning_service = ReviewLearningService(storage_root)
    for index in range(3):
        learning_service.record_issue_decision_case(
            review=ReviewTask(review_id=f"rev_old_{index}", subject=subject, status="completed"),
            issue=DebateIssue(
                review_id=f"rev_old_{index}",
                issue_id=f"iss_old_{index}",
                title="接口声明的方法未在实现类落地",
                summary="OrderRepository 接口定义方法但实现类没有实现。",
                normalized_issue_type="comment_contract_unimplemented",
                file_path="src/main/java/com/example/OrderRepository.java",
                line_start=12 + index,
                evidence=[
                    "JdbcOrderRepository implements OrderRepository。",
                    "JdbcOrderRepository 中存在 @Override public List<Order> findActive() { return jdbc.query(...); }。",
                ],
                context_files=["src/main/java/com/example/JdbcOrderRepository.java"],
            ),
            decision="rejected",
            comment="误报：实现类已 implements 接口，并且 @Override 了同名方法。",
        )

    filtered, decisions = runner._apply_review_learning_case_judgement(
        repo_id="repo_learning",
        issues=[
            {
                "issue_id": "iss_new",
                "title": "接口声明的方法未在实现类落地",
                "summary": "OrderRepository.findActive 没有实现。",
                "normalized_issue_type": "comment_contract_unimplemented",
                "file_path": "src/main/java/com/example/OrderRepository.java",
                "line_start": 12,
                "claim": "接口承诺未落地。",
                "evidence": [
                    "JdbcOrderRepository implements OrderRepository。",
                    "JdbcOrderRepository 存在 @Override public List<Order> findActive() { return jdbc.query(...); }。",
                ],
                "context_files": ["src/main/java/com/example/JdbcOrderRepository.java"],
                "finding_ids": ["fdg_new"],
            }
        ],
        issue_filter_decisions=[],
    )

    assert filtered == []
    assert decisions[0]["rule_code"] == "review_learning_false_positive_case"
    assert decisions[0]["issue_id"] == "iss_new"


def test_review_runner_boosts_issue_by_confirmed_review_learning_case(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_learning",
        project_id="proj",
        source_ref="feature/auth-filter",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderRepository.java"],
    )
    learning_service = ReviewLearningService(storage_root)
    saved = None
    for index in range(2):
        saved = learning_service.record_issue_decision_case(
            review=ReviewTask(review_id=f"rev_old_auth_{index}", subject=subject, status="completed"),
            issue=DebateIssue(
                review_id=f"rev_old_auth_{index}",
                issue_id=f"iss_old_auth_{index}",
                title="查询缺少当前用户过滤",
                summary="queryActive 没有按当前用户过滤。",
                normalized_issue_type="missing_auth_check",
                file_path="src/main/java/com/example/OrderRepository.java",
                line_start=12 + index,
                evidence=["queryActive 方法没有传入 userId 条件。"],
                context_files=["src/main/java/com/example/OrderController.java"],
            ),
            decision="approved",
            comment="确认问题成立：缺少当前用户过滤会导致越权查询。",
        )
    assert saved is not None

    filtered, decisions = runner._apply_review_learning_case_judgement(
        repo_id="repo_learning",
        issues=[
            {
                "issue_id": "iss_new_auth",
                "title": "查询缺少当前用户过滤",
                "summary": "queryActive 没有按当前用户过滤。",
                "normalized_issue_type": "missing_auth_check",
                "file_path": "src/main/java/com/example/OrderRepository.java",
                "line_start": 12,
                "claim": "新增查询缺少 userId 条件，可能越权读取订单。",
                "evidence": ["queryActive 方法没有传入 userId 条件。"],
                "context_files": ["src/main/java/com/example/OrderController.java"],
                "confidence": 0.72,
                "finding_ids": ["fdg_auth"],
            }
        ],
        issue_filter_decisions=[],
    )

    assert decisions == []
    assert len(filtered) == 1
    assert filtered[0]["confidence"] == 0.76
    assert filtered[0]["confidence_breakdown"]["review_learning_case"]["action"] == "boost"
    refreshed_case = ReviewLearningService(storage_root).list_cases(repo_id="repo_learning", issue_type="missing_auth_check")[0]
    assert refreshed_case["match_count"] == 1


def test_review_runner_build_finding_code_context_includes_input_trace(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/context-payload",
        target_ref="main",
        changed_files=["src/main/java/com/example/UserService.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/UserService.java b/src/main/java/com/example/UserService.java\n"
            "--- a/src/main/java/com/example/UserService.java\n"
            "+++ b/src/main/java/com/example/UserService.java\n"
            "@@ -12,1 +12,4 @@\n"
            "+        for (String status : statuses) {\n"
            "+            userRepository.findByStatus(status);\n"
            "+        }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能专家",
        role="performance",
        enabled=True,
        focus_areas=["查询风险"],
        system_prompt="prompt",
        review_spec="检查事务边界与查询放大风险",
    )
    context = runner._build_finding_code_context(
        subject,
        "src/main/java/com/example/UserService.java",
        12,
        {
            "file_path": "src/main/java/com/example/UserService.java",
            "hunk_header": "@@ -12,1 +12,4 @@",
            "start_line": 12,
            "end_line": 15,
            "changed_lines": [12, 13, 14],
            "excerpt": "+        for (String status : statuses) {\n+            userRepository.findByStatus(status);\n+        }",
        },
        {
            "primary_context": {
                "path": "src/main/java/com/example/UserService.java",
                "snippet": "  12 | for (String status : statuses) {\n  13 |     userRepository.findByStatus(status);\n  14 | }",
            },
            "related_contexts": [
                {
                    "path": "src/main/java/com/example/UserRepository.java",
                    "snippet": "  20 | List<UserRecord> findByStatus(String status);",
                }
            ],
            "caller_contexts": [
                {
                    "path": "src/main/java/com/example/UserController.java",
                    "snippet": "  10 | userService.findByStatuses(statuses);",
                }
            ],
            "callee_contexts": [
                {
                    "path": "src/main/java/com/example/UserGateway.java",
                    "snippet": "  18 | gateway.fetch(status);",
                }
            ],
            "context_files": [
                "src/main/java/com/example/UserService.java",
                "src/main/java/com/example/UserRepository.java",
            ],
        },
        expert=expert,
        bound_documents=[],
        rule_screening={
            "enabled_rules": 3,
            "matched_rules_for_llm": [
                {"rule_id": "PERF-JAVA-001", "title": "查询接口必须显式分页或限流", "priority": "P1"}
            ],
        },
    )

    assert context["input_completeness"]["review_spec_present"] is True
    assert context["input_completeness"]["language_guidance_present"] is True
    assert context["input_completeness"]["matched_rule_count"] == 1
    assert context["input_completeness"]["related_context_count"] == 3
    assert context["review_inputs"]["expert_id"] == "performance_reliability"
    assert context["review_inputs"]["language_guidance_language"] == "java"
    assert context["review_inputs"]["language_guidance_present"] is True
    assert "事务与副作用" in context["review_inputs"]["language_guidance_topics"]
    assert context["review_inputs"]["matched_rules"][0]["rule_id"] == "PERF-JAVA-001"
    assert context["review_inputs"]["cross_file_impact_hints"]
    assert any("关联文件" in item or "调用链" in item for item in context["review_inputs"]["cross_file_impact_hints"])
    assert any("调用方入参" in item for item in context["review_inputs"]["cross_file_impact_hints"])
    assert any("调用方未随本次改动一起修改" in item for item in context["review_inputs"]["cross_file_impact_hints"])
    assert context["review_inputs"]["analysis_stages"]["rule_stage"]
    assert context["review_inputs"]["analysis_stages"]["llm_stage"]
    observations = context["review_observations"]
    assert isinstance(observations, list) and observations
    assert observations[0]["kind"] == "control_flow_with_external_call"


def test_review_runner_build_knowledge_review_context_includes_java_mode_and_signals(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Coding Standards",
        name_zh="通用编码规范专家",
        role="coding standards",
        enabled=True,
        focus_areas=["命名与表达"],
        system_prompt="prompt",
        knowledge_sources=["knowledge_search"],
        runtime_tool_bindings=[],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/java-mode",
        target_ref="main",
        changed_files=["src/main/java/com/acme/order/app/OrderApplicationService.java"],
    )

    context = runner._build_knowledge_review_context(
        subject,
        expert,
        "src/main/java/com/acme/order/app/OrderApplicationService.java",
        21,
        {
            "routing_reason": "应用服务可能越层修改领域状态",
            "java_review_mode": "ddd_enhanced",
            "java_context_signals": ["application_service_layer", "transaction_boundary", "domain_model_context"],
        },
        {
            "hunk_header": "@@ -15,9 +15,9 @@ public final class OrderApplicationService {",
            "excerpt": "\n".join(
                [
                    "-        Order order = Order.create(id, status);",
                    "+        Order order = new Order(id, status);",
                    "-        repository.save(order);",
                    "         eventBus.publish(order.pullDomainEvents());",
                    "+        repository.save(order);",
                ]
            ),
        },
    )

    assert "java_mode:ddd_enhanced" in context["query_terms"]
    assert "java_signal:application_service_layer" in context["query_terms"]
    assert "java_signal:transaction_boundary" in context["query_terms"]
    assert "java_signal:domain_model_context" in context["query_terms"]
    assert "java_quality:factory_bypass" in context["query_terms"]
    assert "java_quality:event_ordering_risk" in context["query_terms"]
    assert "java_term:create" in context["query_terms"]


def test_review_runner_apply_issue_consistency_validation_downgrades_conflicted_issue(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_demo",
        title="订单循环里逐条查库",
        summary="for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        status="resolved",
        severity="high",
        confidence=0.92,
        finding_ids=["fdg_demo"],
        participant_expert_ids=["performance_reliability"],
    )

    validated, metadata = runner._apply_issue_consistency_validation(
        issue=issue,
        baseline={
            "title": issue.title,
            "summary": issue.summary,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "remediation_strategy": "将逐条查库改成批量查询",
            "remediation_suggestion": "先批量查询订单，再在内存中组装。",
            "remediation_steps": ["抽取订单ID", "批量查询", "构建映射"],
            "current_code": "for (Long id : ids) { repository.findById(id); }",
            "suggested_code": "Map<Long, Order> orders = repository.findAllById(ids)...;",
        },
        payload={
            "status": "downgraded",
            "summary": "问题说明与当前代码、建议代码无法可靠对齐。",
            "file_path": "src/main/java/com/example/OrderService.java",
            "line_start": 42,
            "remediation_strategy": "将逐条查库改成批量查询",
            "remediation_suggestion": "先补齐准确代码片段，再给出修复代码。",
            "remediation_steps": ["核对真实问题代码", "再输出修复建议"],
            "current_code": "for (Long id : ids) { repository.findById(id); }",
            "suggested_code": "",
            "consistency_conflicts": ["建议代码修的是缓存问题，不是 N+1 查询问题。"],
            "reason": "当前 issue 内容存在明显冲突，需人工确认。",
        },
    )

    assert validated.status == "needs_human"
    assert validated.needs_human is True
    assert validated.resolution == "consistency_validation_failed"
    assert validated.consistency_check_status == "downgraded"
    assert validated.consistency_conflicts == ["建议代码修的是缓存问题，不是 N+1 查询问题。"]
    assert validated.suggested_code == ""
    assert "当前 issue 内容存在明显冲突" in str(metadata["summary"])


def test_review_runner_apply_issue_consistency_validation_ignores_drifted_judge_remediation(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_alignment_demo",
        title="精确匹配被改成模糊匹配",
        summary="builder.equal 被改成 builder.like，查询语义被静默放宽。",
        finding_type="direct_defect",
        normalized_issue_type="query_semantics_changed",
        file_path="src/shared/HibernateCriteriaConverter.java",
        line_start=63,
        status="resolved",
        severity="high",
        confidence=0.93,
        finding_ids=["fdg_alignment_demo"],
        participant_expert_ids=["database_analysis", "security_compliance"],
        expert_views=[
            {
                "expert_id": "database_analysis",
                "title": "精确匹配被改成模糊匹配",
                "summary": "builder.equal 被改成 builder.like，查询语义被静默放宽。",
                "normalized_issue_type": "query_semantics_changed",
            }
        ],
    )

    validated, metadata = runner._apply_issue_consistency_validation(
        issue=issue,
        baseline={
            "title": issue.title,
            "summary": issue.summary,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "normalized_issue_type": issue.normalized_issue_type,
            "remediation_strategy": "恢复精确匹配语义",
            "remediation_suggestion": "把 builder.like 改回 builder.equal。",
            "remediation_steps": ["恢复 equal 条件", "补充语义回归测试"],
            "current_code": 'return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            "suggested_code": "return builder.equal(root.get(filter.field().value()), filter.value().value());",
        },
        payload={
            "status": "passed",
            "title": issue.title,
            "summary": issue.summary,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "normalized_issue_type": "query_semantics_changed",
            "remediation_strategy": "补充权限校验",
            "remediation_suggestion": "在查询入口增加资源级鉴权和租户隔离校验。",
            "remediation_steps": ["增加权限判断", "补充鉴权测试"],
            "current_code": 'return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            "suggested_code": "authorize(user, resource);\nreturn builder.like(root.get(filter.field().value()), escapedValue);",
            "consistency_conflicts": [],
            "reason": "Judge 认为 issue 文案一致。",
        },
    )

    assert validated.remediation_alignment_status == "aligned"
    assert validated.remediation_filtered is False
    assert validated.remediation_strategy == "恢复精确匹配语义"
    assert validated.remediation_suggestion == "把 builder.like 改回 builder.equal。"
    assert validated.remediation_steps == ["恢复 equal 条件", "补充语义回归测试"]
    assert validated.suggested_code == "return builder.equal(root.get(filter.field().value()), filter.value().value());"
    assert metadata["updated_fields"] == ["remediation_strategy", "remediation_suggestion", "current_code", "suggested_code"]


def test_review_runner_apply_issue_consistency_validation_detects_query_anchor_conflict(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_query_anchor_demo",
        title="精确匹配被改成模糊匹配",
        summary="builder.equal 被改成 builder.like，查询语义被静默放宽。",
        finding_type="direct_defect",
        normalized_issue_type="query_semantics_changed",
        file_path="src/shared/HibernateCriteriaConverter.java",
        line_start=63,
        status="resolved",
        severity="high",
        confidence=0.93,
        finding_ids=["fdg_query_anchor_demo"],
        participant_expert_ids=["database_analysis"],
    )

    validated, metadata = runner._apply_issue_consistency_validation(
        issue=issue,
        baseline={
            "title": issue.title,
            "summary": issue.summary,
            "normalized_issue_type": issue.normalized_issue_type,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "remediation_strategy": "恢复精确匹配语义",
            "remediation_suggestion": "把 builder.like 改回 builder.equal。",
            "remediation_steps": ["恢复 equal 条件"],
            "current_code": 'return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            "suggested_code": "return builder.equal(root.get(filter.field().value()), filter.value().value());",
        },
        payload={
            "status": "passed",
            "title": issue.title,
            "summary": issue.summary,
            "normalized_issue_type": issue.normalized_issue_type,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "remediation_strategy": "恢复精确匹配语义",
            "remediation_suggestion": "把 builder.like 改回 builder.equal。",
            "remediation_steps": ["恢复 equal 条件"],
            "current_code": "return authService.authorize(user, resource);",
            "suggested_code": "auditLogger.info(\"authorized\");",
            "consistency_conflicts": [],
            "reason": "Judge 认为一致。",
        },
    )

    assert validated.status == "needs_human"
    assert validated.consistency_check_status == "downgraded"
    assert any("当前代码" in item for item in validated.consistency_conflicts)
    assert "锚点" in str(metadata["summary"])


def test_review_runner_coalesces_same_root_cause_issues_before_final_judge(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    query_correctness = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_query_correctness",
        title="equalsPredicateTransformer语义被从精确匹配改成模糊匹配",
        summary="builder.equal 被改成 builder.like，查询语义被静默放宽。",
        finding_type="direct_defect",
        normalized_issue_type="business_rule_broken",
        file_path="src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        line_start=63,
        status="needs_human",
        severity="blocker",
        confidence=0.98,
        finding_ids=["fdg_correctness"],
        participant_expert_ids=["correctness_business"],
        primary_expert_id="correctness_business",
    )
    query_database = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_query_database",
        title="等于过滤谓词被错误修改为前后模糊查询，破坏SQL语义并导致索引失效",
        summary="equals 查询被替换成 like 查询，精确匹配语义退化。",
        finding_type="direct_defect",
        normalized_issue_type="query_semantics_regression,missing_index_support",
        file_path=query_correctness.file_path,
        line_start=63,
        status="needs_human",
        severity="high",
        confidence=0.99,
        finding_ids=["fdg_database"],
        participant_expert_ids=["database_analysis"],
        primary_expert_id="database_analysis",
    )
    unrelated = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_naming",
        title="常量命名不符合规范且使用弱语义tmp后缀",
        summary="chunksTmp 是弱语义命名。",
        finding_type="direct_defect",
        normalized_issue_type="naming_misleading",
        file_path="src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        line_start=23,
        status="open",
        severity="medium",
        confidence=0.95,
        finding_ids=["fdg_naming"],
        participant_expert_ids=["architecture_design"],
        primary_expert_id="architecture_design",
    )

    issues = runner._coalesce_duplicate_issues([query_correctness, query_database, unrelated])

    assert len(issues) == 2
    merged = next(issue for issue in issues if issue.file_path == query_correctness.file_path)
    assert merged.normalized_issue_type == "query_semantics_regression"
    assert set(merged.finding_ids) == {"fdg_correctness", "fdg_database"}
    assert set(merged.participant_expert_ids) == {"correctness_business", "database_analysis"}
    assert merged.confidence == 0.99
    assert merged.severity == "blocker"


def test_review_runner_disables_issue_coalescing_in_thorough_review_mode(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    assert runner._should_coalesce_final_issues(SimpleNamespace(review_quality_mode="standard")) is True
    assert runner._should_coalesce_final_issues(SimpleNamespace(review_quality_mode="thorough_review")) is True


def test_review_runner_sanitizes_mixed_issue_candidate_to_current_anchor(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        "changed_lines": [9],
        "excerpt": "\n".join(
            [
                "@@ -9 +9 @@",
                "-    private static final Integer CHUNKS = 200;",
                "+    private final Integer chunksTmp = 200;",
            ]
        ),
    }
    parsed = {
        "title": "常量命名与使用违规",
        "claim": "发现多个通用编码规范问题：破坏约定的构造/工厂调用语义、常量命名/使用规范违规、异常处理被完全吞没、查询风险增加。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "naming_misleading",
        "severity": "medium",
        "confidence": 0.95,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "violated_guidelines": ["命名必须表达真实语义"],
        "evidence": [
            "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java:20 直接使用 new Course 替代 Course.create",
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:9 常量改为非 final 非全大写的弱语义变量 chunksTmp 且未使用",
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:26-28 移除了异常打印且吞掉异常后未做任何处理",
        ],
        "fix_strategy": "修复多个业务与正确性问题，包括领域事件、LIMIT 和异常处理。",
        "suggested_fix": "同时恢复 Course.create、LIMIT、异常处理和常量命名。",
        "change_steps": ["恢复 Course.create", "恢复 LIMIT", "恢复异常处理", "修正常量命名"],
        "suggested_code": "private static final Integer CHUNKS = 200;",
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "architecture_design",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        9,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert "发现多个" not in sanitized["claim"]
    assert "CourseCreator" not in "\n".join(sanitized["evidence"])
    assert "26-28" not in "\n".join(sanitized["evidence"])
    assert sanitized["fix_strategy"] == "修正常量命名与使用方式，使当前代码锚点只表达一个具体问题。"
    assert sanitized["suggested_fix"] == "将当前变更行恢复为符合命名、不可变性和实际使用语义的常量写法。"


def test_review_runner_sanitizes_mixed_title_and_evidence_to_anchor_domain(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        "changed_lines": [16],
        "excerpt": "\n".join(
            [
                "@@ -16 +16 @@",
                "-    return builder.equal(root.get(filter.field().value()), filter.value().value());",
                '+    return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            ]
        ),
    }
    parsed = {
        "title": "通用等值查询改为带前后通配符的模糊查询可能导致查询性能退化（查询语义退化）（静默吞掉异常）",
        "claim": "当前变更把 equalsPredicateTransformer 从 equal 改成 like。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "query_semantics_weakened",
        "severity": "high",
        "confidence": 0.72,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "violated_guidelines": ["查询语义不得静默放宽"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:26-29 删除了异常打印逻辑",
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java:16 equalsPredicateTransformer 从 builder.equal 改为 builder.like 并添加前后通配符",
        ],
        "suggested_code": 'return builder.equal(root.get(filter.field().value()), filter.value().value());',
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "performance_reliability",
        "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        16,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert "静默吞掉异常" not in sanitized["title"]
    assert "MySqlDomainEventsConsumer" not in "\n".join(sanitized["evidence"])
    assert "builder.like" in "\n".join(sanitized["evidence"])


def test_review_runner_sanitizes_single_foreign_domain_claim_to_anchor(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        "changed_lines": [16],
        "excerpt": "\n".join(
            [
                "@@ -16 +16 @@",
                "-    return builder.equal(root.get(filter.field().value()), filter.value().value());",
                '+    return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
            ]
        ),
    }
    parsed = {
        "title": "查询语义从精确匹配退化为模糊匹配",
        "claim": "当前变更还让 catch 块静默吞掉异常。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "query_semantics_weakened",
        "severity": "high",
        "confidence": 0.86,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "violated_guidelines": ["查询语义不得静默放宽"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java:16 equalsPredicateTransformer 从 builder.equal 改为 builder.like 并添加前后通配符",
        ],
        "suggested_code": 'return builder.equal(root.get(filter.field().value()), filter.value().value());',
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "performance_reliability",
        "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        16,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert "catch" not in sanitized["claim"].lower()
    assert "HibernateCriteriaConverter.java:16" in sanitized["claim"]


def test_review_runner_trusts_hunk_domain_over_wrong_model_issue_type(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        "changed_lines": [9],
        "excerpt": "\n".join(
            [
                "@@ -9 +9 @@",
                "-    private final Integer CHUNKS = 200;",
                "+    private final Integer chunksTmp = 200;",
            ]
        ),
    }
    parsed = {
        "title": "常量CHUNKS被重命名为chunksTmp，可能导致批处理逻辑直接引用失效（静默吞掉异常）",
        "claim": "CHUNKS 常量从清晰批处理边界名变成 chunksTmp，且当前 hunk 没有异常处理代码。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "exception_swallowed",
        "severity": "high",
        "confidence": 0.82,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "violated_guidelines": ["常量命名必须表达真实语义"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:9 CHUNKS 改成 chunksTmp",
        ],
        "suggested_code": "private final Integer CHUNKS = 200;",
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "correctness_business",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        9,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert "静默吞掉异常" not in sanitized["title"]
    assert sanitized["normalized_issue_type"] == "naming_misleading"


def test_review_runner_rewrites_domain_event_issue_type_for_naming_hunk(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        "changed_lines": [9],
        "excerpt": "\n".join(
            [
                "@@ -9 +9 @@",
                "-    private final Integer CHUNKS = 200;",
                "+    private final Integer chunksTmp = 200;",
            ]
        ),
    }
    parsed = {
        "title": "常量命名语义退化，使用了临时调试风格命名 chunksTmp",
        "claim": "CHUNKS 常量从清晰批处理边界名变成 chunksTmp。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "domain_event_missing",
        "severity": "medium",
        "confidence": 0.82,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "violated_guidelines": ["常量命名必须表达真实语义"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:9 CHUNKS 改成 chunksTmp",
        ],
        "suggested_code": "private final Integer CHUNKS = 200;",
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "architecture_design",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        9,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert sanitized["normalized_issue_type"] == "naming_misleading"


def test_review_runner_infers_naming_issue_type_before_domain_event_path_noise(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        "changed_lines": [9],
        "excerpt": "\n".join(
            [
                "@@ -9 +9 @@",
                "-    private final Integer CHUNKS = 200;",
                "+    private final Integer chunksTmp = 200;",
            ]
        ),
    }
    parsed = {
        "title": "常量命名使用了临时变量风格的驼峰式命名",
        "claim": "常量命名风格不符合规范，违反常量、枚举和变量命名必须表达真实语义的规则",
        "finding_type": "direct_defect",
        "normalized_issue_type": "",
        "severity": "medium",
        "confidence": 0.86,
        "matched_rules": ["CODE-JAVA-001"],
        "violated_guidelines": ["CODE-JAVA-001"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:9 将 CHUNKS 改成 chunksTmp",
        ],
        "suggested_code": "private final Integer CHUNKS = 200;",
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "architecture_design",
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        9,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert sanitized["normalized_issue_type"] == "naming_misleading"


def test_review_runner_infers_naming_issue_type_before_control_flow_context_noise(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "常量被改为临时过渡命名 chunksTmp，命名无清晰语义且不符合常量规范",
        "claim": "常量命名不符合规范。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "",
        "severity": "medium",
        "confidence": 0.82,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "evidence": [
            "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java:9 chunksTmp",
            "上下文里还有 while (true) 批处理循环，但当前问题锚点是常量命名。",
        ],
    }

    assert runner._normalize_issue_type(parsed, "architecture_design") == "naming_violation"


def test_review_runner_infers_query_semantics_before_naming_or_contract_noise(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "title": "equalsPredicateTransformer 查询语义从精确匹配放宽为模糊匹配",
        "claim": "方法名与实现存在不一致，但核心问题是 builder.equal 改为 builder.like。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "",
        "severity": "high",
        "confidence": 0.9,
        "matched_rules": ["GENERAL-EXPERT-CHECKS"],
        "evidence": [
            'HibernateCriteriaConverter.java:16 return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
        ],
    }

    assert runner._normalize_issue_type(parsed, "correctness_business") == "query_semantics_weakened"


def test_review_runner_uses_current_hunk_domain_before_batch_context_noise(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "file_path": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        "changed_lines": [20, 23],
        "excerpt": "\n".join(
            [
                "@@ -19,5 +19,5 @@",
                "-        Course course = Course.create(id, name, duration);",
                "+        Course course = new Course(id, name, duration);",
                "-        repository.save(course);",
                "         eventBus.publish(course.pullDomainEvents());",
                "+        repository.save(course);",
            ]
        ),
    }
    parsed = {
        "title": "课程创建未触发CourseCreatedDomainEvent领域事件",
        "claim": "CourseCreator.java:20 使用 new Course 绕过 Course.create。",
        "finding_type": "direct_defect",
        "normalized_issue_type": "",
        "severity": "high",
        "confidence": 0.9,
        "matched_rules": ["CORR-JDDD-002"],
        "violated_guidelines": ["CORR-JDDD-002"],
        "evidence": [
            "CourseCreator.java:20 使用 new Course",
            "同批上下文还包含 MySqlDomainEventsConsumer.java:19 移除 LIMIT，但当前候选不是查询边界问题。",
        ],
    }

    sanitized = runner._stabilize_expert_analysis(
        parsed,
        "correctness_business",
        "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
        20,
        target_hunk,
        repository_context={},
        input_completeness={"target_file_diff_present": True},
    )

    assert sanitized["normalized_issue_type"] == "aggregate_factory_bypass"


def test_review_runner_auto_confirms_high_confidence_direct_evidence_issue(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_direct",
        title="精确查询被改成模糊查询",
        summary="builder.equal 被改成 builder.like，代码证据直接成立。",
        finding_type="direct_defect",
        file_path="src/Query.java",
        line_start=42,
        status="needs_human",
        severity="blocker",
        confidence=0.97,
        needs_human=True,
        direct_evidence=True,
        evidence_chain=[{"step": "anchor"}, {"step": "verifier"}],
        consistency_check_status="passed",
    )

    updated, confirmed_ids = runner._auto_confirm_high_confidence_issues([issue])

    assert confirmed_ids == ["iss_direct"]
    assert updated[0].needs_human is False
    assert updated[0].status == "resolved"
    assert updated[0].resolution == "auto_confirmed_direct_evidence"
    assert updated[0].verified is True


def test_review_runner_keeps_conflicted_issue_in_human_gate(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_conflicted",
        title="修复建议与问题位置不一致",
        summary="问题证据和修复建议存在冲突。",
        finding_type="direct_defect",
        file_path="src/Query.java",
        line_start=42,
        status="needs_human",
        severity="high",
        confidence=0.98,
        needs_human=True,
        direct_evidence=True,
        evidence_chain=[{"step": "anchor"}, {"step": "verifier"}],
        consistency_check_status="downgraded",
        consistency_conflicts=["修复建议指向另一个文件"],
    )

    updated, confirmed_ids = runner._auto_confirm_high_confidence_issues([issue])

    assert confirmed_ids == []
    assert updated[0].needs_human is True
    assert updated[0].status == "needs_human"


def test_review_runner_coalesces_duplicate_event_consumer_exception_issues(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    file_path = "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
    empty_catch = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_empty_catch",
        title="空catch块吞掉事件处理异常，存在排障与补偿缺口（静默吞掉异常）",
        summary="catch 块删除 printStackTrace 后变为空 catch，异常被静默吞掉。",
        finding_type="direct_defect",
        normalized_issue_type="exception_swallowed",
        file_path=file_path,
        line_start=54,
        status="needs_human",
        severity="high",
        confidence=0.95,
        finding_ids=["fdg_empty_catch"],
        participant_expert_ids=["correctness_business"],
        primary_expert_id="correctness_business",
    )
    reflection_exception = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_reflection_exception",
        title="反射异常被完全吞掉，排障信息丢失（静默吞掉异常）",
        summary="NoSuchMethodException 等反射异常捕获后没有任何处理。",
        finding_type="direct_defect",
        normalized_issue_type="exception_semantics_weakened",
        file_path=file_path,
        line_start=54,
        status="needs_human",
        severity="high",
        confidence=0.9,
        finding_ids=["fdg_reflection_exception"],
        participant_expert_ids=["performance_reliability"],
        primary_expert_id="performance_reliability",
    )

    issues = runner._coalesce_duplicate_issues([empty_catch, reflection_exception])

    assert len(issues) == 1
    assert issues[0].normalized_issue_type == "event_consumer_exception_swallowed"
    assert set(issues[0].finding_ids) == {"fdg_empty_catch", "fdg_reflection_exception"}
    assert set(issues[0].participant_expert_ids) == {"correctness_business", "performance_reliability"}


def test_review_runner_coalesces_event_consumer_batch_boundary_across_lines(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    file_path = "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
    naming_issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_chunks_tmp",
        title="常量命名退化为 chunksTmp",
        summary="CHUNKS 被改成 chunksTmp，批量大小语义被弱化。",
        finding_type="direct_defect",
        normalized_issue_type="naming_misleading",
        file_path=file_path,
        line_start=23,
        status="open",
        severity="medium",
        confidence=0.91,
        finding_ids=["fdg_chunks_tmp"],
        participant_expert_ids=["architecture_design"],
        primary_expert_id="architecture_design",
    )
    query_bound_issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_query_bound_removed",
        title="事件消费查询删除 LIMIT :chunk 后失去批量边界",
        summary="SELECT 查询移除了 LIMIT :chunk，消费循环可能一次性拉取全部事件。",
        finding_type="direct_defect",
        normalized_issue_type="query_bound_removed",
        file_path=file_path,
        line_start=37,
        status="needs_human",
        severity="high",
        confidence=0.97,
        finding_ids=["fdg_query_bound"],
        participant_expert_ids=["database_analysis"],
        primary_expert_id="database_analysis",
    )

    issues = runner._coalesce_duplicate_issues([naming_issue, query_bound_issue])

    assert len(issues) == 1
    assert issues[0].normalized_issue_type == "event_consumer_batch_boundary"
    assert issues[0].severity == "high"
    assert set(issues[0].finding_ids) == {"fdg_chunks_tmp", "fdg_query_bound"}
    assert set(issues[0].participant_expert_ids) == {"architecture_design", "database_analysis"}
    assert "LIMIT :chunk" in issues[0].summary


def test_review_runner_refines_n_plus_one_anchor_to_semantic_changed_line(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunk = {
        "changed_lines": [19, 22, 30, 31, 32, 33, 34, 35, 36],
        "excerpt": "\n".join(
            [
                "# src/main/java/com/example/order/OrderService.java",
                "  19 | +        // TODO 按产品要求，只能返回当前登录用户有权限的订单，避免越权读取",
                "  22 | +            // 每个订单循环查询一次数据库，批量场景会触发 N+1 查询",
                "  31 | +    public void createOrder(OrderRequest request) {",
                "  32 | +        // 这里应该先校验库存并加库存锁，防止并发超卖",
                "  33 | +        Order order = new Order(request.getSkuId(), request.getQuantity());",
                "  34 | +        orderRepository.save(order);",
                "  35 | +        eventPublisher.publish(new OrderCreatedEvent(order.getId()));",
                "  36 | +    }",
            ]
        ),
    }
    parsed = {
        "title": "批量订单查询存在循环内逐条Repository调用的N+1风险",
        "claim": "循环内调用 orderRepository.findById 会触发 N+1 查询",
        "evidence": ["for (Long orderId : orderIds) { Order order = orderRepository.findById(orderId); }"],
        "line_start": 34,
    }

    assert runner._refine_line_start_within_hunk(parsed, target_hunk, 34) == 22


def test_review_runner_refines_stock_lock_anchor_across_changed_lines(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunks = [
        {
            "changed_lines": [19, 22, 30, 31, 32, 33, 34, 35, 36],
            "excerpt": "\n".join(
                [
                    "# src/main/java/com/example/order/OrderService.java",
                    "  19 | +        // TODO 按产品要求，只能返回当前登录用户有权限的订单，避免越权读取",
                    "  22 | +            // 每个订单循环查询一次数据库，批量场景会触发 N+1 查询",
                    "  31 | +    public void createOrder(OrderRequest request) {",
                    "  32 | +        // 这里应该先校验库存并加库存锁，防止并发超卖",
                    "  33 | +        Order order = new Order(request.getSkuId(), request.getQuantity());",
                    "  34 | +        orderRepository.save(order);",
                    "  35 | +        eventPublisher.publish(new OrderCreatedEvent(order.getId()));",
                    "  36 | +    }",
                ]
            ),
        }
    ]
    parsed = {
        "title": "createOrder 未实现注释承诺的库存校验与加锁",
        "claim": "库存校验与加锁未落地，存在并发超卖风险",
        "evidence": ["// 这里应该先校验库存并加库存锁，防止并发超卖"],
        "line_start": 19,
    }

    assert runner._refine_line_start_across_hunks(parsed, target_hunks, 19) == 32


def test_review_runner_coalesces_n_plus_one_findings_across_nearby_lines(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    file_path = "src/main/java/com/example/order/OrderService.java"
    performance_issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_perf_n_plus_one",
        title="批量订单查询存在循环内逐条Repository调用的N+1风险",
        summary="循环内调用 orderRepository.findById，批量输入会放大为 N 次数据库查询。",
        finding_type="direct_defect",
        normalized_issue_type="n_plus_one",
        file_path=file_path,
        line_start=22,
        status="open",
        severity="high",
        confidence=0.92,
        finding_ids=["fdg_perf"],
        participant_expert_ids=["performance_reliability"],
        primary_expert_id="performance_reliability",
    )
    maintainability_issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_maint_loop_call",
        title="循环调用放大",
        summary="循环内逐条 Repository 查询会增加排障和演进成本。",
        finding_type="direct_defect",
        normalized_issue_type="maintainability_regression",
        file_path=file_path,
        line_start=23,
        status="open",
        severity="medium",
        confidence=0.86,
        finding_ids=["fdg_maint"],
        participant_expert_ids=["maintainability_code_health"],
        primary_expert_id="maintainability_code_health",
    )

    issues = runner._coalesce_duplicate_issues([performance_issue, maintainability_issue])

    assert len(issues) == 1
    assert issues[0].normalized_issue_type == "n_plus_one"
    assert set(issues[0].finding_ids) == {"fdg_perf", "fdg_maint"}
    assert set(issues[0].participant_expert_ids) == {"performance_reliability", "maintainability_code_health"}


def test_review_runner_keeps_current_issue_text_when_coalescing_contract_summary(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_stock_lock",
        title="承诺未落地",
        summary="循环内逐条数据库查询会在批量输入时放大 I/O；新增的创建订单方法缺少库存前置校验与并发控制锁，高并发场景下可能引发超卖。",
        finding_type="direct_defect",
        normalized_issue_type="comment_contract_unimplemented",
        file_path="src/main/java/com/example/order/OrderService.java",
        line_start=32,
        status="open",
        severity="high",
        confidence=0.92,
        finding_ids=["fdg_stock"],
        participant_expert_ids=["correctness_business"],
        primary_expert_id="correctness_business",
        needs_human=True,
    )

    normalized = runner._coalesce_duplicate_issues([issue])

    assert normalized[0].title == "承诺未落地"
    assert "创建订单方法缺少库存前置校验与并发控制锁" in normalized[0].summary
    assert "N 次数据库访问" not in normalized[0].summary
    assert normalized[0].needs_human is True


def test_review_runner_appends_deterministic_query_bound_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_query_bound_demo",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/query-bound",
            target_ref="main",
            changed_files=[
                "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
            ],
            unified_diff="""diff --git a/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java b/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java
--- a/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java
+++ b/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java
@@ -37,11 +37,9 @@ public class MySqlDomainEventsConsumer {
 \tpublic void consume() {
 \t\twhile (!shouldStop) {
 \t\t\tNativeQuery query = sessionFactory.getCurrentSession().createNativeQuery(
-\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on LIMIT :chunk\"
+\t\t\t\t\"SELECT * FROM domain_events ORDER BY occurred_on\"
 \t\t\t);
-\t\t\tquery.setParameter(\"chunk\", CHUNKS);
 \t\t\tquery.list();
 \t\t}
 \t}
""",
        ),
        status="running",
        phase="expert_review",
    )
    finding_payloads: list[dict[str, object]] = []

    runner._append_deterministic_query_bound_findings(review, finding_payloads)

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 1
    assert findings[0].expert_id == "database_analysis"
    assert findings[0].normalized_issue_type == "query_bound_removed"
    assert findings[0].category_label == "data_access"
    assert "确定性规则信号" in findings[0].confidence_rationale
    assert findings[0].line_start == 37
    assert "LIMIT" in " ".join(findings[0].evidence)
    assert len(finding_payloads) == 1


def test_review_runner_builds_default_finding_category_and_confidence_rationale(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    category = runner._category_label_for_finding(
        finding_type="direct_defect",
        issue_type="exception_swallowed",
        expert_id="maintainability_code_health",
    )
    rationale = runner._confidence_rationale_for_finding(
        confidence=0.81,
        finding_type="direct_defect",
        evidence=["catch 块为空"],
        matched_rules=["JAVA-ERR-001"],
        verification_needed=True,
        code_context={"sast_cross_validated": True},
    )

    assert category == "exception_handling"
    assert "直接代码证据" in rationale
    assert "命中 1 条规则" in rationale
    assert "SAST/linter 交叉验证" in rationale
    assert "仍需复核" in rationale


def test_review_runner_does_not_add_comment_contract_observation_when_todo_is_implemented(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/comment-and-loop",
        target_ref="main",
        changed_files=["src/main/java/com/example/CourseCreator.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/example/CourseCreator.java b/src/main/java/com/example/CourseCreator.java\n"
            "--- a/src/main/java/com/example/CourseCreator.java\n"
            "+++ b/src/main/java/com/example/CourseCreator.java\n"
            "@@ -12,3 +12,4 @@ public final class CourseCreator {\n"
            "+        // TODO 持久化后同步发送审计事件\n"
            "         repository.save(course);\n"
            "         eventBus.publish(course.pullDomainEvents());\n"
        ),
    )
    repository_context = {
        "primary_context": {
            "path": "src/main/java/com/example/CourseCreator.java",
            "line_start": 12,
            "snippet": (
                "12 |     public void create(Course course) {\n"
                "13 |         // TODO 持久化后同步发送审计事件\n"
                "14 |         repository.save(course);\n"
                "15 |         eventBus.publish(course.pullDomainEvents());"
            ),
        }
    }

    enriched = runner._augment_repository_context_with_quality_signals(
        subject,
        "src/main/java/com/example/CourseCreator.java",
        13,
        repository_context,
        {
            "file_path": "src/main/java/com/example/CourseCreator.java",
            "start_line": 12,
            "end_line": 15,
            "excerpt": (
                "+        // TODO 持久化后同步发送审计事件\n"
                "         repository.save(course);\n"
                "         eventBus.publish(course.pullDomainEvents());"
            ),
        },
    )

    assert "comment_contract_unimplemented" not in enriched.get("java_quality_signals", [])
    assert not any(
        item.get("kind") == "declared_intent_without_implementation"
        for item in list(enriched.get("review_observations") or [])
    )


def test_review_runner_appends_deterministic_loop_observation_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_loop_obs_demo",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/loop-risk",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderBatchService.java"],
            unified_diff="",
        ),
        status="running",
        phase="expert_review",
    )
    finding_payloads: list[dict[str, object]] = []
    expert_jobs = [
        {
            "repository_context": {
                "review_observations": [
                    {
                        "observation_id": "obs_loop_001",
                        "kind": "control_flow_with_external_call",
                        "signal": "loop_call_amplification",
                        "file_path": "src/main/java/com/example/OrderBatchService.java",
                        "line_start": 40,
                        "summary": "检测到循环体中的外部依赖调用现象：for / orderRepository.findByOrderNo",
                        "evidence": [
                            "for (OrderItem item : items) {",
                            "orderRepository.findByOrderNo(item.getOrderNo());",
                        ],
                        "related_symbols": ["for", "orderRepository.findByOrderNo"],
                        "confidence": 0.87,
                    }
                ]
            }
        }
    ]

    runner._append_deterministic_observation_findings(review, expert_jobs, finding_payloads)

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 1
    assert findings[0].expert_id == "performance_reliability"
    assert findings[0].normalized_issue_type == "loop_call_amplification"
    assert findings[0].title == "循环调用放大"
    assert findings[0].line_start == 40
    assert len(finding_payloads) == 1


def test_review_runner_appends_deterministic_comment_contract_finding(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_comment_obs_demo",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/comment-gap",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderService.java"],
            unified_diff="",
        ),
        status="running",
        phase="expert_review",
    )
    finding_payloads: list[dict[str, object]] = []
    expert_jobs = [
        {
            "repository_context": {
                "review_observations": [
                    {
                        "observation_id": "obs_contract_001",
                        "kind": "declared_intent_without_implementation",
                        "signal": "comment_contract_unimplemented",
                        "file_path": "src/main/java/com/example/OrderService.java",
                        "line_start": 22,
                        "summary": "检测到注释、TODO、占位实现或方法意图与当前实现可能不一致",
                        "evidence": [
                            "// TODO: 创建订单后自动扣减库存并发送事件",
                            "return orderRepository.save(order);",
                        ],
                        "related_symbols": ["TODO", "create"],
                        "confidence": 0.9,
                    }
                ]
            }
        }
    ]

    runner._append_deterministic_observation_findings(review, expert_jobs, finding_payloads)

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 1
    assert findings[0].expert_id == "correctness_business"
    assert findings[0].normalized_issue_type == "comment_contract_unimplemented"
    assert findings[0].title == "承诺未落地"
    assert findings[0].line_start == 22
    assert len(finding_payloads) == 1


def test_review_runner_does_not_force_comment_contract_when_interface_is_implemented(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_interface_contract_implemented",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/interface-contract",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderEventPort.java"],
            unified_diff="",
        ),
        status="running",
        phase="expert_review",
    )
    finding_payloads: list[dict[str, object]] = []
    expert_jobs = [
        {
            "repository_context": {
                "review_observations": [
                    {
                        "observation_id": "obs_contract_implemented",
                        "kind": "declared_intent_without_implementation",
                        "signal": "comment_contract_unimplemented",
                        "file_path": "src/main/java/com/example/OrderEventPort.java",
                        "line_start": 12,
                        "summary": "接口声明了发送订单创建事件的承诺，但需要核对实现类是否落地。",
                        "evidence": [
                            "public interface OrderEventPort { void publishCreated(Order order); }",
                            "public class DefaultOrderEventPort implements OrderEventPort {",
                            "@Override public void publishCreated(Order order) { eventPublisher.publish(new OrderCreatedEvent(order.id())); }",
                        ],
                        "related_symbols": ["OrderEventPort", "publishCreated"],
                        "confidence": 0.9,
                    }
                ]
            }
        }
    ]

    runner._append_deterministic_observation_findings(review, expert_jobs, finding_payloads)

    assert runner.finding_repo.list(review.review_id) == []
    assert finding_payloads == []


def test_review_runner_normalizes_single_merged_query_issue_family(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_query_merged",
        title="同一代码行存在 3 个问题：equals操作被错误替换为模糊like",
        summary="问题汇总：equals 查询语义从精确匹配退化为模糊匹配，builder.like 会扩大结果集。",
        finding_type="direct_defect",
        normalized_issue_type="magic_value_overuse",
        file_path="src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java",
        line_start=63,
        finding_ids=["fdg_a", "fdg_b", "fdg_c"],
        participant_expert_ids=["architecture_design", "database_analysis", "correctness_business"],
    )

    normalized = runner._coalesce_duplicate_issues([issue])

    assert len(normalized) == 1
    assert normalized[0].normalized_issue_type == "query_semantics_regression"
    assert normalized[0].title == "查询语义从精确匹配退化为模糊匹配"


def test_review_runner_preserves_comment_contract_issue_family(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_comment_contract",
        title="承诺未落地",
        summary="TODO 承诺的审计事件没有真正实现。",
        finding_type="direct_defect",
        normalized_issue_type="course_creation_semantics",
        file_path="src/main/java/com/example/CourseCreator.java",
        line_start=13,
        finding_ids=["fdg_comment_contract"],
        participant_expert_ids=["correctness_business"],
    )

    normalized = runner._coalesce_duplicate_issues([issue])

    assert len(normalized) == 1
    assert normalized[0].normalized_issue_type == "comment_contract_unimplemented"
    assert normalized[0].title == "承诺未落地"


def test_review_runner_validate_final_issues_with_judge_emits_validation_message(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_demo",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/x",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderService.java"],
        ),
        status="running",
        phase="judge",
    )
    issue = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_demo",
        title="订单循环里逐条查库",
        summary="for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        status="resolved",
        severity="high",
        confidence=0.9,
        finding_ids=["fdg_demo"],
        participant_expert_ids=["performance_reliability"],
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_demo",
        expert_id="performance_reliability",
        title="循环中逐条查库",
        summary="for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        remediation_strategy="改成批量查询",
        remediation_suggestion="先批量查，再组装映射。",
        remediation_steps=["抽取ID", "批量查询", "组装Map"],
        code_excerpt="for (Long id : ids) { repository.findById(id); }",
        code_context={
            "problem_source_context": {
                "snippet": "for (Long id : ids) {\n    repository.findById(id);\n}",
            }
        },
        suggested_code="Map<Long, Order> orderMap = repository.findAllById(ids).stream()\n    .collect(Collectors.toMap(Order::getId, Function.identity()));",
    )

    def _fake_complete_text(**_kwargs):
        return LLMTextResult(
            text='{"results":[{"issue_id":"iss_demo","status":"repaired","title":"订单循环里逐条查库","summary":"for 循环中逐条调用 repository.findById，存在 N+1 查询风险。","file_path":"src/main/java/com/example/OrderService.java","line_start":42,"remediation_strategy":"改成批量查询","remediation_suggestion":"先批量查，再组装映射。","remediation_steps":["抽取ID","批量查询","组装Map"],"current_code":"for (Long id : ids) {\\n    repository.findById(id);\\n}","suggested_code":"Map<Long, Order> orderMap = repository.findAllById(ids).stream()\\n    .collect(Collectors.toMap(Order::getId, Function.identity()));","consistency_conflicts":[],"reason":"Judge 已修正 issue 文案与代码片段，四段内容现已一致。"}]}',
            mode="live",
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", _fake_complete_text)
    monkeypatch.setattr(
        runner.llm_chat_service,
        "resolve_main_agent",
        lambda _runtime: LLMResolution(
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            api_key="secret",
        ),
    )

    validated = runner._validate_final_issues_with_judge(
        review=review,
        issues=[issue],
        findings_by_id={"fdg_demo": finding},
        runtime_settings=runner.runtime_settings_service.get(),
        llm_request_options={"timeout_seconds": 30, "max_attempts": 1},
    )

    assert validated[0].consistency_check_status == "repaired"
    assert validated[0].current_code.startswith("for (Long id : ids)")
    assert validated[0].suggested_code.startswith("Map<Long, Order>")
    messages = runner.message_repo.list(review.review_id)
    validation_message = next(item for item in messages if item.message_type == "judge_consistency_validation")
    assert validation_message.metadata["validation_status"] == "repaired"
    assert "Judge 已修正 issue 文案与代码片段" in validation_message.content


def test_review_runner_judge_does_not_overwrite_confirmed_issue_details_with_drift_payload(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_business_rule",
        title="订单取消缺少库存回滚",
        summary="cancelOrder 新增分支只更新订单状态，没有调用 inventoryService.release 释放库存。",
        normalized_issue_type="business_rule_broken",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        status="resolved",
        severity="high",
        confidence=0.91,
        finding_ids=["fdg_business_rule"],
        participant_expert_ids=["correctness_business"],
        remediation_strategy="补齐取消订单的库存释放动作",
        remediation_suggestion="在订单状态更新成功后调用 inventoryService.release(orderId)。",
        remediation_steps=["更新订单状态", "释放库存", "补充取消订单回归测试"],
        current_code="order.cancel();\norderRepository.save(order);",
        suggested_code="order.cancel();\norderRepository.save(order);\ninventoryService.release(orderId);",
    )
    baseline = {
        "title": issue.title,
        "summary": issue.summary,
        "normalized_issue_type": issue.normalized_issue_type,
        "file_path": issue.file_path,
        "line_start": issue.line_start,
        "remediation_strategy": issue.remediation_strategy,
        "remediation_suggestion": issue.remediation_suggestion,
        "remediation_steps": issue.remediation_steps,
        "current_code": issue.current_code,
        "suggested_code": issue.suggested_code,
    }
    drift_payload = {
        "issue_id": issue.issue_id,
        "status": "repaired",
        "title": "循环中逐条查库",
        "summary": "for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
        "normalized_issue_type": "n_plus_one",
        "file_path": issue.file_path,
        "line_start": 99,
        "remediation_strategy": "改成批量查询",
        "remediation_suggestion": "使用 repository.findAllById 批量查询。",
        "remediation_steps": ["抽取ID", "批量查询"],
        "current_code": "for (Long id : ids) { repository.findById(id); }",
        "suggested_code": "Map<Long, Order> orderMap = repository.findAllById(ids);",
        "consistency_conflicts": [],
        "reason": "Judge 错误地漂移到了另一个问题。",
    }

    validated, metadata = runner._apply_issue_consistency_validation(
        issue=issue,
        baseline=baseline,
        payload=drift_payload,
    )

    assert validated.title == issue.title
    assert validated.summary == issue.summary
    assert validated.normalized_issue_type == issue.normalized_issue_type
    assert validated.line_start == issue.line_start
    assert validated.remediation_strategy == issue.remediation_strategy
    assert validated.remediation_suggestion == issue.remediation_suggestion
    assert validated.remediation_steps == issue.remediation_steps
    assert validated.current_code == issue.current_code
    assert validated.suggested_code == issue.suggested_code
    assert metadata["updated_fields"] == []


def test_review_runner_batches_issue_consistency_validation_by_file(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_demo",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/x",
            target_ref="main",
            changed_files=["src/main/java/com/example/OrderService.java"],
        ),
        status="running",
        phase="judge",
    )
    issues = [
        DebateIssue(
            review_id=review.review_id,
            issue_id="iss_demo_1",
            title="订单循环里逐条查库",
            summary="for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
            file_path="src/main/java/com/example/OrderService.java",
            line_start=42,
            status="resolved",
            severity="high",
            confidence=0.9,
            finding_ids=["fdg_demo_1"],
            participant_expert_ids=["performance_reliability"],
        ),
        DebateIssue(
            review_id=review.review_id,
            issue_id="iss_demo_2",
            title="循环中逐条远程调用",
            summary="for 循环中逐条调用 rpcClient.query，存在调用放大风险。",
            file_path="src/main/java/com/example/OrderService.java",
            line_start=58,
            status="resolved",
            severity="high",
            confidence=0.88,
            finding_ids=["fdg_demo_2"],
            participant_expert_ids=["performance_reliability"],
        ),
    ]
    findings_by_id = {
        "fdg_demo_1": ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_demo_1",
            expert_id="performance_reliability",
            title="循环中逐条查库",
            summary="for 循环中逐条调用 repository.findById，存在 N+1 查询风险。",
            file_path="src/main/java/com/example/OrderService.java",
            line_start=42,
            remediation_strategy="改成批量查询",
            remediation_suggestion="先批量查，再组装映射。",
            remediation_steps=["抽取ID", "批量查询"],
            code_excerpt="for (Long id : ids) { repository.findById(id); }",
            suggested_code="repository.findAllById(ids);",
        ),
        "fdg_demo_2": ReviewFinding(
            review_id=review.review_id,
            finding_id="fdg_demo_2",
            expert_id="performance_reliability",
            title="循环中逐条远程调用",
            summary="for 循环中逐条调用 rpcClient.query，存在调用放大风险。",
            file_path="src/main/java/com/example/OrderService.java",
            line_start=58,
            remediation_strategy="改成批量接口",
            remediation_suggestion="先聚合参数，再批量拉取。",
            remediation_steps=["聚合参数", "批量调用"],
            code_excerpt="for (Long id : ids) { rpcClient.query(id); }",
            suggested_code="rpcClient.batchQuery(ids);",
        ),
    }
    call_count = {"value": 0}

    def _fake_complete_text(**_kwargs):
        call_count["value"] += 1
        return LLMTextResult(
            text='{"results":[{"issue_id":"iss_demo_1","status":"passed","title":"订单循环里逐条查库","summary":"for 循环中逐条调用 repository.findById，存在 N+1 查询风险。","file_path":"src/main/java/com/example/OrderService.java","line_start":42,"remediation_strategy":"改成批量查询","remediation_suggestion":"先批量查，再组装映射。","remediation_steps":["抽取ID","批量查询"],"current_code":"for (Long id : ids) { repository.findById(id); }","suggested_code":"repository.findAllById(ids);","consistency_conflicts":[],"reason":"通过。"},{"issue_id":"iss_demo_2","status":"passed","title":"循环中逐条远程调用","summary":"for 循环中逐条调用 rpcClient.query，存在调用放大风险。","file_path":"src/main/java/com/example/OrderService.java","line_start":58,"remediation_strategy":"改成批量接口","remediation_suggestion":"先聚合参数，再批量拉取。","remediation_steps":["聚合参数","批量调用"],"current_code":"for (Long id : ids) { rpcClient.query(id); }","suggested_code":"rpcClient.batchQuery(ids);","consistency_conflicts":[],"reason":"通过。"}]}',
            mode="live",
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", _fake_complete_text)
    monkeypatch.setattr(
        runner.llm_chat_service,
        "resolve_main_agent",
        lambda _runtime: LLMResolution(
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            api_key="secret",
        ),
    )

    validated = runner._validate_final_issues_with_judge(
        review=review,
        issues=issues,
        findings_by_id=findings_by_id,
        runtime_settings=runner.runtime_settings_service.get(),
        llm_request_options={"timeout_seconds": 30, "max_attempts": 1},
    )

    assert call_count["value"] == 1
    assert len(validated) == 2


def test_review_runner_judge_repairs_empty_issue_suggested_code(storage_root: Path, monkeypatch):
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_judge_repairs_empty_suggested_code",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/bulk-save",
            target_ref="main",
            changed_files=["src/main/java/com/example/BulkEnrollmentService.java"],
        ),
        status="running",
        phase="judge",
    )
    issue = DebateIssue(
        review_id=review.review_id,
        issue_id="iss_bulk_save",
        title="批量写入退化为逐条保存",
        summary="批量报名路径把 saveAll 改成循环内逐条 repository.save，会放大数据库写入次数。",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=31,
        status="resolved",
        severity="high",
        confidence=0.92,
        finding_ids=["fdg_bulk_save"],
        participant_expert_ids=["performance_reliability"],
        current_code="for (CourseEnrollment enrollment : enrollments) {\n    repository.save(enrollment);\n}",
        suggested_code="",
    )
    finding = ReviewFinding(
        review_id=review.review_id,
        finding_id="fdg_bulk_save",
        expert_id="performance_reliability",
        title="循环调用放大",
        summary="批量报名路径循环内逐条 repository.save，应该恢复批量保存。",
        file_path="src/main/java/com/example/BulkEnrollmentService.java",
        line_start=31,
        remediation_strategy="恢复批量写入",
        remediation_suggestion="使用 repository.saveAll(enrollments) 代替循环内逐条 save。",
        remediation_steps=["构造 enrollments", "调用 saveAll", "补充批量失败测试"],
        code_excerpt="for (CourseEnrollment enrollment : enrollments) {\n    repository.save(enrollment);\n}",
        code_context={
            "problem_source_context": {
                "snippet": "for (CourseEnrollment enrollment : enrollments) {\n    repository.save(enrollment);\n}",
            },
            "target_hunk": {
                "excerpt": "+        for (CourseEnrollment enrollment : enrollments) {\n+            repository.save(enrollment);\n+        }",
            },
        },
        suggested_code="",
    )
    calls: list[str] = []

    def _fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        calls.append(phase)
        if phase == "judge_repair_suggested_code":
            text = (
                '{"suggested_code":"List<CourseEnrollment> enrollments = studentIds.stream()\\n'
                '    .map(studentId -> CourseEnrollment.create(courseId, studentId))\\n'
                '    .toList();\\n'
                'repository.saveAll(enrollments);"}'
            )
        else:
            text = (
                '{"results":[{"issue_id":"iss_bulk_save","status":"passed","title":"批量写入退化为逐条保存",'
                '"summary":"批量报名路径把 saveAll 改成循环内逐条 repository.save，会放大数据库写入次数。",'
                '"normalized_issue_type":"n_plus_one","file_path":"src/main/java/com/example/BulkEnrollmentService.java",'
                '"line_start":31,"remediation_strategy":"恢复批量写入","remediation_suggestion":"使用 repository.saveAll(enrollments) 代替循环内逐条 save。",'
                '"remediation_steps":["构造 enrollments","调用 saveAll"],'
                '"current_code":"for (CourseEnrollment enrollment : enrollments) {\\n    repository.save(enrollment);\\n}",'
                '"suggested_code":"","consistency_conflicts":[],"reason":"通过，但建议代码缺失。"}]}'
            )
        return LLMTextResult(
            text=text,
            mode="live",
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", _fake_complete_text)
    monkeypatch.setattr(
        runner.llm_chat_service,
        "resolve_main_agent",
        lambda _runtime: LLMResolution(
            provider="test",
            model="test-model",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            api_key="secret",
        ),
    )

    validated = runner._validate_final_issues_with_judge(
        review=review,
        issues=[issue],
        findings_by_id={"fdg_bulk_save": finding},
        runtime_settings=runner.runtime_settings_service.get(),
        llm_request_options={"timeout_seconds": 30, "max_attempts": 1},
    )

    assert validated[0].suggested_code
    assert "repository.saveAll(enrollments)" in validated[0].suggested_code
    assert "judge_repair_suggested_code" in calls
    messages = runner.message_repo.list(review.review_id)
    assert any(item.message_type == "judge_repair_suggested_code" for item in messages)
    assert all(item.consistency_check_status == "passed" for item in validated)


def test_review_runner_build_issue_consistency_validation_prompt_uses_compact_issue_payload(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    issue = DebateIssue(
        review_id="rev_demo",
        issue_id="iss_demo",
        title="批量查询缺失",
        summary="for 循环里逐条查库",
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        status="resolved",
        severity="high",
        confidence=0.91,
        finding_ids=["fdg_demo"],
        participant_expert_ids=["performance_reliability"],
        aggregated_titles=["不应出现在校验 prompt 里"],
        aggregated_summaries=["这类大字段不需要塞给 Judge"],
        evidence=["e1", "e2"],
    )
    finding = ReviewFinding(
        review_id="rev_demo",
        expert_id="performance_reliability",
        finding_id="fdg_demo",
        title="批量查询缺失",
        summary="for 循环里逐条查库",
        finding_type="direct_defect",
        severity="high",
        confidence=0.91,
        file_path="src/main/java/com/example/OrderService.java",
        line_start=42,
        remediation_strategy="改成批量查询",
        remediation_suggestion="先收集 ID 再批量查询",
        remediation_steps=["抽取 IDs", "批量查询"],
        code_excerpt="for (Long id : ids) { repository.findById(id); }",
        suggested_code="Map<Long, Order> orders = repository.findAllById(ids);",
    )

    prompt = runner._build_issue_consistency_validation_prompt(
        [
            {
                "issue": issue,
                "baseline": runner._build_issue_consistency_baseline(issue, [finding]),
                "related_findings": [finding],
            }
        ]
    )

    assert '"aggregated_titles"' not in prompt
    assert '"confidence_breakdown"' not in prompt
    assert '"issue_id": "iss_demo"' in prompt
    assert '"finding_ids": [' in prompt


def test_review_runner_rule_guided_prompt_states_bound_rules_and_profile_union(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/x",
        target_ref="main",
        title="测试 MR",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff="diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n@@ -1 +1 @@\n+return orders;",
    )
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="关注业务正确性、注释承诺和状态流转",
        system_prompt="只检查业务正确性。",
        review_spec="注释承诺必须落地。",
    )
    prompt = runner._build_rule_guided_expert_prompt(
        subject=subject,
        expert=expert,
        file_path="src/main/java/com/example/OrderService.java",
        line_start=12,
        runtime_tool_results=[],
        repository_context={},
        target_hunk={"file_path": "src/main/java/com/example/OrderService.java", "line_start": 12, "excerpt": "+return orders;"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["注释承诺是否落地"],
        active_skills=[],
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "CORR-CONTRACT-001",
                    "title": "注释承诺必须落地",
                    "must_check_items": ["检查 TODO 是否有对应实现"],
                    "normalized_issue_type": "comment_contract_unimplemented",
                }
            ],
            "enabled_rules": 1,
            "matched_rule_count": 1,
        },
        include_target_file_full_diff=True,
        include_related_diff_summary=True,
        max_context_chars=4000,
        max_rules_per_prompt=8,
    )

    assert "绑定规范和专家画像都要参与检视，候选结果取并集" in prompt
    assert "规则阶段：逐条检查 RULE_CARDS 和专家绑定规范" in prompt
    assert "通用阶段：按专家画像、专家审视规范和语言通用规范扫描目标 hunk" in prompt


def test_review_runner_user_facing_issue_summary_removes_internal_labels(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    summary = runner._build_merged_issue_summary(
        [
            "问题汇总：\n- createOrder 注释承诺先校验库存并加锁，但代码直接保存订单。 定向辩论预裁决：证据充分。",
            "修复建议汇总：\n- 这行不应该作为第二个问题标题展示。",
        ],
        ["修复建议汇总：\n- 在保存前补齐库存校验和加锁保护。"],
    )

    assert "问题汇总" not in summary
    assert "修复建议汇总" not in summary
    assert "定向辩论预裁决" not in summary
    assert summary == "createOrder 注释承诺先校验库存并加锁，但代码直接保存订单。\n建议：在保存前补齐库存校验和加锁保护。"


def test_review_runner_custom_rule_batches_ignore_unmatched_enabled_rules(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    batches = runner._split_custom_rule_scan_batches(
        {
            "matched_rules_for_llm": [
                {"rule_id": "PERF-SQL-002", "title": "N+1 查询风险必须在服务层被识别"}
            ],
            "all_enabled_rules_for_llm": [
                {"rule_id": "PERF-POOL-001", "title": "连接池扩容必须配套容量评估"}
            ],
            "possible_hit_rules": [
                {"rule_id": "PERF-JDDD-002", "title": "循环 Repository 查询必须识别 N+1"}
            ],
        },
        max_rules_per_batch=10,
    )

    rule_ids = [item["rule_id"] for batch in batches for item in batch]
    assert rule_ids == ["PERF-SQL-002", "PERF-JDDD-002"]
    assert "PERF-POOL-001" not in rule_ids


def test_review_runner_normalizes_mixed_candidate_by_refined_anchor(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    stock_candidate = runner._normalize_candidate_for_refined_anchor(
        {
            "title": "createOrder方法未实现库存校验与加锁，同时 listOrders 有 N+1",
            "claim": "本次改动存在两处问题：listOrders N+1；createOrder 缺少库存锁。",
            "normalized_issue_type": "query_bound_removed",
        },
        expert_id="correctness_business",
        line_start=32,
    )
    loop_candidate = runner._normalize_candidate_for_refined_anchor(
        {
            "title": "listOrders 方法在循环中逐条调用 repository.findById",
            "claim": "for 循环内调用 orderRepository.findById。",
            "normalized_issue_type": "query_bound_removed",
        },
        expert_id="database_analysis",
        line_start=22,
    )

    assert stock_candidate["title"] == "createOrder方法未实现库存校验与加锁，同时 listOrders 有 N+1"
    assert stock_candidate["normalized_issue_type"] == "comment_contract_unimplemented"
    assert "listOrders N+1" in stock_candidate["claim"]
    assert loop_candidate["title"] == "listOrders 方法在循环中逐条调用 repository.findById"
    assert loop_candidate["normalized_issue_type"] == "n_plus_one"


def test_review_runner_preserves_explicit_exception_type_when_candidate_mentions_save(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)

    candidate = runner._normalize_candidate_for_refined_anchor(
        {
            "title": "支付结算批处理异常被吞掉并返回成功",
            "claim": "catch RuntimeException 后仍返回 success，capture/save 失败会被误当成成功。",
            "normalized_issue_type": "exception_swallowed",
        },
        expert_id="correctness_business",
        line_start=28,
    )

    assert candidate["normalized_issue_type"] == "exception_swallowed"


def test_review_runner_refines_line_again_after_anchor_normalization(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    target_hunks = [
        {
            "changed_lines": [19, 22, 31, 32, 33, 34, 35],
            "excerpt": "\n".join(
                [
                    "  19 | +        // TODO 按产品要求，只能返回当前登录用户有权限的订单，避免越权读取",
                    "  22 | +            // 每个订单循环查询一次数据库，批量场景会触发 N+1 查询",
                    "  31 | +    public void createOrder(OrderRequest request) {",
                    "  32 | +        // 这里应该先校验库存并加库存锁，防止并发超卖",
                    "  33 | +        Order order = new Order(request.getSkuId(), request.getQuantity());",
                    "  34 | +        orderRepository.save(order);",
                    "  35 | +        eventPublisher.publish(new OrderCreatedEvent(order.getId()));",
                ]
            ),
        }
    ]

    stock = runner._normalize_candidate_for_refined_anchor(
        {"title": "createOrder 缺少库存锁", "claim": "createOrder 缺少库存校验与加锁。"},
        expert_id="correctness_business",
        line_start=19,
    )
    loop = runner._normalize_candidate_for_refined_anchor(
        {"title": "listOrders N+1", "claim": "循环里调用 repository.findById。"},
        expert_id="database_analysis",
        line_start=19,
    )

    assert runner._refine_line_start_across_hunks(stock, target_hunks, 19) == 32
    assert runner._refine_line_start_across_hunks(loop, target_hunks, 19) == 22
