# System Runtime And Review Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不牺牲专家绑定规范、语言通用规范提示、变更代码原文和关联源码上下文完整性的前提下，同时提升系统运行稳定性和代码检视质量，重点覆盖 Java 与 JavaScript/TypeScript 项目。

**Architecture:** 采用“两条主线并行推进”的方案。第一条主线聚焦系统运行质量，解决超时、失败降级、最终播报误导、可观测性不足和页面数据错位问题；第二条主线聚焦检视质量，强化专家输入完整性、语言通用规范注入、Java 通用上下文、DDD 增强上下文、规则命中和真实评测回归。所有改动都基于现有 FastAPI + SQLite + extensions/tools + repo_context_search 架构，不引入新的配置中心或运行时框架。语言通用规范不作为专家绑定配置存储，而是在主 Agent / expert Agent 构造 LLM prompt 时按语言统一注入。

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, React, TypeScript, pytest, ripgrep, httpx

---

### Task 1: 固化专家输入完整性门禁

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/domain/models/finding.py`
- Modify: `frontend/src/services/api.ts`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_expert_input_completeness_marks_missing_repository_context(...):
    context = runner._build_finding_code_context(...)
    assert context["input_completeness"]["has_review_spec"] is True
    assert context["input_completeness"]["has_target_diff"] is True
    assert context["input_completeness"]["has_related_source_context"] is False


def test_expert_input_completeness_records_review_inputs(...):
    context = runner._build_finding_code_context(...)
    assert "review_spec_titles" in context["review_inputs"]
    assert "bound_document_titles" in context["review_inputs"]
    assert "context_files" in context["review_inputs"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "input_completeness or review_inputs" -q`
Expected: FAIL because completeness fields are missing or incomplete.

**Step 3: Write minimal implementation**

在 `review_runner.py` 中：
- 统一构造 `input_completeness`
- 统一构造 `review_inputs`
- 把专家规范、绑定规则、绑定文档、语言通用规范提示、target diff、source context、related context 显式写入 `finding.code_context`

补充要求：
- 每个专家的实际输入必须同时包含：
  - 专家绑定规范（`review_spec` / 绑定文档 / 绑定规则）
  - 语言通用规范提示（如 Java / JavaScript / TypeScript 通用代码规范、质量与安全要求）
  - 当前变更代码原文
  - 关联源码上下文
- 若缺少“绑定规范”或“语言通用规范提示”任一项，`input_completeness` 必须显式反映缺口

在 `finding.py` 和 `api.ts` 中补齐结构化类型。

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "input_completeness or review_inputs" -q`
- `cd frontend && npm run typecheck`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/app/domain/models/finding.py frontend/src/services/api.ts backend/tests/services/test_review_runner.py
git commit -m "feat: track expert input completeness and input trace"
```

### Task 2: 让 repo_context_search 在无 related_files 时自动补齐关联上下文

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_repo_context_search_derives_related_contexts_without_explicit_related_files(...):
    repo_result = gateway.invoke_for_expert(...)[0]
    assert repo_result["related_contexts"]
    assert "processCreationForm" in repo_result["related_contexts"][0]["snippet"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "derives_related_contexts_without_explicit_related_files" -q`
Expected: FAIL because `related_contexts` is empty when `related_files` is missing.

**Step 3: Write minimal implementation**

在 `tool_gateway.py` 中：
- 根据 `symbol_contexts` 和 `search_keyword_sources` 生成 `ranked_anchors`
- 从最佳锚点反推出 `effective_related_files`
- `related_contexts`、`related_source_snippets`、`context_files` 都基于 `effective_related_files`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "derives_related_contexts_without_explicit_related_files" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway.py backend/tests/services/test_skill_gateway.py
git commit -m "fix: derive related contexts from repo search anchors"
```

### Task 3: 提升 Java 关联代码片段排序质量

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_repo_context_search_prefers_diff_hunk_symbol_over_file_header(...):
    repo_result = gateway.invoke_for_expert(...)
    snippet = next(item for item in repo_result["related_source_snippets"] if item["path"].endswith("PetController.java"))
    assert snippet["line_start"] == 106
    assert "processCreationForm" in snippet["snippet"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "prefers_diff_hunk_symbol" -q`
Expected: FAIL because snippets may fall back to `findOwner` or file header.

**Step 3: Write minimal implementation**

在 `tool_gateway.py` 中：
- 提升 `diff_hunk` 来源 symbol 的权重
- 压低 `package/import/comment/header` 片段
- 对同一路径只保留最佳锚点
- 优先展示 Java 方法签名和真实调用点

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "prefers_diff_hunk_symbol" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway.py backend/tests/services/test_skill_gateway.py
git commit -m "fix: prioritize meaningful java anchors in repo context search"
```

### Task 4: 统一运行时 repo context 与落库 code_context

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_runtime_repo_context_overrides_stale_command_context(...):
    merged = runner._merge_runtime_repository_context(stale_context, runtime_tool_results)
    assert merged["related_contexts"][0]["line_start"] == 106
    assert "processCreationForm" in merged["related_contexts"][0]["snippet"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "runtime_repo_context_overrides_stale_command_context" -q`
Expected: FAIL because stale command-time context leaks into final finding payload.

**Step 3: Write minimal implementation**

在 `review_runner.py` 中：
- 合并 `repo_context_search` 的运行时结果
- 在 prompt 组装和 `finding.code_context` 落库时统一使用 merged context
- 覆盖 `related_contexts`、`related_source_snippets`、`symbol_contexts`、Java context pack

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "runtime_repo_context_overrides_stale_command_context" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/tests/services/test_review_runner.py
git commit -m "fix: use runtime repo context in final finding payloads"
```

### Task 5: 主 Agent 最终总结按 partial failure 降级

**Files:**
- Modify: `backend/app/services/main_agent_service.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_main_agent_service.py`

**Step 1: Write the failing test**

```python
def test_main_agent_final_summary_marks_partial_failures_as_inconclusive(...):
    summary, _ = agent.build_final_summary(..., partial_failure_count=1)
    assert "部分完成" in summary
    assert "专家执行失败数: 1" in user_prompt
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_main_agent_service.py -k "partial_failures_as_inconclusive" -q`
Expected: FAIL because final summary still sounds like clean completion.

**Step 3: Write minimal implementation**

在 `main_agent_service.py` 中：
- 把 `partial_failure_count` 纳入 `user_prompt` 和 `fallback_text`
- 当 `partial_failure_count > 0` 时，禁止输出 `Clean Pass` 语义

在 `review_runner.py` 中：
- 调用 `build_final_summary(...)` 时传入 `partial_failure_count`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_main_agent_service.py -k "partial_failures_as_inconclusive" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/main_agent_service.py backend/app/services/review_runner.py backend/tests/services/test_main_agent_service.py
git commit -m "fix: downgrade final summary when experts fail"
```

### Task 6: 过程页与结果页统一优先展示 related_source_snippets

**Files:**
- Modify: `frontend/src/components/review/ReviewDialogueStream.tsx`
- Modify: `frontend/src/components/review/CodeReviewConclusionPanel.tsx`
- Test: `frontend` typecheck

**Step 1: Write the failing test**

```tsx
// No existing frontend unit harness required.
// Use a fixture-like review payload and verify by manual snapshot or component logic:
// process page should prefer related_source_snippets over related_contexts.
```

**Step 2: Run verification to confirm current mismatch**

Run: `cd frontend && npm run typecheck`
Expected: PASS, but process-page display still reads `related_contexts` first.

**Step 3: Write minimal implementation**

在 `ReviewDialogueStream.tsx` 中：
- `repo_context_search` 展示优先读取 `related_source_snippets`
- 没有 `related_source_snippets` 时再回退 `related_contexts`

在 `CodeReviewConclusionPanel.tsx` 中保持相同优先级。

**Step 4: Run verification**

Run: `cd frontend && npm run typecheck`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/components/review/ReviewDialogueStream.tsx frontend/src/components/review/CodeReviewConclusionPanel.tsx
git commit -m "fix: align process and result context display precedence"
```

### Task 7: 接通 Java 通用模式与 DDD 增强模式

**Files:**
- Modify: `backend/app/services/java_ddd_context_assembler.py`
- Modify: `backend/app/services/tool_gateway.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_java_ddd_context_assembler.py`

**Step 1: Write the failing test**

```python
def test_java_review_mode_uses_general_for_plain_spring_project(...):
    assert result["java_review_mode"] == "general"


def test_java_review_mode_uses_ddd_enhanced_when_domain_signals_present(...):
    assert result["java_review_mode"] == "ddd_enhanced"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_ddd_context_assembler.py -k "java_review_mode" -q`
Expected: FAIL if mode split is missing or inconsistent.

**Step 3: Write minimal implementation**

在 `java_ddd_context_assembler.py` 中：
- 固化 `general` 和 `ddd_enhanced`
- 默认 Java 项目走 `general`
- 命中 `domain/aggregate/valueobject/event/application` 等信号时再进入 `ddd_enhanced`

同步写入 `repository_context` 和 `finding.code_context`。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_ddd_context_assembler.py -k "java_review_mode" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/java_ddd_context_assembler.py backend/app/services/tool_gateway.py backend/app/services/review_runner.py backend/tests/services/test_java_ddd_context_assembler.py
git commit -m "feat: split java review mode into general and ddd enhanced"
```

### Task 7.5: 建立语言通用规范提示基座（Java / JavaScript / TypeScript）

**Files:**
- Modify: `backend/app/services/knowledge_service.py`
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_knowledge_rule_screening_service.py`
- Doc: `backend/app/storage/knowledge/docs/**`

**Step 1: Write the failing test**

```python
def test_java_review_prompt_includes_general_java_quality_guidance(...):
    assert "Java 通用代码规范" in prompt_text


def test_typescript_review_prompt_includes_general_ts_quality_guidance(...):
    assert "TypeScript 通用代码规范" in prompt_text
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "general_java_quality_guidance or general_ts_quality_guidance" -q`
Expected: FAIL because LLM prompt has no stable language-level guidance block.

**Step 3: Write minimal implementation**

建立“语言通用规范提示基座”，至少覆盖：
- Java 通用代码规范与质量要求
  - 输入校验
  - 空值与异常处理
  - 事务边界
  - Repository / SQL 风险
  - 并发与资源释放
  - 可维护性与复杂度
- JavaScript / TypeScript 通用代码规范与质量要求
  - 空值与类型安全
  - Promise / async 错误处理
  - React 状态与副作用
  - API 契约与输入校验
  - 安全与性能基础要求

接入方式：
- 以语言维度维护通用规范提示模板，而不是绑定到专家
- 在 main agent / expert agent 构造 prompt 时，按当前文件语言自动注入对应通用规范提示
- 在 expert prompt 中显式标注“除专家绑定规范外，还需遵循当前编程语言的通用规范与质量要求”
- `review_inputs` 中记录本轮实际注入了哪一类语言规范提示，便于回放和验收

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "general_java_quality_guidance or general_ts_quality_guidance" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/app/services/main_agent_service.py backend/tests/services/test_review_runner.py
git commit -m "feat: inject language-general quality guidance into llm prompts"
```

### Task 8: 扩展 Java 通用上下文包

**Files:**
- Modify: `backend/app/services/java_ddd_context_assembler.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `frontend/src/services/api.ts`
- Test: `backend/tests/services/test_skill_gateway.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_java_context_pack_contains_current_class_caller_callee_transaction(...):
    assert result["current_class_context"]
    assert result["caller_contexts"]
    assert result["callee_contexts"]
    assert result["transaction_context"]
```

**Step 2: Run test to verify it fails**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "current_class_context" -q`
- `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "finding_code_context_contains" -q`
Expected: FAIL because general Java context pack is incomplete.

**Step 3: Write minimal implementation**

通用 Java 项目必须稳定输出：
- `current_class_context`
- `caller_contexts`
- `callee_contexts`
- `transaction_context`
- `persistence_contexts`

DDD 增强模式追加：
- `parent_contract_contexts`
- `domain_model_contexts`

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "current_class_context" -q`
- `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -k "finding_code_context_contains" -q`
- `cd frontend && npm run typecheck`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/java_ddd_context_assembler.py backend/app/services/review_runner.py frontend/src/services/api.ts backend/tests/services/test_skill_gateway.py backend/tests/services/test_review_runner.py
git commit -m "feat: expand java context pack for review and ui"
```

### Task 9: 新增 Java 通用运行时核验工具

**Files:**
- Modify: `backend/app/services/tool_gateway.py`
- Modify: `backend/app/domain/models/runtime_settings.py`
- Modify: `backend/app/domain/models/app_config.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

```python
def test_java_runtime_tools_include_controller_entry_guard_inspector(...):
    assert "controller_entry_guard_inspector" in tool_names


def test_java_runtime_tools_include_repository_query_risk_inspector(...):
    assert "repository_query_risk_inspector" in tool_names
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "controller_entry_guard_inspector or repository_query_risk_inspector" -q`
Expected: FAIL because the tools are missing.

**Step 3: Write minimal implementation**

新增并接入：
- `controller_entry_guard_inspector`
- `repository_query_risk_inspector`
- 保持已有 `transaction_boundary_inspector`
- 保持已有 `application_service_boundary_inspector`
- 保持已有 `aggregate_invariant_inspector`

按专家自动分配。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_skill_gateway.py -k "controller_entry_guard_inspector or repository_query_risk_inspector" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway.py backend/app/domain/models/runtime_settings.py backend/app/domain/models/app_config.py backend/tests/services/test_skill_gateway.py
git commit -m "feat: add java runtime inspection tools"
```

### Task 10: 重构规则筛选为“项目绑定规则优先，DDD 再增强”

**Files:**
- Modify: `backend/app/services/knowledge_rule_screening_service.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/knowledge_service.py`
- Test: `backend/tests/services/test_knowledge_rule_screening_service.py`

**Step 1: Write the failing test**

```python
def test_general_java_project_skips_ddd_only_rules(...):
    assert "DDD-JDDD-001" not in matched_ids


def test_ddd_project_loads_general_and_ddd_rules(...):
    assert "DDD-JDDD-001" in matched_ids
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_knowledge_rule_screening_service.py -k "general_java_project or ddd_project" -q`
Expected: FAIL because rules are not split cleanly by mode.

**Step 3: Write minimal implementation**

规则加载分两层：
- 项目绑定规则
  - 专家绑定规则 / 团队知识库规则
- 项目增强规则
  - Java DDD 增强规则等

并把 `java_review_mode`、`java_context_signals`、运行时工具信号写入 `query_terms`。语言通用规范不走专家绑定规则加载，而走 prompt 注入层。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_knowledge_rule_screening_service.py -k "general_java_project or ddd_project" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_rule_screening_service.py backend/app/services/review_runner.py backend/app/services/knowledge_service.py backend/tests/services/test_knowledge_rule_screening_service.py
git commit -m "feat: split java rule screening into general and ddd layers"
```

### Task 11: 建立真实 Java 评测集与端到端回归脚本

**Files:**
- Modify: `scripts/bench_java_review_cases.py`
- Modify: `backend/tests/fixtures/java_cases/cases.json`
- Test: `backend/tests/services/test_java_review_benchmarks.py`
- Doc: `README.md`

**Step 1: Write the failing test**

```python
def test_java_benchmark_manifest_contains_general_and_ddd_cases(...):
    assert any(item["repo_key"] == "spring-petclinic" for item in manifest["cases"])
    assert any(item["repo_key"] == "java-ddd-example" for item in manifest["cases"])
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py -q`
Expected: FAIL if benchmark coverage is incomplete.

**Step 3: Write minimal implementation**

扩到至少 12 条 case：
- 通用 Java 安全
- 通用 Java 性能
- 通用 Java 架构
- DDD 安全
- DDD 性能
- DDD 架构

保持 runner 支持：
- `--list`
- `--prepare-only`
- `--submit`
- `--analysis-mode`

**Step 4: Run test to verify it passes**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py -q`
- `python3 scripts/bench_java_review_cases.py --list`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/bench_java_review_cases.py backend/tests/fixtures/java_cases/cases.json backend/tests/services/test_java_review_benchmarks.py README.md
git commit -m "feat: expand java review benchmark suite"
```

### Task 12: 用真实 GitHub Java 用例做最终验收

**Files:**
- Verify only: `/tmp/java-review-eval-workspaces/*`
- Verify only: `frontend/src/components/review/*`
- Verify only: `backend/app/storage/app.db`

**Step 1: Run the benchmark case**

Run:
```bash
python3 scripts/bench_java_review_cases.py --case petclinic-owner-create-validation-regression --submit --analysis-mode light
```

Expected: review created successfully.

**Step 2: Verify runtime payload and UI**

Run / inspect:
- 查询 `/api/reviews/<review_id>/replay`
- 查询 `/api/reviews/<review_id>/report`
- 打开 `/review/<review_id>?tab=process`
- 打开 `/review/<review_id>?tab=result`

Expected:
- `related_contexts` 和 `related_source_snippets` 都优先落到 `processCreationForm`
- 过程页 `repo_context_search` 显示真实方法级片段
- 结果页“当前代码”显示源码上下文，不是 diff 片段
- 若专家失败，最终播报不再使用 `Clean Pass` 语义

**Step 3: Record acceptance notes**

把验收结果写入：
- `docs/plans/2026-03-31-system-runtime-and-review-quality-implementation-plan.md`
- 或新增 `docs/plans/2026-03-31-system-runtime-and-review-quality-acceptance.md`

**Step 4: Commit**

```bash
git add docs/plans
git commit -m "docs: record runtime and review quality acceptance results"
```

---

## Success Metrics

- Java 真实 benchmark 中，`repo_context_search` 的首个关联片段命中目标方法比例 ≥ 80%
- 有专家失败时，最终播报中不再出现 `Clean Pass` / `清洁通过` 语义
- 结果页与过程页展示的关联上下文来源一致
- 专家输入中“绑定规范 + 语言通用规范提示 + 变更代码 + 关联源码上下文”四类要素覆盖率 = 100%
- Java 通用项目不再默认触发 DDD 强约束误报
- Java 真实 review 的误报率逐步下降，且可以通过 benchmark 复现和比较

## Rollout Priority

1. Task 1-6：先解决输入完整性、上下文错位、失败误报和页面展示一致性
2. Task 7-10：再做 Java 通用基座和 DDD 增强层提质
3. Task 11-12：最后用真实 benchmark 做持续回归和最终验收
