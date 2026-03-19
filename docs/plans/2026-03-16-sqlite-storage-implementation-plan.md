# SQLite Storage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the project's file-backed structured storage with SQLite for all new structured data while keeping heavy artifacts and exports on the filesystem, with no historical data migration.

**Architecture:** Introduce a SQLite persistence layer for reviews, events, messages, findings, issues, feedback, knowledge documents, expert metadata, and runtime settings. Keep artifact exports, PPT files, images, and other heavy/generated files on disk. Replace file repositories behind the existing service layer in phased steps so APIs and business logic remain stable.

**Tech Stack:** Python, sqlite3, Pydantic, FastAPI, pytest

---

## Scope

**Move to SQLite**
- `reviews`
- `review_events`
- `messages`
- `findings`
- `issues`
- `feedback`
- `knowledge_documents`
- `experts`
- `runtime_settings`

**Keep on filesystem**
- `reviews/<review_id>/artifacts/*`
- exported PPT / images / Mermaid / draw.io sources
- any future large binary export

**Non-goals**
- No migration of old JSON history under `backend/app/storage`
- No dual-write mode
- No change to API contract unless a field is impossible to preserve

## Implementation strategy

### Task 1: Add SQLite foundation

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/db/sqlite.py`
- Create: `/Users/neochen/multi-codereview-agent/backend/app/db/schema.sql`
- Create: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_sqlite_bootstrap.py`

**Step 1: Write the failing test**

Create a bootstrap test that:
- creates a temporary storage root
- initializes SQLite
- asserts the DB file exists
- asserts all required tables exist

```python
def test_sqlite_bootstrap_creates_expected_tables(tmp_path: Path):
    db = SqliteDatabase(tmp_path / "app.db")
    db.initialize()
    tables = db.list_tables()
    assert "reviews" in tables
    assert "messages" in tables
    assert "knowledge_documents" in tables
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_bootstrap.py -q
```

Expected: FAIL because `SqliteDatabase` does not exist yet.

**Step 3: Write minimal implementation**

Implement:
- `SqliteDatabase` with:
  - constructor accepting db path
  - `connect()`
  - `initialize()`
  - `executescript()` using `schema.sql`
  - `list_tables()`
- `schema.sql` with tables:
  - `reviews`
  - `review_events`
  - `messages`
  - `findings`
  - `issues`
  - `feedback`
  - `knowledge_documents`
  - `experts`
  - `runtime_settings`

Use text/json columns for complex payloads in phase 1.

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_bootstrap.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/db/sqlite.py backend/app/db/schema.sql backend/tests/services/test_sqlite_bootstrap.py
git commit -m "feat: add sqlite storage foundation"
```

### Task 2: Add SQLite review repository

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_review_repository.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/domain/models/review.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_sqlite_review_repository.py`

**Step 1: Write the failing test**

Test save/get/list for `ReviewTask`.

```python
def test_sqlite_review_repository_round_trip(tmp_path: Path):
    repo = SqliteReviewRepository(tmp_path / "app.db")
    review = ReviewTask(...)
    repo.save(review)
    loaded = repo.get(review.review_id)
    assert loaded is not None
    assert loaded.review_id == review.review_id
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_review_repository.py -q
```

Expected: FAIL because repository does not exist.

**Step 3: Write minimal implementation**

Implement `SqliteReviewRepository`:
- store scalar columns directly
- store `selected_experts` as JSON text
- store `subject` as JSON text
- keep timestamps serialized with existing Pydantic behavior

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_review_repository.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories/sqlite_review_repository.py backend/tests/services/test_sqlite_review_repository.py
git commit -m "feat: add sqlite review repository"
```

### Task 3: Add SQLite repositories for event/message/finding/issue/feedback

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_event_repository.py`
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_message_repository.py`
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_finding_repository.py`
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_issue_repository.py`
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_feedback_repository.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_sqlite_stream_repositories.py`

**Step 1: Write the failing test**

Add round-trip tests for append/list and save/list behavior for each repository.

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_stream_repositories.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Implement repositories with:
- `review_id` foreign-key-like field
- append-only semantics for events/messages
- replace-by-id semantics for findings/issues/feedback
- JSON text for payload/metadata-rich fields

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_stream_repositories.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories/sqlite_*_repository.py backend/tests/services/test_sqlite_stream_repositories.py
git commit -m "feat: add sqlite review stream repositories"
```

### Task 4: Switch ReviewService and ReviewRunner to SQLite repositories

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_service.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/feedback_learner_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/api/test_reviews_api.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_review_runner.py`

**Step 1: Write the failing integration test**

Add a test that creates a review and asserts:
- `app.db` is created
- review/event/message/finding rows are written there
- no JSON files for those entities are required anymore

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/api/test_reviews_api.py backend/tests/services/test_review_runner.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Update service constructors to instantiate SQLite repositories instead of file repositories.

Do not change:
- artifact generation paths
- API surface
- domain models

**Step 4: Run tests to verify they pass**

Run:
```bash
.venv/bin/pytest backend/tests/api/test_reviews_api.py backend/tests/services/test_review_runner.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_service.py backend/app/services/review_runner.py backend/app/services/feedback_learner_service.py backend/tests/api/test_reviews_api.py backend/tests/services/test_review_runner.py
git commit -m "feat: switch review pipeline storage to sqlite"
```

### Task 5: Add SQLite knowledge repository

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_knowledge_repository.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/knowledge_service.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/knowledge_ingestion_service.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/knowledge_retrieval_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_sqlite_knowledge_repository.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_knowledge_retrieval_service.py`

**Step 1: Write the failing test**

Test:
- create/list/delete knowledge docs
- retrieve docs for expert by context
- dedupe on title/filename/content fingerprint

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_knowledge_repository.py backend/tests/services/test_knowledge_retrieval_service.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Store markdown content directly in SQLite in phase 1.

Columns:
- `doc_id`
- `expert_id`
- `title`
- `doc_type`
- `content`
- `tags_json`
- `source_filename`
- `created_at`

Preserve existing dedupe semantics from `FileKnowledgeRepository`.

**Step 4: Run tests to verify they pass**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_sqlite_knowledge_repository.py backend/tests/services/test_knowledge_retrieval_service.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories/sqlite_knowledge_repository.py backend/app/services/knowledge_service.py backend/app/services/knowledge_ingestion_service.py backend/app/services/knowledge_retrieval_service.py backend/tests/services/test_sqlite_knowledge_repository.py backend/tests/services/test_knowledge_retrieval_service.py
git commit -m "feat: store knowledge documents in sqlite"
```

### Task 6: Add SQLite expert repository

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_expert_repository.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/expert_registry.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/repositories/file_expert_repository.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_expert_registry.py`

**Step 1: Write the failing test**

Add a test that proves:
- built-in experts still load
- user overrides now persist in SQLite
- extension-bound skills still merge correctly

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_expert_registry.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Important design:
- Keep built-in expert templates on disk as immutable defaults
- Store only user overrides / mutable expert metadata in SQLite
- Merge built-in + SQLite override + extension skill bindings at read time

This avoids moving the built-in expert seed files into DB.

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_expert_registry.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories/sqlite_expert_repository.py backend/app/services/expert_registry.py backend/tests/services/test_expert_registry.py
git commit -m "feat: persist expert overrides in sqlite"
```

### Task 7: Add SQLite runtime settings repository

**Files:**
- Create: `/Users/neochen/multi-codereview-agent/backend/app/repositories/sqlite_runtime_settings_repository.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/runtime_settings_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_runtime_settings_service.py`

**Step 1: Write the failing test**

Test:
- save settings
- load settings
- ensure defaults still work when DB is empty

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_runtime_settings_service.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Use a single-row table or `key/value` table.
Phase 1 recommendation:
- one table `runtime_settings`
- one row `settings_id = 'default'`
- `payload_json`

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_runtime_settings_service.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories/sqlite_runtime_settings_repository.py backend/app/services/runtime_settings_service.py backend/tests/services/test_runtime_settings_service.py
git commit -m "feat: move runtime settings to sqlite"
```

### Task 8: Add central storage configuration

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/config.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_service.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_storage_backend_config.py`

**Step 1: Write the failing test**

Test:
- default DB path resolves to `backend/app/storage/app.db`
- storage directory is created automatically

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_storage_backend_config.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Add config values:
- `SQLITE_DB_PATH`
- optional `STORAGE_BACKEND=sqlite`

Even if file backend is no longer used, keep the config explicit.

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_storage_backend_config.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/services/test_storage_backend_config.py
git commit -m "chore: configure sqlite storage backend"
```

### Task 9: Keep artifacts on filesystem and verify end-to-end behavior

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/artifact_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_artifact_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/api/test_reviews_api.py`

**Step 1: Write the failing test**

Test:
- review metadata and structured outputs land in SQLite
- artifacts still land under `storage/reviews/<review_id>/artifacts`

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_artifact_service.py backend/tests/api/test_reviews_api.py -q
```

Expected: FAIL

**Step 3: Write minimal implementation**

Do not change artifact paths.
Only update any code that still assumes JSON review files exist nearby.

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_artifact_service.py backend/tests/api/test_reviews_api.py -q
```

Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/artifact_service.py backend/tests/services/test_artifact_service.py backend/tests/api/test_reviews_api.py
git commit -m "test: verify sqlite storage with filesystem artifacts"
```

### Task 10: Run final regression suite

**Files:**
- Modify: none unless regressions appear

**Step 1: Run backend regression**

Run:
```bash
.venv/bin/pytest backend/tests/services backend/tests/api -q
```

Expected: PASS

**Step 2: Run frontend build**

Run:
```bash
cd frontend && npm run build
```

Expected: PASS

**Step 3: Run one live review smoke**

Run one real review and verify:
- `app.db` receives new rows
- no file-backed review JSON is required
- UI still shows review process/history correctly

**Step 4: Commit final stabilization**

```bash
git add -A
git commit -m "feat: switch structured storage to sqlite"
```

## Notes for implementation

- Do not migrate historical file-backed data.
- Do not add file/SQLite dual-write.
- Keep built-in expert seed definitions on disk; only mutable expert state should move to SQLite.
- Keep `ArtifactService` file-backed.
- Prefer JSON text columns in phase 1 over over-normalizing schema.
- All new repository implementations should preserve current service contracts so frontend and API code remain stable.

## Validation checklist

- Creating a review writes review/event/message/finding/issue rows to SQLite.
- Uploading knowledge markdown writes docs to SQLite.
- Editing expert metadata persists in SQLite.
- Runtime settings save/load works after restart.
- Artifacts still appear under `storage/reviews/<review_id>/artifacts`.
- Existing API payload shapes are unchanged.

