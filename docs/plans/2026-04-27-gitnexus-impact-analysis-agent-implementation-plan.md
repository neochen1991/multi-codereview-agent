# GitNexus Impact Analysis Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a GitNexus-backed change impact analysis expert that produces an impact report for every MR.

**Architecture:** GitNexus graph indexing runs in the background per repository. Review execution queries the latest graph through a runtime tool, falls back to existing repository-context heuristics when the graph is missing, and persists a structured `impact_report` on the final review report.

**Tech Stack:** FastAPI backend, Pydantic models, existing ReviewRunner/ReviewToolGateway, built-in expert YAML/prompt/spec files, GitNexus CLI or Python integration, React frontend.

---

### Task 1: Add Impact Report Domain Models

**Files:**
- Modify: `backend/app/domain/models/report.py`
- Test: `backend/tests/services/test_review_issues.py`

**Step 1: Write the failing test**

Add a test that builds a report and asserts `impact_report` exists with default values when no analysis has run.

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_issues.py -k impact_report -q
```

Expected: fails because `impact_report` is not defined.

**Step 3: Implement models**

Add Pydantic models:

- `ImpactSymbol`
- `ImpactFile`
- `ImpactModule`
- `ImpactPath`
- `TestScopeRecommendation`
- `ImpactReport`

Add `impact_report: ImpactReport | None = None` to `ReviewReport`.

**Step 4: Run test**

Run the same pytest command. Expected: pass.

### Task 2: Add GitNexus Impact Service

**Files:**
- Create: `backend/app/services/gitnexus_impact_service.py`
- Test: `backend/tests/services/test_gitnexus_impact_service.py`

**Step 1: Write fallback test**

Create a test where GitNexus graph is missing. Assert the service returns:

- `graph_status="missing"`
- changed files from `ReviewSubject.changed_files`
- non-empty `recommended_test_scope`
- `limitations` explaining fallback mode

**Step 2: Implement fallback service**

Implement:

```python
class GitNexusImpactService:
    def analyze(self, subject: ReviewSubject, runtime: RuntimeSettings) -> ImpactReport:
        ...
```

First version should support fallback without requiring GitNexus installed.

**Step 3: Run tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_gitnexus_impact_service.py -q
```

Expected: pass.

### Task 3: Add GitNexus Tool Binding

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Test: `backend/tests/services/test_tool_gateway.py`

**Step 1: Write tool test**

Add a test that invokes `gitnexus_impact_analysis` and asserts returned payload has:

- `tool_name`
- `graph_status`
- `changed_files`
- `recommended_tests`
- `limitations`

**Step 2: Register tool**

In `ReviewToolGateway._register_defaults`, register:

```python
self._gateway.register("gitnexus_impact_analysis", "tool", self._gitnexus_impact_analysis)
```

Implement `_gitnexus_impact_analysis` by delegating to `GitNexusImpactService`.

**Step 3: Run tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_tool_gateway.py -k gitnexus -q
```

Expected: pass.

### Task 4: Add Built-In Expert

**Files:**
- Create: `backend/app/builtin_experts/change_impact_analysis/expert.yaml`
- Create: `backend/app/builtin_experts/change_impact_analysis/prompt.md`
- Create: `backend/app/builtin_experts/change_impact_analysis/review_spec.md`
- Test: `backend/tests/services/test_expert_registry.py`

**Step 1: Write registry test**

Assert enabled experts include `change_impact_analysis`, and its runtime tools include `gitnexus_impact_analysis`.

**Step 2: Add expert files**

Create the expert with:

- `expert_id: change_impact_analysis`
- `name_zh: 关联性影响分析专家`
- `runtime_tool_bindings: gitnexus_impact_analysis, repo_context_search, diff_inspector, test_surface_locator`
- clear out-of-scope boundaries

**Step 3: Run registry test**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_expert_registry.py -q
```

Expected: pass.

### Task 5: Produce Impact Report For Every Review

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/review_report_builder.py`
- Modify: `backend/app/services/review_service.py`
- Test: `backend/tests/services/test_review_runner.py`
- Test: `backend/tests/services/test_review_issues.py`

**Step 1: Write runner test**

Add a test that runs a small review and asserts the final report has `impact_report`.

**Step 2: Implement report generation**

During review execution, call `GitNexusImpactService.analyze(...)` once per review.

Persist the result in review metadata or report artifact payload.

Ensure `ReviewService.build_report` exposes it as `ReviewReport.impact_report`.

**Step 3: Run tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k impact_report -q
.venv/bin/python -m pytest backend/tests/services/test_review_issues.py -k impact_report -q
```

Expected: pass.

### Task 6: Add Background Index Service Skeleton

**Files:**
- Create: `backend/app/services/gitnexus_index_service.py`
- Modify: `backend/app/services/auto_review_scheduler.py`
- Test: `backend/tests/services/test_gitnexus_index_service.py`

**Step 1: Write status test**

Assert index service can return a status object with:

- `repo_id`
- `graph_status`
- `last_indexed_commit`
- `last_indexed_at`
- `error_message`

**Step 2: Implement service skeleton**

Support:

- `get_status(repo_id)`
- `mark_ready(...)`
- `mark_failed(...)`
- `should_reindex(...)`

First version can leave actual GitNexus execution behind a method boundary.

**Step 3: Hook scheduler**

Add a scheduler call that periodically checks whether indexing should run. Keep it fail-open and log failures.

**Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_gitnexus_index_service.py -q
```

Expected: pass.

### Task 7: Add Frontend Impact Report Panel

**Files:**
- Modify: `frontend/src/services/api.ts`
- Create: `frontend/src/components/review/ImpactReportPanel.tsx`
- Modify: relevant result page component under `frontend/src/pages` or `frontend/src/components/review`
- Test: existing frontend smoke or Playwright screenshot flow

**Step 1: Add API type**

Add TypeScript type for `impact_report`.

**Step 2: Create panel**

Render:

- graph status
- changed files/symbols
- impacted files/modules
- impact paths
- recommended tests
- limitations

**Step 3: Wire into result page**

Place panel above issue list.

**Step 4: Verify UI**

Run frontend smoke or use Playwright to inspect the result page.

### Task 8: End-to-End Smoke

**Files:**
- Modify or add script under `scripts/`
- Test: real local Java smoke

**Step 1: Create smoke**

Use a local Java MR with two changed files.

**Step 2: Start backend**

Run:

```bash
.venv/bin/uvicorn app.main:app --app-dir backend --port 8011
```

**Step 3: Submit review**

Create review through API and wait for report.

**Step 4: Assert report**

Assert:

- `impact_report` exists
- `changed_files` is non-empty
- `recommended_test_scope` is non-empty
- `graph_status` is one of `ready|stale|missing|failed`

---

## Execution Notes

First implementation should be useful even before GitNexus is installed. The fallback report is not a placeholder; it gives changed files, related files, and suggested test scope from existing repo context. GitNexus integration can then replace or enrich the same report shape.
