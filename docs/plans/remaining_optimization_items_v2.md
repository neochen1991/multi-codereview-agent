# 代码检视 & 影响分析：第二轮复查结果

> 基于最新代码（commit fbe89d0），逐项核实14个优化项的实际状态

---

## 检视质量 — 6项复查

### A1. `design_concern` 仍被系统性压制 — ❌ 未修复

**实测代码** (`detect_conflicts.py`):

```python
# 行72-77 — 权重未变
FINDING_TYPE_WEIGHTS = {
    "direct_defect": 1.0,
    "test_gap": 0.8,
    "risk_hypothesis": 0.65,
    "design_concern": 0.55,  # ← 仍然是最低权重
}

# 行938-949 — reject规则未变
if (
    bool(config.get("suppress_low_risk_hint_issues", True))
    and finding_types <= {"design_concern"}
    and highest_severity in {"low", "medium"}
    and highest_severity not in {"high", "critical", "blocker"}  # ← 冗余条件，永远为True
):
    return {"rule_code": "design_concern_only", ...}
```

**问题**：
1. 权重0.55意味着design_concern的基础confidence被砍到55%
2. reject规则中 `highest_severity not in {"high","critical","blocker"}` 在 `highest_severity in {"low","medium"}` 时永远为True，是冗余代码
3. 高severity的design_concern虽不被此规则杀死，但0.55权重使其几乎不可能达到confidence阈值

**需修复**：
- 权重 0.55 → 0.65
- 删除冗余条件
- 或者：severity>=high的design_concern跳过此reject规则

---

### A2. forced observation `finding_type` 默认值 — ⚠️ 部分修复

**实测代码** (`review_runner_expert_output.py`):

已改善：3/6种观察信号现在正确使用 `finding_type="risk_hypothesis"` + `verification_needed=True` + `direct_evidence=False`：
- `performance_reliability` + `control_flow_with_external_call`
- `ddd_architecture` + `construction_path_changed`
- `correctness_business` + `declared_intent_without_implementation`

仍问题：另外3种仍使用 `finding_type="direct_defect"` + `verification_needed=False`：
- `database_analysis` + `query_without_bound` / `query_plan_risk`
- `performance_reliability` + `bulk_processing_boundary_missing` / `transactional_side_effect`
- `security_compliance` + `input_validation_removed` / `security_guard_removed`

**仍问题**：`review_runner.py:2018` 的消费者兜底默认仍是 `"direct_defect"`：
```python
else str(candidate.get("finding_type") or "direct_defect")
```

**需修复**：
- 兜底默认改为 `"risk_hypothesis"`
- 对于 `database_analysis` 的无界查询：`query_without_bound` 可能只是推测（如循环内的仓库调用可能被误判），应改为 `risk_hypothesis`
- `security_compliance` + `security_guard_removed` 确实有较高置信度，可保留 `direct_defect`，但应设 `verification_needed=True`

---

### A3. DDD factory bypass 硬编码特例 — ❌ 未修复

**实测代码** (`review_runner.py:2002-2036`):

完全未变。仍然：
- `verification_needed = False` — 跳过验证
- `confidence >= 0.9` — 强制高置信度
- `finding_type = "direct_defect"` — 强制类型

**需修复**：
- 取消硬编码bypass，改为通用机制
- 至少设 `verification_needed = True`，让它走正常验证流程
- confidence 不应强制0.9，改为 `min(confidence + 0.05, 0.85)`

---

### A4. SAST预扫描默认关闭 — ❌ 未修复

**实测代码** (`runtime_settings.py:111`):
```python
enable_sast_prescan: bool = False
```

SAST服务本身完整（支持Semgrep/ESLint/Bandit，有CWE提取和`why_it_matters`解释），但默认关闭意味着：
- `detect_conflicts` 的 `sast_cross_validated` 逃生门永远不会触发
- SAST verification bonus (+0.08) 永远不会加
- 证据验证中的 `static_analysis_signals` 永远为空

**需修复**：
- 默认改为 `True`（Semgrep不存在时自动跳过，不会报错）
- 或在首次启动时检测Semgrep可用性，自动设置

---

### A5. Confidence基础分数未做feedback缩放 — ❌ 未修复

**实测代码**：
- `feedback_learner_service.py` 已生成 `confidence_penalty`/`confidence_bonus` 数据 ✅
- `judge_and_merge.py` 的 `_apply_feedback_quality_profile` 已使用这些数据调整confidence ✅
- **但** `detect_conflicts.py` 的 `_score_issue_confidence` **完全不使用** feedback profiles ❌

问题链路：
```
_score_issue_confidence (detect_conflicts) — 不用feedback → 基础分不准确
    ↓
evidence_verification — 可能进一步调整
    ↓
_apply_feedback_quality_profile (judge_and_merge) — 终于用了feedback → 但太晚了
```

**需修复**：在 `_score_issue_confidence` 中增加feedback-based基础分缩放：
```python
# 在计算 weighted_confidence 之后
if quality_profiles:
    for item in items:
        profile = quality_profiles.get(f"{item['expert_id']}:{item.get('normalized_issue_type')}", {})
        penalty = float(profile.get("confidence_penalty") or 0)
        bonus = float(profile.get("confidence_bonus") or 0)
        item["confidence"] = max(0.3, min(0.95, item["confidence"] * (1 - penalty * 0.5) + bonus * 0.3))
```

---

### A6. `LOW_RISK_HINT_TOKENS` 仍含"代码健康" — ❌ 未修复

**实测代码** (`detect_conflicts.py:8-23`):

```python
LOW_RISK_HINT_TOKENS = {
    "命名", "命名约定", "可读性", "注释", "风格", "格式化", "缩进",
    "统一写法", "常量约定", "日志补充", "文档说明", "提示性", "提醒",
    "代码健康",  # ← 仍在
}
```

无 `HIGH_PRIORITY_OVERRIDE_TOKENS`。虽有 `HIGH_VALUE_CONTRACT_MISMATCH_TOKENS` 和 `HIGH_VALUE_DIRECT_DEFECT_TOKENS` 作为逃生门，但不覆盖安全/并发/数据丢失类issue。

**需修复**：
- 移除 `"代码健康"`
- 增加反过滤token集：`"泄露"`, `"注入"`, `"鉴权"`, `"并发"`, `"死代码"`, `"不可达"`, `"数据丢失"`

---

## GitNexus影响分析 — 5项复查

### B1. MCP持久连接 — ❌ 未修复

**实测代码** (`mcp_stdio_client.py`):

`call_many()` 每次调用仍创建新 `subprocess.Popen`。无连接复用、无进程池、无持久状态。

**影响**：这是12/8/8硬上限的根因。每次MR分析最多创建18个进程。

**需修复**：实现持久连接模式（详见之前方案）

---

### B2. target/query上限硬编码 — ❌ 未修复

**实测代码** (`gitnexus_impact_service.py:98-101`):

```python
MAX_GITNEXUS_TARGETS = 12
MAX_GITNEXUS_CONTEXT_QUERIES = 8
MAX_GITNEXUS_IMPACT_QUERIES = 8
MAX_GITNEXUS_DYNAMIC_TARGETS = 6
```

`RuntimeSettings` 中无对应字段。

**需修复**：移入 `RuntimeSettings` 可配置

---

### B3. 影响分析反馈闭环 — ⚠️ 部分修复

**已改善**：
- `_apply_impact_feedback_profiles()` (gitnexus_impact_service.py:2412-2459) 现在对影响路径设置 `confidence_label`：
  - `"historically_confirmed"` — 历史确认
  - `"needs_verification"` — 需人工验证
- 排序优先级：`historically_confirmed` > `candidate` > `needs_verification`

**仍问题**：
- `_build_targets()` 和 `_target_priority_score()` **完全不使用** feedback profiles
- 反馈只调整报告输出（post-hoc），不在target选择阶段抑制或降权

**需修复**：在 `_build_targets` 中根据历史反馈降权高误报率target

---

### B4. 影响路径confirmed/candidate结构化字段 — ✅ 已修复

**实测代码** (`report.py:77-86`):

```python
class ImpactPath(BaseModel):
    source: str = ""
    target: str = ""
    path: list[str] = Field(default_factory=list)
    depth: int = 0
    risk: str = ""
    confidence_label: str = "candidate"        # ← 新增
    confirmation_reason: str = ""               # ← 新增
```

值域：`"candidate"` | `"needs_verification"` | `"historically_confirmed"`

✅ 此项已完成。

---

### B5. AST符号提取 — ❌ 未修复

`import ast` 存在但只用于 `ast.literal_eval`。所有符号提取仍为正则。无tree-sitter。

---

## 新发现的问题 — 3项复查

### C1. 专家overlap执行前未去重 — ❌ 未修复

**实测代码** (`main_agent_routing_support.py:132-144`):

```python
if {"query_semantics_weakened", "exception_swallowed"} & signal_set:
    _add_if_requested("security_compliance", ...)

if {"naming_convention_violation", "magic_value_literal", "exception_swallowed", ...} & signal_set:
    _add_if_requested("maintainability_code_health", ...)
```

`exception_swallowed` 仍同时触发两个专家。无 `SIGNAL_EXPERT_PRIMARY` 映射。

**需修复**：定义管辖权映射，执行前只注入primary专家

---

### C2. 反馈冷启动 — ❌ 未修复

所有反馈逻辑要求 `sample_count >= 3` 才生效。无全局平均fallback、无贝叶斯平滑。

---

### C3. 影响报告与检视发现无交叉引用 — ❌ 未修复

`review_report_builder.py` 的 `build_report()` 直接附加 `impact_report`，不做任何交叉比对。

---

## 新发现的额外问题

### N1. SAST交叉验证仅基于位置共现，无语义校验

**现状**：`detect_conflicts.py` 的 `_collect_sast_prescan_matches` 仅检查SAST发现和专家finding是否在同一文件+行范围。不做语义校验（如SAST发现SQL注入，专家finding是空指针，两者在同一行但无关）。

**风险**：位置共现的 `sast_cross_validated` 会给予 +0.08 verification bonus，可能抬高无关finding的confidence。

**建议**：在设置 `sast_cross_validated=True` 前，检查SAST发现的CWE/rule_id与专家finding的 `normalized_issue_type` 是否相关。

### N2. `_should_use_static_diff` 的token列表与上游信号需手动同步

**现状**：`evidence_verification.py:175-199` 的 `_should_use_static_diff` 用硬编码token列表匹配风险信号。如果上游 `slice_change.py` 新增了风险信号名称，这里不会自动同步。

**建议**：改为从 `slice_change.py` 导出信号名称常量，`evidence_verification.py` 引用同一常量。

### N3. SAST scanner不支持配置文件

**现状**：`sast_prescan_service.py` 运行 `semgrep --json` 时不指定 `--config`，使用默认规则。项目若有 `.semgrep.yml` 会被忽略。ESLint同理。

**建议**：
```python
# 检测项目级Semgrep配置
config_args = []
if os.path.exists(os.path.join(repo_path, ".semgrep.yml")):
    config_args = ["--config", os.path.join(repo_path, ".semgrep.yml")]
```

---

## 总结：14项优化项状态

| # | 项目 | 状态 | 优先级 |
|---|------|------|--------|
| A1 | design_concern权重+reject规则 | ❌ 未修复 | P0 |
| A2 | forced observation finding_type | ⚠️ 部分修复(3/6改善，兜底默认仍为direct_defect) | P1 |
| A3 | DDD factory bypass硬编码 | ❌ 未修复 | P1 |
| A4 | SAST预扫描默认关闭 | ❌ 未修复 | P1 |
| A5 | Confidence基础分feedback缩放 | ❌ 未修复(feedback数据已生成但未消费) | P1 |
| A6 | LOW_RISK_HINT_TOKENS含"代码健康" | ❌ 未修复 | P1 |
| B1 | MCP持久连接 | ❌ 未修复 | P0 |
| B2 | target/query上限硬编码 | ❌ 未修复 | P1 |
| B3 | 影响分析反馈闭环 | ⚠️ 部分修复(报告阶段有效，target选择阶段无效) | P2 |
| B4 | 影响路径confirmed/candidate字段 | ✅ 已修复 | — |
| B5 | AST符号提取 | ❌ 未修复 | P3 |
| C1 | 专家overlap执行前去重 | ❌ 未修复 | P1 |
| C2 | 反馈冷启动 | ❌ 未修复 | P3 |
| C3 | 影响报告与检视交叉引用 | ❌ 未修复 | P2 |

**额外发现**：3个新问题（SAST语义校验N1、token同步N2、SAST配置文件N3）

---

## 建议实施顺序

**第一优先（1-2天，改动小、效果好）**：

| 项目 | 改动量 | 效果 |
|------|--------|------|
| A1: design_concern权重0.55→0.65，修reject规则 | 改2行 | 架构回归不再被压制 |
| A4: enable_sast_prescan默认True | 改1行 | SAST交叉验证逃生门生效 |
| A6: 移除"代码健康"，增加反过滤token | 改20行 | 代码健康类真问题不再被误杀 |
| A2: 兜底默认"direct_defect"→"risk_hypothesis" | 改1行 | 观察结果不再伪装 |

**第二优先（2-3天，需测试）**：

| 项目 | 改动量 | 效果 |
|------|--------|------|
| A3: 取消DDD bypass硬编码 | 改10行 | 消除脆弱特例 |
| A5: _score_issue_confidence消费feedback profiles | 改20行 | 历史高误报专家自动降权 |
| C1: 信号专家管辖权映射 | 改15行 | 节省LLM token，减少重复 |
| B2: 上限移入RuntimeSettings | 改10行 | 可配置 |

**第三优先（3-5天，需架构改动）**：

| 项目 | 改动量 | 效果 |
|------|--------|------|
| B1: MCP持久连接 | 改50行 | 性能提升3-5x |
| B3: feedback闭环到target选择 | 改30行 | 历史高误报target自动降权 |
| C3: 影响报告与检视交叉引用 | 改40行 | 两个产出互为补充 |
| N1: SAST语义校验 | 改30行 | 避免无关SAST匹配抬高confidence |
| N3: SAST配置文件支持 | 改20行 | 使用项目自定义规则 |
