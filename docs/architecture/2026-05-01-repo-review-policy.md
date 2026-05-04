# Repo Review Policy

Repositories can now define AI review policy in `.ai-review.yml`. The policy is intentionally small and focused on review quality: path exclusions, path-level required experts, comment budget hints, and path-specific instructions.

## Supported Fields

```yaml
max_comments_per_review: 8

excluded_paths:
  - dist/**
  - package-lock.json

path_rules:
  backend/app/payments/**:
    required_experts: [security_compliance, database_analysis]
    comment_level: strict
    max_comments: 5
    instructions: Payment changes must prove authorization and transaction safety.
```

## Runtime Behavior

- `RepoReviewPolicyService` loads `.ai-review.yml` or `.ai-review.yaml`.
- Matching path rules add `required_experts` to automatic expert selection when those experts are enabled.
- Manual expert selection remains manual and is not widened by repository policy.
- `RepoReviewInstructionService` exposes matching path instructions to prompts through `repo_review_instructions`.
- `route_experts` also honors `review_policy.required_experts` in graph state.
- `route_experts` ignores `excluded_changed_files` when matching deterministic diff/file-path signals.
- `judge_and_merge` applies `max_comments_per_review` by keeping the highest-value issues and recording over-budget issues as `repo_policy_comment_budget` filter decisions.
- The policy payload records `excluded_changed_files`, `reviewable_changed_files`, and comment budgets for UI/report rendering.
- `ReviewReport.confidence_summary` records policy counts and comment-budget downgrades, so downstream dashboards and eval scripts do not need to recalculate them from raw messages.

## Template

Use `docs/templates/ai-review-policy-template.yml` as a starting point.
