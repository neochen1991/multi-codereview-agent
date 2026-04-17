# Java Quality Signal Gap Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 提升 Java 检视链路对性能问题、循环调用放大问题、注释或 TODO 承诺未实现问题的稳定检出率。

**Architecture:** 在 Java 通用质量信号层新增高置信静态信号，把这些信号注入专家审查上下文和后置保留逻辑，减少仅靠 LLM 临场理解导致的漏检。实现保持在现有主链路内，不新增独立预检服务，不改变现有页面展示协议。

**Tech Stack:** Python, FastAPI, pytest, existing review orchestration pipeline

---

### Task 1: 补静态信号提取测试

**Files:**
- Modify: `backend/tests/services/test_java_quality_signal_extractor.py`

**Step 1: Write the failing tests**

- 增加 `loop_call_amplification` 场景：
  - `for (...) { repository.find...(); }`
  - `while (...) { restTemplate.postForObject(...); }`
- 增加 `comment_contract_unimplemented` 场景：
  - `// TODO: 创建订单后自动扣减库存`
  - 实现仅 `return orderRepository.save(order);`

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_quality_signal_extractor.py -q`

**Step 3: Write minimal implementation**

- 在 `backend/app/services/java_quality_signal_extractor.py` 新增信号提取：
  - `loop_call_amplification`
  - `comment_contract_unimplemented`

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_java_quality_signal_extractor.py -q`

### Task 2: 补性能 finding 保留测试

**Files:**
- Modify: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing tests**

- 增加一条性能 `risk_hypothesis`：
  - 标题或证据包含“循环内查库”“repository”“批量查询被放进 for”
  - 只有单文件上下文
  - 预期：**不应**被 `_should_skip_finding` 吞掉

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -q`

**Step 3: Write minimal implementation**

- 调整 `backend/app/services/review_runner.py` 中性能 token 和弱 finding 抑制逻辑
- 让“循环调用放大 / 仓储查询 / 远程调用放大”属于高价值性能证据

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/services/test_review_runner.py -q`

### Task 3: 把静态信号接入专家语言增强

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Optional: `backend/app/services/main_agent_service.py`

**Step 1: Add language enrichment**

- 对 `loop_call_amplification`：
  - 强化性能专家的 summary/claim/evidence
- 对 `comment_contract_unimplemented`：
  - 强化正确性专家的 summary/claim/evidence

**Step 2: Route signal if needed**

- 若现有路由覆盖不稳，再把新信号接入专家补选逻辑

**Step 3: Verify no regressions**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_java_quality_signal_extractor.py backend/tests/services/test_review_runner.py backend/tests/services/test_expert_capability_service.py -q`

### Task 4: 评测集补洞并出报告

**Files:**
- Modify: `backend/tests/fixtures/java_cases/cases.json`
- Optional: `backend/tests/services/test_java_review_benchmarks.py`
- Optional: `docs/`

**Step 1: Add benchmark cases**

- 一个循环查库性能问题用例
- 一个循环远程调用性能问题用例
- 一个 TODO/注释承诺未实现用例

**Step 2: Verify benchmark parsing**

Run:
- `.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py -q`

**Step 3: Produce comparison notes**

- 记录新增问题类型在 benchmark 中的命中情况
