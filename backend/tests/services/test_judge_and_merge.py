from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult
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
    assert result["issue_filter_decisions"][0]["rule_code"] == "non_issue_formatting_or_no_risk"
    assert result["issue_filter_decisions"][0]["rule_label"] == "非问题类条目过滤"


def test_judge_uses_feedback_profile_to_tighten_risk_hypothesis():
    state = {
        "feedback_quality_profiles": {
            "experts": {
                "security_compliance": {
                    "sample_count": 4,
                    "false_positive_rate": 0.75,
                    "confidence_penalty": 0.12,
                    "needs_human_confidence": 0.9,
                    "prefer_needs_verification": True,
                }
            },
            "issue_types": {
                "missing_auth_check": {
                    "sample_count": 3,
                    "false_positive_rate": 0.67,
                    "confidence_penalty": 0.07,
                    "needs_human_confidence": 0.85,
                    "prefer_needs_verification": True,
                }
            },
        },
        "issues": [
            {
                "issue_id": "iss_feedback_profile",
                "primary_expert_id": "security_compliance",
                "normalized_issue_type": "missing_auth_check",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.88,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["鉴权分支看起来被删除"],
                "cross_file_evidence": ["controller -> service"],
                "context_files": ["authz.py", "service.py"],
                "assumptions": [],
                "participant_expert_ids": ["security_compliance"],
            }
        ],
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["confidence"] == 0.7
    assert issue["status"] == "needs_verification"
    assert issue["resolution"] == "feedback_profile_requires_more_evidence"
    assert issue["confidence_breakdown"]["feedback_profile"]["applied"] is True


def test_judge_uses_llm_judge_to_reject_low_confidence_issue(monkeypatch):
    def _fake_complete_text(_self, **_kwargs):
        return LLMTextResult(
            text='{"final_verdict":"reject","confidence_adjustment":-0.12,"reason":"证据不足，结论依赖推测"}',
            mode="live",
            provider="test",
            model="judge-model",
            base_url="http://judge",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.issue_judge_service.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(
            enable_llm_issue_judge=True,
            llm_issue_judge_confidence_threshold=0.78,
        ),
        "issues": [
            {
                "issue_id": "iss_llm_reject",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.72,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["可能存在并发冲突，但当前 diff 没直接体现"],
                "assumptions": ["需要依赖运行时并发条件才会触发"],
            }
        ],
    }

    result = judge_and_merge(state)

    assert result["issues"] == []


def test_judge_llm_judge_failure_falls_back_to_existing_rules(monkeypatch):
    def _fake_complete_text(_self, **_kwargs):
        return LLMTextResult(
            text="not-json-response",
            mode="fallback",
            provider="test",
            model="judge-model",
            base_url="http://judge",
            api_key_env="TEST_KEY",
            error="mock failure",
        )

    monkeypatch.setattr(
        "app.services.issue_judge_service.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(
            enable_llm_issue_judge=True,
            llm_issue_judge_confidence_threshold=0.78,
        ),
        "issues": [
            {
                "issue_id": "iss_llm_fallback",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.7,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["如果下游没跟着改，可能有兼容性风险"],
                "assumptions": ["需要确认调用方行为"],
            }
        ],
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["status"] == "needs_verification"
    assert issue["resolution"] == "needs_verification"
    assert issue["llm_judge_result"]["final_verdict"] == "abstain"
    assert issue["confidence_breakdown"]["llm_judge"]["applied"] is True


def test_judge_uses_llm_judge_for_cross_file_contract_even_above_threshold(monkeypatch):
    def _fake_complete_text(_self, **_kwargs):
        return LLMTextResult(
            text='{"final_verdict":"needs_verification","confidence_adjustment":-0.08,"reason":"跨文件契约证据存在，但调用方上下文不完整"}',
            mode="live",
            provider="test",
            model="judge-model",
            base_url="http://judge",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.issue_judge_service.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(
            enable_llm_issue_judge=True,
            llm_issue_judge_confidence_threshold=0.78,
        ),
        "issues": [
            {
                "issue_id": "iss_cross_file_judge",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.84,
                "verified": True,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["方法签名已变更"],
                "cross_file_evidence": ["OwnerRepository -> OwnerController"],
                "assumptions": [],
            }
        ],
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["llm_judge_result"]["final_verdict"] == "needs_verification"
    assert "cross_file_contract" in issue["llm_judge_result"]["trigger_reason"]
    assert issue["status"] == "needs_verification"
    assert issue["resolution"] == "llm_judge_needs_verification"


def test_judge_uses_feedback_profile_to_expand_llm_judge_trigger(monkeypatch):
    def _fake_complete_text(_self, **_kwargs):
        return LLMTextResult(
            text='{"final_verdict":"needs_verification","confidence_adjustment":-0.05,"reason":"该专家历史误报偏高，当前证据仍偏薄"}',
            mode="live",
            provider="test",
            model="judge-model",
            base_url="http://judge",
            api_key_env="TEST_KEY",
        )

    monkeypatch.setattr(
        "app.services.issue_judge_service.LLMChatService.complete_text",
        _fake_complete_text,
    )

    state = {
        "runtime_settings": RuntimeSettings(
            enable_llm_issue_judge=True,
            llm_issue_judge_confidence_threshold=0.78,
        ),
        "feedback_quality_profiles": {
            "experts": {
                "security_compliance": {
                    "sample_count": 4,
                    "false_positive_rate": 0.75,
                    "confidence_penalty": 0.12,
                    "needs_human_confidence": 0.9,
                    "prefer_needs_verification": True,
                }
            },
            "issue_types": {},
        },
        "issues": [
            {
                "issue_id": "iss_feedback_judge",
                "primary_expert_id": "security_compliance",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.8,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["权限判断分支看起来被放宽"],
                "assumptions": [],
            }
        ],
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["llm_judge_result"]["final_verdict"] == "needs_verification"
    assert "feedback_profile" in issue["llm_judge_result"]["trigger_reason"]
    assert issue["status"] == "needs_verification"
    assert issue["resolution"] == "llm_judge_needs_verification"
