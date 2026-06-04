# Static Tool Adoption Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让静态工具从“偶尔跑一下的旁路信号”变成“默认启用、专家必须消费、最终输出可度量”的代码评审证据源，同时不让静态工具绕过专家判断直接生成正式 issue。

**Architecture:** SAST 预扫描在 review 入口默认 best-effort 执行，产出结构化 `tool_observations`。主审与专家提示词必须显式引用或拒绝这些观察项，冲突合并阶段只把被语义确认的工具信号作为置信度证据，最终 `/issues`、报告和治理页暴露工具采纳率、确认率、误报率等指标。

**Tech Stack:** FastAPI, Pydantic settings models, pytest, React/TypeScript, SAST command line tools such as Semgrep/GitNexus/Infer.

---

## Non-Goals

- 不让 SAST 结果直接成为正式 issue；工具只能产生 evidence/observation。
- 不要求所有静态工具都安装成功；缺失工具要可诊断、可降级。
- 不改变 finding 到 issue 的核心规则；正式 issue 仍以置信度阈值和现有评审流水线为准。
- 不用 UI 文案掩盖后端不可用；Settings/Governance 必须展示真实状态。

## Acceptance Criteria

- 默认配置开启 SAST 预扫描，并且运行时设置、配置文件、接口文案保持一致。
- 每条工具结果都有稳定 observation id、来源、规则、位置、置信度、归一化类别，并标记为 `is_issue=False`。
- 专家输出必须消费相关 observation id；漏掉时 schema validation/retry/metadata 能明确记录。
- 冲突合并只接受语义相关的 SAST match，不能把无关规则当作置信度加分。
- 最终用户可见的 `/issues` 和报告保留准确的问题描述、规范描述、建议修改方案、原始代码位置。
- Governance 可看到工具观察项数量、采纳率、确认率、交叉验证 issue 数、误报率。
- 一键质量脚本覆盖后端单测、前端 typecheck、质量 smoke。

---

## Phase 0: Baseline and Safety

### Task 0.1: Record Current Review Surface

**Files:**
- Read: `backend/app/services/review_service.py`
- Read: `backend/app/services/review_runner.py`
- Read: `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- Read: `backend/app/services/review_service_projection.py`

**Step 1: Capture current pipeline**

Document the active path:

```text
sast_prescan -> main_agent_service -> review_runner expert prompt
-> detect_conflicts -> judge_and_merge -> review_service.list_issues()/build_report()
```

**Step 2: Capture user-visible baseline**

Run a known review case and save:

```bash
python3 scripts/bench_java_review_cases.py --case java-ddd-composite-quality-regression --submit
```

Expected: review id is printed and `/issues` can be inspected.

**Step 3: Verify dirty worktree boundary**

Run:

```bash
git status --short
```

Expected: unrelated local changes are listed and not reverted.

**Step 4: Commit**

No commit unless a baseline note file is added.

---

## Phase 1: Default-On Best-Effort SAST

### Task 1.1: Make SAST Prescan Default-On

**Files:**
- Modify: `config.json`
- Modify: `backend/app/domain/models/runtime_settings.py`
- Modify: `backend/app/domain/models/app_config.py`
- Test: `tests/test_runtime_settings.py` or the closest existing settings test

**Step 1: Write failing tests**

Add tests asserting:

```python
def test_sast_prescan_defaults_to_enabled():
    settings = RuntimeSettings()
    assert settings.enable_sast_prescan is True

def test_app_config_sast_prescan_defaults_to_enabled():
    config = AppConfig()
    assert config.enable_sast_prescan is True
```

**Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_runtime_settings.py -q
```

Expected: FAIL if current default is false or unset.

**Step 3: Implement minimal config change**

Set:

```json
"enable_sast_prescan": true
```

and make both Pydantic defaults `True`.

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_runtime_settings.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add config.json backend/app/domain/models/runtime_settings.py backend/app/domain/models/app_config.py tests/test_runtime_settings.py
git commit -m "feat: enable sast prescan by default"
```

### Task 1.2: Expose Tool Availability Diagnostics

**Files:**
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/app/services/sast_prescan_service.py`
- Test: `tests/api/test_settings.py`

**Step 1: Write failing API test**

Assert `GET /settings/sast-tools/status` returns:

```json
{
  "enabled": true,
  "command_tools": [{"tool": "semgrep", "status": "available|missing|error"}],
  "report_tools": [{"tool": "gitnexus", "status": "available|missing|error"}],
  "limitations": []
}
```

**Step 2: Run focused test**

```bash
.venv/bin/python -m pytest tests/api/test_settings.py -q
```

Expected: FAIL because endpoint/shape is missing or incomplete.

**Step 3: Implement status model and endpoint**

Add a small helper that checks command availability without failing review execution. Missing tools should produce `status=missing` and a human-readable limitation.

**Step 4: Run focused test**

```bash
.venv/bin/python -m pytest tests/api/test_settings.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/api/routes/settings.py backend/app/services/sast_prescan_service.py tests/api/test_settings.py
git commit -m "feat: expose sast tool status"
```

---

## Phase 2: Structured Tool Observations

### Task 2.1: Normalize SAST Findings into Observations

**Files:**
- Modify: `backend/app/services/sast_prescan_service.py`
- Test: `tests/services/test_sast_prescan_service.py`

**Step 1: Write failing normalization test**

Use a fixture with two raw SAST findings and assert the converted observations contain:

```python
assert observation["id"].startswith("sast:")
assert observation["source"] in {"semgrep", "gitnexus", "infer"}
assert observation["rule_id"]
assert observation["file_path"]
assert observation["line_start"] >= 1
assert observation["is_issue"] is False
assert observation["expert_must_decide"] is True
```

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_sast_prescan_service.py -q
```

Expected: FAIL until normalized observation fields exist.

**Step 3: Implement minimal converter**

Implement deterministic ids and stable fields:

```python
observation_id = f"sast:{tool}:{rule_id}:{file_path}:{line_start}"
```

Set `is_issue=False` and `expert_must_decide=True` for every tool observation.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_sast_prescan_service.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/sast_prescan_service.py tests/services/test_sast_prescan_service.py
git commit -m "feat: normalize sast findings as observations"
```

### Task 2.2: Attach Observations to Review Context

**Files:**
- Modify: `backend/app/services/main_agent_service.py`
- Test: `tests/services/test_main_agent_service.py`

**Step 1: Write failing integration test**

Assert review context contains:

```python
assert context["sast_tool_status"]
assert context["sast_prescan"]
assert context["tool_observations"]
```

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_main_agent_service.py -q
```

Expected: FAIL until observations are wired into context.

**Step 3: Implement context injection**

Add status, raw prescan summary, and normalized observations to the review context. If prescan fails, include status metadata but allow review to continue.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_main_agent_service.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/main_agent_service.py tests/services/test_main_agent_service.py
git commit -m "feat: include tool observations in review context"
```

---

## Phase 3: Force Expert Consumption

### Task 3.1: Require Experts to Decide on Relevant Observations

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `tests/services/test_review_runner_tool_observations.py`

**Step 1: Write failing schema test**

Create a payload where an expert finding touches a line covered by a tool observation but omits `required_rule_ids`.

Expected validation result:

```python
assert result.valid is False
assert "required_rule_ids" in result.error
```

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_tool_observations.py -q
```

Expected: FAIL until validation enforces observation decisions.

**Step 3: Implement required observation validation**

If an expert finding overlaps relevant tool observations, require:

```json
"required_rule_ids": ["sast:semgrep:..."]
```

or an explicit not-applicable decision with reason.

**Step 4: Persist validation metadata**

Record counts:

```json
{
  "tool_observation_count": 3,
  "tool_observations_referenced": 2,
  "tool_observations_omitted": 1
}
```

**Step 5: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_tool_observations.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/app/services/review_runner.py tests/services/test_review_runner_tool_observations.py
git commit -m "feat: require expert decisions on tool observations"
```

### Task 3.2: Add Prompt Contract for Tool Observation Review

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `tests/services/test_review_runner_prompts.py`

**Step 1: Write failing prompt test**

Assert prompt contains a dedicated section like:

```text
TOOL_OBSERVATION_REVIEW_ONLY
```

and instructs experts that observations are not issues until semantically confirmed.

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_prompts.py -q
```

Expected: FAIL if prompt does not include the contract.

**Step 3: Implement prompt section**

Add explicit instructions:

- Reference relevant observation ids.
- Reject irrelevant/noisy observations with reason.
- Do not copy tool text directly into user-visible issue fields.
- Preserve source-derived issue details.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_prompts.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py tests/services/test_review_runner_prompts.py
git commit -m "feat: add tool observation review prompt contract"
```

---

## Phase 4: Semantic Cross-Validation

### Task 4.1: Match SAST Findings to Expert Findings

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Test: `tests/services/test_review_runner_sast_matching.py`

**Step 1: Write failing match tests**

Cases:

- Same file + nearby lines + compatible category -> match.
- Same file + nearby lines + incompatible category -> no match.
- Same CWE/rule family + semantically aligned finding -> match.
- Same line but unrelated warning -> no match.

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_sast_matching.py -q
```

Expected: FAIL until semantic filter exists.

**Step 3: Implement matching**

Use a small mapping table:

```python
{
    "sql_injection": {"cwe-89", "injection", "tainted-sql"},
    "xss": {"cwe-79", "cross-site-scripting"},
    "authz": {"authorization", "access-control"},
}
```

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_runner_sast_matching.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py tests/services/test_review_runner_sast_matching.py
git commit -m "feat: semantically match sast observations"
```

### Task 4.2: Add SAST Bonus Only for Confirmed Matches

**Files:**
- Modify: `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- Test: `tests/services/orchestrator/test_detect_conflicts_sast.py`

**Step 1: Write failing conflict-score tests**

Assert:

```python
assert confirmed_match.confidence > base_confidence
assert unrelated_tool_warning.confidence == base_confidence
```

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/orchestrator/test_detect_conflicts_sast.py -q
```

Expected: FAIL until bonus ignores unrelated observations.

**Step 3: Implement filtered bonus**

Only apply SAST confidence bonus from `_collect_sast_prescan_matches()` after semantic alignment passes.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/orchestrator/test_detect_conflicts_sast.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/orchestrator/nodes/detect_conflicts.py tests/services/orchestrator/test_detect_conflicts_sast.py
git commit -m "feat: apply sast confidence only for semantic matches"
```

---

## Phase 5: Final Output Fidelity

### Task 5.1: Preserve Final Issue Detail Accuracy

**Files:**
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/app/services/review_service_projection.py`
- Test: `tests/services/test_review_service_projection.py`

**Step 1: Write failing final-surface test**

Use a stored-report-shaped fixture and assert final `/issues` projection keeps:

```python
assert issue["problem_description"]
assert issue["specification_description"]
assert issue["remediation_suggestion"]
assert issue["file_path"]
assert issue["line_start"]
```

Also assert tool observations do not overwrite these fields.

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_service_projection.py -q
```

Expected: FAIL if projection loses or rewrites fields.

**Step 3: Implement projection safeguards**

Treat tool data as evidence metadata only. Do not use SAST title/remediation as the primary issue fields unless the expert explicitly authored them.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_service_projection.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/review_service.py backend/app/services/review_service_projection.py tests/services/test_review_service_projection.py
git commit -m "fix: preserve final issue details with tool evidence"
```

---

## Phase 6: Governance Metrics and UI

### Task 6.1: Add Tool Adoption Metrics

**Files:**
- Modify: `backend/app/services/review_service_projection.py`
- Test: `tests/services/test_review_service_projection.py`

**Step 1: Write failing metrics test**

Assert summary metrics include:

```python
assert summary["tool_observation_count"] == 4
assert summary["tool_adoption_rate"] == 0.75
assert summary["tool_confirmation_rate"] == 0.5
assert summary["sast_cross_validated_issue_count"] == 2
assert summary["tool_false_positive_rate"] == 0.25
```

**Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_service_projection.py -q
```

Expected: FAIL until metrics exist.

**Step 3: Implement metrics calculation**

Use persisted observation metadata. If denominator is zero, return `0` and avoid division errors.

**Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/services/test_review_service_projection.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/review_service_projection.py tests/services/test_review_service_projection.py
git commit -m "feat: add static tool adoption metrics"
```

### Task 6.2: Surface Status and Metrics in Frontend

**Files:**
- Modify: `frontend/src/pages/Settings/index.tsx`
- Modify: `frontend/src/pages/Governance/index.tsx`
- Test: `frontend` TypeScript typecheck

**Step 1: Add UI states**

Settings should show:

- SAST enabled/disabled.
- Tool available/missing/error.
- Best-effort limitations.

Governance should show:

- Observation count.
- Adoption rate.
- Confirmation rate.
- Cross-validated issue count.
- False positive rate.

**Step 2: Run typecheck**

```bash
npm --prefix frontend run typecheck
```

Expected: PASS.

**Step 3: Commit**

```bash
git add frontend/src/pages/Settings/index.tsx frontend/src/pages/Governance/index.tsx
git commit -m "feat: show static tool status and governance metrics"
```

---

## Phase 7: Quality Gate and Rollout

### Task 7.1: Extend Quality Check Script

**Files:**
- Modify: `scripts/check_review_quality.sh`

**Step 1: Add focused commands**

Include:

```bash
.venv/bin/python -m pytest tests/services/test_sast_prescan_service.py -q
.venv/bin/python -m pytest tests/services/test_review_runner_tool_observations.py -q
.venv/bin/python -m pytest tests/services/orchestrator/test_detect_conflicts_sast.py -q
npm --prefix frontend run typecheck
```

**Step 2: Run quality script**

```bash
bash scripts/check_review_quality.sh
```

Expected: all test suites pass and smoke quality gates pass.

**Step 3: Commit**

```bash
git add scripts/check_review_quality.sh
git commit -m "test: include static tool adoption gates"
```

### Task 7.2: Run End-to-End Verification

**Files:**
- No code changes expected.

**Step 1: Run backend focused tests**

```bash
.venv/bin/python -m pytest \
  tests/services/test_sast_prescan_service.py \
  tests/services/test_review_runner_tool_observations.py \
  tests/services/test_review_runner_sast_matching.py \
  tests/services/orchestrator/test_detect_conflicts_sast.py \
  tests/services/test_review_service_projection.py \
  -q
```

Expected: PASS.

**Step 2: Run frontend typecheck**

```bash
npm --prefix frontend run typecheck
```

Expected: PASS.

**Step 3: Run full quality gate**

```bash
bash scripts/check_review_quality.sh
```

Expected:

```text
quality_gates.passed = true
```

**Step 4: Manual API checks**

Check:

```bash
curl -sS http://127.0.0.1:8011/api/settings/sast-tools/status | jq .
```

Expected: enabled status plus command/report tool diagnostics.

Check a completed review:

```bash
curl -sS http://127.0.0.1:8011/api/reviews/<review_id>/issues | jq .
```

Expected: formal issues preserve source-derived details and include tool evidence metadata when applicable.

---

## Rollout Plan

1. Merge behind existing `enable_sast_prescan` setting, default `true`.
2. Run in best-effort mode for all reviews; missing tools are diagnostics, not hard failures.
3. For the first week, watch `tool_false_positive_rate` and schema retry counts.
4. If false positives are high, tune semantic mapping before expanding confidence bonus.
5. After metrics stabilize, add a CI gate that fails only on schema/contract regressions, not on missing local tools.

## Operational Risks

- Existing SQLite/runtime settings may override `config.json`; verify runtime value through Settings API.
- Tool availability can differ between local shell, service process, and CI PATH.
- Semantic mapping gaps can either miss useful tool evidence or over-credit noisy warnings.
- Overly strict expert validation can increase retries; cap retries and record omitted observation metadata.
- Governance rates need enough review volume before they are meaningful.

## Final Definition of Done

- Backend focused tests pass.
- Frontend typecheck passes.
- `bash scripts/check_review_quality.sh` passes.
- Settings shows actual SAST tool status.
- Governance shows adoption/confirmation/false-positive metrics.
- A real review result proves SAST observations influence expert reasoning only after semantic confirmation.
- Final `/issues` and rendered report remain the acceptance surface for quality, not raw findings alone.
