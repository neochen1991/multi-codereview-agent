from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
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
          "context_files": ["packages/lib/schedules/getScheduleListItemData.ts"],
          "assumptions": ["当前只看到了局部 diff"],
          "claim": "存在跨文件语义漂移风险"
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
    assert parsed["context_files"] == ["packages/lib/schedules/getScheduleListItemData.ts"]
    assert parsed["assumptions"] == ["当前只看到了局部 diff"]


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
                "skill_name": "repo_context_search",
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
    assert "没有可执行的专家" in (review.failure_reason or "")
    assert any(event.event_type == "review_failed" for event in runner.list_events(review_id))


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
    assert "未获取到真实 diff" in (updated.failure_reason or "")
