# Priority Tiered Context Compression Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a structured, priority-tiered context budgeting pipeline that compresses expert prompts by whole-request budget without reducing review quality.

**Architecture:** Introduce a `ContextBlock` abstraction and a prompt budget planner in the review pipeline. Existing prompt inputs will first be normalized into blocks, then ranked, deduplicated, compressed by level, and finally rendered back into the expert prompt in priority order.

**Tech Stack:** Python, Pydantic-style domain models, pytest

---

### Task 1: Define context block schema

**Files:**
- Create: `backend/app/services/context_block.py`
- Test: `backend/tests/services/test_context_block.py`

**Step 1: Write the failing test**

Add tests that validate:
- a `ContextBlock` carries required fields
- `must_keep` blocks default to `compression_level="L0"`
- priority ordering is comparable

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_context_block.py`

Expected: FAIL because file and class do not exist.

**Step 3: Write minimal implementation**

Create a context block model with:
- `block_id`
- `type`
- `priority`
- `expert_relevance`
- `evidence_strength`
- `must_keep`
- `compression_level`
- `token_cost`
- `summary`
- `content`
- metadata fields for file/line/tags/rules/observations

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_context_block.py`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/context_block.py backend/tests/services/test_context_block.py
git commit -m "feat: add structured context block model"
```

### Task 2: Encode priority map and expert weighting

**Files:**
- Create: `backend/app/services/context_priority_policy.py`
- Test: `backend/tests/services/test_context_priority_policy.py`

**Step 1: Write the failing test**

Add tests for:
- default priority by block type
- security/performance/ddd expert-specific priority uplift
- P0 blocks remaining non-droppable

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_context_priority_policy.py`

Expected: FAIL because policy module does not exist.

**Step 3: Write minimal implementation**

Implement:
- base type -> priority map
- expert-specific weight uplift
- helper to compute effective ranking score

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_context_priority_policy.py`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/context_priority_policy.py backend/tests/services/test_context_priority_policy.py
git commit -m "feat: add context priority policy"
```

### Task 3: Build whole-request budget planner

**Files:**
- Create: `backend/app/services/prompt_budget_planner.py`
- Test: `backend/tests/services/test_prompt_budget_planner.py`

**Step 1: Write the failing test**

Add tests for:
- must-keep blocks always preserved
- duplicate low-priority blocks removed first
- overflow handled by `L1 -> L2 -> L3 -> L4`
- whole-request budget applied across all blocks

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_prompt_budget_planner.py`

Expected: FAIL because planner module does not exist.

**Step 3: Write minimal implementation**

Implement planner functions:
- dedupe blocks
- reserve must-keep
- sort by effective score
- compress by level before dropping
- emit kept/compressed/dropped metadata

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_prompt_budget_planner.py`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/prompt_budget_planner.py backend/tests/services/test_prompt_budget_planner.py
git commit -m "feat: add prompt budget planner"
```

### Task 4: Convert review runner context into blocks

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

Add tests that verify:
- review runner maps repository context sections into typed blocks
- target hunk, current code, matched rules, key observations become P0
- expert-specific context order influences block ranking rather than raw text order

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "context_block or prompt_budget"`

Expected: FAIL because block generation path does not exist yet.

**Step 3: Write minimal implementation**

In `review_runner.py`:
- replace direct prompt chunk assembly with block generation
- map existing sections into `ContextBlock`
- preserve current behavior for standard mode while wiring in block metadata

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "context_block or prompt_budget"`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/tests/services/test_review_runner.py
git commit -m "feat: build prompt context blocks in review runner"
```

### Task 5: Apply planner to light mode only

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

Add tests for:
- light mode uses whole-request budget planner
- standard mode still preserves full context path
- light mode returns block budget metadata for observability

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "light_mode_budget or standard_mode_full_context"`

Expected: FAIL because planner is not connected yet.

**Step 3: Write minimal implementation**

Connect planner into light mode prompt construction:
- generate blocks
- apply budget
- render prompt from kept blocks
- persist compression metadata

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "light_mode_budget or standard_mode_full_context"`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/tests/services/test_review_runner.py
git commit -m "feat: apply tiered context budgeting in light mode"
```

### Task 6: Extend rule screening prompt compaction to block-aware budgeting

**Files:**
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Test: `backend/tests/services/test_knowledge_rule_screening_service.py`

**Step 1: Write the failing test**

Add tests for:
- screening prompt uses compact structured query-term blocks
- repeated terms are removed before truncation
- whole-request screening budget is enforced

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_knowledge_rule_screening_service.py`

Expected: FAIL because screening still uses raw text compaction.

**Step 3: Write minimal implementation**

Refactor screening prompt construction to:
- classify changed files / query terms / rule cards as structured blocks
- dedupe repeated query terms
- enforce a screening-specific whole-request budget

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_knowledge_rule_screening_service.py`

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_rule_screening_service.py backend/tests/services/test_knowledge_rule_screening_service.py
git commit -m "feat: add structured budgeted rule screening prompts"
```

### Task 7: Add observability and regression verification

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/tests/services/test_review_runner.py`
- Optionally modify: `scripts/bench_java_review_cases.py`

**Step 1: Write the failing test**

Add tests for:
- prompt metadata includes kept/compressed/dropped block summaries
- must-keep blocks are always reported

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "prompt_budget_metadata"`

Expected: FAIL because metadata is not emitted yet.

**Step 3: Write minimal implementation**

Emit prompt budget metadata to:
- conversation message metadata
- optional debug log / memory probe payloads

**Step 4: Run verification**

Run:
- `pytest -q backend/tests/services/test_context_block.py backend/tests/services/test_context_priority_policy.py backend/tests/services/test_prompt_budget_planner.py`
- `pytest -q backend/tests/services/test_review_runner.py -k "context_block or prompt_budget or observation"`
- `python3 scripts/bench_java_review_cases.py --case java-ddd-composite-quality-regression --submit --analysis-mode light`

Expected:
- tests PASS
- real benchmark still completes
- no evidence that category detection regresses

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/tests/services/test_review_runner.py scripts/bench_java_review_cases.py
git commit -m "feat: add prompt budget observability"
```
