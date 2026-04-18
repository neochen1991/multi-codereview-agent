# Observation-Driven Review Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Introduce a generalized observation pipeline that helps the LLM explicitly review high-value code phenomena without turning the system into rule-driven finding generation.

**Architecture:** Keep model judgment as the source of truth, but enrich each expert prompt with structured observations extracted from code/context. Observations are not findings; they are review anchors that the model must explicitly confirm or reject. Persist the observation context into finding code context so downstream validation and UI remain aligned.

**Tech Stack:** Python, pytest, existing `ReviewRunner` prompt/parser pipeline, `JavaQualitySignalExtractor`

---

### Task 1: Define generalized observations in the Java extractor

**Files:**
- Modify: `backend/app/services/java_quality_signal_extractor.py`
- Test: `backend/tests/services/test_java_quality_signal_extractor.py`

**Step 1: Write failing tests**

Add tests asserting extractor output now contains `observations` with stable fields like `observation_id`, `kind`, `summary`, `line_start`, `evidence`, `risk_hints`.

**Step 2: Run targeted tests to verify failure**

Run: `pytest -q backend/tests/services/test_java_quality_signal_extractor.py -k "observation"`
Expected: FAIL because `observations` does not exist yet.

**Step 3: Implement minimal observation model output**

Extend extractor return payload to include generalized observations for current Java quality signals while preserving existing `signals`, `summary`, `matched_terms`, `signal_terms`.

**Step 4: Run targeted tests**

Run: `pytest -q backend/tests/services/test_java_quality_signal_extractor.py -k "observation"`
Expected: PASS.

### Task 2: Thread observations through review context and prompts

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write failing tests**

Add tests asserting:
- finding code context contains `review_observations`
- expert prompt contains an “observations” section
- prompt instructs model to explicitly review each observation without treating it as a pre-decided defect

**Step 2: Run targeted tests to verify failure**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation"`
Expected: FAIL.

**Step 3: Implement minimal plumbing**

Populate observations from extractor into:
- repository context summary
- finding code context
- expert prompt instructions

Prompt rules:
- observations are suspicious code phenomena, not final conclusions
- model must review them one by one
- model may reject them with reasons
- if model confirms them, it must anchor finding file and line precisely

**Step 4: Run targeted tests**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation"`
Expected: PASS.

### Task 3: Preserve observation linkage in parsed findings

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write failing tests**

Add tests asserting parsed findings can carry `observation_ids` and that stabilization does not drop them.

**Step 2: Run targeted tests to verify failure**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation_ids"`
Expected: FAIL.

**Step 3: Implement minimal parser/stabilizer updates**

Allow parsed JSON to contain `observation_ids`, normalize them, and preserve them into finding `code_context.review_observations` / metadata for downstream judge verification.

**Step 4: Run targeted tests**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation_ids"`
Expected: PASS.

### Task 4: Run focused regression verification

**Files:**
- Test: `backend/tests/services/test_java_quality_signal_extractor.py`
- Test: `backend/tests/services/test_review_runner.py`
- Check: `backend/app/services/java_quality_signal_extractor.py`
- Check: `backend/app/services/review_runner.py`

**Step 1: Run extractor and review-runner focused suites**

Run:
- `pytest -q backend/tests/services/test_java_quality_signal_extractor.py`
- `pytest -q backend/tests/services/test_review_runner.py -k "observation or loop or comment_contract"`

Expected: PASS.

**Step 2: Run syntax verification**

Run: `python -m py_compile backend/app/services/java_quality_signal_extractor.py backend/app/services/review_runner.py`
Expected: PASS.

**Step 3: Commit**

```bash
git add docs/plans/2026-04-19-observation-driven-review-quality-plan.md backend/app/services/java_quality_signal_extractor.py backend/app/services/review_runner.py backend/tests/services/test_java_quality_signal_extractor.py backend/tests/services/test_review_runner.py
git commit -m "feat: add observation-driven review guidance"
```
