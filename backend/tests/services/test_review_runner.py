from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.knowledge import KnowledgeDocument, KnowledgeDocumentSection
from app.domain.models.finding import ReviewFinding
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_skill import ReviewSkillProfile
from app.repositories.file_expert_repository import FileExpertRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository
from app.services.llm_chat_service import LLMTextResult
from app.services.review_runner import ReviewRunner

PERFORMANCE_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-ultra-spec.md"
)


def test_review_runner_emits_finding_created_event(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    events = runner.list_events(review_id)
    assert any(event.event_type == "finding_created" for event in events)


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
    monkeypatch.setattr(
        runner.main_agent_service,
        "build_final_summary",
        lambda review, issues, runtime_settings, timeout_seconds, max_attempts: (
            "演示总结",
            {"provider": "test", "model": "test", "mode": "mock"},
        ),
    )

    runner.run_once(review_id)

    messages = runner.message_repo.list(review_id)
    issue_filter_message = next(item for item in messages if item.message_type == "issue_filter_applied")
    assert issue_filter_message.expert_id == "main_agent"
    assert "未升级为 issues" in issue_filter_message.content
    decisions = issue_filter_message.metadata.get("issue_filter_decisions", [])
    assert isinstance(decisions, list) and decisions
    assert decisions[0]["rule_code"] == "hint_like_medium"


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
        analysis_mode="light",
        llm_request_options={"timeout_seconds": 1, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={},
        finding_payloads=finding_payloads,
    )

    findings = runner.finding_repo.list(review.review_id)
    assert len(findings) == 2
    assert len(finding_payloads) == 2


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


def test_review_runner_builds_jobs_for_all_candidate_hunks(storage_root: Path, monkeypatch):
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

    assert len(recorded_jobs) == 2
    assert {int(job["line_start"]) for job in recorded_jobs} == {18, 42}


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
    assert any("constructor" in item for item in stabilized["assumptions"])


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
    assert any("完整方法/类定义" in item for item in stabilized["assumptions"])


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
    assert "先补齐 语言通用规范提示 / 关联源码上下文" in str(stabilized["verification_plan"])


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
        selected_experts=["ddd_specification"],
    )
    expert = ExpertProfile(
        expert_id="ddd_specification",
        name="DDD",
        name_zh="DDD规范专家",
        role="ddd",
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
        "ddd_specification",
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

    assert "aggregate factory bypass" in str(stabilized["title"]).lower()
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
        name="Architecture",
        name_zh="架构与设计",
        role="architecture",
        enabled=True,
        focus_areas=["DDD"],
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
                {"expert_id": "architecture_design", "expert_name": "架构与设计", "reason": "命中架构风险"},
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
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["检查跨文件调用链上的输入校验与业务约束"],
        active_skills=[],
    )

    assert "validateOrder(payload);" in prompt
    assert "auditCreate(payload.id);" in prompt
    assert "return this.orderService.createOrder(req.body);" in prompt
    assert "createOrder" in prompt


def test_review_runner_build_code_excerpt_prefers_repository_source_context(storage_root: Path):
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

    assert "constructor(private readonly client: HttpClient)" in excerpt
    assert "auditCreate(payload.id);" in excerpt
    assert "return this.client.post('/orders', payload);" in excerpt


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
        },
    )

    assert "validateOrder(payload);" in str(context["target_file_full_diff"])
    assert "order.controller.ts" in str(context["related_diff_summary"])
    assert context["target_hunk"]["changed_lines"] == [5, 6]
    assert "auditCreate(payload.id);" in str(context["problem_source_context"]["snippet"])
    assert "return payload;" in str(context["source_file_context"])
    assert context["java_review_mode"] == "general"
    assert context["java_context_signals"] == ["controller_entry", "transaction_boundary", "repository_dependency"]
    assert len(context["related_contexts"]) == 1
    assert context["symbol_contexts"][0]["symbol"] == "createOrder"


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
        "ddd_specification",
        {
            "java_review_mode": "ddd_enhanced",
            "java_context_signals": ["ddd_package_layout", "domain_model_context", "domain_aggregate"],
            "current_class_context": {"snippet": "order.setStatus(CLOSED);", "path": "src/main/java/com/acme/order/app/OrderApplicationService.java"},
            "domain_model_contexts": [{"path": "src/main/java/com/acme/order/domain/OrderAggregate.java", "snippet": "class OrderAggregate {}", "symbol": "OrderAggregate"}],
        },
    )

    assert "Java 通用审查要求" in general_focus
    assert "Java 通用模式" in general_focus
    assert "聚合边界是否被破坏" in general_focus
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
        selected_ids=["ddd_specification"],
        experts_by_id=experts_by_id,
        skipped_experts=[
            {
                "expert_id": "ddd_specification",
                "expert_name": "DDD规范专家",
                "reason": "当前 hunk 仅为 import 级调整",
            }
        ],
        effective_experts=[
            {
                "expert_id": "architecture_design",
                "expert_name": "架构与设计专家",
                "source": "system_fallback",
            }
        ],
        system_added_experts=[
            {
                "expert_id": "architecture_design",
                "expert_name": "架构与设计专家",
                "reason": "系统已自动补入架构与设计专家作为兜底审查者",
            }
        ],
    )

    assert summary["fallback_expert_added"] is True
    assert summary["user_selected_experts"][0]["expert_id"] == "ddd_specification"
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
        selected_experts=["ddd_specification"],
    )

    job = runner._maybe_build_fallback_job(
        review=review,
        enabled_experts=runner.registry.list_enabled(),
        existing_jobs=[],
        selected_ids=["ddd_specification"],
        skipped_experts=[
            {
                "expert_id": "ddd_specification",
                "expert_name": "DDD规范专家",
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
        }
    )

    effective = runner._effective_runtime_settings(runtime, "light")
    llm_options = runner._build_llm_request_options(runtime, "light")

    assert effective.default_max_debate_rounds == 1
    assert llm_options["timeout_seconds"] >= 120
    assert runner._max_parallel_experts(runtime, "light") == 1


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
            "@@ -12,1 +12,2 @@\n"
            "-        userRepository.findByStatus(status);\n"
            "+        validate(status);\n"
            "+        userRepository.findByStatus(status);\n"
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
            "hunk_header": "@@ -12,1 +12,2 @@",
            "start_line": 12,
            "end_line": 13,
            "changed_lines": [12, 13],
            "excerpt": "+        userRepository.findByStatus(status);",
        },
        {
            "primary_context": {
                "path": "src/main/java/com/example/UserService.java",
                "snippet": "  12 | validate(status);\n  13 | userRepository.findByStatus(status);",
            },
            "related_contexts": [
                {
                    "path": "src/main/java/com/example/UserRepository.java",
                    "snippet": "  20 | List<UserRecord> findByStatus(String status);",
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
    assert context["input_completeness"]["related_context_count"] == 1
    assert context["review_inputs"]["expert_id"] == "performance_reliability"
    assert context["review_inputs"]["language_guidance_language"] == "java"
    assert context["review_inputs"]["language_guidance_present"] is True
    assert "事务与副作用" in context["review_inputs"]["language_guidance_topics"]
    assert context["review_inputs"]["matched_rules"][0]["rule_id"] == "PERF-JAVA-001"


def test_review_runner_build_knowledge_review_context_includes_java_mode_and_signals(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="architecture_design",
        name="Architecture",
        name_zh="架构与设计专家",
        role="architecture",
        enabled=True,
        focus_areas=["分层边界"],
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
