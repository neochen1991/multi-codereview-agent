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


def test_judge_keeps_speculative_low_confidence_hypothesis_in_needs_verification():
    state = {
        "issues": [
            {
                "issue_id": "iss_speculative_low",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.4,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["diff 未展示完整构造函数"],
                "cross_file_evidence": ["配置类存在相关注入点"],
                "context_files": ["MySqlDomainEventsConsumer.java"],
                "assumptions": ["需要查看完整类定义后确认"],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["status"] == "needs_verification"
    assert result["issues"][0]["resolution"] == "needs_verification"


def test_judge_accepts_verified_non_speculative_hypothesis_with_strong_evidence():
    state = {
        "issues": [
            {
                "issue_id": "iss_verified_strong",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.74,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["schema_diff 命中", "调用路径存在影响"],
                "cross_file_evidence": ["schema.prisma -> repository.ts"],
                "context_files": ["schema.prisma", "repository.ts"],
                "assumptions": [],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"][0]["status"] == "resolved"
    assert result["issues"][0]["resolution"] == "accepted_with_verification"


def test_judge_drops_non_issue_formatting_entries():
    state = {
        "issues": [
            {
                "issue_id": "iss_format",
                "title": "代码格式化变更无架构风险",
                "summary": "当前改动仅涉及缩进调整，无架构问题。",
                "claim": "无风险",
                "finding_type": "design_concern",
                "severity": "low",
                "confidence": 0.9,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "evidence": ["仅涉及格式化调整"],
            }
        ]
    }

    result = judge_and_merge(state)

    assert result["issues"] == []
