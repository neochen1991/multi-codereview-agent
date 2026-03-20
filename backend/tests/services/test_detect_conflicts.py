from app.services.orchestrator.nodes.detect_conflicts import detect_conflicts


def test_detect_conflicts_skips_low_risk_hint_like_findings():
    state = {
        "findings": [
            {
                "finding_id": "fdg_hint_1",
                "expert_id": "maintainability_code_health",
                "title": "常量命名建议统一为小驼峰",
                "summary": "这是一个提示性问题，主要影响可读性与命名风格，一般不会导致运行时风险。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.62,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 18,
                "evidence": ["命名风格不统一"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["命名一致性"],
                "violated_guidelines": ["常量约定"],
            }
        ]
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert len(result["issue_filter_decisions"]) == 1
    assert result["issue_filter_decisions"][0]["rule_code"] == "hint_like_medium"
    assert "仅保留为 finding" in result["issue_filter_decisions"][0]["reason"]


def test_detect_conflicts_keeps_high_risk_runtime_findings():
    state = {
        "findings": [
            {
                "finding_id": "fdg_risk_1",
                "expert_id": "performance_reliability",
                "title": "线程池容量扩大可能导致请求风暴",
                "summary": "maxPoolSize 从 16 提升到 512，queueCapacity 从 200 提升到 20000，会显著放大堆积与上下游压力。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.88,
                "verification_needed": True,
                "file_path": "infra/executor/async-runtime.conf",
                "line_start": 2,
                "evidence": ["线程池配置扩大", "拒绝策略从 CALLER_RUNS 改为 ABORT"],
                "cross_file_evidence": ["executor -> downstream client"],
                "context_files": ["infra/executor/async-runtime.conf", "infra/client/http.conf"],
                "matched_rules": ["线程池扩容需配套背压"],
                "violated_guidelines": ["缺少容量评估与渐进扩容"],
            }
        ]
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["title"] == "线程池容量扩大可能导致请求风暴"


def test_detect_conflicts_respects_disabled_issue_filter():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
        },
        "findings": [
            {
                "finding_id": "fdg_hint_2",
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
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_hint_2"


def test_detect_conflicts_respects_issue_priority_threshold():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P1",
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
        },
        "findings": [
            {
                "finding_id": "fdg_medium_1",
                "expert_id": "maintainability_code_health",
                "title": "重复的空值分支增加维护成本",
                "summary": "当前实现存在重复的空值分支，容易导致后续修改遗漏，但暂未形成直接运行时故障。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.89,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 66,
                "evidence": ["同一判空逻辑出现 3 次"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["重复逻辑应收敛"],
                "violated_guidelines": ["维护性要求"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert result["issue_filter_decisions"][0]["rule_code"] == "below_issue_priority_threshold"
    assert "P1" in result["issue_filter_decisions"][0]["reason"]


def test_detect_conflicts_skips_non_code_review_scope_findings():
    state = {
        "findings": [
            {
                "finding_id": "fdg_scope_1",
                "expert_id": "correctness_business",
                "title": "业务背景不清晰，无法确认这里为何要新增这个分支",
                "summary": "当前 MR 没有说明业务需求，缺少业务上下文，难以判断产品意图是否正确。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.92,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 88,
                "evidence": [],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            }
        ]
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert result["issue_filter_decisions"][0]["rule_code"] == "non_code_review_scope"
