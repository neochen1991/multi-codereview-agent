from pathlib import Path

from app.services.repo_review_instruction_service import RepoReviewInstructionService


def test_repo_review_instruction_service_loads_review_md_chain(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}", encoding="utf-8")
    (repo / "REVIEW.md").write_text("全局规则：所有 Controller 必须检查权限。", encoding="utf-8")
    (repo / "src/main/java/app/REVIEW.md").write_text("应用服务规则：事务内不要调用外部接口。", encoding="utf-8")

    payload = RepoReviewInstructionService().load_for_file(repo, "src/main/java/app/OrderService.java")

    assert len(payload["instructions"]) == 2
    assert "全局规则" in payload["summary"]
    assert "事务内不要调用外部接口" in payload["summary"]


def test_repo_review_instruction_service_matches_codereview_yaml_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OwnerController.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OwnerController {}", encoding="utf-8")
    (repo / ".codereview.yaml").write_text(
        "\n".join(
            [
                "rules:",
                "  - title: Controller 安全入口",
                "    paths: [\"src/main/java/**/*Controller.java\"]",
                "    experts: [\"security_compliance\"]",
                "    instruction: Controller 删除 @Valid、权限校验、ownerId 一致性校验时必须报高风险。",
                "  - title: SQL 规则",
                "    paths: [\"db/**/*.sql\"]",
                "    instruction: SQL 必须检查索引。",
            ]
        ),
        encoding="utf-8",
    )

    payload = RepoReviewInstructionService().load_for_file(repo, "src/main/java/app/OwnerController.java")

    assert len(payload["instructions"]) == 1
    assert payload["instructions"][0]["title"] == "Controller 安全入口"
    assert "ownerId 一致性校验" in payload["summary"]


def test_repo_review_instruction_service_includes_ai_review_path_rules(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "backend" / "app" / "payments" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("def charge(): pass", encoding="utf-8")
    (repo / ".ai-review.yml").write_text(
        "\n".join(
            [
                "path_rules:",
                "  backend/app/payments/**:",
                "    required_experts: [security_compliance, database_analysis]",
                "    comment_level: strict",
                "    instructions: Payment code must prove authorization, idempotency, and transaction safety.",
            ]
        ),
        encoding="utf-8",
    )

    payload = RepoReviewInstructionService().load_for_file(repo, "backend/app/payments/service.py")

    assert len(payload["instructions"]) == 1
    assert payload["instructions"][0]["source"] == ".ai-review.yml"
    assert payload["instructions"][0]["expert_ids"] == ["security_compliance", "database_analysis"]
    assert "transaction safety" in payload["summary"]


def test_repo_review_instruction_service_loads_agent_standard_files(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}", encoding="utf-8")
    (repo / "AGENTS.md").write_text("Agent 规则：代码检视必须确认幂等性。", encoding="utf-8")
    (repo / ".github").mkdir()
    (repo / ".github/copilot-instructions.md").write_text("Copilot 规则：高风险接口必须说明测试。", encoding="utf-8")

    payload = RepoReviewInstructionService().load_for_file(repo, "src/main/java/app/OrderService.java")

    sources = [str(item["source"]) for item in payload["instructions"]]
    assert "AGENTS.md" in sources
    assert ".github/copilot-instructions.md" in sources
    assert "确认幂等性" in payload["summary"]
    assert "高风险接口必须说明测试" in payload["summary"]
