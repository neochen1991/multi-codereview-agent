from app.services.orchestrator.nodes.detect_conflicts import _score_issue_confidence, detect_conflicts


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


def test_detect_conflicts_keeps_high_risk_runtime_findings_as_findings_when_verification_is_required():
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

    assert result["conflicts"] == []
    assert len(result["issue_filter_decisions"]) == 1
    assert result["issue_filter_decisions"][0]["rule_code"] == "conditional_conclusion"
    assert "仅保留为 finding" in result["issue_filter_decisions"][0]["reason"]


def test_detect_conflicts_upgrades_empty_catch_with_direct_code_evidence():
    state = {
        "findings": [
            {
                "finding_id": "fdg_swallowed_exception",
                "expert_id": "correctness_business",
                "title": "领域事件消费者反射异常被静默吞掉",
                "summary": "catch 块删除 printStackTrace 后变为空 catch，NoSuchMethodException 等反射异常没有日志、失败标记或补偿。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.8,
                "verification_needed": True,
                "file_path": "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
                "line_start": 29,
                "evidence": [
                    "catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException | InstantiationException e) { }",
                    "diff 删除了 e.printStackTrace();",
                    "目标文件完整内容已加载。",
                ],
                "cross_file_evidence": [],
                "context_files": [
                    "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
                ],
                "matched_rules": ["CORR-JDDD-002"],
                "violated_guidelines": ["异常不能被静默吞掉"],
                "normalized_issue_type": "exception_swallowed",
            }
        ]
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_swallowed_exception"
    assert result["conflicts"][0]["direct_evidence"] is True
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_keeps_comment_contract_mismatch_even_if_text_contains_comment_tokens():
    state = {
        "findings": [
            {
                "finding_id": "fdg_contract_1",
                "expert_id": "correctness_business",
                "title": "订单创建逻辑（承诺未落地）",
                "summary": "注释或 TODO 承诺了扣减库存并发送事件，但当前实现没有对应动作。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": False,
                "file_path": "src/main/java/com/example/OrderService.java",
                "line_start": 21,
                "evidence": ["检测到注释/待办承诺未实现：// TODO: 扣减库存并发送事件"],
                "cross_file_evidence": [],
                "context_files": ["src/main/java/com/example/OrderService.java"],
                "matched_rules": [],
                "violated_guidelines": [],
            }
        ]
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["title"] == "订单创建逻辑（承诺未落地）"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_keeps_high_priority_issue_when_text_contains_hint_tokens():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.9,
            "hint_issue_evidence_cap": 3,
            "issue_confidence_threshold_p2": 0.8,
        },
        "findings": [
            {
                "finding_id": "fdg_auth_hint_overlap",
                "expert_id": "security_compliance",
                "title": "注释说明掩盖鉴权绕过风险",
                "summary": "注释声称会校验资源归属，但当前代码删除了 ownerId 校验，存在越权访问路径。",
                "finding_type": "direct_defect",
                "severity": "medium",
                "confidence": 0.86,
                "verification_needed": True,
                "direct_evidence": True,
                "file_path": "src/main/java/com/example/OrderController.java",
                "line_start": 42,
                "evidence": ["删除 ownerId 校验", "保留对外接口入口", "存在未授权访问路径"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["接口必须做资源级鉴权"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_auth_hint_overlap"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_applies_feedback_profiles_before_issue_scoring():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "feedback_quality_profiles": {
            "experts": {
                "database_analysis": {
                    "sample_count": 4,
                    "confidence_penalty": 0.12,
                }
            },
            "issue_types": {
                "query_boundary_missing": {
                    "sample_count": 4,
                    "confidence_penalty": 0.07,
                }
            },
        },
        "findings": [
            {
                "finding_id": "fdg_feedback_penalty",
                "expert_id": "database_analysis",
                "title": "查询边界缺失",
                "summary": "删除分页可能导致无界查询。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "query_boundary_missing",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": True,
                "file_path": "src/main/java/com/example/OrderRepository.java",
                "line_start": 42,
                "evidence": ["删除 limit"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    breakdown = result["conflicts"][0]["confidence_breakdown"]
    assert breakdown["base_weighted_confidence"] == 0.72
    assert breakdown["feedback_adjustments"][0]["confidence_penalty"] == 0.18


def test_detect_conflicts_does_not_cross_validate_unrelated_sast_match():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_null_pointer",
                "expert_id": "correctness_business",
                "title": "空指针风险",
                "summary": "customer 可能为空。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "null_pointer",
                "severity": "high",
                "confidence": 0.88,
                "verification_needed": True,
                "file_path": "src/main/java/com/example/OrderService.java",
                "line_start": 42,
                "evidence": ["customer.getId()"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
                "sast_prescan_matches": [
                    {
                        "tool": "semgrep",
                        "rule_id": "java.sql-injection",
                        "message": "SQL injection risk",
                        "cwe": "CWE-89",
                        "file_path": "src/main/java/com/example/OrderService.java",
                        "line_start": 42,
                    }
                ],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["sast_cross_validated"] is False
    assert conflict["confidence_breakdown"]["verification_bonus"] == 0.0


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
    assert result["issue_filter_decisions"][0]["rule_code"] == "conditional_conclusion"
    assert "仅保留为 finding" in result["issue_filter_decisions"][0]["reason"]


def test_detect_conflicts_upgrades_concrete_security_rule_finding_even_when_verification_is_requested():
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
                "finding_id": "fdg_security_like_wildcard",
                "expert_id": "security_compliance",
                "title": "搜索参数直接拼接到 LIKE 模式中",
                "summary": "用户输入 keyword 被直接拼入 LIKE 查询模式，未转义 %/_ 通配符，会导致查询边界被绕过并扩大可访问数据范围。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "input_validation_removed",
                "severity": "medium",
                "confidence": 0.86,
                "verification_needed": True,
                "file_path": "src/main/java/com/acme/order/OrderRepository.java",
                "line_start": 72,
                "evidence": [
                    'builder.like(root.get("name"), String.format("%%%s%%", keyword))',
                    "keyword 未经过 LIKE 通配符转义或长度限制",
                ],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["JAVA-SQL-SEC-002"],
                "violated_guidelines": ["外部输入进入 SQL/Criteria 查询前必须做白名单校验和转义"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_security_like_wildcard"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_upgrades_security_direct_defect_with_context_gap_and_moderate_confidence():
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
                "finding_id": "fdg_security_context_gap",
                "expert_id": "security_compliance",
                "title": "通配符未转义可能导致查询范围绕过",
                "summary": "Criteria 查询从 equal 改为 like，并把 filter.value().value() 直接放入 %...% 模式。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "query_semantics_regression",
                "severity": "medium",
                "confidence": 0.72,
                "verification_needed": True,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 16,
                "evidence": [
                    'return builder.like(root.get(filter.field().value()), String.format("%%%s%%", filter.value().value()));',
                    "filter.value().value() 未对 LIKE 通配符 % 和 _ 做转义",
                    "检测到查询语义从 equal 精确匹配退化为 like 模糊匹配",
                    "security_surface",
                    "local_diff:Local diff inspected for 3 files.",
                    "当前代码锚点 src/shared/HibernateCriteriaConverter.java:16",
                ],
                "cross_file_evidence": [],
                "context_files": ["src/shared/HibernateCriteriaConverter.java"],
                "assumptions": [
                    "缺失上下文: Filter 的 value 来源是否为用户可控输入",
                    "缺失上下文: equalsPredicateTransformer 的调用方场景",
                ],
                "matched_rules": ["GENERAL-EXPERT-CHECKS"],
                "violated_guidelines": ["GENERAL-EXPERT-CHECKS"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_security_context_gap"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_keeps_verification_required_finding_out_of_issues_even_when_priority_confidence_threshold_is_met():
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

    assert result["conflicts"] == []
    assert result["issue_filter_decisions"][0]["rule_code"] == "conditional_conclusion"


def test_detect_conflicts_filters_verification_required_findings_even_when_confidence_is_high():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.98,
            "issue_confidence_threshold_p1": 0.85,
            "issue_confidence_threshold_p2": 0.75,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_verify_only_1",
                "expert_id": "correctness_business",
                "title": "注释承诺的分支可能未完全落地",
                "summary": "如果调用链确实走到该分支，则当前实现缺少注释承诺的补偿逻辑，仍需结合上游入口确认。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.93,
                "verification_needed": True,
                "file_path": "src/app/service/OrderService.java",
                "line_start": 88,
                "evidence": ["TODO: 成功后补发通知"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["承诺行为需落地"],
                "violated_guidelines": ["接口承诺不可悬空"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert len(result["issue_filter_decisions"]) == 1
    assert result["issue_filter_decisions"][0]["rule_code"] == "conditional_conclusion"


def test_detect_conflicts_promotes_strong_direct_ddd_factory_bypass_even_when_verification_is_requested():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.95,
            "issue_confidence_threshold_p1": 0.85,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_factory_bypass",
                "expert_id": "ddd_architecture",
                "title": "聚合根创建绕过工厂方法导致领域事件丢失 (Aggregate factory bypass)",
                "summary": "CourseCreator 将 Course.create() 改为 new Course()，绕过聚合工厂方法，导致 CourseCreatedDomainEvent 不再被记录。",
                "finding_type": "direct_defect",
                "severity": "blocker",
                "confidence": 0.95,
                "verification_needed": True,
                "file_path": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
                "line_start": 18,
                "evidence": [
                    "CourseCreator.java第18行直接使用 new Course(id, name, duration)",
                    "Course.java第25-31行 Course.create() 内部调用 record(new CourseCreatedDomainEvent(...))",
                    "CourseCreator.java第21行仍调用 eventBus.publish(course.pullDomainEvents())",
                ],
                "cross_file_evidence": ["Course.java 证明工厂方法负责领域事件注册"],
                "context_files": [
                    "src/mooc/main/tv/codely/mooc/courses/domain/Course.java",
                    "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
                ],
                "matched_rules": ["ARCH-JDDD-002"],
                "violated_guidelines": ["聚合根应通过工厂方法封装领域事件注册逻辑"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_factory_bypass"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_keeps_verification_required_group_as_findings_even_when_one_item_has_stronger_confidence():
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
                "line_start": 55,
                "evidence": ["鉴权分支被显式绕开", "存在未授权访问路径"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["访问控制规则"],
                "violated_guidelines": ["高风险接口必须做资源级鉴权"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert len(result["issue_filter_decisions"]) == 2
    assert {item["rule_code"] for item in result["issue_filter_decisions"]} == {"conditional_conclusion"}
    assert {tuple(item["finding_ids"]) for item in result["issue_filter_decisions"]} == {
        ("fdg_group_low",),
        ("fdg_group_high",),
    }


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
                "normalized_issue_type": "auth_bypass",
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
                "normalized_issue_type": "auth_bypass",
                "severity": "high",
                "confidence": 0.78,
                "verification_needed": True,
                "file_path": "src/app/controller/OrderController.java",
                "line_start": 21,
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
    assert conflict["finding_ids"] == ["fdg_weighted_1", "fdg_weighted_2"]
    assert conflict["participant_expert_ids"] == ["security_compliance", "architecture_design"]
    assert conflict["primary_expert_id"] == "security_compliance"
    assert conflict["confidence"] == 0.95
    assert conflict["confidence_breakdown"]["participant_count"] == 2
    assert conflict["confidence_breakdown"]["consensus_bonus"] == 0.03
    assert conflict["confidence_breakdown"]["hypothesis_penalty"] == 0.0


def test_detect_conflicts_does_not_apply_consensus_bonus_for_different_issue_types():
    _confidence, breakdown = _score_issue_confidence(
        [
            {
                "expert_id": "security_compliance",
                "title": "鉴权绕过风险",
                "finding_type": "direct_defect",
                "normalized_issue_type": "auth_bypass",
                "confidence": 0.92,
                "verification_needed": False,
                "evidence": ["资源级鉴权缺失"],
            },
            {
                "expert_id": "maintainability_code_health",
                "title": "鉴权绕过风险",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "audit_logging_gap",
                "confidence": 0.78,
                "verification_needed": True,
                "evidence": ["审计日志字段不足"],
            },
        ]
    )

    assert breakdown["participant_count"] == 2
    assert breakdown["consensus_bonus"] == 0.0


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


def test_detect_conflicts_splits_findings_on_different_lines():
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


def test_detect_conflicts_promotes_high_value_design_concern():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
            "issue_confidence_threshold_p0": 0.95,
            "issue_confidence_threshold_p1": 0.85,
            "issue_confidence_threshold_p2": 0.8,
            "issue_confidence_threshold_p3": 0.7,
        },
        "findings": [
            {
                "finding_id": "fdg_design_boundary",
                "expert_id": "ddd_architecture",
                "title": "应用服务职责边界被绕开",
                "summary": "当前改动让 Controller 直接编排仓储和领域事件，破坏应用服务职责边界。",
                "finding_type": "design_concern",
                "severity": "medium",
                "confidence": 0.86,
                "verification_needed": True,
                "file_path": "src/main/java/com/example/order/OrderController.java",
                "line_start": 42,
                "evidence": ["Controller 直接调用 repository.save", "Controller 直接发布 domain event"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["DDD-BOUNDARY-001"],
                "violated_guidelines": ["应用服务职责边界不能泄漏到 Controller"],
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["issue_id"] == "fdg_design_boundary"
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_merges_same_line_same_problem_into_single_issue():
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
                "line_start": 21,
                "evidence": ["入口层缺少统一鉴权守卫"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["SEC-AUTH-001"],
                "violated_guidelines": ["安全校验不能依赖调用方自觉"],
                "remediation_strategy": "在入口层补齐统一鉴权守卫",
                "remediation_suggestion": "恢复资源级鉴权并补充拒绝分支",
                "remediation_steps": ["补充入口鉴权", "增加未授权访问测试"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["finding_ids"] == ["fdg_same_1", "fdg_same_2"]
    assert conflict["aggregated_titles"] == ["权限绕过风险"]
    assert conflict["primary_expert_id"] == "security_compliance"
    assert [view["expert_id"] for view in conflict["expert_views"]] == [
        "security_compliance",
        "architecture_design",
    ]
    assert [view["title"] for view in conflict["expert_views"]] == ["权限绕过风险", "权限绕过风险"]
    assert [view["finding_type"] for view in conflict["expert_views"]] == [
        "direct_defect",
        "risk_hypothesis",
    ]
    assert all(view["normalized_issue_type"] for view in conflict["expert_views"])


def test_detect_conflicts_merges_nearby_lines_for_same_normalized_issue_type():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_near_1",
                "expert_id": "performance_reliability",
                "title": "循环内逐条查库导致放大",
                "summary": "for 循环中逐条查询仓储，批量路径会放大。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "loop_call_amplification",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": False,
                "file_path": "src/main/java/com/example/BatchService.java",
                "line_start": 42,
                "evidence": ["for (OrderItem item : items)", "orderRepository.findById(item.id())"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["PERF-LOOP-001"],
                "violated_guidelines": ["循环体内避免逐条外部调用"],
            },
            {
                "finding_id": "fdg_near_2",
                "expert_id": "database_analysis",
                "title": "循环内逐条查库导致放大",
                "summary": "同一段批处理逻辑在循环里逐条访问 repository，存在 N+1 风险。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "loop_call_amplification",
                "severity": "high",
                "confidence": 0.82,
                "verification_needed": True,
                "file_path": "src/main/java/com/example/BatchService.java",
                "line_start": 43,
                "evidence": ["orderRepository.findById(item.id())"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["批量查询需避免 N+1"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["finding_ids"] == ["fdg_near_1", "fdg_near_2"]
    assert conflict["normalized_issue_type"] == "loop_call_amplification"


def test_detect_conflicts_keeps_same_line_different_problems_as_separate_issues():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_line_1",
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
                "remediation_strategy": "恢复精确匹配语义",
                "remediation_suggestion": "把 like 改回 equal，并补充查询语义回归测试",
                "remediation_steps": ["恢复 equal 条件", "补充 SQL 语义测试"],
            },
            {
                "finding_id": "fdg_line_2",
                "expert_id": "maintainability_code_health",
                "title": "临时变量命名不符合约定",
                "summary": "chunksTmp 这种命名会降低可读性，且与常量语义不一致。",
                "finding_type": "design_concern",
                "severity": "medium",
                "confidence": 0.9,
                "verification_needed": True,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 63,
                "evidence": ["命名与语义不一致"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["命名一致性"],
                "violated_guidelines": ["变量命名需表达稳定语义"],
                "remediation_strategy": "把临时变量改成表达语义的名称",
                "remediation_suggestion": "将 chunksTmp 重命名为语义稳定的变量名",
                "remediation_steps": ["统一变量命名", "同步更新引用"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 2
    conflicts_by_id = {conflict["issue_id"]: conflict for conflict in result["conflicts"]}
    assert conflicts_by_id["fdg_line_1"]["finding_ids"] == ["fdg_line_1"]
    assert conflicts_by_id["fdg_line_1"]["aggregated_titles"] == ["SQL 查询语义被放宽为模糊匹配"]
    assert conflicts_by_id["fdg_line_1"]["finding_type"] == "direct_defect"
    assert conflicts_by_id["fdg_line_1"]["aggregated_finding_types"] == ["direct_defect"]
    assert "恢复精确匹配语义" in conflicts_by_id["fdg_line_1"]["aggregated_remediation_strategies"]
    assert "恢复 equal 条件" in conflicts_by_id["fdg_line_1"]["aggregated_remediation_steps"]
    assert conflicts_by_id["fdg_line_2"]["finding_ids"] == ["fdg_line_2"]
    assert conflicts_by_id["fdg_line_2"]["aggregated_titles"] == ["临时变量命名不符合约定"]
    assert conflicts_by_id["fdg_line_2"]["aggregated_finding_types"] == ["design_concern"]
    assert "把临时变量改成表达语义的名称" in conflicts_by_id["fdg_line_2"]["aggregated_remediation_strategies"]
    assert "统一变量命名" in conflicts_by_id["fdg_line_2"]["aggregated_remediation_steps"]


def test_detect_conflicts_keeps_same_line_secondary_problem_as_separate_issue():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": True,
            "issue_min_priority_level": "P2",
            "suppress_low_risk_hint_issues": True,
            "hint_issue_confidence_threshold": 0.85,
            "hint_issue_evidence_cap": 2,
        },
        "findings": [
            {
                "finding_id": "fdg_combo_1",
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
                "finding_id": "fdg_combo_2",
                "expert_id": "maintainability_code_health",
                "title": "临时变量命名不符合约定",
                "summary": "chunksTmp 这种命名会降低可读性，且与常量语义不一致。",
                "finding_type": "design_concern",
                "severity": "medium",
                "confidence": 0.78,
                "verification_needed": True,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 63,
                "evidence": ["命名与语义不一致"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["命名一致性"],
                "violated_guidelines": ["变量命名需表达稳定语义"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["finding_ids"] == ["fdg_combo_1"]
    assert len(result["issue_filter_decisions"]) == 1
    assert result["issue_filter_decisions"][0]["finding_ids"] == ["fdg_combo_2"]


def test_detect_conflicts_merges_same_line_query_semantics_family_even_when_issue_type_differs():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_same_title_1",
                "expert_id": "database_analysis",
                "title": "查询条件实现存在问题",
                "summary": "equal 被改成 like，查询语义被放宽，可能返回错误结果。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "query_semantics_changed",
                "severity": "high",
                "confidence": 0.94,
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
                "finding_id": "fdg_same_title_2",
                "expert_id": "security_compliance",
                "title": "查询条件实现存在问题",
                "summary": "LIKE 模式未对特殊字符做转义，存在越权查询与注入放大风险。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "query_input_boundary_risk",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": False,
                "file_path": "src/shared/HibernateCriteriaConverter.java",
                "line_start": 63,
                "evidence": ["String.format(\"%%%s%%\", filter.value().value())"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["SEC-JAVA-INPUT-001"],
                "violated_guidelines": ["外部输入拼接查询条件前必须做边界约束"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert set(conflict["finding_ids"]) == {"fdg_same_title_1", "fdg_same_title_2"}
    assert conflict["primary_expert_id"] in {"database_analysis", "security_compliance"}
    assert "query_semantics_changed" in {
        view["normalized_issue_type"] for view in conflict["expert_views"]
    }


def test_detect_conflicts_merges_query_bound_and_query_plan_as_one_root_cause():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_bound_1",
                "expert_id": "database_analysis",
                "title": "查询边界缺失",
                "summary": "SQL 删除 LIMIT :chunk 后，当前查询路径缺少分页或批量边界保护。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "query_bound_removed",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": False,
                "file_path": "src/main/java/com/example/MySqlDomainEventsConsumer.java",
                "line_start": 37,
                "evidence": ["- LIMIT :chunk", "+ ORDER BY occurred_on"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["批量读取必须保留分页或 LIMIT 边界"],
            },
            {
                "finding_id": "fdg_bound_2",
                "expert_id": "performance_reliability",
                "title": "删除 LIMIT 可能导致大结果集",
                "summary": "同一个查询从有限批量读取变成无界查询，数据量放大后会拖垮消费任务。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "query_plan_risk",
                "severity": "high",
                "confidence": 0.86,
                "verification_needed": False,
                "file_path": "src/main/java/com/example/MySqlDomainEventsConsumer.java",
                "line_start": 38,
                "evidence": ["SELECT * FROM domain_events ORDER BY occurred_on"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["查询必须有边界"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert set(conflict["finding_ids"]) == {"fdg_bound_1", "fdg_bound_2"}
    assert conflict["primary_expert_id"] == "database_analysis"


def test_detect_conflicts_merges_same_line_synonym_problem_types():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_null_1",
                "expert_id": "correctness_business",
                "title": "空指针风险",
                "summary": "新增分支直接访问 request.user.name，缺少空值保护。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": False,
                "file_path": "src/app/UserService.java",
                "line_start": 32,
                "evidence": ["request.user.name 直接解引用"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["CORR-NULL-001"],
                "violated_guidelines": ["入口对象必须做空值保护"],
            },
            {
                "finding_id": "fdg_null_2",
                "expert_id": "maintainability_code_health",
                "title": "NPE risk",
                "summary": "The new code may throw NullPointerException when request.user is null.",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.82,
                "verification_needed": False,
                "file_path": "src/app/UserService.java",
                "line_start": 32,
                "evidence": ["NullPointerException on request.user.name"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["CORR-NULL-001"],
                "violated_guidelines": ["Null values must be guarded"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert set(result["conflicts"][0]["finding_ids"]) == {"fdg_null_1", "fdg_null_2"}


def test_detect_conflicts_applies_sast_cross_validation_bonus():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_sast_1",
                "expert_id": "security_compliance",
                "title": "eval 调用存在注入风险",
                "summary": "新增代码直接 eval 用户输入，存在代码注入风险。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "code_injection_risk",
                "severity": "medium",
                "confidence": 0.77,
                "verification_needed": True,
                "file_path": "src/app.py",
                "line_start": 12,
                "evidence": ["eval(user_input)"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
                "code_context": {
                    "sast_prescan_matches": [
                        {
                            "tool": "semgrep",
                            "rule_id": "python.lang.security.audit.eval",
                            "message": "Use of eval",
                            "severity": "error",
                            "file_path": "src/app.py",
                            "line_start": 12,
                        }
                    ]
                },
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["sast_cross_validated"] is True
    assert conflict["tool_verified"] is True
    assert conflict["confidence_breakdown"]["verification_bonus"] > 0
    assert conflict["confidence_breakdown"]["sast_match_count"] == 1
    assert any("SAST/linter 佐证" in item for item in conflict["evidence"])


def test_detect_conflicts_promotes_sast_supported_medium_even_when_verification_needed():
    state = {
        "findings": [
            {
                "finding_id": "fdg_sast_medium",
                "expert_id": "security_compliance",
                "title": "eval 调用存在注入风险",
                "summary": "新增代码直接 eval 用户输入，SAST 已命中同一行。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "code_injection_risk",
                "severity": "medium",
                "confidence": 0.82,
                "verification_needed": True,
                "file_path": "src/app.py",
                "line_start": 12,
                "evidence": ["eval(user_input)"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
                "code_context": {
                    "sast_prescan_matches": [
                        {
                            "tool": "semgrep",
                            "rule_id": "python.lang.security.audit.eval",
                            "message": "Use of eval",
                            "file_path": "src/app.py",
                            "line_start": 12,
                        }
                    ]
                },
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["issue_filter_decisions"] == []
    assert result["conflicts"][0]["sast_cross_validated"] is True


def test_detect_conflicts_promotes_observation_signal_medium_with_enough_evidence():
    state = {
        "findings": [
            {
                "finding_id": "fdg_obs_medium",
                "expert_id": "performance_reliability",
                "title": "循环内远程调用放大",
                "summary": "观察信号发现新增循环内逐条调用外部 client。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "loop_call_amplification",
                "severity": "medium",
                "confidence": 0.83,
                "verification_needed": True,
                "file_path": "src/OrderService.java",
                "line_start": 42,
                "evidence": ["for item in orders", "client.fetch(item.id)"],
                "cross_file_evidence": [],
                "context_files": ["src/OrderService.java"],
                "matched_rules": [],
                "violated_guidelines": [],
                "confidence_breakdown": {"evidence_source": "observation_signal"},
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert result["issue_filter_decisions"] == []


def test_detect_conflicts_uses_issue_jurisdiction_for_primary_expert():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_sec_1",
                "expert_id": "maintainability_code_health",
                "title": "接口存在注入风险",
                "summary": "新增拼接 SQL 的代码需要收敛到安全边界。",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "code_injection_risk",
                "severity": "high",
                "confidence": 0.86,
                "verification_needed": False,
                "file_path": "src/app/UserRepository.java",
                "line_start": 42,
                "evidence": ["拼接 SQL"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            },
            {
                "finding_id": "fdg_sec_2",
                "expert_id": "security_compliance",
                "title": "接口存在注入风险",
                "summary": "用户输入进入 SQL 字符串拼接，属于注入风险。",
                "finding_type": "direct_defect",
                "normalized_issue_type": "code_injection_risk",
                "severity": "high",
                "confidence": 0.88,
                "verification_needed": False,
                "file_path": "src/app/UserRepository.java",
                "line_start": 42,
                "evidence": ["用户输入进入 SQL 字符串拼接"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["primary_expert_id"] == "security_compliance"
    assert conflict["supporting_expert_ids"] == ["maintainability_code_health"]


def test_detect_conflicts_does_not_merge_when_severity_gap_is_large():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_high",
                "expert_id": "security_compliance",
                "title": "权限绕过风险",
                "summary": "资源级鉴权被绕过。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": False,
                "file_path": "src/app/OrderController.java",
                "line_start": 18,
                "evidence": ["未授权路径可达"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["SEC-AUTH-001"],
                "violated_guidelines": ["高风险接口必须鉴权"],
            },
            {
                "finding_id": "fdg_low",
                "expert_id": "maintainability_code_health",
                "title": "权限绕过风险",
                "summary": "注释表述可以更清晰。",
                "finding_type": "design_concern",
                "severity": "low",
                "confidence": 0.86,
                "verification_needed": True,
                "file_path": "src/app/OrderController.java",
                "line_start": 18,
                "evidence": ["注释中提到权限"],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": ["DOC-001"],
                "violated_guidelines": ["注释需清晰"],
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 2


def test_detect_conflicts_keeps_each_finding_as_separate_issue_in_thorough_review_mode():
    state = {
        "runtime_settings": {"review_quality_mode": "thorough_review"},
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_limit",
                "expert_id": "performance_reliability",
                "title": "查询删除 LIMIT 导致无界读取",
                "summary": "SELECT 删除 LIMIT :chunk 后可能一次性读取全部事件。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.91,
                "verification_needed": False,
                "file_path": "src/EventConsumer.java",
                "line_start": 19,
                "evidence": ["SELECT * FROM domain_events ORDER BY occurred_on ASC"],
                "cross_file_evidence": [],
                "context_files": ["src/EventConsumer.java"],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["查询必须保留分页或 LIMIT"],
                "normalized_issue_type": "query_bound_removed",
            },
            {
                "finding_id": "fdg_catch",
                "expert_id": "correctness_business",
                "title": "catch 块静默吞掉异常",
                "summary": "catch 块删除日志后为空，异常无法被感知。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": False,
                "file_path": "src/EventConsumer.java",
                "line_start": 19,
                "evidence": ["catch (Exception e) { }"],
                "cross_file_evidence": [],
                "context_files": ["src/EventConsumer.java"],
                "matched_rules": ["REL-ERR-001"],
                "violated_guidelines": ["异常不能被静默吞掉"],
                "normalized_issue_type": "exception_swallowed",
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 2
    assert {item["issue_id"] for item in result["conflicts"]} == {"fdg_limit", "fdg_catch"}
    assert all("同一代码行存在" not in item["title"] for item in result["conflicts"])
    limit_issue = next(item for item in result["conflicts"] if item["issue_id"] == "fdg_limit")
    assert limit_issue["summary"] == "SELECT 删除 LIMIT :chunk 后可能一次性读取全部事件。"


def test_detect_conflicts_dedupes_same_problem_same_line_in_thorough_review_mode():
    state = {
        "runtime_settings": {"review_quality_mode": "thorough_review"},
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_sql_security",
                "expert_id": "security_compliance",
                "title": "查询访问范围可能被放大",
                "summary": "builder.equal 改成 builder.like 后扩大了安全边界字段的查询范围。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": False,
                "file_path": "src/HibernateCriteriaConverter.java",
                "line_start": 60,
                "evidence": ["builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()))"],
                "cross_file_evidence": [],
                "context_files": ["src/HibernateCriteriaConverter.java"],
                "matched_rules": ["SEC-SQL-001"],
                "violated_guidelines": ["安全边界字段查询必须保持精确匹配"],
                "normalized_issue_type": "query_authorization_scope_broadened",
            },
            {
                "finding_id": "fdg_sql_business",
                "expert_id": "correctness_business",
                "title": "查询访问范围可能被放大",
                "summary": "builder.equal 改成 builder.like 后扩大了安全边界字段的查询范围。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.88,
                "verification_needed": False,
                "file_path": "src/HibernateCriteriaConverter.java",
                "line_start": 60,
                "evidence": ["builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()))"],
                "cross_file_evidence": [],
                "context_files": ["src/HibernateCriteriaConverter.java"],
                "matched_rules": ["CORR-SQL-001"],
                "violated_guidelines": ["查询语义不能扩大业务访问范围"],
                "normalized_issue_type": "query_authorization_scope_broadened",
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert set(conflict["finding_ids"]) == {"fdg_sql_security", "fdg_sql_business"}
    assert set(conflict["participant_expert_ids"]) == {"security_compliance", "correctness_business"}


def test_detect_conflicts_dedupes_same_issue_type_same_line_with_different_titles_in_thorough_review_mode():
    state = {
        "runtime_settings": {"review_quality_mode": "thorough_review"},
        "issue_filter_config": {"issue_filter_enabled": False},
        "findings": [
            {
                "finding_id": "fdg_query_db",
                "expert_id": "database_analysis",
                "title": "查询语义变更可能导致索引失效和结果集扩大",
                "summary": "equal 改成 like 后查询范围扩大。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.9,
                "verification_needed": False,
                "file_path": "src/HibernateCriteriaConverter.java",
                "line_start": 16,
                "evidence": ["builder.like(...)"],
                "cross_file_evidence": [],
                "context_files": ["src/HibernateCriteriaConverter.java"],
                "matched_rules": ["PERF-SQL-001"],
                "violated_guidelines": ["共享过滤器必须保持精确查询语义"],
                "normalized_issue_type": "query_semantics_weakened",
            },
            {
                "finding_id": "fdg_query_sec",
                "expert_id": "security_compliance",
                "title": "LIKE query missing wildcard escape allows result set expansion attacks",
                "summary": "equal 改成 like 后安全边界字段可能被放宽。",
                "finding_type": "direct_defect",
                "severity": "high",
                "confidence": 0.88,
                "verification_needed": False,
                "file_path": "src/HibernateCriteriaConverter.java",
                "line_start": 16,
                "evidence": ["builder.like(...)"],
                "cross_file_evidence": [],
                "context_files": ["src/HibernateCriteriaConverter.java"],
                "matched_rules": ["SEC-JDDD-002"],
                "violated_guidelines": ["安全边界字段不能从精确匹配退化为模糊匹配"],
                "normalized_issue_type": "query_semantics_weakened",
            },
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    assert set(result["conflicts"][0]["finding_ids"]) == {"fdg_query_db", "fdg_query_sec"}


def test_detect_conflicts_builds_specific_summary_for_inventory_todo_contract():
    state = {
        "runtime_settings": {"review_quality_mode": "thorough_review"},
        "issue_filter_config": {"issue_filter_enabled": False},
        "findings": [
            {
                "finding_id": "fdg_todo_inventory",
                "expert_id": "correctness_business",
                "title": "注释承诺未实现",
                "summary": "本次 diff 新增或保留了 TODO/注释承诺，但当前实现没有对应业务动作，调用方会误以为该能力已经落地。",
                "finding_type": "risk_hypothesis",
                "severity": "high",
                "confidence": 0.78,
                "verification_needed": True,
                "file_path": "src/mooc/main/tv/codely/mooc/courses/application/enroll/BulkEnrollmentService.java",
                "line_start": 37,
                "evidence": [
                    "+ // TODO 批量报名成功后扣减库存并发送预占事件",
                    "+ eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()))",
                ],
                "cross_file_evidence": [],
                "context_files": ["src/mooc/main/tv/codely/mooc/courses/application/enroll/BulkEnrollmentService.java"],
                "matched_rules": ["CORRECTNESS-CONTRACT-001"],
                "violated_guidelines": ["注释、TODO、接口说明和方法意图必须与真实实现保持一致"],
                "normalized_issue_type": "comment_contract_unimplemented",
            }
        ],
    }

    result = detect_conflicts(state)

    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["title"] == "TODO 里的库存扣减未实现"
    assert "扣减库存" in conflict["summary"]
    assert "预占事件" in conflict["summary"]


def test_detect_conflicts_does_not_upgrade_expert_failure_placeholder_even_when_filter_disabled():
    state = {
        "issue_filter_config": {
            "issue_filter_enabled": False,
        },
        "findings": [
            {
                "finding_id": "fdg_failure_placeholder",
                "expert_id": "security_compliance",
                "title": "安全与合规专家 执行失败后保守保留的待验证风险",
                "summary": "",
                "finding_type": "risk_hypothesis",
                "severity": "medium",
                "confidence": 0.28,
                "verification_needed": True,
                "file_path": "src/CourseCreator.java",
                "line_start": 20,
                "evidence": [
                    "专家执行失败: 1 validation error for ReviewRuleCheckResult",
                    "规则命中: Repository、Mapper 与 Query 组装必须避免动态拼接与越权查询",
                ],
                "cross_file_evidence": [],
                "context_files": [],
                "matched_rules": [],
                "violated_guidelines": [],
            }
        ],
    }

    result = detect_conflicts(state)

    assert result["conflicts"] == []
    assert result["issue_filter_decisions"][0]["rule_code"] == "expert_failure_placeholder"
