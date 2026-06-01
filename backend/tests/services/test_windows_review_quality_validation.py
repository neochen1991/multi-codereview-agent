import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_windows_review_quality.py"


def _load_windows_validation_module():
    spec = importlib.util.spec_from_file_location("validate_windows_review_quality", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_windows_validation_dry_run_accepts_drive_space_and_chinese_paths() -> None:
    module = _load_windows_validation_module()

    report = module.build_windows_review_quality_report(
        workspace_path="C:\\业务 仓库\\中文项目\\mr-worktree",
        changed_files=["src\\main\\java\\CourseCreator.java"],
        prompt_text="workspace=C:/业务 仓库/中文项目/mr-worktree",
        model_name="minimax-2.5",
        dry_run=True,
    )

    assert report["passed"] is True
    assert report["normalized_workspace_path"] == "C:/业务 仓库/中文项目/mr-worktree"
    assert report["normalized_changed_files"] == ["src/main/java/CourseCreator.java"]
    assert report["prompt_profile"] == "rule-guided-compact"


def test_windows_validation_real_workspace_requires_graph_and_prompt_match(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space"
    workspace.mkdir()

    report = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\Missing.java"],
        prompt_text="workspace=/elsewhere",
        metadata={},
        model_name="doubao-seed-2.0-code",
        dry_run=False,
    )

    assert report["passed"] is False
    assert "git_repo_readable" in report["missing"]
    assert "tree_sitter_graph_ready" in report["missing"]
    assert "gitnexus_graph_ready" in report["missing"]
    assert "prompt_workspace_matches_mr_workspace" in report["missing"]


def test_windows_validation_checks_minimax_review_quality_report(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space 中文"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    graph_db = workspace / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("", encoding="utf-8")
    (workspace / ".gitnexus").mkdir()

    report = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\main\\java\\BulkEnrollmentService.java"],
        prompt_text=f"workspace={str(workspace).replace(chr(92), '/')}",
        metadata={
            "expert_selection": {
                "selected_experts": [
                    {"expert_id": "security_compliance"},
                    {"expert_id": "correctness_business"},
                ]
            }
        },
        report={
            "findings": [
                {
                    "expert_id": "correctness_business",
                    "title": "TODO 里的库存扣减未实现",
                    "summary": "TODO 承诺扣减库存并发送预占事件，但当前实现没有对应代码。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\main\\java\\BulkEnrollmentService.java",
                    "line_start": 37,
                    "code_excerpt": "37 | +        // TODO 批量报名成功后扣减库存并发送预占事件",
                }
            ],
            "issues": [
                {
                    "primary_expert_id": "correctness_business",
                    "participant_expert_ids": ["security_compliance"],
                    "title": "TODO 里的库存扣减未实现",
                    "summary": "BulkEnrollmentService 在批量报名成功路径新增 TODO，当前没有库存扣减或预占事件实现。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\main\\java\\BulkEnrollmentService.java",
                    "line_start": 37,
                    "current_code": "37 | +        // TODO 批量报名成功后扣减库存并发送预占事件",
                    "remediation_suggestion": "删除误导性 TODO，或补齐库存扣减和预占事件。",
                }
            ],
        },
        replay={"messages": [{"expert_id": "security_compliance", "message_type": "expert_analysis"}]},
        model_name="MiniMax-M2.7",
    )

    assert report["passed"] is True
    assert "security_compliance" in report["executed_experts"]
    assert "correctness_business" in report["executed_experts"]


def test_windows_validation_flags_review_quality_regressions(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space 中文"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    graph_db = workspace / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("", encoding="utf-8")
    (workspace / ".gitnexus").mkdir()

    report = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\main\\java\\HibernateCriteriaConverter.java"],
        prompt_text=f"workspace={str(workspace).replace(chr(92), '/')}",
        metadata={"expert_selection": {"selected_experts": [{"expert_id": "database_analysis"}]}},
        report={
            "findings": [],
            "issues": [
                {
                    "primary_expert_id": "database_analysis",
                    "title": "订单权限过滤承诺未落地",
                    "summary": "listOrders 的 TODO 明确要求只返回当前登录用户有权限的订单，但当前实现没有权限过滤逻辑。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\shared\\HibernateCriteriaConverter.java",
                    "line_start": 16,
                    "current_code": "16 | +        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));",
                    "remediation_suggestion": "补齐缺失实现，并增加能复现该风险的回归测试。",
                },
                {
                    "primary_expert_id": "database_analysis",
                    "title": "订单权限过滤承诺未落地",
                    "summary": "listOrders 的 TODO 明确要求只返回当前登录用户有权限的订单，但当前实现没有权限过滤逻辑。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\other\\PaymentService.java",
                    "line_start": 16,
                    "current_code": "16 | +        return paymentRepository.save(payment);",
                    "remediation_suggestion": "当前未生成可直接落地的建议代码，请结合本条问题说明和修改思路处理。",
                },
            ],
        },
        replay={"messages": [{"expert_id": "database_analysis", "message_type": "expert_analysis"}]},
        model_name="MiniMax-M2.7",
    )

    assert report["passed"] is False
    assert "security_expert_activated" in report["missing"]
    assert "business_expert_activated" in report["missing"]
    assert "effective_issues_have_no_cross_anchor_duplicate_text" in report["missing"]
    assert "review_findings_have_no_cross_anchor_duplicate_text" not in report["missing"]
    assert "todo_contract_issues_have_code_anchor" in report["missing"]
    assert "issue_text_matches_code_anchor" in report["missing"]
    assert "no_user_facing_fallback_text" in report["missing"]


def test_windows_validation_flags_duplicate_review_findings(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space 中文"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    graph_db = workspace / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("", encoding="utf-8")
    (workspace / ".gitnexus").mkdir()

    report = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\main\\java\\BatchService.java"],
        prompt_text=f"workspace={str(workspace).replace(chr(92), '/')}",
        metadata={
            "expert_selection": {
                "selected_experts": [
                    {"expert_id": "security_compliance"},
                    {"expert_id": "correctness_business"},
                ]
            }
        },
        report={
            "findings": [
                {
                    "expert_id": "performance_reliability",
                    "title": "批量保存改成了循环逐条保存",
                    "summary": "当前批量路径在循环内逐条保存，批量输入会被放大为多次数据库访问。",
                    "normalized_issue_type": "n_plus_one",
                    "file_path": "src\\main\\java\\a\\BatchService.java",
                    "line_start": 36,
                    "code_excerpt": "36 | +            orderRepository.save(order);",
                },
                {
                    "expert_id": "performance_reliability",
                    "title": "批量保存改成了循环逐条保存",
                    "summary": "当前批量路径在循环内逐条保存，批量输入会被放大为多次数据库访问。",
                    "normalized_issue_type": "n_plus_one",
                    "file_path": "src\\main\\java\\b\\BatchService.java",
                    "line_start": 36,
                    "code_excerpt": "36 | +            paymentRepository.save(payment);",
                },
            ],
            "issues": [
                {
                    "primary_expert_id": "correctness_business",
                    "participant_expert_ids": ["security_compliance"],
                    "title": "查询语义从精确匹配退化为模糊匹配",
                    "summary": "HibernateCriteriaConverter 把 equal 精确匹配改成 like 模糊匹配。",
                    "normalized_issue_type": "query_semantics_regression",
                    "file_path": "src\\main\\java\\HibernateCriteriaConverter.java",
                    "line_start": 16,
                    "current_code": "16 | +        return builder.like(root.get(filter.field().value()), String.format(\"%%%s%%\", filter.value().value()));",
                    "remediation_suggestion": "恢复 builder.equal，或新增明确的 contains/like 操作符。",
                }
            ],
        },
        replay={"messages": [{"expert_id": "security_compliance", "message_type": "expert_analysis"}]},
        model_name="MiniMax-M2.7",
    )

    assert report["passed"] is False
    assert "review_findings_have_no_cross_anchor_duplicate_text" in report["missing"]


def test_windows_validation_flags_issue_finding_family_mismatch_and_neighbor_todo(tmp_path: Path) -> None:
    module = _load_windows_validation_module()
    workspace = tmp_path / "repo with space 中文"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    graph_db = workspace / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("", encoding="utf-8")
    (workspace / ".gitnexus").mkdir()

    quality = module.build_windows_review_quality_report(
        workspace_path=str(workspace),
        changed_files=["src\\main\\java\\BulkEnrollmentService.java"],
        prompt_text=f"workspace={str(workspace).replace(chr(92), '/')}",
        metadata={
            "expert_selection": {
                "selected_experts": [
                    {"expert_id": "security_compliance"},
                    {"expert_id": "correctness_business"},
                ]
            }
        },
        report={
            "findings": [
                {
                    "finding_id": "fdg_loop_saved_as_todo",
                    "expert_id": "performance_reliability",
                    "title": "TODO 里的库存扣减未实现",
                    "summary": "BulkEnrollmentService.java 第 37 行 的注释或 TODO 已经承诺业务动作。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\main\\java\\BulkEnrollmentService.java",
                    "line_start": 37,
                    "code_excerpt": (
                        "35 | +            repository.save(enrollment);\n"
                        "36 | +        }\n"
                        "37 | +        // TODO 批量报名成功后扣减库存并发送预占事件"
                    ),
                }
            ],
            "issues": [
                {
                    "issue_id": "fdg_loop_saved_as_todo",
                    "finding_ids": ["fdg_loop_saved_as_todo"],
                    "primary_expert_id": "performance_reliability",
                    "participant_expert_ids": ["correctness_business"],
                    "title": "批量报名从 saveAll 退化为循环逐条保存",
                    "summary": "BulkEnrollmentService 第 35 行在循环内逐条 repository.save。",
                    "normalized_issue_type": "n_plus_one",
                    "file_path": "src\\main\\java\\BulkEnrollmentService.java",
                    "line_start": 35,
                    "current_code": (
                        "35 | +            repository.save(enrollment)\n"
                        "36 | +        }\n"
                        "37 | +        // TODO 批量报名成功后扣减库存并发送预占事件"
                    ),
                    "remediation_suggestion": "恢复 saveAll 或批量保存。",
                },
                {
                    "issue_id": "iss_neighbor_todo_false_positive",
                    "primary_expert_id": "correctness_business",
                    "title": "TODO 里的库存扣减未实现",
                    "summary": "片段里出现 TODO，但目标行实际是 repository.save。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "file_path": "src\\main\\java\\BulkEnrollmentService.java",
                    "line_start": 35,
                    "current_code": (
                        "35 | +            repository.save(enrollment)\n"
                        "36 | +        }\n"
                        "37 | +        // TODO 批量报名成功后扣减库存并发送预占事件"
                    ),
                    "remediation_suggestion": "补齐 TODO 业务动作。",
                },
            ],
        },
        replay={"messages": [{"expert_id": "security_compliance", "message_type": "expert_analysis"}]},
        model_name="MiniMax-M2.7",
    )

    assert quality["passed"] is False
    assert "finding_issue_family_alignment" in quality["missing"]
    assert "todo_contract_issues_have_code_anchor" in quality["missing"]


def test_windows_validation_can_fetch_real_review_payloads(monkeypatch) -> None:
    module = _load_windows_validation_module()
    calls: list[str] = []

    def fake_get_json(url: str) -> dict[str, object]:
        calls.append(url)
        if url.endswith("/reviews/rev_windows"):
            return {
                "review_id": "rev_windows",
                "subject": {
                    "changed_files": ["src\\main\\java\\OrderService.java"],
                    "metadata": {"review_workspace_path": "D:\\workspace\\ipc-fnd-service"},
                },
            }
        if url.endswith("/reviews/rev_windows/report"):
            return {"issues": [], "findings": []}
        if url.endswith("/reviews/rev_windows/replay"):
            return {"messages": []}
        raise AssertionError(url)

    monkeypatch.setattr(module, "_http_get_json", fake_get_json)

    review, report, replay = module._fetch_review_payloads("http://127.0.0.1:8011/api/", "rev_windows")
    metadata = module._extract_review_metadata(review)

    assert calls == [
        "http://127.0.0.1:8011/api/reviews/rev_windows",
        "http://127.0.0.1:8011/api/reviews/rev_windows/report",
        "http://127.0.0.1:8011/api/reviews/rev_windows/replay",
    ]
    assert module._infer_workspace_path(review, metadata) == "D:\\workspace\\ipc-fnd-service"
    assert module._extract_changed_files(review) == ["src\\main\\java\\OrderService.java"]
    assert report == {"issues": [], "findings": []}
    assert replay == {"messages": []}
