from pathlib import Path

from app.services.repo_review_policy_service import RepoReviewPolicyService


def test_repo_review_policy_service_matches_path_rules_and_exclusions(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".ai-review.yml").write_text(
        "\n".join(
            [
                "max_comments_per_review: 6",
                "excluded_paths:",
                "  - dist/**",
                "  - package-lock.json",
                "path_rules:",
                "  backend/app/payments/**:",
                "    required_experts: [security_compliance, database_analysis]",
                "    comment_level: strict",
                "    instructions: Payment changes must prove authorization and transaction safety.",
                "  frontend/**:",
                "    required_experts:",
                "      - frontend_accessibility",
                "    max_comments: 3",
            ]
        ),
        encoding="utf-8",
    )

    policy = RepoReviewPolicyService().load_for_files(
        repo,
        [
            "backend/app/payments/service.py",
            "frontend/src/App.tsx",
            "dist/bundle.js",
            "package-lock.json",
        ],
    )

    assert policy["max_comments_per_review"] == 6
    assert policy["excluded_changed_files"] == ["dist/bundle.js", "package-lock.json"]
    assert policy["reviewable_changed_files"] == ["backend/app/payments/service.py", "frontend/src/App.tsx"]
    assert policy["required_experts"] == [
        "security_compliance",
        "database_analysis",
        "frontend_accessibility",
    ]
    assert policy["path_rules"][0]["comment_level"] == "strict"
    assert "authorization" in policy["path_rules"][0]["instructions"]


def test_repo_review_policy_service_returns_empty_policy_when_file_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    policy = RepoReviewPolicyService().load_for_files(repo, ["src/app.py"])

    assert policy["required_experts"] == []
    assert policy["excluded_changed_files"] == []
    assert policy["reviewable_changed_files"] == ["src/app.py"]


def test_repo_review_policy_service_applies_required_experts_to_selection_plan():
    service = RepoReviewPolicyService()
    selection_plan = {
        "selected_expert_ids": ["correctness_business"],
        "candidate_expert_ids": ["correctness_business"],
        "selected_experts": [{"expert_id": "correctness_business", "source": "llm"}],
    }
    policy = {
        "required_experts": ["security_compliance", "missing_expert"],
        "path_rules": [{"title": "payment policy"}],
    }

    updated = service.apply_to_selection_plan(
        selection_plan,
        policy,
        enabled_expert_ids=["correctness_business", "security_compliance"],
    )

    assert updated["selected_expert_ids"] == ["correctness_business", "security_compliance"]
    assert updated["candidate_expert_ids"] == ["correctness_business", "security_compliance"]
    assert updated["review_policy"]["added_required_experts"] == ["security_compliance"]
    assert updated["review_policy"]["missing_required_experts"] == ["missing_expert"]
