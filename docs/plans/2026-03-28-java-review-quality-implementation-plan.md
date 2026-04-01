# Java Review Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 提升 Java 项目代码检视质量，同时兼容通用 Java 项目和 DDD 项目，重点提高安全、性能、架构问题的命中率与可解释性。

**Architecture:** 采用“Java 通用基座 + DDD 增强层”方案。先把上下文组装、规则筛选、运行时工具和 prompt 收敛为 Java 通用能力，再根据 DDD 信号追加领域规则和上下文，避免普通 Java 项目被 DDD 语义绑架，同时保留 DDD 项目的深度审查能力。

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, ripgrep, pytest

---

### Task 1: 固化 Java 通用模式与 DDD 增强模式识别

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Modify: `backend/app/services/java_ddd_context_assembler.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_java_review_mode_uses_general_java_context_when_no_ddd_signal(...):
    ...
    assert result["java_review_mode"] == "general"


def test_java_review_mode_uses_ddd_context_when_signal_present(...):
    ...
    assert result["java_review_mode"] == "ddd"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "java_review_mode" -q`
Expected: FAIL because mode field / detection logic does not exist.

**Step 3: Write minimal implementation**

在 `tool_gateway.py` 增加 Java 项目模式识别：
- `general`: 所有 `.java` / `.kt` / `.groovy` 文件默认进入
- `ddd`: 命中 `domain/aggregate/valueobject/event/application` 包路径、类名或上下文信号时启用

在 `java_ddd_context_assembler.py` 中拆分：
- 通用 Java 上下文组装
- DDD 扩展上下文组装

在 `review_runner.py` 中把模式信息写入 `repository_context` 和 `finding.code_context`。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "java_review_mode" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway.py backend/app/services/java_ddd_context_assembler.py backend/app/services/review_runner.py backend/tests/services/test_skill_gateway.py
git commit -m "feat: split java review into general and ddd modes"
```

### Task 2: 扩展 Java 通用代码上下文包

**Files:**
- Modify: `backend/app/services/java_ddd_context_assembler.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `frontend/src/services/api.ts`
- Test: `backend/tests/services/test_skill_gateway.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_java_context_pack_contains_controller_service_repository_and_transaction(...):
    ...
    assert result["current_class_context"]
    assert result["caller_contexts"]
    assert result["callee_contexts"]
    assert result["transaction_context"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "context_pack_contains" -q`
Expected: FAIL because general Java context is incomplete.

**Step 3: Write minimal implementation**

把现有上下文字段收敛成通用 Java 必备结构：
- `current_class_context`
- `caller_contexts`
- `callee_contexts`
- `transaction_context`
- `persistence_contexts`
- `security_entry_contexts`

DDD 模式额外再带：
- `parent_contract_contexts`
- `domain_model_contexts`

同步更新：
- `review_runner._build_repository_source_blocks(...)`
- `review_runner._build_finding_code_context(...)`
- 前端 `api.ts` 类型

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "context_pack_contains" -q`
- `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "code_context" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/java_ddd_context_assembler.py backend/app/services/review_runner.py frontend/src/services/api.ts backend/tests/services/test_skill_gateway.py backend/tests/services/test_review_runner.py
git commit -m "feat: expand java review context pack"
```

### Task 3: 新增 Java 通用运行时核验工具

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Modify: `backend/app/domain/models/runtime_settings.py`
- Modify: `backend/app/domain/models/app_config.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_java_runtime_tools_include_controller_entry_guard_inspector(...):
    ...
    assert "controller_entry_guard_inspector" in tool_names


def test_java_runtime_tools_include_repository_query_risk_inspector(...):
    ...
    assert "repository_query_risk_inspector" in tool_names
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "controller_entry_guard_inspector or repository_query_risk_inspector" -q`
Expected: FAIL because the tools do not exist.

**Step 3: Write minimal implementation**

新增并接入：
- `controller_entry_guard_inspector`
  - 检查 `@Validated`、参数校验、入口保护信号
- `repository_query_risk_inspector`
  - 检查 `findAll/findByStatus/list/select *`、分页缺失、大结果集信号
- 保留已有：
  - `transaction_boundary_inspector`
  - `application_service_boundary_inspector`
  - `aggregate_invariant_inspector`

按专家自动追加：
- `security_compliance`: entry + transaction
- `performance_reliability`: transaction + query risk
- `architecture_design`: service boundary
- `database_analysis`: query risk
- `ddd_specification`: aggregate invariant + transaction

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "controller_entry_guard_inspector or repository_query_risk_inspector" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway.py backend/app/domain/models/runtime_settings.py backend/app/domain/models/app_config.py backend/tests/services/test_skill_gateway.py
git commit -m "feat: add general java runtime inspection tools"
```

### Task 4: 将规则筛选改为 Java 通用优先、DDD 追加

**Files:**
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Modify: `backend/app/services/knowledge_service.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_knowledge_rule_screening_service.py`

**Step 1: Write the failing test**

```python
def test_java_general_project_only_loads_general_java_rules(...):
    ...
    assert "DDD-JDDD-001" not in matched_ids


def test_java_ddd_project_loads_general_and_ddd_rules(...):
    ...
    assert "DDD-JDDD-001" in matched_ids
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_knowledge_rule_screening_service.py -k "general_project or ddd_project" -q`
Expected: FAIL because rules are not split by mode.

**Step 3: Write minimal implementation**

规则加载分两层：
- Java 通用规则包
  - 安全、性能、架构、数据库、正确性
- Java DDD 增强规则包
  - 仅在 `java_review_mode == "ddd"` 时启用

同时把 `repo_context_search` 和运行时工具信号并入 `query_terms`，减少纯 diff 驱动的漏筛。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_knowledge_rule_screening_service.py -k "general_project or ddd_project" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_rule_screening_service.py backend/app/services/knowledge_service.py backend/app/services/review_runner.py backend/tests/services/test_knowledge_rule_screening_service.py
git commit -m "feat: split java rule screening into general and ddd layers"
```

### Task 5: 压缩 Java rule screening 和 expert prompt，降低超时率

**Files:**
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/main_agent_service.py`
- Test: `backend/tests/services/test_review_runner.py`
- Test: `backend/tests/services/test_llm_chat_service_timeouts.py`

**Step 1: Write the failing test**

```python
def test_java_rule_screening_prompt_is_trimmed_to_core_fields(...):
    ...
    assert "problem_code_example" not in prompt
    assert "rule_id" in prompt
    assert "title" in prompt


def test_database_expert_prompt_uses_compact_context(...):
    ...
    assert len(prompt) < SOME_LIMIT
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "trimmed_to_core_fields or compact_context" -q`
Expected: FAIL because prompts are still verbose.

**Step 3: Write minimal implementation**

压缩顺序固定为：
1. 规则卡只保留 `rule_id/title/priority/scene/description`
2. 目标 hunk 保留
3. 目标文件完整 diff 保留
4. 其他变更摘要裁短
5. 绑定文档只保留命中章节摘要
6. 日志中的 `reasoning_content` 不再带入 prompt

对 `database_analysis` 和 `rule_screening` 单独做 compact 模式。

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "trimmed_to_core_fields or compact_context" -q`
- `.venv/bin/python -m pytest backend/tests/services/test_llm_chat_service_timeouts.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_rule_screening_service.py backend/app/services/review_runner.py backend/app/services/main_agent_service.py backend/tests/services/test_review_runner.py backend/tests/services/test_llm_chat_service_timeouts.py
git commit -m "perf: trim java rule screening and expert prompts"
```

### Task 6: 接通本地 repo context，确保 Java 审核能看到源码

**Files:**
- Modify: `backend/app/services/repository_context_service.py`
- Modify: `backend/app/services/tool_gateway.py`
- Modify: `backend/app/services/review_service.py`
- Test: `backend/tests/services/test_repository_context_service.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_manual_java_review_can_use_workspace_as_repo_context(...):
    ...
    assert result["summary"] != "代码仓上下文未配置或本地仓不可用"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "workspace_as_repo_context" -q`
Expected: FAIL because manual review still lacks repo context.

**Step 3: Write minimal implementation**

增加安全回退：
- 对 manual / local smoke / benchmark 场景
- 如果当前工作区可映射到 review subject 的路径
- 允许直接把工作区作为 `code_repo_local_path`

保持线上默认行为不变，不影响显式配置的 clone/sync 逻辑。

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_repository_context_service.py -q`
- `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "workspace_as_repo_context" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/repository_context_service.py backend/app/services/tool_gateway.py backend/app/services/review_service.py backend/tests/services/test_repository_context_service.py backend/tests/services/test_skill_gateway.py
git commit -m "feat: allow local java reviews to resolve workspace repo context"
```

### Task 7: 建立 Java 真实用例基准集

**Files:**
- Create: `backend/tests/fixtures/java_cases/`
- Create: `scripts/bench_java_review_cases.py`
- Create: `backend/tests/services/test_java_review_benchmarks.py`

**Step 1: Write the failing test**

```python
def test_java_benchmark_case_transactional_order_close_hits_expected_rules(...):
    ...
    assert "ARCH-JDDD-002" in matched_rule_ids
    assert "DDD-JDDD-001" in matched_rule_ids
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py -q`
Expected: FAIL because the benchmark fixture set does not exist.

**Step 3: Write minimal implementation**

至少沉淀 12 条用例：
- 安全 4 条
- 性能 4 条
- 架构 / DDD 4 条

每条用例包含：
- `changed_files`
- `unified_diff`
- 预期专家
- 预期命中规则
- 预期 finding 类型

脚本输出：
- review_id
- 命中专家
- 命中规则
- findings / issues
- 失败阶段

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py -q`
- `.venv/bin/python scripts/bench_java_review_cases.py`
Expected: PASS and prints benchmark summary.

**Step 5: Commit**

```bash
git add backend/tests/fixtures/java_cases scripts/bench_java_review_cases.py backend/tests/services/test_java_review_benchmarks.py
git commit -m "test: add java review benchmark suite"
```

### Task 8: 页面与报告增强 Java 可解释性

**Files:**
- Modify: `frontend/src/components/review/CodeReviewConclusionPanel.tsx`
- Modify: `frontend/src/components/review/IssueDetailPanel.tsx`
- Modify: `frontend/src/services/api.ts`
- Test: `frontend` existing typecheck/build pipeline

**Step 1: Write the failing test**

```tsx
// type-level / snapshot expectation
expect(issueDetail).toContain("事务边界");
expect(issueDetail).toContain("调用方");
expect(issueDetail).toContain("Repository / SQL");
```

**Step 2: Run test to verify it fails**

Run: `npm run typecheck`
Expected: FAIL or missing fields in UI rendering.

**Step 3: Write minimal implementation**

把 Java 通用上下文按固定顺序展示：
- 当前问题代码
- 调用方
- 被调方
- 事务边界
- Repository / Mapper / SQL
- DDD 命中时再展示聚合/领域模型

同时显示：
- `java_review_mode`
- 命中的 Java 通用规则
- 命中的 DDD 增强规则

**Step 4: Run test to verify it passes**

Run:
- `npm run typecheck`
- `npm run build`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/components/review/CodeReviewConclusionPanel.tsx frontend/src/components/review/IssueDetailPanel.tsx frontend/src/services/api.ts
git commit -m "feat: surface java review context and rule hits in UI"
```

### Task 9: 真实回归与发布前验收

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/code-wiki.md`
- Create: `docs/plans/2026-03-28-java-review-quality-rollout-checklist.md`

**Step 1: Write the failing checklist**

```md
- [ ] 通用 Java 项目能跑完整 review
- [ ] DDD 项目能额外命中 DDD 规则
- [ ] rule screening 平均耗时下降
- [ ] repo context 在本地真实用例中可用
```

**Step 2: Run validation**

Run:
- `.venv/bin/python scripts/bench_java_review_cases.py`
- `npm run typecheck`
- `npm run build`

Expected:
- 基准集输出命中规则与预期接近
- 前端构建通过

**Step 3: Update docs**

更新：
- Java 通用模式
- Java DDD 增强模式
- 默认专家组合
- 质量边界与已知限制

**Step 4: Commit**

```bash
git add README.md docs/architecture/code-wiki.md docs/plans/2026-03-28-java-review-quality-rollout-checklist.md
git commit -m "docs: add java review quality rollout guidance"
```
