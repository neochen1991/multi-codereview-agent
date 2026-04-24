# Large File Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把审核主链和工作台中的巨石文件拆成职责清楚的协作模块，同时保持现有行为不变，并在拆分后跑完整回归。

**Architecture:** 采用“先主链、后外围”的渐进式重构。先从 `review_runner`、`main_agent_service`、`ReviewWorkbench`、`ReviewDialogueStream` 里抽出边界最清楚、最容易验证的纯逻辑模块，再继续拆 `review_service` 和 `tool_gateway`。旧入口文件继续保留为容器与编排层，新模块只接收必要参数，避免循环依赖。

**Tech Stack:** Python, FastAPI, Pydantic, React, TypeScript, pytest, npm, ripgrep

---

### Task 1: 为 `review_runner` 建立子模块目录并抽出 observation follow-up

**Files:**
- Create: `backend/app/services/review_runner/__init__.py`
- Create: `backend/app/services/review_runner/observation_followup.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

在 `backend/tests/services/test_review_runner.py` 中补一组针对 observation follow-up 的直接调用测试，覆盖：

- uncovered observations 识别
- forced observation candidate 生成
- same-anchor candidate merge

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -k "observation or forced_observation or uncovered" -q`
Expected: FAIL because helper module does not exist and call sites are not wired.

**Step 3: Write minimal implementation**

在 `observation_followup.py` 中迁移这些逻辑：

- `_collect_batch_review_observations`
- `_find_uncovered_review_observations`
- `_build_observation_followup_prompt`
- `_append_observation_followup_candidates`
- `_build_forced_observation_candidates`
- `_merge_expert_analysis_candidates`
- `_candidate_strength_score`

在 `review_runner.py` 中保留方法名，但改成薄包装，委托给新模块。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -k "observation or forced_observation or uncovered" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner/__init__.py backend/app/services/review_runner/observation_followup.py backend/app/services/review_runner.py backend/tests/services/test_review_runner.py
git commit -m "refactor: extract review runner observation followup helpers"
```

### Task 2: 为 `review_runner` 抽出 finding stabilizer

**Files:**
- Create: `backend/app/services/review_runner/finding_stabilizer.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`
- Test: `backend/tests/services/test_detect_conflicts.py`

**Step 1: Write the failing test**

补测试直接覆盖：

- Java loop/comment contract signal escalation
- input completeness quality gate
- candidate dedupe 保级

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -k "loop or contract or input_quality or dedupe" -q`
Expected: FAIL because logic has not yet been moved and adapters are missing.

**Step 3: Write minimal implementation**

在 `finding_stabilizer.py` 中迁移：

- `_enrich_java_quality_signal_language`
- `_apply_input_quality_gate`
- `_dedupe_finding_candidates`
- finding signal / contract mismatch 相关的稳定化逻辑

`review_runner.py` 只保留薄包装方法。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -k "loop or contract or input_quality or dedupe" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner/finding_stabilizer.py backend/app/services/review_runner.py backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py
git commit -m "refactor: extract review runner finding stabilizer"
```

### Task 3: 为 `review_runner` 抽出 prompt 与 repository context formatter

**Files:**
- Create: `backend/app/services/review_runner/expert_prompt_builder.py`
- Create: `backend/app/services/review_runner/repository_context_formatter.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

补测试覆盖：

- 专家 prompt 中的 review spec / bound docs / route hints / tool result 摘要
- repository context summary 和 source snippet formatting

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -k "prompt or repository_context or source_snippet" -q`
Expected: FAIL because extracted builders do not exist.

**Step 3: Write minimal implementation**

迁移：

- `_build_expert_system_prompt`
- `_build_expert_prompt`
- `_prepare_prompt_repository_context`
- `_build_repository_context_summary`

把复杂字符串拼装与格式化逻辑移到新模块，`ReviewRunner` 只负责传参。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -k "prompt or repository_context or source_snippet" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner/expert_prompt_builder.py backend/app/services/review_runner/repository_context_formatter.py backend/app/services/review_runner.py backend/tests/services/test_review_runner.py
git commit -m "refactor: extract review runner prompt and repository context builders"
```

### Task 4: 为 `main_agent_service` 抽出路由 prompt builder

**Files:**
- Create: `backend/app/services/main_agent/__init__.py`
- Create: `backend/app/services/main_agent/routing_prompt_builder.py`
- Modify: `backend/app/services/main_agent_service.py`
- Test: `backend/tests/services/test_main_agent_service.py`

**Step 1: Write the failing test**

补测试覆盖：

- 专家选择 system prompt
- 派工 routing system prompt
- 主责专家速查文案存在且顺序正确

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py -k "selection_system_prompt or routing_system_prompt" -q`
Expected: FAIL because new builder module does not exist.

**Step 3: Write minimal implementation**

迁移：

- `_build_expert_selection_system_prompt`
- `_build_expert_selection_user_prompt`
- `_build_routing_system_prompt`
- `_build_routing_user_prompt`

让 `MainAgentService` 通过 helper 生成 prompt。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py -k "selection_system_prompt or routing_system_prompt" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/main_agent/__init__.py backend/app/services/main_agent/routing_prompt_builder.py backend/app/services/main_agent_service.py backend/tests/services/test_main_agent_service.py
git commit -m "refactor: extract main agent routing prompt builders"
```

### Task 5: 为 `main_agent_service` 抽出 route hint builder

**Files:**
- Create: `backend/app/services/main_agent/route_hint_builder.py`
- Modify: `backend/app/services/main_agent_service.py`
- Test: `backend/tests/services/test_main_agent_service.py`

**Step 1: Write the failing test**

补测试覆盖：

- baseline route 选择
- candidate hunks 构建
- related_files / route hints 组装

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py -k "candidate_hunks or route_hint or baseline_route" -q`
Expected: FAIL because helper module does not exist.

**Step 3: Write minimal implementation**

迁移：

- `_build_rule_route`
- `_build_candidate_hunks`
- `_build_routing_plan_payload`
- related files / hunk picking / route hint helper 逻辑

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py -k "candidate_hunks or route_hint or baseline_route" -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/main_agent/route_hint_builder.py backend/app/services/main_agent_service.py backend/tests/services/test_main_agent_service.py
git commit -m "refactor: extract main agent route hint helpers"
```

### Task 6: 为 `ReviewWorkbench` 抽出页面 helper 和状态 hook

**Files:**
- Create: `frontend/src/pages/ReviewWorkbench/helpers.ts`
- Create: `frontend/src/pages/ReviewWorkbench/useReviewWorkbenchState.ts`
- Modify: `frontend/src/pages/ReviewWorkbench/index.tsx`
- Test: `frontend`

**Step 1: Write the failing test**

先不引入新的前端单测框架，使用类型检查和构建作为回归门槛。把解析函数迁出后，保证：

- `index.tsx` 能正常引用新 helpers
- 类型无回退

**Step 2: Run verification to confirm current baseline**

Run: `npm run build`
Expected: PASS before refactor.

**Step 3: Write minimal implementation**

迁移：

- `normalizeDesignDocs`
- `normalizeRoutingItems`
- `readExpertRoutingSummary`
- `readExpertSelectionSummary`
- `normalizeIssueFilterDecisions`
- 其他纯数据解析函数

同时抽出页面轮询和过程事件累积状态到 `useReviewWorkbenchState.ts`。

**Step 4: Run test to verify it passes**

Run: `npm run build`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/pages/ReviewWorkbench/helpers.ts frontend/src/pages/ReviewWorkbench/useReviewWorkbenchState.ts frontend/src/pages/ReviewWorkbench/index.tsx
git commit -m "refactor: split review workbench helpers and state hook"
```

### Task 7: 为 `ReviewDialogueStream` 抽出 formatter 模块

**Files:**
- Create: `frontend/src/components/review/reviewDialogueTypes.ts`
- Create: `frontend/src/components/review/reviewDialogueFormatters.ts`
- Modify: `frontend/src/components/review/ReviewDialogueStream.tsx`
- Test: `frontend`

**Step 1: Write the failing test**

同样以类型检查和构建作为门槛，确保 formatter 迁出后：

- `ReviewDialogueStream.tsx` 只保留渲染逻辑
- 结构化数据转换函数都能从新模块导入

**Step 2: Run verification to confirm current baseline**

Run: `npm run build`
Expected: PASS before refactor.

**Step 3: Write minimal implementation**

迁移：

- tool call / tool result 格式化
- routing / issue / knowledge / replay 相关展示数据转换
- 私有展示类型定义

**Step 4: Run test to verify it passes**

Run: `npm run build`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/components/review/reviewDialogueTypes.ts frontend/src/components/review/reviewDialogueFormatters.ts frontend/src/components/review/ReviewDialogueStream.tsx
git commit -m "refactor: extract review dialogue formatters"
```

### Task 8: 为 `review_service` 抽出 report builder

**Files:**
- Create: `backend/app/services/review_service/__init__.py`
- Create: `backend/app/services/review_service/report_builder.py`
- Modify: `backend/app/services/review_service.py`
- Test: `backend/tests/services/test_review_service_auto_queue.py`
- Test: `backend/tests/api/test_review_report_api.py`

**Step 1: Write the failing test**

补测试覆盖：

- report findings / issues / threshold filtered 列表构造
- llm usage summary 汇总

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/api/test_review_report_api.py backend/tests/services/test_review_service_auto_queue.py -q`
Expected: FAIL because helper module does not exist.

**Step 3: Write minimal implementation**

迁移 report 生成和汇总逻辑到 `report_builder.py`，`ReviewService` 保持 API 编排职责。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/api/test_review_report_api.py backend/tests/services/test_review_service_auto_queue.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_service/__init__.py backend/app/services/review_service/report_builder.py backend/app/services/review_service.py backend/tests/api/test_review_report_api.py backend/tests/services/test_review_service_auto_queue.py
git commit -m "refactor: extract review report builder"
```

### Task 9: 为 `tool_gateway` 抽出 repo/knowledge/datasource 工具模块

**Files:**
- Create: `backend/app/services/tool_gateway/__init__.py`
- Create: `backend/app/services/tool_gateway/repo_context_tools.py`
- Create: `backend/app/services/tool_gateway/knowledge_tools.py`
- Create: `backend/app/services/tool_gateway/datasource_tools.py`
- Modify: `backend/app/services/tool_gateway.py`
- Test: `backend/tests/services/test_skill_gateway.py`

**Step 1: Write the failing test**

保留现有 gateway 对外行为，补测试覆盖：

- `repo_context_search`
- `knowledge_search`
- `pg_schema_context`

三类工具仍通过统一入口调用。

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_skill_gateway.py -q`
Expected: FAIL because helper modules do not exist.

**Step 3: Write minimal implementation**

迁移三类工具实现到新模块，`ReviewToolGateway` 只做入口分发和公共依赖装配。

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_skill_gateway.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/tool_gateway/__init__.py backend/app/services/tool_gateway/repo_context_tools.py backend/app/services/tool_gateway/knowledge_tools.py backend/app/services/tool_gateway/datasource_tools.py backend/app/services/tool_gateway.py backend/tests/services/test_skill_gateway.py
git commit -m "refactor: split tool gateway by tool families"
```

### Task 10: Run full regression for the refactor branch

**Files:**
- Modify: none unless fixes are needed

**Step 1: Run backend regression**

Run:

```bash
.venv/bin/pytest \
  backend/tests/services/test_review_runner.py \
  backend/tests/services/test_main_agent_service.py \
  backend/tests/services/test_detect_conflicts.py \
  backend/tests/services/test_skill_gateway.py \
  backend/tests/services/test_review_service_auto_queue.py \
  backend/tests/api/test_review_report_api.py -q
```

Expected: PASS

**Step 2: Run frontend build**

Run:

```bash
npm run build
```

Expected: PASS

**Step 3: Sanity-check large-file reductions**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
for path in [
    "backend/app/services/review_runner.py",
    "backend/app/services/main_agent_service.py",
    "backend/app/services/review_service.py",
    "backend/app/services/tool_gateway.py",
    "frontend/src/pages/ReviewWorkbench/index.tsx",
    "frontend/src/components/review/ReviewDialogueStream.tsx",
]:
    p = Path(path)
    print(path, sum(1 for _ in p.open()))
PY
```

Expected: all target files noticeably smaller than baseline.

**Step 4: Commit final refactor batch**

```bash
git add backend frontend docs/plans
git commit -m "refactor: split large review workflow modules"
```
