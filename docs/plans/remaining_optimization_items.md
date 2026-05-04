# 代码检视 & 影响分析：剩余优化项

> 基于当前代码最新状态（2026-05-04），已识别已完成的优化和仍需优化的项目

---

## 已完成的优化（Codex已做）

| # | 优化项 | 状态 |
|---|--------|------|
| 1 | SAST预扫描层 | ✅ `sast_prescan_service.py` 已实现，支持Semgrep/ESLint/Bandit |
| 2 | 编码规范文件自动检测 | ✅ `repo_review_instruction_service.py` 已实现，支持REVIEW.md/CLAUDE.md/.cursorrules等 |
| 3 | 证据验证机制 | ✅ `evidence_verifier_service.py` + `evidence_verification` 图节点已实现 |
| 4 | LLM误报过滤器 | ✅ `evidence_false_positive_filter_service.py` 已实现 |
| 5 | 共识bonus检查专家是否同意 | ✅ `_has_same_issue_type_consensus` 已实现 |
| 6 | 签名变更检测流入影响报告 | ✅ `_detect_signature_change` 已集成 |
| 7 | 反馈学习服务 | ✅ `feedback_learner_service.py` 已实现 |
| 8 | 单专家medium finding逃生通道 | ✅ reject规则增加了SAST/observation/high_value等7个逃生门 |
| 9 | Issue去重与合并 | ✅ `ISSUE_JURISDICTION` + `_is_same_problem_type` 已实现 |
| 10 | 质量评估框架 | ✅ `eval_review_quality.py` + `check_review_quality.sh` 已实现 |
| 11 | 前端质量治理面板 | ✅ QualityGovernancePanel/IssueThresholdFilteredPanel/ToolAuditPanel |
| 12 | 动态target发现 | ✅ `_dynamic_targets_from_contexts` 最多追加6个target |
| 13 | 影响报告模板系统 | ✅ 报告模板 + LLM综合 + Mermaid流程图 |
| 14 | DDD架构专家 | ✅ `ddd_architecture/` 专家已添加 |
| 15 | 仓库检视策略 | ✅ `.ai-review.yml` + `repo_review_policy_service.py` |

---

## 仍需优化的项目

### A. 代码检视质量 —— 6项

#### A1. `design_concern` 仍被系统性压制 🔴

**现状**：`FINDING_TYPE_WEIGHTS` 中 `design_concern: 0.55`，加上 `design_concern_only` reject 规则（detect_conflicts.py:938-949），medium级别的架构回归几乎不可能成为issue。

**问题**：且reject规则中有一个冗余条件——`highest_severity not in {"high", "critical", "blocker"}` 在 `highest_severity in {"low", "medium"}` 时永远为True，说明原意是允许high+的design_concern通过，但逻辑写反了。

**修复**：
```python
# 1. 提升权重
FINDING_TYPE_WEIGHTS = {
    "direct_defect": 1.0,
    "test_gap": 0.8,
    "risk_hypothesis": 0.65,
    "design_concern": 0.65,  # 从0.55提升到0.65
}

# 2. 修正reject规则：允许high+的design_concern通过
if (
    config.get("suppress_low_risk_hint_issues", True)
    and finding_types <= {"design_concern"}
    and highest_severity in {"low", "medium"}  # 只压制low/medium
    # 删除冗余且错误的 not in {"high", "critical", "blocker"} 条件
):
    return {"rule_code": "design_concern_only", ...}
```

#### A2. forced observation的`finding_type`默认值过于宽松 🟡

**现状**：review_runner.py:2008，非DDD-bypass的forced observation默认 `finding_type="direct_defect"`。没有LLM确认的观察结果不应该声称是"直接缺陷"。

**修复**：
```python
# 改为 risk_hypothesis —— 没有LLM确认的观察结果只是"风险假设"
str(candidate.get("finding_type") or "risk_hypothesis")
```

#### A3. DDD factory bypass硬编码特例 🟡

**现状**：`is_forced_ddd_factory_bypass` 对特定规则(DDD-JDDD-001)和特定专家(ddd_architecture)设置 `verification_needed=False`、`confidence>=0.9`、`finding_type=direct_defect`，跳过所有质量门控。

**问题**：这是一个脆弱的硬编码特例，如果规则ID变化或其他专家需要类似处理就会失效。

**修复**：
```python
# 取消硬编码bypass，改为通用的"规则匹配+高信号"机制
if candidate.get("rule_id") and candidate.get("confidence", 0) >= 0.85:
    # 保留 verification_needed=True，让它走正常验证流程
    # 但给予适当的confidence boost
    candidate["confidence"] = min(candidate.get("confidence", 0.85) + 0.05, 0.90)
    candidate["verification_needed"] = True  # 不跳过验证
```

#### A4. SAST预扫描默认关闭 🟡

**现状**：`RuntimeSettings.enable_sast_prescan` 默认 `False`。SAST交叉验证的逃生门永远不会触发。

**修复**：
- 将默认值改为 `True`（Semgrep不存在时自动跳过，不会报错）
- 或者至少在README中明确建议开启

#### A5. Confidence校准只调整judge阈值，不调整基础分数 🟡

**现状**：`FeedbackLearnerService` 和 `IssueJudgeService._build_profile_trigger` 只调整judge触发阈值（+0.07/+0.12），不调整基础confidence。如果一个专家的历史false_positive_rate=70%，它的findings的base confidence应该被缩放。

**修复**：在 `_score_issue_confidence` 中增加feedback-based confidence缩放

```python
def _score_issue_confidence(self, items, config, quality_profiles):
    # ... existing logic ...

    # 新增：基于feedback的基础confidence缩放
    for item in items:
        expert_id = item.get("expert_id")
        issue_type = item.get("normalized_issue_type")
        profile_key = f"{expert_id}:{issue_type}"
        profile = quality_profiles.get(profile_key, {})

        accept_rate = profile.get("accept_rate", 0.5)
        sample_count = profile.get("sample_count", 0)

        if sample_count >= 5:
            # accept_rate 0.3 → scale 0.85, 0.5 → 1.0, 0.8 → 1.05
            scale = max(0.85, min(1.05, 0.7 + accept_rate * 0.6))
            item["confidence"] = item.get("confidence", 0.7) * scale
```

#### A6. `LOW_RISK_HINT_TOKENS` 仍含"代码健康" 🟢

**现状**：`"代码健康"` 仍在token列表中，但空catch块、死代码等真实问题可能被归类为"代码健康"。

**修复**：
- 从 `LOW_RISK_HINT_TOKENS` 中移除 `"代码健康"`
- 增加反过滤token：`"泄露"`, `"注入"`, `"鉴权"`, `"并发"`, `"数据丢失"`, `"死代码"`, `"不可达"`

---

### B. GitNexus影响分析 —— 5项

#### B1. MCP持久连接 🔴 最高优先级

**现状**：`McpStdioClient` 每次调用spawn新进程。虽然有 `call_many` 支持批内多请求，但进程仍按批次创建和销毁。

**修复**：
```python
class McpStdioClient:
    def __init__(self, command, persistent=True):
        self._persistent = persistent
        self._process = None
        self._lock = asyncio.Lock()

    async def _ensure_connection(self):
        async with self._lock:
            if self._process and self._process.poll() is None:
                return
            self._process = subprocess.Popen(...)
            await self._send_initialize()

    async def call_tool(self, tool_name, arguments):
        await self._ensure_connection()
        return await self._send_tool_call(tool_name, arguments)

    async def close(self):
        if self._process:
            self._process.terminate()
            self._process = None
```

**效果**：去除进程开销后，可以将硬编码上限从12/8/8提升到25/15/15，或改为RuntimeSettings可配置。

#### B2. 硬编码target/query上限应可配置 🟡

**现状**：`MAX_GITNEXUS_TARGETS=12`, `MAX_GITNEXUS_CONTEXT_QUERIES=8` 等是硬编码常量。

**修复**：移入 `RuntimeSettings`
```python
class RuntimeSettings:
    gitnexus_max_targets: int = 12
    gitnexus_max_context_queries: int = 8
    gitnexus_max_impact_queries: int = 8
    gitnexus_max_dynamic_targets: int = 6
```

#### B3. 影响分析反馈闭环不完整 🟡

**现状**：`FeedbackLearnerService.build_impact_feedback_profiles` 收集了反馈数据，但只用于排序test scope推荐，不用于抑制或重新加权影响路径。

**修复**：
```python
# 在 gitnexus_impact_service.py 的 _build_targets 中
def _build_targets(self, ...):
    # ... existing logic ...

    # 新增：根据历史反馈降权高误报率的target类型
    if feedback_profiles:
        for target in targets:
            profile = feedback_profiles.get(target.kind, {})
            fp_rate = profile.get("false_positive_rate", 0)
            if fp_rate >= 0.5 and profile.get("sample_count", 0) >= 5:
                target.score -= 15  # 降权，但不完全排除

    return sorted(targets, key=lambda t: t.score, reverse=True)[:max_targets]
```

#### B4. 影响报告缺少结构化的 confirmed/candidate 标记 🟡

**现状**：只在LLM prompt中要求"如果某个影响只是候选关系，要说'候选'"，但没有结构化字段。

**修复**：在 `ImpactPath` 中增加 `confidence` 字段
```python
class ImpactPath:
    path: list[str]
    confidence: str = "candidate"  # "confirmed" | "candidate" | "inferred"
    # confirmed: GitNexus图中有直接边
    # candidate: 2跳以内路径
    # inferred: 基于命名/路径推断
```

并在 `_normalize_gitnexus_payload` 中根据图数据自动设置：
```python
def _classify_path_confidence(self, path, graph_data):
    if self._has_direct_edge(path, graph_data):
        return "confirmed"
    elif self._has_multi_hop_path(path, graph_data, max_hops=2):
        return "candidate"
    else:
        return "inferred"
```

#### B5. 符号提取仍无AST支持 🟢（长期）

**现状**：`ast` import存在但只用于`literal_eval`。`JavaQualitySignalExtractor`是Java专用的正则增强，不是真正的AST。

**修复**：引入tree-sitter（Phase 3，长期计划）
- 优先级低，因为GitNexus的 `detect_changes` 是最权威的符号来源
- tree-sitter只作为Level 1补充（Level 0=GitNexus, Level 1=AST, Level 2=regex）

---

### C. 新发现的问题 —— 3项

#### C1. 专家overlap在执行前未去重 🟡

**现状**：`exception_swallowed` 仍同时触发 `security_compliance` 和 `maintainability_code_health`，两个专家都执行LLM调用，浪费token。去重只在detect_conflicts阶段做（post-hoc）。

**修复**：在 `route_experts` 或 `main_agent_routing_support` 中，当信号触发多个专家时，标记一个为primary、另一个为secondary，secondary专家的finding自动降权。

```python
SIGNAL_EXPERT_PRIMARY = {
    "exception_swallowed": "maintainability_code_health",
    "loop_call_amplification": "performance_reliability",
}

def apply_java_signal_expert_retention(self, signals, selected_experts):
    for signal in signals:
        primary = SIGNAL_EXPERT_PRIMARY.get(signal.kind)
        if primary:
            # 只注入primary专家，secondary不注入
            # 或者注入secondary但标记 role="secondary"
            self._inject_expert(primary, signal, role="primary")
```

#### C2. 反馈冷启动问题 🟢

**现状**：`FeedbackLearnerService` 和 `IssueJudgeService` 都要求 `sample_count >= 3`（judge）或 `>= 5`（calibration）才生效。不提供反馈的团队永远无法受益。

**修复**：无反馈数据时，使用保守默认值而非完全跳过
```python
if sample_count < 3:
    # 使用全局平均而非跳过
    global_avg = self._get_global_average_accept_rate(expert_id)
    accept_rate = global_avg or 0.5
```

#### C3. 影响分析结果与检视发现无交叉引用 🟢

**现状**：影响分析报告和代码检视issue是两个完全独立的产出，没有交叉引用。如果security专家在`OrderController.createOrder`发现漏洞，影响报告不会标记该入口点已被检视出问题。

**修复**：在影响报告中增加"检视覆盖"标记
```python
# 在 build_report 中，交叉比对 issues 和 impact_report
for path in impact_report.impact_paths:
    for node in path.path:
        matching_issues = [i for i in issues if node in i.file_path]
        if matching_issues:
            path.review_covered = True
            path.review_findings = [{"severity": i.severity, "title": i.title} for i in matching_issues]
```

---

## 实施优先级

| 优先级 | 项目 | 预期效果 | 工作量 |
|--------|------|---------|--------|
| **P0** | A1: design_concern权重+reject规则修复 | 架构回归不再被系统性压制 | 0.5天 |
| **P0** | B1: MCP持久连接 | 性能提升3-5x，解锁更高上限 | 1天 |
| **P1** | A2: forced observation finding_type改为risk_hypothesis | 观察结果不再伪装成直接缺陷 | 0.5天 |
| **P1** | A3: 取消DDD factory bypass硬编码 | 消除脆弱特例 | 0.5天 |
| **P1** | A4: SAST预扫描默认开启 | SAST交叉验证逃生门可生效 | 0.5天 |
| **P1** | A6: LOW_RISK_HINT_TOKENS收紧 | 代码健康类issue不再被误过滤 | 0.5天 |
| **P1** | B2: target/query上限可配置 | 适配不同规模的MR | 0.5天 |
| **P1** | C1: 专家overlap执行前去重 | 节省LLM token | 0.5天 |
| **P2** | A5: confidence基础分数缩放 | 历史误报高的专家自动降权 | 1天 |
| **P2** | B3: 影响分析反馈闭环完善 | 历史高误报影响路径自动降权 | 1天 |
| **P2** | B4: 影响路径confirmed/candidate结构化 | 影响可信度一目了然 | 1天 |
| **P2** | C3: 影响报告与检视发现交叉引用 | 两个产出互为补充 | 1天 |
| **P3** | C2: 反馈冷启动处理 | 新团队也能受益 | 0.5天 |
| **P3** | B5: tree-sitter AST符号提取 | 多语言覆盖+删除符号追踪 | 3天 |
