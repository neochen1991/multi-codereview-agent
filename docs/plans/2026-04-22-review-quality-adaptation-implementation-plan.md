# Review Quality Adaptation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将代码检视质量优化设计落到当前项目实现中，统一后端分级和过滤口径，增强高价值专项 signal，补齐前端结果表达，并用基准与真实 MR 验证效果。

**Architecture:** 沿用当前项目的主链路，不引入新的 agent 框架。后端继续以 `main_agent_service -> review_runner -> detect_conflicts -> review_service` 为核心，先统一分级、主责归因和过滤规则，再增强 signal/observation，最后由前端结果页、过程页和设置页准确表达这些状态。整个实施按“后端口径 -> 专项 signal -> 前端联动 -> 验证回归”的顺序推进。

**Tech Stack:** FastAPI, Pydantic, LangGraph, pytest, React, TypeScript, Vite

---

### Task 1: 固化统一分级与过滤口径

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/domain/models/runtime_settings.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/nodes/detect_conflicts.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_review_runner.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_detect_conflicts.py`

**Step 1: 写失败测试，锁定统一口径**

在 `test_review_runner.py` 和 `test_detect_conflicts.py` 里增加覆盖：
- `verification_needed=true` 的 finding 不能进入有效 issue
- 命中 `conditional_conclusion` 的 finding 只能保留为 finding
- 命中 `removed_line_only` 的 finding 会被过滤
- 强 observation 直出的 finding 不会被质量门控重新降级

**Step 2: 运行测试确认当前存在缺口**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- 至少新增测试失败
- 失败原因能明确指向当前口径不统一

**Step 3: 最小实现统一口径**

实现要求：
- 在 `runtime_settings.py` 中明确 issue 升级相关字段的语义注释或模型字段说明
- 在 `review_runner.py` 中统一处理：
  - `severity`
  - `confidence`
  - `finding_type`
  - `verification_needed`
- 在 `detect_conflicts.py` 中统一 issue 收敛时的过滤原因：
  - `threshold_filtered`
  - `conditional_conclusion`
  - `removed_line_only`

**Step 4: 回跑测试确认通过**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add backend/app/domain/models/runtime_settings.py backend/app/services/review_runner.py backend/app/services/orchestrator/nodes/detect_conflicts.py backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py
git commit -m "refactor: unify review severity and filtering semantics"
```

### Task 2: 强化规则优先与 observation 优先链路

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/java_quality_signal_extractor.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/knowledge_rule_screening_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_review_runner.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_knowledge_rule_screening_service.py`

**Step 1: 写失败测试，锁定“规则优先、LLM 后置”**

新增测试覆盖：
- 命中强 signal 时，即使 LLM 没提，也能兜底生成 finding
- 命中确定性规则时，系统会优先形成 observation
- 上下文规则不会直接产出 finding，而是影响专家选择和 prompt 组装

**Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_knowledge_rule_screening_service.py -q
```

Expected:
- 至少新增测试失败

**Step 3: 实现 signal / rule 分层**

实现要求：
- 在 `java_quality_signal_extractor.py` 中把 signal 明确区分成：
  - 确定性强 signal
  - 需要上下文辅助的 signal
- 在 `review_runner.py` 中把 observation 到 finding 的兜底策略与信号强度对齐
- 在 `knowledge_rule_screening_service.py` 中保留当前规则预筛逻辑，但明确：
  - 哪些是直接可用规则
  - 哪些只作为专家上下文提示

**Step 4: 回跑测试**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_knowledge_rule_screening_service.py -q
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add backend/app/services/java_quality_signal_extractor.py backend/app/services/review_runner.py backend/app/services/knowledge_rule_screening_service.py backend/tests/services/test_review_runner.py backend/tests/services/test_knowledge_rule_screening_service.py
git commit -m "feat: prioritize deterministic signals before llm review"
```

### Task 3: 补强高价值专项 signal

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/java_quality_signal_extractor.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/nodes/detect_conflicts.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_review_runner.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_detect_conflicts.py`

**Step 1: 写失败测试，覆盖本轮专项能力**

新增测试覆盖以下场景：
- 循环中的外部调用放大
- 注释 / TODO / 接口承诺未实现
- 静默失败 / 吃掉异常
- 条件化结论降级
- 删除代码误报过滤

**Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- 新增场景至少有部分失败

**Step 3: 最小实现专项增强**

实现要求：
- `java_quality_signal_extractor.py`：
  - 增加或强化上述专项 signal 的识别
- `review_runner.py`：
  - 对强 signal 命中场景，兜底转为确定性 finding
- `detect_conflicts.py`：
  - 对注释未实现、静默失败等高价值问题避免被误判为低价值提示

**Step 4: 回跑测试**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add backend/app/services/java_quality_signal_extractor.py backend/app/services/review_runner.py backend/app/services/orchestrator/nodes/detect_conflicts.py backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py
git commit -m "feat: strengthen high-value review signals"
```

### Task 4: 继续收紧主责专家路由与归因

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/main_agent_service.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/review_runner.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/nodes/detect_conflicts.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_main_agent_service.py`
- Test: `/Users/neochen/multi-codereview-agent/backend/tests/services/test_detect_conflicts.py`

**Step 1: 写失败测试，锁定主责专家唯一性**

覆盖：
- 命名/日志/魔法值 -> `architecture_design`
- 注释承诺未实现 -> `correctness_business`
- 聚合边界/应用服务越界 -> `ddd_architecture`
- SQL/事务/schema/索引 -> `database_analysis`
- 并发放大/超时/故障恢复 -> `performance_reliability`

**Step 2: 运行测试确认失败**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_main_agent_service.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- 至少新增测试失败

**Step 3: 收紧入口路由和结果归因**

实现要求：
- `main_agent_service.py` 在选专家和派工阶段明确优先主责专家
- `review_runner.py` 避免不必要地把同类 observation 推给多个专家
- `detect_conflicts.py` 对同类问题最终保留一个 `primary_expert_id`

**Step 4: 回跑测试**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_main_agent_service.py backend/tests/services/test_detect_conflicts.py -q
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add backend/app/services/main_agent_service.py backend/app/services/review_runner.py backend/app/services/orchestrator/nodes/detect_conflicts.py backend/tests/services/test_main_agent_service.py backend/tests/services/test_detect_conflicts.py
git commit -m "refactor: tighten primary expert routing and attribution"
```

### Task 5: 前端结果页补齐问题分层和主责信息

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/pages/ReviewWorkbench/index.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/components/review/FindingsPanel.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/components/review/ResultIssuePanel.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/components/review/IssueThresholdFilteredPanel.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/services/api.ts`
- Test: `/Users/neochen/multi-codereview-agent/frontend/src/pages/ReviewWorkbench/helpers.ts`

**Step 1: 写或补前端映射测试**

验证：
- findings、有效问题、阈值过滤问题三类数据被正确区分
- issue 展示 `primary_expert_id`
- 过滤原因能展示 `conditional_conclusion` / `removed_line_only`

**Step 2: 运行前端构建或测试确认当前缺口**

Run:
```bash
npm run build
```

Expected:
- 现有代码无法完整表达新增状态，或需要调整映射

**Step 3: 最小实现结果表达**

实现要求：
- 结果页明确区分：
  - 审核发现
  - 有效问题清单
  - 被过滤的问题
- 有效问题清单展示主责专家
- 被过滤问题展示过滤原因

**Step 4: 运行构建**

Run:
```bash
npm run build
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add frontend/src/pages/ReviewWorkbench/index.tsx frontend/src/components/review/FindingsPanel.tsx frontend/src/components/review/ResultIssuePanel.tsx frontend/src/components/review/IssueThresholdFilteredPanel.tsx frontend/src/services/api.ts
git commit -m "feat: surface issue tiers and primary expert in review results"
```

### Task 6: 前端过程页、设置页和专家中心联动

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/components/review/ReviewDialogueStream.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/pages/Settings/index.tsx`
- Modify: `/Users/neochen/multi-codereview-agent/frontend/src/services/api.ts`
- Modify: `/Users/neochen/multi-codereview-agent/README.md`
- Modify: `/Users/neochen/multi-codereview-agent/docs/architecture/2026-04-19-expert-agent-boundary-handbook.md`

**Step 1: 明确前端要表达的后端信息**

先列出并校验以下字段来源：
- rule screening metadata
- knowledge hit metadata
- tool result metadata
- primary expert
- participant experts
- filter reason

**Step 2: 实现过程页和设置页联动**

实现要求：
- `ReviewDialogueStream.tsx` 能清楚展示规则命中、工具调用、知识引用
- 设置页补齐统一分级和过滤规则的说明入口
- README 和边界手册继续与页面口径一致

**Step 3: 运行构建**

Run:
```bash
npm run build
```

Expected:
- PASS

**Step 4: Commit**

```bash
git add frontend/src/components/review/ReviewDialogueStream.tsx frontend/src/pages/Settings/index.tsx frontend/src/services/api.ts README.md docs/architecture/2026-04-19-expert-agent-boundary-handbook.md
git commit -m "docs: align frontend review flow with review quality semantics"
```

### Task 7: 基准 case 与真实 MR 验证

**Files:**
- Modify: `/Users/neochen/multi-codereview-agent/scripts/bench_java_review_cases.py`
- Modify: `/Users/neochen/multi-codereview-agent/backend/tests/fixtures/java_cases/cases.json`
- Modify: `/Users/neochen/multi-codereview-agent/docs/architecture/2026-04-22-review-quality-verification.md`

**Step 1: 增补验证样本**

确保样本覆盖：
- 循环放大
- 注释未实现
- 静默失败
- 删除代码误报
- 条件化结论
- DDD/正确性/性能/数据库的边界重叠

**Step 2: 跑基准验证**

Run:
```bash
python3 scripts/bench_java_review_cases.py
```

Expected:
- 输出每个 case 的 findings / issues / filtered 情况

**Step 3: 跑至少 2 条真实 MR**

Run:
```bash
.venv/bin/python scripts/smoke_review.py --help
```

然后用实际可用的 MR URL 跑真实任务，并记录：
- 是否重复主提
- 是否还有删除代码误报
- 是否还有条件化 issue

**Step 4: 写验证报告**

将结果整理到：
- `/Users/neochen/multi-codereview-agent/docs/architecture/2026-04-22-review-quality-verification.md`

报告至少包含：
- 命中情况
- 误报/漏报情况
- 是否达成目标
- 下一轮仍需补的场景

**Step 5: Commit**

```bash
git add scripts/bench_java_review_cases.py backend/tests/fixtures/java_cases/cases.json docs/architecture/2026-04-22-review-quality-verification.md
git commit -m "test: verify adapted review quality pipeline"
```

### Task 8: 完整回归与收尾

**Files:**
- Verify only

**Step 1: 跑后端完整相关回归**

Run:
```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_main_agent_service.py backend/tests/services/test_detect_conflicts.py backend/tests/services/test_knowledge_rule_screening_service.py backend/tests/services/test_review_service_auto_queue.py backend/tests/api/test_review_report_api.py -q
```

Expected:
- PASS

**Step 2: 跑前端构建**

Run:
```bash
npm run build
```

Expected:
- PASS

**Step 3: 总结本轮变更**

确认以下结果：
- 分级和过滤口径统一
- 规则优先链路可用
- 高价值专项 signal 增强完成
- 前端表达和后端语义一致
- 基准与真实 MR 有验证结果

**Step 4: Commit**

```bash
git status
```

确认工作区干净后，不新增功能，只做收尾说明。
