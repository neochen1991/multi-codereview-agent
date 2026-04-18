# Generic Observation Framework Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Introduce a language-agnostic observation extraction framework and migrate Java to the first producer so review guidance can expand without rewriting the prompt pipeline per language.

**Architecture:** Add a generic `CodeObservationExtractor` that routes by file language to producer implementations and returns a stable payload shape for `signals`, `matched_terms`, `signal_terms`, and `observations`. Keep `JavaQualitySignalExtractor` as a compatibility shim over the Java producer while expanding Java observations toward language-neutral kinds such as cross-layer dependency and transactional side-effect coupling.

**Tech Stack:** Python, pytest, existing `ReviewRunner`, `MainAgentService`, `KnowledgeRuleScreeningService`

---

### Task 1: Add generic observation extractor routing

**Files:**
- Create: `backend/app/services/code_observation_extractor.py`
- Modify: `backend/app/services/java_quality_signal_extractor.py`
- Test: `backend/tests/services/test_code_observation_extractor.py`

**Step 1: Write the failing test**

Add tests asserting:
- `.java` files route to the Java producer
- unsupported languages return an empty but shape-stable payload

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_code_observation_extractor.py -v`
Expected: FAIL because the generic extractor does not exist.

**Step 3: Write minimal implementation**

Create `CodeObservationExtractor` with:
- language inference by file suffix
- producer registry
- stable empty payload helper

Keep `JavaQualitySignalExtractor` as a compatibility wrapper so old imports still work.

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_code_observation_extractor.py -v`
Expected: PASS.

### Task 2: Migrate service entry points to the generic extractor

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/main_agent_service.py`
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

Add or update tests asserting review context still includes observations and signals after swapping from the Java-specific extractor to the generic extractor.

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation" -v`
Expected: FAIL if the new extractor is not wired.

**Step 3: Write minimal implementation**

Replace direct instantiation of `JavaQualitySignalExtractor` in service entry points with `CodeObservationExtractor`.

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "observation" -v`
Expected: PASS.

### Task 3: Expand Java producer coverage to generic observation kinds

**Files:**
- Modify: `backend/app/services/java_quality_signal_extractor.py`
- Test: `backend/tests/services/test_java_quality_signal_extractor.py`

**Step 1: Write the failing test**

Add tests for generalized observation kinds such as:
- `cross_layer_dependency`
- `transactional_side_effect`

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_java_quality_signal_extractor.py -k "cross_layer or transactional_side_effect" -v`
Expected: FAIL because these observations are not emitted yet.

**Step 3: Write minimal implementation**

Extend Java extraction to emit:
- `cross_layer_dependency` when code structure suggests controller/domain/application layer crossing directly into repository/infrastructure concerns
- `transactional_side_effect` when transactional code mixes persistence with event publish / remote call / outbound side effects

Emit them with language-neutral `kind` values and stable observation metadata.

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_java_quality_signal_extractor.py -k "cross_layer or transactional_side_effect" -v`
Expected: PASS.

### Task 4: Broaden normalized observation schema

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

Add tests asserting normalized review observations preserve:
- `language`
- `tags`

**Step 2: Run test to verify it fails**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "normalize_review_observations" -v`
Expected: FAIL because schema normalization drops those fields.

**Step 3: Write minimal implementation**

Update review-runner normalization and summaries to retain the widened observation schema while keeping prompt size bounded.

**Step 4: Run test to verify it passes**

Run: `pytest -q backend/tests/services/test_review_runner.py -k "normalize_review_observations" -v`
Expected: PASS.

### Task 5: Run focused regression verification

**Files:**
- Test: `backend/tests/services/test_code_observation_extractor.py`
- Test: `backend/tests/services/test_java_quality_signal_extractor.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Run targeted suites**

Run:
- `pytest -q backend/tests/services/test_code_observation_extractor.py`
- `pytest -q backend/tests/services/test_java_quality_signal_extractor.py -k "observation or cross_layer or transactional_side_effect"`
- `pytest -q backend/tests/services/test_review_runner.py -k "observation or normalize_review_observations"`

Expected: PASS.

**Step 2: Run syntax verification**

Run: `python -m py_compile backend/app/services/code_observation_extractor.py backend/app/services/java_quality_signal_extractor.py backend/app/services/review_runner.py backend/app/services/main_agent_service.py backend/app/services/knowledge_rule_screening_service.py`
Expected: PASS.
