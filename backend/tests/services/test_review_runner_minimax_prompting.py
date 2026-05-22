from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMTextResult
from app.services.review_runner import ReviewRunner


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
    assert "只能描述一个具体问题、一个主文件和一个主代码锚点" in prompt
    assert "每条 finding 的 JSON 字段要求" not in prompt


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
    assert "有当前代码锚点但缺上下文" in prompt
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
        runtime_settings=runner.runtime_settings_service.get(),
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
    custom_batch_empty = (
        '{"rule_check_results":[{"rule_id":"SEC-JAVA-LOOP-IO-001","status":"passed",'
        '"evidence":[],"missing_context":[],"reason":"主审已经覆盖"}],'
        '"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,'
        '"used_context_files":["src/main/java/demo/UserController.java"],"unverified_assumptions":[]}}'
    )
    phases: list[str] = []

    def fake_complete_text(**kwargs):
        phase = str((kwargs.get("log_context") or {}).get("phase") or "")
        phases.append(phase)
        if phase == "expert_general_profile_scan":
            text = general_candidate
        elif phase == "expert_custom_rule_batch_scan":
            text = custom_batch_empty
        else:
            text = custom_candidate
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
        repository_context={"summary": "新增 token 日志与循环外部接口调用。"},
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
    assert any("循环中逐条调用外部风控接口" in title for title in titles)
    assert any("日志明文输出 Authorization token" in title for title in titles)


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
