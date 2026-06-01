# Review Quality Improvement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve multi-codereview-agent review quality for Java Web transaction systems on minimax2.7 by adding structured change understanding, risk-domain recall, expert routing reinforcement, candidate finding governance, evidence validation, display-quality gates, and an evaluation loop.

**Architecture:** Treat the LLM as one reasoning component, not the whole reviewer. The system first turns an MR into structured change facts, then recalls risk domains and experts deterministically, runs narrower expert tasks, stores candidate findings, validates evidence anchors, and only then publishes developer-readable issues.

**Tech Stack:** Python, FastAPI backend, Pydantic domain models, LangGraph-style orchestrator nodes, pytest, existing diff/context/review services, React/TypeScript frontend for result display.

---

## 1. Problem Statement

Current quality issues are not isolated prompt bugs. They come from the review pipeline asking one LLM pass to do too many things at once:

- Understand a large MR.
- Choose experts.
- Find risks.
- Locate code evidence.
- Write issue summaries.
- Deduplicate results.
- Judge whether an issue is valid.
- Generate remediation text.

This is especially fragile with minimax2.7. The model can work well on focused, evidence-backed tasks, but it is less stable when long diffs, multiple issue types, and strict JSON output are mixed in one prompt.

The review quality goal is therefore:

- Increase high-value issue recall.
- Reduce false positives.
- Keep issue descriptions aligned with the displayed code.
- Avoid duplicate or cross-file-mismerged issues.
- Make final issue text readable and actionable for Java Web engineers.

## 2. Target Pipeline

```mermaid
flowchart LR
  A["MR Diff"] --> B["Change Understanding"]
  B --> C["Risk Domain Recall"]
  C --> D["Focused Expert Review"]
  D --> E["Candidate Finding Pool"]
  E --> F["Evidence Validation"]
  F --> G["Issue Merge and Dedup"]
  G --> H["Display Quality Gate"]
  H --> I["Evaluation Loop"]
```

The main shift is:

- Rules, AST, and tools handle recall and anchoring.
- LLMs handle focused judgment and Chinese explanation.
- Programmatic gates handle evidence consistency and publication safety.

## 3. Phase 1: Change Understanding Layer

### Purpose

Create a structured "change facts" object before expert selection or expert review. This gives later steps a map of what changed.

### Output Shape

```json
{
  "files": [
    {
      "path": "src/main/java/com/example/order/OrderService.java",
      "language": "java",
      "file_role": "service",
      "is_test": false,
      "changed_methods": ["submitOrder"],
      "annotations": ["Transactional"],
      "risk_domains": ["business", "transaction", "database"],
      "risk_signals": ["transactional_side_effect"]
    }
  ],
  "changed_symbols": ["orderId", "userId", "amount", "status"],
  "business_terms": ["order", "payment", "inventory"],
  "risk_domains": ["business", "security", "database", "transaction"],
  "expert_hints": ["correctness_business", "security_compliance", "database_analysis"],
  "summary": "本次变更涉及订单提交服务、用户身份字段和数据库查询边界。"
}
```

### Implementation

Add `backend/app/services/change_understanding_service.py`.

The first implementation should be deterministic:

- Parse `ReviewSubject.changed_files`.
- Parse unified diff hunks with `DiffExcerptService`.
- Classify file roles from path, file name, package, and annotations.
- Extract changed Java-like symbols with lightweight regex.
- Detect risk domains from added/deleted lines.
- Generate expert hints without calling LLM.

LLM summarization can be added later, but must not be required for routing.

### Initial Risk Domains

| Risk Domain | Signals |
|---|---|
| `security` | Controller entry, user/tenant/resource id, token, permission, SQL string interpolation |
| `business` | order, payment, refund, inventory, amount, status, state transition |
| `database` | Repository, Mapper, DAO, SQL, JPA, Criteria, pagination, limit |
| `transaction` | `@Transactional`, event publish near DB write, external call in transaction |
| `performance` | loop with Repository/Mapper/Client/Gateway call, batch without boundary |
| `concurrency` | lock, synchronized, idempotency, setnx, retry |
| `mq` | producer, consumer, publish, ack, retry, dead letter |
| `cache` | Redis, cache annotations, TTL, expire, cache eviction |
| `test` | test files changed or missing for high-risk production changes |
| `maintainability` | large method, duplicated branch, magic value, confusing fallback |

## 4. Phase 2: Risk Domain Recall and Expert Routing

### Purpose

Expert selection should not depend only on LLM judgment. It should be:

1. Deterministic recall from change understanding.
2. Repository policy required experts.
3. User-selected experts.
4. LLM supplementation.

### Routing Rules

| Risk Domain | Required Experts |
|---|---|
| `security` | `security_compliance` |
| `business` | `correctness_business` |
| `database` | `database_analysis` |
| `transaction` | `correctness_business`, `database_analysis`, `performance_reliability` |
| `performance` | `performance_reliability`, `database_analysis` |
| `concurrency` | `performance_reliability`, `test_verification` |
| `mq` | `mq_analysis`, `test_verification` |
| `cache` | `redis_analysis`, `test_verification` |
| `test` | `test_verification` |
| `maintainability` | `architecture_design`, `maintainability_code_health` |

### Implementation

Modify:

- `backend/app/services/orchestrator/nodes/slice_change.py`
- `backend/app/services/orchestrator/nodes/route_experts.py`
- `backend/app/services/main_agent_service.py`
- `backend/app/services/main_agent_prompting.py`

The orchestration state should carry:

- `change_understanding`
- `risk_domains`
- `expert_hints`

The main agent prompt should show these fields as deterministic facts, not as suggestions from the LLM.

## 5. Phase 3: Focused Expert Review Tasks

### Purpose

Expert prompts should be narrower. minimax2.7 should judge one risk domain and one code anchor at a time whenever possible.

### Prompt Contract

Each expert prompt should include:

- Risk domain.
- Target file, method, hunk, and line.
- Relevant change facts.
- Relevant rules only.
- Related source context only.
- Explicit out-of-scope reminders.

Expert output should be `candidate_finding`, not final issue.

### Candidate Finding Fields

```json
{
  "risk_domain": "security",
  "expert_id": "security_compliance",
  "file_path": "OrderController.java",
  "method_name": "createOrder",
  "line_start": 42,
  "code_anchor": "orderRepository.findByUserId(userId)",
  "claim": "用户可用传入 userId 查询订单，缺少资源归属校验。",
  "evidence": ["第 42 行使用请求参数 userId 查询订单", "当前 hunk 未看到登录用户与 userId 绑定校验"],
  "confidence": 0.82,
  "verification_needed": false
}
```

## 6. Phase 4: Candidate Finding Pool

### Purpose

Separate high-recall finding collection from strict issue publication.

Experts should be allowed to produce more candidate findings than final issues. The publication pipeline then validates and filters them.

### Implementation

Use the existing `ReviewFinding` path as the storage base, but normalize incoming findings into candidate-like metadata:

- `risk_domain`
- `method_name`
- `code_anchor`
- `change_understanding_refs`
- `evidence_anchor_status`

Do not overload `DebateIssue` before evidence validation succeeds.

## 7. Phase 5: Evidence Validation

### Purpose

Prevent invalid or mismatched final issues.

### Required Gates

| Gate | Rule |
|---|---|
| File gate | `file_path` must belong to the current MR or verified related context |
| Line gate | `line_start` must be a changed line or a verified impacted line |
| Code anchor gate | `code_excerpt/current_code` must be extracted from the same file and line neighborhood |
| Claim-code alignment gate | Title and summary must mention behavior that exists in the code anchor or evidence |
| Deleted-code gate | Do not report issues that only exist in removed code |
| Suggested-code gate | Hide or regenerate suggested code if it does not reference current symbols |
| Duplicate gate | Merge only same file, same root cause, compatible code anchor |

### Implementation

Modify:

- `backend/app/services/review_runner_issue_validation.py`
- `backend/app/services/orchestrator/nodes/evidence_verification.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`

The gates should return explicit reasons so the result page can explain why a finding was filtered.

## 8. Phase 6: Judge Agent Scope Reduction

### Purpose

The judge agent should not rewrite issue details. It should only decide publication state.

### New Contract

```json
{
  "verdict": "accept | downgrade | needs_human | reject",
  "reason": "证据不足，无法确认资源归属校验缺失。",
  "evidence_score": 0.71
}
```

The final issue text should come from:

1. Primary expert finding.
2. Evidence validation facts.
3. Display quality formatter.

## 9. Phase 7: Display Quality Gate

### Purpose

Every final issue must be readable and actionable.

### Field Requirements

| Field | Requirement |
|---|---|
| Title | Concrete, Chinese, action-oriented, not generic |
| Summary | Explain code location, why it is wrong, and consequence |
| Evidence | Include code, rule, or context proof |
| Rule basis | Use real rule IDs or generic engineering principles only |
| Remediation | Match the current code and method |
| Suggested code | Show only when validated |
| Test suggestion | Match the risk domain |

This gate should reject or rewrite placeholders such as:

- "需要确认其他条件"
- "当前未生成可直接落地的建议代码"
- "存在风险"
- "建议完善处理"

## 10. Phase 8: Evaluation Loop

### Purpose

Quality changes must be measured, not judged by a single UI run.

### Benchmark Suites

| Suite | Purpose |
|---|---|
| Java Web security | SQL injection, authorization, tenant isolation, sensitive data |
| Java Web business | order, payment, refund, inventory, status transition |
| Database and performance | pagination, N+1, transaction, batch boundary |
| Reliability | locks, idempotency, MQ retry, exception handling |
| Clean MR | false positive rate |
| Boundary MR | judge and downgrade quality |

### Metrics

| Metric | Definition |
|---|---|
| Recall | Expected high-value issues detected |
| Precision | Published issues confirmed valid |
| Anchor Accuracy | Issue code matches title and summary |
| Duplicate Rate | Duplicate published issues over total issues |
| Display Quality | Human-readable title and actionable details |
| Latency | End-to-end review duration |

Add or extend:

- `backend/tests/services/test_review_quality_eval.py`
- `backend/tests/services/test_java_review_benchmarks.py`
- `scripts/bench_java_review_cases.py`

## 11. Implementation Tasks

### Task 1: Add Change Understanding Service

**Files:**

- Create: `backend/app/services/change_understanding_service.py`
- Test: `backend/tests/services/test_change_understanding_service.py`

**Acceptance:**

- Classifies Java Controller, Service, Repository, DTO, Entity, Config, Test.
- Extracts changed methods and symbols from diff hunks.
- Emits risk domains and expert hints deterministically.
- Does not require LLM.

### Task 2: Carry Change Understanding Through Orchestrator State

**Files:**

- Modify: `backend/app/services/orchestrator/state.py`
- Modify: `backend/app/services/orchestrator/nodes/ingest_subject.py`
- Modify: `backend/app/services/orchestrator/nodes/slice_change.py`
- Test: `backend/tests/orchestrator/test_graph_boot.py`
- Test: `backend/tests/services/test_route_experts.py`

**Acceptance:**

- State includes `change_understanding`, `risk_domains`, and `expert_hints`.
- Existing graph boots.
- Existing route behavior remains compatible.

### Task 3: Use Risk Domains in Expert Routing

**Files:**

- Modify: `backend/app/services/orchestrator/nodes/route_experts.py`
- Test: `backend/tests/services/test_route_experts.py`

**Acceptance:**

- Security, business, database, transaction, performance, mq, cache, concurrency, test risk domains recall expected experts.
- User-selected and policy-required experts are preserved.
- `change_impact_analysis` remains always selectable/default where configured and must not suppress LLM expert selection.

### Task 4: Add Change Facts to Main Agent Prompt and Expert Commands

**Files:**

- Modify: `backend/app/services/main_agent_service.py`
- Modify: `backend/app/services/main_agent_prompting.py`
- Modify: `backend/app/services/review_runner_prompting.py`
- Test: `backend/tests/services/test_main_agent_service.py`
- Test: `backend/tests/services/test_review_runner_minimax_prompting.py`

**Acceptance:**

- minimax prompts include short structured change facts.
- Prompts state that change facts are deterministic hints, not final conclusions.
- Prompt length remains bounded.

### Task 5: Candidate Finding Metadata Normalization

**Files:**

- Modify: `backend/app/domain/models/finding.py`
- Modify: `backend/app/services/review_runner_expert_output.py`
- Test: `backend/tests/services/test_review_runner_minimax_output.py`

**Acceptance:**

- Findings can carry risk domain, method name, and code anchor.
- Old findings remain loadable.
- Missing metadata does not break older reviews.

### Task 6: Evidence Anchor Validation Gate

**Files:**

- Modify: `backend/app/services/review_runner_issue_validation.py`
- Modify: `backend/app/services/orchestrator/nodes/evidence_verification.py`
- Test: `backend/tests/services/test_evidence_verification.py`
- Test: `backend/tests/services/test_review_issues.py`

**Acceptance:**

- Issues based only on deleted code are downgraded.
- Issues with code/description mismatch are downgraded or marked for rewrite.
- Different files with same line and same vague description are not merged incorrectly.

### Task 7: Reduce Judge Agent Scope

**Files:**

- Modify: `backend/app/services/issue_judge_service.py`
- Modify: `backend/app/services/orchestrator/nodes/judge_and_merge.py`
- Test: `backend/tests/services/test_judge_and_merge.py`

**Acceptance:**

- Judge verdict changes status/confidence only.
- Judge does not overwrite title, summary, remediation, or suggested code.
- Existing high-confidence direct defects are not degraded by judge rewriting.

### Task 8: Display Quality Gate

**Files:**

- Modify: `backend/app/services/review_report_builder.py`
- Modify: `backend/app/api/routes/issues.py`
- Modify: `frontend/src/components/review/IssueDetailPanel.tsx`
- Test: `backend/tests/services/test_review_report_builder.py` if present, otherwise add focused tests under `backend/tests/services/test_review_issues.py`.

**Acceptance:**

- Generic placeholders are hidden or replaced before display.
- Suggested code is hidden if unvalidated.
- UI still shows enough context for manual repair.

### Task 9: Evaluation Loop

**Files:**

- Modify: `scripts/bench_java_review_cases.py`
- Modify: `backend/tests/services/test_java_review_benchmarks.py`
- Modify: `backend/tests/services/test_review_quality_eval.py`

**Acceptance:**

- Benchmark reports recall, precision, anchor accuracy, duplicate rate, and display quality.
- Includes minimax-oriented Java Web cases.

## 12. Verification Commands

Run targeted tests first:

```bash
.venv/bin/python -m pytest \
  backend/tests/services/test_change_understanding_service.py \
  backend/tests/services/test_route_experts.py \
  backend/tests/services/test_main_agent_service.py \
  backend/tests/services/test_review_runner_minimax_prompting.py \
  backend/tests/services/test_review_runner_minimax_output.py \
  backend/tests/services/test_evidence_verification.py \
  backend/tests/services/test_judge_and_merge.py \
  -q
```

Then run broader quality tests:

```bash
.venv/bin/python -m pytest \
  backend/tests/services/test_java_review_benchmarks.py \
  backend/tests/services/test_review_quality_eval.py \
  backend/tests/services/test_review_issues.py \
  -q
```

Finally run frontend typecheck if UI changes are included:

```bash
npm run typecheck
```

## 13. Rollout Strategy

1. Land deterministic change understanding and routing first.
2. Keep existing LLM expert selection as supplement, not replacement.
3. Add gates in observe-only mode if needed, then enforce once tests pass.
4. Compare benchmark output before and after enabling stricter gates.
5. Keep settings toggles for quality mode and prompt profile.

## 14. Non-Goals

- Do not rewrite the backend in TypeScript.
- Do not require external components for phase 1.
- Do not depend on GitNexus or database metadata for the first implementation.
- Do not let the judge agent rewrite final issue content.
- Do not publish issues that cannot be anchored to current code evidence.
