# Performance Knowledge Doc Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为性能与可靠性专家新增一份超过 10000 行的长版参考 Markdown 文档，完成绑定，并用自动化测试证明系统只按命中章节加载文档内容。

**Architecture:** 长版文档作为仓库内维护资产保存，绑定时沿用现有 KnowledgeService + KnowledgePageIndexService + KnowledgeRetrievalService 链路入库到 SQLite。测试直接读取该长文，验证索引构建、命中章节召回和专家 prompt 注入行为，确保不会回退成整篇全文注入。

**Tech Stack:** Python, pytest, SQLite, FastAPI service layer, Markdown knowledge assets

---

### Task 1: Add Failing Tests For Large Performance Doc Retrieval

**Files:**
- Modify: `backend/tests/services/test_knowledge_retrieval_service.py`
- Modify: `backend/tests/services/test_review_runner.py`
- Read: `backend/app/services/knowledge_retrieval_service.py`
- Read: `backend/app/services/review_runner.py`

**Step 1: Write the failing tests**

- Add a test that loads a repo-managed performance Markdown asset and asserts:
  - file exists
  - line count is greater than 10000
  - retrieval for `performance_reliability` returns matched sections, not the whole document
- Add a test that builds the expert system prompt from the retrieved document and asserts:
  - prompt contains matched section paths/content
  - prompt does not contain unrelated section content from distant chapters
  - prompt length is materially smaller than the original document content

**Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/services/test_knowledge_retrieval_service.py backend/tests/services/test_review_runner.py -q`

Expected: FAIL because the long Markdown asset does not exist yet.

### Task 2: Generate And Commit The Long Performance Markdown Asset

**Files:**
- Create: `docs/expert-specs-export/performance_reliability/performance-reliability-ultra-spec.md`

**Step 1: Generate the Markdown asset**

- Build a long, structured document with:
  - Java 性能基础规范
  - JVM / GC / 内存 / 线程 / 锁 / I/O / 网络 / 数据库 / 缓存 / 消息 / 批处理 / 压测 / 稳定性 / 安全基线
  - 每个场景包含规则、风险、正例、反例、检查项、上线建议
- Ensure line count exceeds 10000 and content is varied by topic, not repeated filler.

**Step 2: Re-run tests**

Run: `pytest backend/tests/services/test_knowledge_retrieval_service.py backend/tests/services/test_review_runner.py -q`

Expected: retrieval-related tests should pass if no additional code changes are needed.

### Task 3: Bind The Long Doc To Performance Expert

**Files:**
- No source-code change required if current API/service contract is sufficient
- Runtime asset target: SQLite knowledge tables under `backend/app/storage/app.db`

**Step 1: Bind the document**

- Use the existing knowledge creation path to create one knowledge document:
  - `expert_id = performance_reliability`
  - `doc_type = review_rule`
  - `source_filename = performance-reliability-ultra-spec.md`
  - `content = file contents`

**Step 2: Verify binding**

- Query grouped knowledge or inspect SQLite rows to confirm the document is bound to `performance_reliability`.

### Task 4: Add Regression Test For On-Demand Loading

**Files:**
- Modify: `backend/tests/services/test_knowledge_retrieval_service.py`
- Modify: `backend/tests/services/test_review_runner.py`

**Step 1: Add assertions proving on-demand behavior**

- Assert retrieval only carries the top matched sections into `matched_sections`
- Assert compiled `document.content` contains targeted performance sections and excludes unrelated distant sections
- Assert prompt assembly uses matched sections rather than full long text

**Step 2: Run focused tests**

Run: `pytest backend/tests/services/test_knowledge_retrieval_service.py backend/tests/services/test_review_runner.py -q`

Expected: PASS

### Task 5: Run End Verification

**Files:**
- Read: `backend/app/storage/app.db`

**Step 1: Verify runtime binding**

- Confirm the long doc exists in `knowledge_documents`
- Confirm indexed nodes exist in `knowledge_document_nodes`

**Step 2: Summarize evidence**

- Report:
  - file path
  - line count
  - bound expert id
  - node count
  - test commands and outcomes

