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
    assert result["issues"][0]["category_label"] == "risk_hypothesis"
    assert "工具/证据核验已通过" in result["issues"][0]["confidence_rationale"]


def test_judge_adds_confidence_rationale_for_observation_signal():
    state = {
        "issues": [
            {
                "issue_id": "iss_observation_signal",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.78,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["循环体内存在外部调用"],
                "context_files": ["OrderBatchService.java"],
                "confidence_breakdown": {
                    "participant_count": 1,
                    "consensus_bonus": 0.0,
                    "evidence_bonus": 0.02,
                    "hypothesis_penalty": 0.05,
                    "evidence_source": "observation_signal",
                },
            }
        ]
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["status"] == "needs_verification"
    assert issue["category_label"] == "risk_hypothesis"
    assert "观察信号" in issue["confidence_rationale"]
    assert "需要复核" in issue["confidence_rationale"]


def test_judge_lightweight_verification_marks_gray_zone_as_needs_context():
    state = {
        "issues": [
            {
                "issue_id": "iss_gray_zone",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.76,
                "verified": False,
                "tool_verified": False,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": False,
                "evidence": ["调用方可能没有同步修改"],
                "participant_expert_ids": ["correctness_business"],
            }
        ]
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["status"] == "needs_verification"
    assert issue["confidence_breakdown"]["lightweight_verification"]["verdict"] == "needs_context"
    assert issue["confidence_breakdown"]["lightweight_verification"]["confidence_adjustment"] < 0


def test_judge_lightweight_verification_confirms_direct_rich_evidence():
    state = {
        "issues": [
            {
                "issue_id": "iss_direct_rich",
                "finding_type": "direct_defect",
                "severity": "medium",
                "confidence": 0.79,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": True,
                "evidence": ["空值分支被删除", "调用方传入 nullable request"],
                "cross_file_evidence": ["Controller -> Service"],
                "context_files": ["OrderController.java"],
            }
        ]
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["status"] == "resolved"
    assert issue["confidence"] == 0.82
    assert issue["confidence_breakdown"]["lightweight_verification"]["verdict"] == "confirmed"


def test_judge_rationale_mentions_sast_cross_validation():
    state = {
        "issues": [
            {
                "issue_id": "iss_sast",
                "title": "eval 调用存在注入风险",
                "summary": "新增代码直接 eval 用户输入。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.84,
                "direct_evidence": False,
                "tool_verified": True,
                "sast_cross_validated": True,
                "verified": False,
                "needs_human": False,
                "participant_expert_ids": ["security_compliance"],
                "evidence": [
                    "eval(user_input)",
                    "SAST/linter 佐证: semgrep:python.lang.security.audit.eval L12 Use of eval",
                    "安全专家确认 eval 直接消费用户输入",
                    "代码片段位于新增逻辑",
                ],
                "cross_file_evidence": [],
                "context_files": [],
                "assumptions": [],
                "confidence_breakdown": {"sast_cross_validated": True, "verification_bonus": 0.04},
            }
        ],
        "feedback_quality_profiles": {},
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["status"] == "resolved"
    assert issue["resolution"] == "accepted_with_verification"
    assert "SAST/linter" in issue["confidence_rationale"]


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


def test_judge_applies_repo_policy_comment_budget():
    state = {
        "review_policy": {"max_comments_per_review": 2},
        "issues": [
            {
                "issue_id": "iss_low",
                "title": "低风险建议",
                "finding_type": "design_concern",
                "severity": "low",
                "confidence": 0.95,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "evidence": ["命名建议"],
            },
            {
                "issue_id": "iss_high",
                "title": "高风险鉴权问题",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.82,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "evidence": ["权限分支被删除"],
            },
            {
                "issue_id": "iss_medium",
                "title": "中风险事务问题",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.9,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "evidence": ["schema_diff 命中", "调用路径存在影响"],
                "cross_file_evidence": ["service -> repository"],
                "context_files": ["service.py"],
            },
        ],
    }

    result = judge_and_merge(state)

    assert [item["issue_id"] for item in result["issues"]] == ["iss_high", "iss_medium"]
    assert result["issue_filter_decisions"][-1]["rule_code"] == "repo_policy_comment_budget"
    assert result["issue_filter_decisions"][-1]["finding_titles"] == ["低风险建议"]


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
    assert issue["confidence"] == 0.67
    assert issue["status"] == "needs_verification"
    assert issue["resolution"] == "feedback_profile_requires_more_evidence"
    assert issue["confidence_breakdown"]["feedback_profile"]["applied"] is True
    assert issue["confidence_breakdown"]["lightweight_verification"]["verdict"] == "needs_context"


def test_judge_uses_feedback_accept_rate_to_lift_confidence():
    state = {
        "feedback_quality_profiles": {
            "experts": {
                "database_analysis": {
                    "sample_count": 8,
                    "false_positive_rate": 0.12,
                    "accept_rate": 0.88,
                    "confidence_bonus": 0.05,
                }
            },
            "issue_types": {},
        },
        "issues": [
            {
                "issue_id": "iss_accept_profile",
                "primary_expert_id": "database_analysis",
                "normalized_issue_type": "query_boundary_missing",
                "finding_type": "direct_defect",
                "severity": "medium",
                "confidence": 0.86,
                "verified": True,
                "tool_verified": True,
                "needs_human": False,
                "status": "open",
                "resolution": "",
                "direct_evidence": True,
                "evidence": ["limit 被删除", "查询入口仍可传入大范围条件"],
                "cross_file_evidence": [],
                "context_files": ["OrderRepository.java"],
                "assumptions": [],
                "participant_expert_ids": ["database_analysis"],
            }
        ],
    }

    result = judge_and_merge(state)

    issue = result["issues"][0]
    assert issue["confidence"] == 0.91
    assert issue["confidence_breakdown"]["feedback_profile"]["confidence_bonus"] == 0.05
    assert "高接受率" in issue["confidence_rationale"]


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
