# PostgreSQL Datasource For Database Expert Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add repository-bound PostgreSQL datasource support so the database analysis expert can review code with table metadata and lightweight database statistics.

**Architecture:** Extend system config with repository-scoped PostgreSQL datasource definitions, add a read-only Postgres metadata service plus a `pg_schema_context` runtime tool, then inject the summarized metadata into the database expert review flow and expose the configuration in Settings.

**Tech Stack:** FastAPI, Pydantic, SQLite, JSON config, React + Ant Design, PostgreSQL metadata queries

---

### Task 1: Add datasource models

**Files:**
- Modify: `backend/app/domain/models/app_config.py`
- Modify: `backend/app/domain/models/runtime_settings.py`

**Step 1: Write the failing tests**

Add tests that assert:
- `AppConfig` can parse `database_sources`
- `RuntimeSettings` carries repository-bound PG datasource payload

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_runtime_settings_service.py -q`
Expected: FAIL because datasource fields do not exist

**Step 3: Write minimal implementation**

Add:
- `PostgresDataSourceConfig`
- `DatabaseSourceConfig` container
- runtime field carrying datasource definitions

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_runtime_settings_service.py -q`
Expected: PASS

### Task 2: Persist datasource config through config.json only

**Files:**
- Modify: `backend/app/services/runtime_settings_service.py`
- Modify: `backend/app/api/routes/settings.py`

**Step 1: Write the failing test**

Add a test verifying datasource config is treated as config-managed, not SQLite-managed.

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_runtime_settings_service.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**

Update config-managed field handling and API request/response schema.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_runtime_settings_service.py backend/tests/api/test_settings_api.py -q`
Expected: PASS

### Task 3: Add PostgreSQL metadata service

**Files:**
- Create: `backend/app/services/postgres_metadata_service.py`
- Create: `backend/tests/services/test_postgres_metadata_service.py`

**Step 1: Write the failing test**

Cover:
- datasource matching by repo URL
- metadata summary formatting
- graceful failure on missing env / no datasource

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_postgres_metadata_service.py -q`
Expected: FAIL because service does not exist

**Step 3: Write minimal implementation**

Add:
- repo URL normalization
- read-only datasource resolution
- candidate table extraction helpers
- summarized output contract

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_postgres_metadata_service.py -q`
Expected: PASS

### Task 4: Add pg_schema_context runtime tool

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Create: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

Assert `database_analysis` can receive `pg_schema_context` result and non-database experts do not.

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_skill_gateway.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**

Register tool and return:
- datasource summary
- matched tables
- metadata summary
- stats summary
- safe skip reasons

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_skill_gateway.py -q`
Expected: PASS

### Task 5: Inject PG metadata into database expert prompt

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

Assert database expert prompt contains PostgreSQL metadata context when tool returns data.

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_review_runner.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**

Append summarized `PostgreSQL 元信息上下文` into the database expert prompt and message metadata.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_review_runner.py -q`
Expected: PASS

### Task 6: Expose datasource config in Settings UI

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/pages/Settings/index.tsx`

**Step 1: Write the failing test or build expectation**

Prepare for a build-only verification that the settings page can edit repo-bound PG datasources.

**Step 2: Implement minimal UI**

Add a folded section for:
- repo URL
- provider
- host / port / database / user
- password env
- schema allowlist
- timeouts
- enabled

**Step 3: Run build verification**

Run: `npm run build`
Expected: PASS

### Task 7: Show tool summary in review process

**Files:**
- Modify: `frontend/src/components/review/ReviewDialogueStream.tsx`
- Modify: `frontend/src/styles/global.css`

**Step 1: Implement UI support**

Render `pg_schema_context` output as structured cards showing:
- datasource
- matched tables
- metadata fetched
- stats fetched
- skip reason if degraded

**Step 2: Verify**

Run: `npm run build`
Expected: PASS

### Task 8: End-to-end regression

**Files:**
- Reuse existing backend/frontend files

**Step 1: Run targeted backend tests**

Run:
`pytest backend/tests/services/test_runtime_settings_service.py backend/tests/services/test_postgres_metadata_service.py backend/tests/services/test_skill_gateway.py backend/tests/services/test_review_runner.py backend/tests/api/test_settings_api.py -q`

Expected: PASS

**Step 2: Run frontend build**

Run: `npm run build`

Expected: PASS

**Step 3: Optional local smoke**

If local backend is available, create one review task whose repo URL matches a configured PG datasource and verify database expert messages include PG metadata summary.

### Task 9: Commit

**Step 1: Commit backend and frontend changes**

```bash
git add backend frontend docs/plans
git commit -m "feat: add pg datasource support for database expert"
```
