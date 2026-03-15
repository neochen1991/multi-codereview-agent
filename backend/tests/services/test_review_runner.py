from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.finding import ReviewFinding
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.review_skill import ReviewSkillProfile
from app.repositories.file_expert_repository import FileExpertRepository
from app.services.review_runner import ReviewRunner


def test_review_runner_emits_finding_created_event(storage_root: Path):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    events = runner.list_events(review_id)
    assert any(event.event_type == "finding_created" for event in events)


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
        repository_context={"summary": "目标分支中存在 order controller 和 dto 实现。"},
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
