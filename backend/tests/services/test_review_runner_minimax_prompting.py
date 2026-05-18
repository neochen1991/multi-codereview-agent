from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
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
    assert "非 minimax 模型也不能绕过规则驱动质量框架" in prompt


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
