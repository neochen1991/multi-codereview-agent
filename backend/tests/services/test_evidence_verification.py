from app.services.orchestrator.nodes.evidence_verification import _pick_verification_strategy


def test_evidence_verification_prefers_local_diff_for_correctness_transformer_issue():
    issue = {
        "finding_type": "risk_hypothesis",
        "file_path": "packages/lib/schedules/transformers/getScheduleListItemData.ts",
        "participant_expert_ids": ["correctness_business"],
        "evidence": ["transformer 未同步更新"],
        "topic": "packages/lib/schedules/transformers/getScheduleListItemData.ts::1",
    }

    assert _pick_verification_strategy(issue) == "local_diff"


def test_evidence_verification_prefers_schema_diff_for_database_issue():
    issue = {
        "finding_type": "risk_hypothesis",
        "file_path": "packages/prisma/schema.prisma",
        "participant_expert_ids": ["database_analysis"],
        "evidence": ["database_migration"],
        "topic": "packages/prisma/schema.prisma::40",
    }

    assert _pick_verification_strategy(issue) == "schema_diff"


def test_evidence_verification_prefers_coverage_diff_for_test_gap():
    issue = {
        "finding_type": "test_gap",
        "file_path": "packages/platform/types/schedules/schedules-2024-06-11/outputs/schedule.output.ts",
        "participant_expert_ids": ["test_verification"],
        "evidence": ["test_surface"],
        "topic": "packages/platform/types/schedules/schedules-2024-06-11/outputs/schedule.output.ts::1",
    }

    assert _pick_verification_strategy(issue) == "coverage_diff"
