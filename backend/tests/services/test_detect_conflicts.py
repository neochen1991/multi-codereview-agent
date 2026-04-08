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


def test_detect_conflicts_respects_per_priority_confidence_thresholds():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.98,
            "issue_confidence_threshold_p1": 0.95,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_high_1",
                "expert_id": "security_compliance",
                "title": "权限绕过风险",
                "summary": "当前改动绕过了资源级鉴权校验，存在高风险访问控制漏洞。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 55,
                "evidence": ["鉴权分支被绕开"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert result["issue_filter_decisions"][0]["rule_code"] == "below_priority_confidence_threshold"
    assert "P1" in result["issue_filter_decisions"][0]["reason"]
    assert "0.95" in result["issue_filter_decisions"][0]["reason"]


def test_detect_conflicts_keeps_issue_when_priority_confidence_threshold_is_met():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.98,
            "issue_confidence_threshold_p1": 0.9,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_medium_2",
                "expert_id": "database_analysis",
                "title": "大事务批量更新缺少分批提交",
                "summary": "当前 SQL 变更会在一次事务内更新过多记录，容易导致锁持有时间过长。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.86,
                "verification_needed": True,
                "file_path": "sql/migration/V42__backfill_orders.sql",
                "line_start": 12,
                "evidence": ["单事务更新全表"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["批量更新需分批提交"],
                "violated_guidelines": ["数据库回填需控制事务范围"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["title"] == "大事务批量更新缺少分批提交"


def test_detect_conflicts_filters_low_confidence_finding_before_grouping_issue():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.98,
            "issue_confidence_threshold_p1": 0.95,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_group_low",
                "expert_id": "security_compliance",
                "title": "鉴权绕过风险提示一",
                "summary": "同一代码块里存在一个高风险访问控制问题，但当前这条 finding 的证据较弱。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 55,
                "evidence": ["存在绕过资源鉴权的分支"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            },
            {
                "finding_id": "fdg_group_high",
                "expert_id": "security_compliance",
                "title": "鉴权绕过风险提示二",
                "summary": "同一代码块里的另一个 finding 证据更强，达到 issue 升级阈值。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.97,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 56,
                "evidence": ["鉴权分支被显式绕开", "存在未授权访问路径"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["finding_ids"] == ["fdg_group_high"]
    assert len(result["issue_filter_decisions"]) == 1
    assert result["issue_filter_decisions"][0]["finding_ids"] == ["fdg_group_low"]
    assert result["issue_filter_decisions"][0]["rule_code"] == "below_priority_confidence_threshold"


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


def test_detect_conflicts_uses_weighted_confidence_with_consensus_and_evidence_bonus():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_weighted_1",
                "expert_id": "security_compliance",
                "title": "鉴权绕过风险",
                "summary": "存在可直接利用的未授权访问路径。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.92,
                "verification_needed": False,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 21,
                "evidence": ["资源级鉴权被绕开", "未授权路径可直达"],
                "cross_file_evidence": ["controller -> service 鉴权链路断裂"],
                "context_files": ["src/app/controller/OrderController.java", "src/app/service/OrderService.java"],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            },
            {
                "finding_id": "fdg_weighted_2",
                "expert_id": "architecture_design",
                "title": "鉴权绕过风险",
                "summary": "鉴权职责被下沉后没有在入口层补齐。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.78,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 22,
                "evidence": ["入口层缺少统一鉴权守卫"],
                "cross_file_evidence": ["controller -> interceptor 没有接入"],
                "context_files": ["src/app/interceptor/AuthInterceptor.java"],
                "matched_rules": ["边界层职责闭合"],
                "violated_guidelines": ["安全校验不能依赖调用方自觉"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["confidence"] == 0.95
    assert conflict["confidence_breakdown"]["base_weighted_confidence"] == 0.86
    assert conflict["confidence_breakdown"]["consensus_bonus"] == 0.03
    assert conflict["confidence_breakdown"]["evidence_bonus"] == 0.06
    assert conflict["confidence_breakdown"]["hypothesis_penalty"] == 0.0
    assert conflict["confidence_breakdown"]["participant_count"] == 2


def test_detect_conflicts_penalizes_single_expert_hypothesis_only_issue():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_penalty_1",
                "expert_id": "maintainability_code_health",
                "title": "这里可能需要补充更多日志",
                "summary": "当前日志信息略少，后续排查可能不够方便。",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.84,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 33,
                "evidence": ["日志字段较少"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["confidence"] == 0.75
    assert conflict["confidence_breakdown"]["base_weighted_confidence"] == 0.84
    assert conflict["confidence_breakdown"]["evidence_bonus"] == 0.01
    assert conflict["confidence_breakdown"]["hypothesis_penalty"] == 0.1


def test_detect_conflicts_splits_different_semantic_findings_in_same_line_bucket():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_semantic_sql",
                "expert_id": "database_analysis",
                "title": "SQL 查询语义被放宽为模糊匹配",
                "summary": "equal 被改成 like，查询语义发生变化，可能扩大结果集。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.93,
                "verification_needed": False,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 63,
                "evidence": ["builder.equal -> builder.like"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["查询语义不能静默放宽"],
            },
            {
                "finding_id": "fdg_semantic_name",
                "expert_id": "maintainability_code_health",
                "title": "临时变量命名不符合约定",
                "summary": "chunksTmp 这种命名会降低可读性，且与常量语义不一致。",
                "finding_type": "design_concern",
                "severity": "medium",
                "confidence": 0.9,
                "verification_needed": True,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 66,
                "evidence": ["命名与语义不一致"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["命名一致性"],
                "violated_guidelines": ["变量命名需表达稳定语义"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 2
    conflict_finding_ids = [tuple(conflict["finding_ids"]) for conflict in result["conflicts"]]
    assert ("fdg_semantic_sql",) in conflict_finding_ids
    assert ("fdg_semantic_name",) in conflict_finding_ids


def test_detect_conflicts_keeps_same_semantic_findings_grouped_together():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_same_1",
                "expert_id": "security_compliance",
                "title": "权限绕过风险",
                "summary": "资源级鉴权被绕过，存在未授权访问路径。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.92,
                "verification_needed": False,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 21,
                "evidence": ["资源级鉴权被绕开"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["SEC-AUTH-001"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            },
            {
                "finding_id": "fdg_same_2",
                "expert_id": "architecture_design",
                "title": "权限绕过风险",
                "summary": "入口层没有守住鉴权边界，导致未授权路径可达。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.81,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 22,
                "evidence": ["入口层缺少统一鉴权守卫"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["SEC-AUTH-001"],
                "violated_guidelines": ["安全校验不能依赖调用方自觉"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["finding_ids"] == ["fdg_same_1", "fdg_same_2"]
