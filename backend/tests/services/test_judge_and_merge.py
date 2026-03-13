from app.services.orchestrator.nodes.judge_and_merge import judge_and_merge


def test_judge_keeps_risk_hypothesis_in_needs_verification():
    state = {
        "issues": [
            {
                "issue_id": "iss_1",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.88,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "evidence": ["只有 import 变化"],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["resolution"] == "needs_verification"
    assert result["issues"][0]["status"] == "needs_verification"


def test_judge_preserves_human_gate_for_high_risk_verified_hypothesis():
    state = {
        "issues": [
            {
                "issue_id": "iss_2",
                "finding_type": "risk_hypothesis",
                "severity": "blocker",
                "confidence": 0.93,
                "verified": True,
                "tool_verified": True,
                "needs_human": True,
                "status": "open",
                "resolution": "",
                "evidence": ["security_surface"],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["status"] == "needs_human"
    assert result["issues"][0]["resolution"] == "needs_human_review"


def test_judge_keeps_thin_verified_hypothesis_in_needs_verification():
    state = {
        "issues": [
            {
                "issue_id": "iss_3",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.9,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "evidence": ["只有一条工具提示"],
                "cross_file_evidence": [],
                "context_files": [],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["status"] == "needs_verification"
    assert result["issues"][0]["resolution"] == "needs_verification"


def test_judge_accepts_verified_hypothesis_with_richer_evidence():
    state = {
        "issues": [
            {
                "issue_id": "iss_4",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.9,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "evidence": ["schema_diff 命中", "调用路径存在影响"],
                "cross_file_evidence": ["schema.prisma -> repository.ts"],
                "context_files": ["schema.prisma", "repository.ts"],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["status"] == "resolved"
    assert result["issues"][0]["resolution"] == "accepted_with_verification"
