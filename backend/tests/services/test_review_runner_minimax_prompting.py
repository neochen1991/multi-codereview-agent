from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMResolution, LLMTextResult
from app.services.model_prompt_profiles import resolve_model_prompt_profile
from app.services.review_runner import ReviewRunner


def test_general_rule_contract_does_not_force_full_file_context(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    cards = runner._build_rule_guided_rule_cards(
        {},
        ["检查当前 hunk 是否有直接代码风险"],
        max_rules_per_prompt=8,
    )

    assert "GENERAL-EXPERT-CHECKS" in cards
    assert "target_hunks" in cards
    assert "current_code_excerpt" in cards
    assert "changed_file_full_content" not in cards


def test_minimax_skips_separate_prepass_for_general_fallback_rule(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    profile = resolve_model_prompt_profile("MiniMax-M2.7", profile_name="auto")

    assert (
        runner._should_run_rule_guided_prepass(
            prompt_profile=profile,
            rule_screening={"matched_rules_for_llm": []},
            required_rule_ids=["GENERAL-EXPERT-CHECKS"],
        )
        is False
    )
    assert (
        runner._should_run_rule_guided_prepass(
            prompt_profile=profile,
            rule_screening={"matched_rules_for_llm": [{"rule_id": "SEC-JDDD-002"}]},
            required_rule_ids=["SEC-JDDD-002"],
        )
        is True
    )


def test_minimax_expert_prompt_uses_short_rule_guided_contract(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/ddd",
        target_ref="main",
        title="DDD review",
        changed_files=["src/mooc/courses/application/create/CourseCreator.java"],
        unified_diff=(
            "diff --git a/src/mooc/courses/application/create/CourseCreator.java b/src/mooc/courses/application/create/CourseCreator.java\n"
            "--- a/src/mooc/courses/application/create/CourseCreator.java\n"
            "+++ b/src/mooc/courses/application/create/CourseCreator.java\n"
            "@@ -16,1 +16,1 @@\n"
            "+ Course course = new Course(id, name);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD 架构专家",
        role="ddd",
        model="minimax-2.5",
        review_spec="这是一段很长的专家规范正文，不应该在 minimax 短 prompt 中整段展开。",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/mooc/courses/application/create/CourseCreator.java",
        16,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={"summary": "Course 聚合根存在 Course.create 工厂方法。"},
        target_hunk={"hunk_header": "@@ -16,1 +16,1 @@", "excerpt": "+ Course course = new Course(id, name);"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["不要在缺少上下文时推断"],
        expected_checks=["检查是否绕过聚合工厂"],
        active_skills=[],
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "ARCH-JDDD-002",
                    "title": "应用服务不得绕过聚合工厂",
                    "priority": "P1",
                    "must_check_items": ["是否直接 new 聚合根"],
                    "false_positive_guards": ["构造函数本身是唯一合法工厂时不要报"],
                    "normalized_issue_type": "aggregate_factory_bypassed",
                }
            ]
        },
        model_name="minimax-2.5",
    )

    assert "[SYSTEM RULES]" in prompt
    assert "[PROMPT_CONTRACT]" in prompt
    assert '"phase": "rule_guided_expert_scan"' in prompt
    assert '"non_goals"' in prompt
    assert "[RULE_CARDS]" in prompt
    assert "[CONTEXT_PACKET]" in prompt
    assert "[OUTPUT_JSON]" in prompt
    assert "rule_check_results" in prompt
    assert "insufficient_context" in prompt
    assert "ARCH-JDDD-002" in prompt
    assert "Course course = new Course" in prompt
    assert "[EXPERT_PROFILE]" in prompt
    assert "专家审视规范摘要" in prompt
    assert "这是一段很长的专家规范正文" in prompt
    assert "语言通用规范" in prompt
    assert "只能描述一个具体问题、一个主文件和一个主代码位置" in prompt
    assert "每条 finding 的 JSON 字段要求" not in prompt


def test_minimax_prompt_schema_does_not_pin_candidate_to_first_file(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/composite",
        target_ref="main",
        title="Composite Java review",
        changed_files=[
            "src/app/CourseCreator.java",
            "src/app/BulkEnrollmentService.java",
        ],
        unified_diff=(
            "diff --git a/src/app/CourseCreator.java b/src/app/CourseCreator.java\n"
            "@@ -1,1 +1,1 @@\n"
            "+ Course course = new Course(id, name);\n"
            "diff --git a/src/app/BulkEnrollmentService.java b/src/app/BulkEnrollmentService.java\n"
            "@@ -10,2 +10,3 @@\n"
            "- repository.saveAll(enrollments);\n"
            "+ for (CourseEnrollment enrollment : enrollments) {\n"
            "+     repository.save(enrollment);\n"
            "+ }\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="performance_reliability",
        name="Performance",
        name_zh="性能与可靠性专家",
        role="performance",
        model="MiniMax-M2.7",
        review_spec="检查批量路径是否退化为逐条调用。",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/app/CourseCreator.java",
        1,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={},
        target_hunk={"hunk_header": "@@ -1,1 +1,1 @@", "excerpt": "+ Course course = new Course(id, name);"},
        target_hunks=[
            {"hunk_header": "@@ -1,1 +1,1 @@", "excerpt": "+ Course course = new Course(id, name);", "changed_lines": [1]},
            {
                "file_path": "src/app/BulkEnrollmentService.java",
                "hunk_header": "@@ -10,2 +10,3 @@",
                "excerpt": "- repository.saveAll(enrollments);\n+ for (CourseEnrollment enrollment : enrollments) {\n+     repository.save(enrollment);\n+ }",
                "changed_lines": [10, 11, 12],
            },
        ],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["检查循环内仓储调用"],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": []},
        model_name="MiniMax-M2.7",
        include_target_file_full_diff=False,
    )

    assert '"target_id": "必须从 TARGET_HUNKS[].target_id 原样选择"' in prompt
    assert '"file_path": "必须从 TARGET_HUNKS[].file_path 原样选择"' in prompt
    assert '"line": "必须取对应 hunk changed_lines 中的当前代码行号"' in prompt
    assert '"file_path": "src/app/CourseCreator.java"' not in prompt.split("[OUTPUT_JSON]", 1)[1]


def test_expert_language_guidance_is_scoped_by_expert(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    correctness_guidance = runner._build_expert_language_general_guidance("java", "correctness_business")
    ddd_guidance = runner._build_expert_language_general_guidance("java", "ddd_architecture")
    database_guidance = runner._build_expert_language_general_guidance("java", "database_analysis")

    assert "注释/TODO/接口承诺" in correctness_guidance
    assert "不主提命名、代码风格、索引、分页、N+1" in correctness_guidance
    assert "聚合工厂、聚合边界" in ddd_guidance
    assert "不主提命名、普通代码风格、SQL 分页、N+1" in ddd_guidance
    assert "无分页、全表扫描、N+1" in database_guidance


def test_non_minimax_model_also_uses_rule_guided_quality_contract(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/ddd",
        target_ref="main",
        title="DDD review",
        changed_files=["src/mooc/courses/application/create/CourseCreator.java"],
        unified_diff=(
            "diff --git a/src/mooc/courses/application/create/CourseCreator.java b/src/mooc/courses/application/create/CourseCreator.java\n"
            "--- a/src/mooc/courses/application/create/CourseCreator.java\n"
            "+++ b/src/mooc/courses/application/create/CourseCreator.java\n"
            "@@ -16,1 +16,1 @@\n"
            "+ Course course = new Course(id, name);\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD 架构专家",
        role="ddd",
        model="doubao-seed-2.0-code",
        review_spec="非 minimax 模型也不能绕过规则驱动质量框架。",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/mooc/courses/application/create/CourseCreator.java",
        16,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={"summary": "Course 聚合根存在 Course.create 工厂方法。"},
        target_hunk={"hunk_header": "@@ -16,1 +16,1 @@", "excerpt": "+ Course course = new Course(id, name);"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["不要在缺少上下文时推断"],
        expected_checks=["检查是否绕过聚合工厂"],
        active_skills=[],
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "ARCH-JDDD-002",
                    "title": "应用服务不得绕过聚合工厂",
                    "priority": "P1",
                    "must_check_items": ["是否直接 new 聚合根"],
                    "false_positive_guards": ["构造函数本身是唯一合法工厂时不要报"],
                    "normalized_issue_type": "aggregate_factory_bypassed",
                }
            ]
        },
        model_name="doubao-seed-2.0-code",
    )

    assert "[RULE_CARDS]" in prompt
    assert "[CONTEXT_PACKET]" in prompt
    assert "[EXPERT_PROFILE]" in prompt
    assert "rule_check_results" in prompt
    assert "candidate_findings" in prompt
    assert "不要把多个文件、多个风险点或多个修复方向合并成一条" in prompt
    assert "非 minimax 模型也不能绕过规则驱动质量框架" in prompt


def test_security_java_prompt_keeps_context_gap_candidates_instead_of_suppressing_them(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/security",
        target_ref="main",
        title="Security review",
        changed_files=["src/main/java/com/acme/order/OrderRepository.java"],
        unified_diff=(
            "diff --git a/src/main/java/com/acme/order/OrderRepository.java b/src/main/java/com/acme/order/OrderRepository.java\n"
            "--- a/src/main/java/com/acme/order/OrderRepository.java\n"
            "+++ b/src/main/java/com/acme/order/OrderRepository.java\n"
            "@@ -72,1 +72,1 @@\n"
            '+ return builder.like(root.get("name"), String.format("%%%s%%", keyword));\n'
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全合规专家",
        role="检查鉴权、输入校验、敏感数据和安全边界",
        model="minimax-2.5",
        review_spec="按安全专家职责检查真实代码风险。",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/com/acme/order/OrderRepository.java",
        72,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={"summary": "Java repository security context", "java_review_mode": "general"},
        target_hunk={
            "hunk_header": "@@ -72,1 +72,1 @@",
            "excerpt": '+ return builder.like(root.get("name"), String.format("%%%s%%", keyword));',
        },
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=["证据不足时不要输出 finding"],
        expected_checks=["检查输入校验和 SQL/Criteria 查询安全"],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": []},
        model_name="minimax-2.5",
    )

    assert "若结论依赖未展示的鉴权实现，不要输出该条" not in prompt
    assert "有当前代码证据但缺少鉴权、租户或输入校验上下文时" in prompt
    assert "candidate_findings" in prompt
    assert "context_requests" in prompt


def test_legacy_expert_prompt_keeps_evidenced_uncertain_findings_instead_of_silent_drop(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/security",
        target_ref="main",
        title="Legacy prompt",
        changed_files=["src/main/java/demo/UserController.java"],
        unified_diff=(
            "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
            "--- a/src/main/java/demo/UserController.java\n"
            "+++ b/src/main/java/demo/UserController.java\n"
            "@@ -1,1 +1,1 @@\n"
            "+ log.info(\"phone={}\", user.getPhone());\n"
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="legacy-compatible-model",
    )

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/main/java/demo/UserController.java",
        1,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context={"summary": "新增手机号日志输出。"},
        target_hunk={"hunk_header": "@@ -1,1 +1,1 @@", "excerpt": "+ log.info(\"phone={}\", user.getPhone());"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["敏感信息输出"],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": []},
        model_name="legacy-compatible-model",
        prompt_profile_name="legacy",
    )

    assert "证据不足时不要输出 finding" not in prompt
    assert "请直接不输出该条 finding" not in prompt
    assert "有当前变更代码位置但缺上下文" in prompt
    assert "verification_needed" in prompt


def test_empty_rule_guided_response_retries_and_preserves_candidate(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_empty_retry",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/security",
            target_ref="main",
            title="Security retry",
            changed_files=["src/main/java/demo/UserController.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
                "--- a/src/main/java/demo/UserController.java\n"
                "+++ b/src/main/java/demo/UserController.java\n"
                "@@ -1,1 +1,1 @@\n"
                "+ log.info(\"phone={}\", user.getPhone());\n"
            ),
        ),
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="必须检查敏感信息输出。",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查安全问题",
        metadata={},
    )
    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    empty_structured = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"passed",'
        '"evidence":[],"missing_context":[],"reason":"未发现问题"}],'
        '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    retry_candidate = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"violated",'
        '"evidence":["log.info 输出 user.getPhone()"],"missing_context":[],"reason":"手机号属于敏感信息，不应明文写入日志"}],'
        '"candidate_findings":[{"rule_id":"GENERAL-EXPERT-CHECKS","title":"日志明文输出手机号",'
        '"file_path":"src/main/java/demo/UserController.java","line":1,'
        '"evidence":"log.info(\\"phone={}\\", user.getPhone())","confidence":"high"}],'
        '"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    phases: list[str] = []

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        phases.append(phase)
        text = retry_candidate if phase == "expert_empty_candidate_retry" else empty_structured
        return LLMTextResult(
            text=text,
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"call-{phase or 'main'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/demo/UserController.java",
        line_start=1,
        repository_context={"summary": "新增日志输出手机号。"},
        target_hunk={
            "hunk_header": "@@ -1,1 +1,1 @@",
            "start_line": 1,
            "end_line": 1,
            "changed_lines": [1],
            "excerpt": "+ log.info(\"phone={}\", user.getPhone());",
        },
        target_hunks=[],
        related_files=[],
        expected_checks=["敏感信息输出"],
        disallowed_inference=[],
        runtime_settings=RuntimeSettings(review_quality_mode="standard"),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={"matched_rules_for_llm": []},
        finding_payloads=[],
    )

    findings = runner.finding_repo.list(review.review_id)
    assert "expert_empty_candidate_retry" in phases
    assert len(findings) == 1
    assert "日志明文输出手机号" in findings[0].title
    assert "GENERAL-EXPERT-CHECKS" in findings[0].matched_rules


def test_bound_custom_rules_are_scanned_in_batches_after_empty_main_review(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_custom_rule_batches",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/security-batch",
            target_ref="main",
            title="Security custom rule batch",
            changed_files=["src/main/java/demo/SyncService.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/SyncService.java b/src/main/java/demo/SyncService.java\n"
                "--- a/src/main/java/demo/SyncService.java\n"
                "+++ b/src/main/java/demo/SyncService.java\n"
                "@@ -10,1 +10,1 @@\n"
                "+ for (User user : users) { externalRiskClient.check(user.getId()); }\n"
            ),
        ),
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="必须按绑定安全规范检查 Java 和 SQL 安全风险。",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查安全问题",
        metadata={},
    )
    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    empty_structured = (
        '{"rule_check_results":[{"rule_id":"SEC-JAVA-LOOP-IO-001","status":"passed",'
        '"evidence":[],"missing_context":[],"reason":"未发现问题"}],'
        '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/SyncService.java"],"unverified_assumptions":[]}}'
    )
    custom_rule_candidate = (
        '{"rule_check_results":[{"rule_id":"SEC-JAVA-LOOP-IO-001","status":"violated",'
        '"evidence":["for 循环中调用 externalRiskClient.check"],"missing_context":[],'
        '"reason":"绑定安全规范禁止在循环中逐条调用外部接口，容易造成级联放大和限流绕过"}],'
        '"candidate_findings":[{"rule_id":"SEC-JAVA-LOOP-IO-001","title":"循环中逐条调用外部风控接口",'
        '"file_path":"src/main/java/demo/SyncService.java","line":10,'
        '"evidence":"for (User user : users) { externalRiskClient.check(user.getId()); }",'
        '"confidence":"high"}],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/SyncService.java"],"unverified_assumptions":[]}}'
    )
    phases: list[str] = []
    prompts: dict[str, str] = {}

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        phases.append(phase)
        prompts[phase] = str(kwargs.get("user_prompt") or "")
        text = custom_rule_candidate if phase == "expert_custom_rule_batch_scan" else empty_structured
        return LLMTextResult(
            text=text,
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"call-{phase or 'main'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/demo/SyncService.java",
        line_start=10,
        repository_context={"summary": "新增循环内外部风控接口调用。"},
        target_hunk={
            "hunk_header": "@@ -10,1 +10,1 @@",
            "start_line": 10,
            "end_line": 10,
            "changed_lines": [10],
            "excerpt": "+ for (User user : users) { externalRiskClient.check(user.getId()); }",
        },
        target_hunks=[],
        related_files=[],
        expected_checks=["安全专家应检查接口滥用和资源放大风险"],
        disallowed_inference=[],
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "SEC-JAVA-LOOP-IO-001",
                    "title": "禁止在循环中逐条调用外部接口或数据库",
                    "priority": "P1",
                    "must_check_items": ["检查新增循环体是否调用外部 HTTP/RPC/DB 操作"],
                    "required_context": ["changed_file_full_content", "repository_context"],
                    "evidence_required": ["循环代码行", "外部接口或数据库调用代码行"],
                    "false_positive_guards": ["已显式批量化、限流、短路和超时保护时不要误报"],
                    "normalized_issue_type": "loop_external_io_amplification",
                }
            ]
        },
        finding_payloads=[],
    )

    findings = runner.finding_repo.list(review.review_id)
    assert "expert_custom_rule_batch_scan" in phases
    assert "[CUSTOM_BOUND_RULE_REVIEW_ONLY]" in prompts["expert_custom_rule_batch_scan"]
    assert "[PROMPT_CONTRACT]" in prompts["expert_custom_rule_batch_scan"]
    assert '"phase": "custom_rule_batch_scan"' in prompts["expert_custom_rule_batch_scan"]
    assert "不要输出 GENERAL-EXPERT-CHECKS" in prompts["expert_custom_rule_batch_scan"]
    assert "[CUSTOM_RULE_BATCH]" in prompts["expert_custom_rule_batch_scan"]
    assert "SEC-JAVA-LOOP-IO-001" in prompts["expert_custom_rule_batch_scan"]
    custom_messages = [
        message
        for message in runner.message_repo.list(review.review_id)
        if message.message_type == "expert_custom_rule_batch_scan"
    ]
    assert custom_messages
    assert custom_messages[-1].metadata["custom_rule_batch_scan"]["prompt_contract"]["valid"] is True
    assert len(findings) == 1
    assert "循环中逐条调用外部风控接口" in findings[0].title
    assert "SEC-JAVA-LOOP-IO-001" in findings[0].matched_rules


def test_empty_review_without_bound_rule_hits_runs_general_expert_profile_scan(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_general_profile_scan",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/general-scan",
            target_ref="main",
            title="General expert scan",
            changed_files=["src/main/java/demo/UserController.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
                "--- a/src/main/java/demo/UserController.java\n"
                "+++ b/src/main/java/demo/UserController.java\n"
                "@@ -20,1 +20,1 @@\n"
                "+ log.info(\"token={}\", request.getHeader(\"Authorization\"));\n"
            ),
        ),
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="必须按照安全专家画像检查敏感信息泄露、权限绕过和输入边界。",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查安全问题",
        metadata={},
    )
    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    empty_structured = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"passed",'
        '"evidence":[],"missing_context":[],"reason":"未发现问题"}],'
        '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    general_candidate = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"violated",'
        '"evidence":["log.info 输出 Authorization header"],"missing_context":[],'
        '"reason":"安全专家画像要求检查敏感信息泄露，Authorization token 不应写入日志"}],'
        '"candidate_findings":[{"rule_id":"GENERAL-EXPERT-CHECKS","title":"日志明文输出 Authorization token",'
        '"file_path":"src/main/java/demo/UserController.java","line":20,'
        '"evidence":"log.info(\\"token={}\\", request.getHeader(\\"Authorization\\"))",'
        '"confidence":"high"}],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    phases: list[str] = []
    prompts: dict[str, str] = {}

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        phases.append(phase)
        prompts[phase] = str(kwargs.get("user_prompt") or "")
        text = general_candidate if phase == "expert_general_profile_scan" else empty_structured
        return LLMTextResult(
            text=text,
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"call-{phase or 'main'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/demo/UserController.java",
        line_start=20,
        repository_context={"summary": "新增 Authorization header 日志输出。"},
        target_hunk={
            "hunk_header": "@@ -20,1 +20,1 @@",
            "start_line": 20,
            "end_line": 20,
            "changed_lines": [20],
            "excerpt": "+ log.info(\"token={}\", request.getHeader(\"Authorization\"));",
        },
        target_hunks=[],
        related_files=[],
        expected_checks=["安全专家画像通用检查"],
        disallowed_inference=[],
        runtime_settings=runner.runtime_settings_service.get(),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={"matched_rules_for_llm": []},
        finding_payloads=[],
    )

    findings = runner.finding_repo.list(review.review_id)
    assert "expert_general_profile_scan" in phases
    assert "[GENERAL_EXPERT_PROFILE_REVIEW_ONLY]" in prompts["expert_general_profile_scan"]
    assert "[PROMPT_CONTRACT]" in prompts["expert_general_profile_scan"]
    assert '"phase": "general_expert_scan"' in prompts["expert_general_profile_scan"]
    assert "不要检查 CUSTOM_RULE_BATCH" in prompts["expert_general_profile_scan"]
    assert "专家画像" in prompts["expert_general_profile_scan"]
    general_messages = [
        message
        for message in runner.message_repo.list(review.review_id)
        if message.message_type == "expert_general_profile_scan"
    ]
    assert general_messages
    assert general_messages[-1].metadata["general_expert_profile_scan"]["prompt_contract"]["valid"] is True
    assert len(findings) == 1
    assert "Authorization token" in findings[0].title
    assert "GENERAL-EXPERT-CHECKS" in findings[0].matched_rules


def test_thorough_review_runs_general_scan_even_when_main_review_finds_custom_rule(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_thorough_union_scan",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/thorough-union",
            target_ref="main",
            title="Thorough review union",
            changed_files=["src/main/java/demo/UserController.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
                "--- a/src/main/java/demo/UserController.java\n"
                "+++ b/src/main/java/demo/UserController.java\n"
                "@@ -20,2 +20,2 @@\n"
                "+ log.info(\"token={}\", request.getHeader(\"Authorization\"));\n"
                "+ for (User user : users) { externalRiskClient.check(user.getId()); }\n"
            ),
        ),
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="必须按照安全专家画像检查敏感信息泄露、接口滥用和输入边界。",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查安全问题",
        metadata={},
    )
    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    custom_candidate = (
        '{"rule_check_results":[{"rule_id":"SEC-JAVA-LOOP-IO-001","status":"violated",'
        '"evidence":["for 循环中调用 externalRiskClient.check"],"missing_context":[],'
        '"reason":"绑定安全规范禁止循环中逐条调用外部接口"}],'
        '"candidate_findings":[{"rule_id":"SEC-JAVA-LOOP-IO-001","title":"循环中逐条调用外部风控接口",'
        '"file_path":"src/main/java/demo/UserController.java","line":21,'
        '"evidence":"for (User user : users) { externalRiskClient.check(user.getId()); }",'
        '"confidence":"high"}],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    general_candidate = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"violated",'
        '"evidence":["log.info 输出 Authorization header"],"missing_context":[],'
        '"reason":"安全专家画像要求检查敏感信息泄露，Authorization token 不应写入日志"}],'
        '"candidate_findings":[{"rule_id":"GENERAL-EXPERT-CHECKS","title":"日志明文输出 Authorization token",'
        '"file_path":"src/main/java/demo/UserController.java","line":20,'
        '"evidence":"log.info(\\"token={}\\", request.getHeader(\\"Authorization\\"))",'
        '"confidence":"high"}],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    tool_candidate = (
        '{"rule_check_results":[{"rule_id":"sast:semgrep:java.spring.security.audit.token-log:src/main/java/demo/UserController.java:20","status":"violated",'
        '"evidence":["semgrep 命中 Authorization header 进入日志"],"missing_context":[],'
        '"reason":"工具信号与本次新增日志行一致"}],'
        '"candidate_findings":[{"rule_id":"sast:semgrep:java.spring.security.audit.token-log:src/main/java/demo/UserController.java:20","title":"工具确认 Authorization token 日志风险",'
        '"file_path":"src/main/java/demo/UserController.java","line":20,'
        '"evidence":"log.info(\\"token={}\\", request.getHeader(\\"Authorization\\"))",'
        '"confidence":"high","adopted_tool_observations":["sast:semgrep:java.spring.security.audit.token-log:src/main/java/demo/UserController.java:20"]}],'
        '"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    phases: list[str] = []

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        phases.append(phase)
        if phase == "expert_general_profile_scan":
            text = general_candidate
        elif phase == "expert_tool_observation_scan":
            text = tool_candidate
        elif phase == "expert_custom_rule_batch_scan":
            text = custom_candidate
        else:
            raise AssertionError(f"unexpected LLM phase in split pipeline test: {phase}")
        return LLMTextResult(
            text=text,
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"call-{phase or 'main'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/demo/UserController.java",
        line_start=20,
        target_hunk={
            "hunk_header": "@@ -20,2 +20,2 @@",
            "start_line": 20,
            "end_line": 21,
            "changed_lines": [20, 21],
            "excerpt": (
                '+ log.info("token={}", request.getHeader("Authorization"));\n'
                "+ for (User user : users) { externalRiskClient.check(user.getId()); }"
            ),
        },
        target_hunks=[],
        related_files=[],
        expected_checks=["安全专家画像通用检查"],
        disallowed_inference=[],
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        repository_context={
            "summary": "新增 token 日志与循环外部接口调用。",
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.spring.security.audit.token-log",
                    "observation_id": "semgrep:java.spring.security.audit.token-log:20",
                    "category": "security",
                    "file_path": "src/main/java/demo/UserController.java",
                    "line_start": 20,
                    "message": "Authorization header reaches application log.",
                    "confidence": 0.86,
                    "is_issue": False,
                    "expert_must_decide": True,
                }
            ],
        },
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "SEC-JAVA-LOOP-IO-001",
                    "title": "禁止在循环中逐条调用外部接口",
                    "priority": "P1",
                    "must_check_items": ["检查新增循环体是否调用外部 HTTP/RPC 操作"],
                    "required_context": ["changed_file_full_content", "repository_context"],
                    "evidence_required": ["循环代码行", "外部接口调用代码行"],
                    "false_positive_guards": ["已显式批量化、限流、短路和超时保护时不要误报"],
                    "normalized_issue_type": "loop_external_io_amplification",
                }
            ]
        },
        finding_payloads=[],
    )

    findings = runner.finding_repo.list(review.review_id)
    titles = {finding.title for finding in findings}
    assert "expert_general_profile_scan" in phases
    assert "expert_tool_observation_scan" in phases
    assert any("循环中逐条调用外部风控接口" in title for title in titles)
    assert any("日志明文输出 Authorization token" in title for title in titles)
    assert any(title.startswith("工具确认 Authorization token 日志风险") for title in titles)


def test_tool_observations_are_forced_into_uncovered_followup_candidates(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
    )

    observations = runner._collect_batch_review_observations(
        {
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql-injection",
                    "observation_id": "semgrep:java.sql-injection:42",
                    "category": "security",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 42,
                    "message": "User input is concatenated into SQL.",
                    "why_it_matters": "该工具命中涉及 SQL 注入风险。",
                    "confidence": 0.86,
                }
            ]
        },
        [],
    )
    forced = runner._build_forced_observation_candidates(
        expert=expert,
        uncovered_observations=observations,
        max_findings=5,
    )

    assert observations[0]["kind"] == "tool_observation"
    assert forced
    assert forced[0]["evidence_source"] == "tool_observation"
    assert forced[0]["normalized_issue_type"] == "sql_injection_risk"
    assert "SQL/命令注入" in forced[0]["violated_guidelines"][0]
    assert forced[0]["adopted_tool_observations"] == [
        "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    ]
    assert forced[0]["verification_needed"] is True


def test_tool_observation_scan_builds_canonical_sast_ids_when_missing(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    observations = runner._collect_tool_observations_for_scan(
        {
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql-injection",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 42,
                    "message": "User input is concatenated into SQL.",
                }
            ]
        },
        [],
    )

    assert observations[0]["id"] == "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    assert observations[0]["observation_id"] == "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    assert observations[0]["legacy_observation_id"] == "semgrep:java.sql-injection:42"


def test_tool_observation_scan_filters_observations_outside_target_diff(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    observations = runner._collect_tool_observations_for_scan(
        {
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql.concat-user-input",
                    "file_path": "src/main/java/demo/UserController.java",
                    "line_start": 9,
                    "message": "String concatenation reaches SQL sink.",
                },
                {
                    "tool": "semgrep",
                    "rule_id": "java.logging.authorization-token",
                    "file_path": "src/main/java/demo/UserController.java",
                    "line_start": 20,
                    "message": "Authorization header reaches application log.",
                },
            ],
            "target_hunks": [
                {
                    "file_path": "src/main/java/demo/UserController.java",
                    "changed_lines": [20, 21],
                    "excerpt": '+ log.info("token={}", request.getHeader("Authorization"));\n',
                }
            ],
        },
        [],
    )

    rule_ids = {str(item.get("rule_id") or "") for item in observations}
    assert "java.logging.authorization-token" in rule_ids
    assert "java.sql.concat-user-input" not in rule_ids


def test_batch_review_observations_build_canonical_sast_ids_when_missing(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    observations = runner._collect_batch_review_observations(
        {
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql-injection",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 42,
                    "message": "User input is concatenated into SQL.",
                }
            ]
        },
        [],
    )

    assert observations[0]["kind"] == "tool_observation"
    assert observations[0]["id"] == "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    assert observations[0]["observation_id"] == "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    assert observations[0]["legacy_observation_id"] == "semgrep:java.sql-injection:42"


def test_business_changed_files_excludes_static_tool_config_and_reports(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/static-tooling",
        target_ref="main",
        title="Static tooling files",
        changed_files=[
            ".semgrep.yml",
            "eslint.config.js",
            "target/spotbugsXml.xml",
            "src/main/java/demo/UserController.java",
            "src/main/java/demo/UserControllerTest.java",
        ],
        unified_diff="",
    )

    assert runner._business_changed_files(subject) == ["src/main/java/demo/UserController.java"]


def test_sast_prescan_summary_and_fast_lane_create_visible_tool_finding(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_sast_fast_lane",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/security",
            target_ref="main",
            title="SAST fast lane",
            changed_files=["src/main/java/demo/UserDao.java"],
            unified_diff="",
        ),
    )
    finding_payloads: list[dict[str, object]] = []
    expert_jobs = [
        {
            "expert": ExpertProfile(
                expert_id="security_compliance",
                name="Security",
                name_zh="安全专家",
                role="security",
            ),
            "repository_context": {
                "tool_observations": [
                    {
                        "tool": "semgrep",
                        "rule_id": "java.sql-injection",
                        "category": "security",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 42,
                        "message": "User input is concatenated into SQL.",
                        "confidence": 0.88,
                    }
                ],
                "target_hunks": [
                    {
                        "file_path": "src/main/java/demo/UserDao.java",
                        "changed_lines": [42],
                        "excerpt": '+ statement.executeQuery("select * from user where id=" + userId);\n',
                    }
                ],
            },
        }
    ]

    runner._append_sast_prescan_summary_message(review, expert_jobs)
    runner._append_fast_tool_observation_findings(review, expert_jobs, finding_payloads)

    messages = runner.message_repo.list(review.review_id)
    findings = runner.finding_repo.list(review.review_id)

    assert any(message.message_type == "sast_prescan_summary" for message in messages)
    assert [finding.title for finding in findings] == ["静态工具候选需复核：java.sql-injection"]
    assert findings[0].code_context["adopted_tool_observations"] == [
        "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"
    ]
    assert finding_payloads and finding_payloads[0]["code_context"]["sast_fast_lane"] is True


def test_tool_observation_scan_fails_when_expert_omits_relevant_observation(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_tool_observation_coverage",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/security",
            target_ref="main",
            title="Tool observation coverage",
            changed_files=["src/main/java/demo/UserDao.java"],
            unified_diff="",
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
    )

    def fake_complete_text(**_kwargs):
        return LLMTextResult(
            text=(
                '{"rule_check_results":[{"rule_id":"sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42",'
                '"status":"violated","evidence":["sql concat"],"missing_context":[],'
                '"reason":"sql concat"}],"candidate_findings":[],"context_requests":[],'
                '"self_check":{"checked_all_rules":true,"used_context_files":[],'
                '"unverified_assumptions":[]}}'
            ),
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id="call-tool-observation-coverage",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    text, metadata = runner._run_rule_guided_tool_observation_scan(
        review=review,
        expert=expert,
        runtime_settings=RuntimeSettings(review_quality_mode="standard"),
        resolution=LLMResolution(
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
        ),
        normalized_batch_items=[],
        repository_context={
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql-injection",
                    "observation_id": "semgrep:java.sql-injection:42",
                    "category": "security",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 42,
                    "message": "User input is concatenated into SQL.",
                },
                {
                    "tool": "bandit",
                    "rule_id": "B105",
                    "observation_id": "bandit:B105:45",
                    "category": "python_security",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 45,
                    "message": "Possible hardcoded password.",
                },
            ]
        },
        file_path="src/main/java/demo/UserDao.java",
        line_start=42,
        timeout_seconds=60,
    )

    assert text == ""
    assert metadata["success"] is False
    assert "rule_coverage_missing:sast:bandit:B105:src/main/java/demo/UserDao.java:45" in metadata["schema_errors"]


def test_sast_prescan_match_requires_semantic_alignment_even_on_same_line(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    matches = runner._match_sast_prescan_findings(
        parsed={
            "title": "空指针风险提到了 python.lang.security.audit.eval",
            "claim": "这里讨论的是 None dereference，不是 eval 注入。",
            "normalized_issue_type": "null_pointer_risk",
            "evidence": ["python.lang.security.audit.eval"],
            "matched_rules": [],
        },
        file_path="src/app.py",
        line_start=12,
        repository_context={
            "sast_prescan": {
                "enabled": True,
                "findings": [
                    {
                        "tool": "semgrep",
                        "rule_id": "python.lang.security.audit.eval",
                        "message": "Use of eval",
                        "category": "security",
                        "cwe": "CWE-95",
                        "file_path": "src/app.py",
                        "line_start": 12,
                    }
                ],
            }
        },
    )

    assert matches == []


def test_sast_prescan_match_accepts_semantically_aligned_security_issue(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    matches = runner._match_sast_prescan_findings(
        parsed={
            "title": "eval 调用存在注入风险",
            "claim": "新增代码直接 eval 用户输入。",
            "normalized_issue_type": "code_injection_risk",
            "evidence": ["eval(user_input)", "Use of eval"],
            "matched_rules": [],
        },
        file_path="src/app.py",
        line_start=12,
        repository_context={
            "sast_prescan": {
                "enabled": True,
                "findings": [
                    {
                        "tool": "semgrep",
                        "rule_id": "python.lang.security.audit.eval",
                        "message": "Use of eval",
                        "category": "security",
                        "cwe": "CWE-95",
                        "file_path": "src/app.py",
                        "line_start": 12,
                    }
                ],
            }
        },
    )

    assert len(matches) == 1
    assert matches[0]["rule_id"] == "python.lang.security.audit.eval"


def test_expert_job_observation_collection_includes_tool_observations(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    observations = runner._collect_observations_from_expert_jobs(
        [
            {
                "repository_context": {
                    "tool_observations": [
                        {
                            "tool": "semgrep",
                            "rule_id": "java.sql-injection",
                            "observation_id": "semgrep:java.sql-injection:42",
                            "category": "security",
                            "file_path": "src/main/java/demo/UserDao.java",
                            "line_start": 42,
                            "message": "User input is concatenated into SQL.",
                            "confidence": 0.88,
                        }
                    ]
                },
                "batch_items": [
                    {
                        "repository_context": {
                            "tool_observations": [
                                {
                                    "tool": "pmd",
                                    "rule_id": "AvoidCatchingGenericException",
                                    "observation_id": "pmd:AvoidCatchingGenericException:51",
                                    "category": "java_quality",
                                    "file_path": "src/main/java/demo/Worker.java",
                                    "line_start": 51,
                                    "message": "Generic exception is swallowed.",
                                    "confidence": 0.74,
                                }
                            ]
                        }
                    }
                ],
            }
        ]
    )

    ids = {str(item.get("observation_id") or "") for item in observations}
    assert "sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42" in ids
    assert "sast:pmd:AvoidCatchingGenericException:src/main/java/demo/Worker.java:51" in ids
    assert all(item.get("kind") == "tool_observation" for item in observations)


def test_observation_followup_uses_dedicated_non_conflicting_system_prompt(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_observation_followup_system",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/security",
            target_ref="main",
            title="Observation followup",
            changed_files=["src/main/java/demo/UserDao.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/UserDao.java b/src/main/java/demo/UserDao.java\n"
                "--- a/src/main/java/demo/UserDao.java\n"
                "+++ b/src/main/java/demo/UserDao.java\n"
                "@@ -42,1 +42,1 @@\n"
                '+ String sql = "select * from user where name = " + name;\n'
            ),
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="绑定规范：本轮只审查自定义 SQL 模板，不要输出工具候选。",
    )
    captured: dict[str, str] = {}

    def fake_complete_text(**kwargs):
        captured["system_prompt"] = str(kwargs.get("system_prompt") or "")
        return LLMTextResult(
            text=(
                '{"rule_check_results":[],"candidate_findings":[],"context_requests":[],'
                '"self_check":{"checked_all_rules":true,"used_context_files":[],"unverified_assumptions":[]}}'
            ),
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id="call-observation-followup",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._append_observation_followup_candidates(
        review=review,
        subject=review.subject,
        expert=expert,
        file_path="src/main/java/demo/UserDao.java",
        line_start=42,
        repository_context={
            "tool_observations": [
                {
                    "tool": "semgrep",
                    "rule_id": "java.sql-injection",
                    "observation_id": "semgrep:java.sql-injection:42",
                    "category": "security",
                    "file_path": "src/main/java/demo/UserDao.java",
                    "line_start": 42,
                    "message": "User input is concatenated into SQL.",
                    "confidence": 0.86,
                }
            ]
        },
        normalized_batch_items=[],
        runtime_settings=RuntimeSettings(review_quality_mode="standard"),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": []},
        initial_candidates=[],
        max_findings=5,
    )

    assert "静态观察信号复核专家" in captured["system_prompt"]
    assert "只复核本轮给出的 observation" in captured["system_prompt"]
    assert "绑定规范：本轮只审查自定义 SQL 模板" not in captured["system_prompt"]
    assert "不要输出工具候选" not in captured["system_prompt"]


def test_tool_observation_candidate_keeps_tool_issue_type_during_anchor_refinement(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    candidate = {
        "title": "静态工具候选需复核：java.lang.security.audit.unsafe-reflection.unsafe-reflection",
        "claim": "semgrep 命中 unsafe-reflection 候选信号，需要安全专家确认是否真实可达。",
        "normalized_issue_type": "authorization_bypass_risk",
        "matched_rules": ["semgrep:java.lang.security.audit.unsafe-reflection.unsafe-reflection"],
        "violated_guidelines": ["涉及用户、租户、资源归属或管理操作的入口必须做鉴权和越权校验。"],
        "evidence": [
            "semgrep:java.lang.security.audit.unsafe-reflection.unsafe-reflection",
            "Class.forName(row.toString())",
        ],
        "adopted_tool_observations": ["semgrep:java.lang.security.audit.unsafe-reflection.unsafe-reflection:23"],
        "evidence_source": "tool_observation",
    }

    sanitized = runner._sanitize_candidate_to_current_anchor(
        candidate,
        "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java",
        19,
        {
            "excerpt": (
                '# MySqlDomainEventsConsumer.java\n'
                '18 | NativeQuery query = sessionFactory.getCurrentSession().createNativeQuery(\n'
                '19 | + "SELECT * FROM domain_events ORDER BY occurred_on ASC"\n'
            )
        },
    )
    result = runner._normalize_candidate_for_refined_anchor(
        sanitized,
        expert_id="correctness_business",
        line_start=19,
    )

    assert result["normalized_issue_type"] == "authorization_bypass_risk"
    assert result["title"].startswith("静态工具候选需复核")


def test_observation_followup_uses_fast_deterministic_path_after_initial_candidates(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_observation_fast_path",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/db",
            target_ref="main",
            title="Observation fast path",
            changed_files=["src/main/java/demo/Consumer.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/Consumer.java b/src/main/java/demo/Consumer.java\n"
                "@@ -19,1 +19,1 @@\n"
                '+ "SELECT * FROM domain_events ORDER BY occurred_on ASC"\n'
            ),
        ),
    )
    expert = ExpertProfile(
        expert_id="database_analysis",
        name="Database",
        name_zh="数据库专家",
        role="database",
        model="minimax-2.5",
    )

    calls = {"count": 0}

    def fake_complete_text(**_kwargs):
        calls["count"] += 1
        return LLMTextResult(
            text=(
                '{"rule_check_results":[],"candidate_findings":[],"context_requests":[],'
                '"self_check":{"checked_all_rules":true,"used_context_files":[],"unverified_assumptions":[]}}'
            ),
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id="call-observation-followup",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    merged = runner._append_observation_followup_candidates(
        review=review,
        subject=review.subject,
        expert=expert,
        file_path="src/main/java/demo/Consumer.java",
        line_start=19,
        repository_context={
            "review_observations": [
                {
                    "observation_id": "obs_query_bound",
                    "kind": "query_without_bound",
                    "file_path": "src/main/java/demo/Consumer.java",
                    "line_start": 19,
                    "summary": "LIMIT :chunk 被删除，查询缺少边界。",
                    "evidence": ['+ "SELECT * FROM domain_events ORDER BY occurred_on ASC"'],
                    "confidence": 0.82,
                }
            ]
        },
        normalized_batch_items=[],
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": []},
        initial_candidates=[
            {
                "title": "已有查询候选",
                "file_path": "src/main/java/demo/Consumer.java",
                "line_start": 18,
                "claim": "已有候选",
                "confidence": 0.72,
            }
        ],
        max_findings=5,
    )

    assert calls["count"] == 0
    assert any(item.get("normalized_issue_type") == "query_bound_removed" for item in merged)


def test_custom_rule_scan_batches_use_all_bound_rules_not_only_screening_hits(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    batches = runner._split_custom_rule_scan_batches(
        {
            "matched_rules_for_llm": [],
            "all_enabled_rules_for_llm": [
                {
                    "rule_id": "SEC-JAVA-SQL-001",
                    "title": "SQL 必须参数化",
                    "priority": "P0",
                    "must_check_items": ["检查新增 SQL 是否拼接用户输入"],
                    "required_context": ["changed_file_full_content"],
                    "evidence_required": ["SQL 拼接代码行"],
                    "false_positive_guards": ["使用 PreparedStatement 绑定参数时不要误报"],
                    "normalized_issue_type": "sql_injection",
                }
            ],
        },
        max_rules_per_batch=8,
    )

    assert len(batches) == 1
    assert batches[0][0]["rule_id"] == "SEC-JAVA-SQL-001"


def test_thorough_review_zero_candidates_records_quality_gate(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_zero_quality_gate",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/zero-gate",
            target_ref="main",
            title="Zero finding quality gate",
            changed_files=["src/main/java/demo/UserController.java"],
            unified_diff=(
                "diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n"
                "--- a/src/main/java/demo/UserController.java\n"
                "+++ b/src/main/java/demo/UserController.java\n"
                "@@ -20,1 +20,1 @@\n"
                "+ log.info(\"token={}\", request.getHeader(\"Authorization\"));\n"
            ),
        ),
    )
    runner.review_repo.save(review)
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
        review_spec="必须按照安全专家画像检查敏感信息泄露。",
    )
    command_message = ConversationMessage(
        review_id=review.review_id,
        issue_id="review_orchestration",
        expert_id="main_agent",
        message_type="main_agent_command",
        content="请检查安全问题",
        metadata={},
    )
    monkeypatch.setattr(runner.capability_service, "collect_tool_evidence", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_skill_activation_service, "activate", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner.review_tool_gateway, "invoke_for_expert", lambda *_args, **_kwargs: [])

    empty_structured = (
        '{"rule_check_results":[{"rule_id":"GENERAL-EXPERT-CHECKS","status":"passed",'
        '"evidence":[],"missing_context":[],"reason":"未发现问题"}],'
        '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        return LLMTextResult(
            text=empty_structured,
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id=f"call-{phase or 'main'}",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    runner._run_expert_from_command(
        review=review,
        expert=expert,
        command_message=command_message,
        file_path="src/main/java/demo/UserController.java",
        line_start=20,
        repository_context={"summary": "新增 Authorization header 日志输出。"},
        target_hunk={
            "hunk_header": "@@ -20,1 +20,1 @@",
            "start_line": 20,
            "end_line": 20,
            "changed_lines": [20],
            "excerpt": "+ log.info(\"token={}\", request.getHeader(\"Authorization\"));",
        },
        target_hunks=[],
        related_files=[],
        expected_checks=["安全专家画像通用检查"],
        disallowed_inference=[],
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
        analysis_mode="standard",
        llm_request_options={"timeout_seconds": 60, "max_attempts": 1},
        bound_documents=[],
        knowledge_context={},
        rule_screening={"matched_rules_for_llm": [], "all_enabled_rules_for_llm": []},
        finding_payloads=[],
    )

    zero_messages = [
        message
        for message in runner.message_repo.list(review.review_id)
        if message.message_type == "expert_zero_candidate_quality_gate"
    ]
    assert zero_messages
    assert zero_messages[-1].metadata["review_quality_mode"] == "thorough_review"
    assert zero_messages[-1].metadata["general_scan_attempted"] is True


def test_rule_check_prepass_uses_dedicated_inputs_without_nested_main_prompt(storage_root: Path, monkeypatch) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    review = ReviewTask(
        review_id="rev_prepass_prompt",
        status="running",
        phase="expert_review",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="proj",
            source_ref="feature/prepass",
            target_ref="main",
            title="Prepass prompt",
            changed_files=["src/main/java/demo/UserController.java"],
            unified_diff="diff --git a/src/main/java/demo/UserController.java b/src/main/java/demo/UserController.java\n@@ -1 +1 @@\n+ log.info(user.getPhone());\n",
        ),
    )
    expert = ExpertProfile(
        expert_id="security_compliance",
        name="Security",
        name_zh="安全专家",
        role="security",
        model="minimax-2.5",
    )
    captured: dict[str, str] = {}

    def fake_complete_text(**kwargs):
        captured["user_prompt"] = str(kwargs.get("user_prompt") or "")
        return LLMTextResult(
            text=(
                '{"rule_check_results":[{"rule_id":"SEC-JAVA-001","status":"violated",'
                '"evidence":["log.info(user.getPhone())"],"missing_context":[],"reason":"敏感信息输出"}],'
                '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
                '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
            ),
            mode="mock",
            provider="test",
            model="minimax-2.5",
            base_url="http://llm.test",
            api_key_env="TEST_KEY",
            call_id="call-prepass",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )

    monkeypatch.setattr(runner.llm_chat_service, "complete_text", fake_complete_text)
    text, metadata = runner._run_rule_guided_rule_check_prepass(
        review=review,
        expert=expert,
        runtime_settings=runner.runtime_settings_service.get(),
        resolution=runner.llm_chat_service.resolve_expert(expert, runner.runtime_settings_service.get()),
        base_user_prompt="[SYSTEM RULES]\n第二阶段任务：基于上述 rule_check_results 进行高召回 candidate_findings 发现",
        required_rule_ids=["SEC-JAVA-001"],
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "SEC-JAVA-001",
                    "title": "禁止输出敏感信息",
                    "must_check_items": ["检查日志是否输出手机号"],
                }
            ]
        },
        normalized_batch_items=[
            {
                "file_path": "src/main/java/demo/UserController.java",
                "line_start": 1,
                "target_hunk": {
                    "hunk_header": "@@ -1 +1 @@",
                    "start_line": 1,
                    "changed_lines": [1],
                    "excerpt": "+ log.info(user.getPhone());",
                },
            }
        ],
        repository_context={"summary": "新增手机号日志。"},
        file_path="src/main/java/demo/UserController.java",
        line_start=1,
        timeout_seconds=60,
    )

    assert text
    assert metadata["success"] is True
    assert "[PROMPT_CONTRACT]" in captured["user_prompt"]
    assert '"phase": "rule_check_prepass"' in captured["user_prompt"]
    assert "不要发现或撰写 candidate_findings" in captured["user_prompt"]
    assert "[RULE_CARDS]" in captured["user_prompt"]
    assert "[TARGET_HUNKS]" in captured["user_prompt"]
    assert "[COMPACT_CONTEXT]" in captured["user_prompt"]
    assert "[ORIGINAL_REVIEW_TASK]" not in captured["user_prompt"]
    assert "第二阶段任务" not in captured["user_prompt"]


def test_rule_guided_prompt_compacts_graph_impact_noise(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/graph",
        target_ref="main",
        title="Graph review",
        changed_files=["src/CourseCreator.java"],
        unified_diff="diff --git a/src/CourseCreator.java b/src/CourseCreator.java\n@@ -1 +1 @@\n+new Course();\n",
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD 架构专家",
        role="ddd",
        model="minimax-2.5",
    )
    noisy_paths = [{"depth": depth, "path": [f"Node{idx}" for idx in range(40)]} for depth in range(12)]
    repository_context = {
        "summary": "Course create path",
        "code_graph_context_source_summary": {"primary_source": "gitnexus", "context_count": 3},
        "code_graph_minimal_context": {
            "summary": "识别 CourseCreator.create 会影响 Course 聚合事件发布。",
            "risk_level": "high",
            "risk_score": 0.91,
            "affected_flows": [
                {"entrypoint": "CourseController.create", "changed_node": "CourseCreator.create", "criticality": 0.8}
            ],
        },
        "code_graph_impact_analysis": {
            "changed_nodes": [
                {
                    "qualified_name": "CourseCreator.create",
                    "file_path": "src/CourseCreator.java",
                    "line_start": 16,
                }
            ],
            "impacted_files": ["src/Course.java", "src/CourseCreatedDomainEvent.java"],
            "byDepth": noisy_paths,
            "impact_paths": noisy_paths,
            "affected_processes": noisy_paths,
        },
    }

    prompt = runner._build_expert_prompt(
        subject,
        expert,
        "src/CourseCreator.java",
        16,
        tool_evidence=[],
        runtime_tool_results=[],
        repository_context=repository_context,
        target_hunk={"hunk_header": "@@ -1 +1 @@", "excerpt": "+new Course();"},
        target_hunks=[],
        bound_documents=[],
        disallowed_inference=[],
        expected_checks=["检查聚合工厂和领域事件"],
        active_skills=[],
        rule_screening={"matched_rules_for_llm": [{"rule_id": "DDD-JDDD-001", "title": "聚合创建规则"}]},
        model_name="minimax-2.5",
    )

    assert "图谱确认事实" in prompt
    assert "图谱候选影响" in prompt
    assert "CourseCreator.create" in prompt
    assert "src/Course.java" in prompt
    assert "byDepth" not in prompt
    assert "impact_paths" not in prompt
    assert "affected_processes" not in prompt


def test_observation_followup_prompt_uses_rule_guided_contract(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/ddd",
        target_ref="main",
        title="Observation followup",
        changed_files=["src/CourseCreator.java"],
        unified_diff="diff --git a/src/CourseCreator.java b/src/CourseCreator.java\n@@ -1 +1 @@\n+new Course();\n",
    )
    expert = ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD 架构专家",
        role="ddd",
        model="minimax-2.5",
    )

    prompt = runner._build_observation_followup_prompt(
        subject=subject,
        expert=expert,
        repository_context={},
        batch_items=[
            {
                "file_path": "src/CourseCreator.java",
                "line_start": 1,
                "target_hunk": {"hunk_header": "@@ -1 +1 @@", "excerpt": "+new Course();"},
            }
        ],
        uncovered_observations=[
            {
                "observation_id": "obs-1",
                "file_path": "src/CourseCreator.java",
                "line_start": 1,
                "kind": "construction_path_changed",
                "summary": "直接构造聚合根",
                "evidence": ["new Course()"],
            }
        ],
        existing_candidates=[],
        max_findings=3,
    )

    assert "rule_check_results" in prompt
    assert "candidate_findings" in prompt
    assert "context_requests" in prompt
    assert '{"findings"' not in prompt
