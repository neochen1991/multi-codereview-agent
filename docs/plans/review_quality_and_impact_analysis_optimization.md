# 代码检视质量提升 & GitNexus关联影响分析优化

> 两个功能隔离分析，各自独立优化

---

# 第一部分：代码检视质量提升

## 一、当前系统的三层质量门控（已有的好设计）

```
Layer 1: detect_conflicts (7条reject规则 + confidence评分)
    ↓
Layer 2: run_targeted_debate (LLM辩论 + 规则fallback)
    ↓
Layer 3: evidence_verification + false_positive_filter + issue_judge
```

这套设计比大多数工具只有单层judge强得多，且反馈学习闭环(quality_profiles)是独特优势。**以下优化是在此基础上的补强，不是推翻重来。**

---

## 二、漏检问题分析（False Negatives —— 不该漏的漏了）

### 问题1：单专家medium发现几乎必被杀 —— 系统性漏检

**根因**：`detect_conflicts` 的 reject 规则 6(`hint_like_medium`) 和规则 7(`conditional_conclusion`) 组合，加上 `verification_needed` 默认为 True，导致单专家的 medium-severity finding 生存率极低。

**具体机制**：
- 规则6：单专家 + medium + 无直接证据 + confidence < 0.85 + evidence <= 2 + 含hint token → reject
- 规则7：所有finding的 `verification_needed=True`（LLM默认输出True） + 不满足"强直接代码问题"的5个条件 → reject
- "强直接代码问题"要求同时满足：direct_evidence + high/critical/blocker + confidence >= 阈值 + evidence >= 4 + 高价值token

**真实损失**：一个专家发现某medium级别的空catch块吞了异常，但只有2条evidence、confidence=0.78 → 被规则6杀掉。如果LLM恰好用了"日志补充"一词 → 匹配 `LOW_RISK_HINT_TOKENS` → 更快被杀。

**修复方案**：

```python
# detect_conflicts.py — 调整规则6和7

# 规则6修复：增加"安全网"例外
def _classify_issue_candidate(self, group):
    # ... existing rules ...

    # 规则6：hint_like_medium — 增加3个例外
    if is_hint_like_medium:
        # 例外1：如果是observation signal驱动注入的专家（说明静态分析也发现了）
        if any(f.get("source") == "observation_signal" for f in group):
            return "keep"  # 规则引擎和LLM双确认，不放行
        # 例外2：如果finding_type是 direct_defect（即使只有一个专家）
        if any(f.get("finding_type") == "direct_defect" for f in group):
            return "keep"
        # 例外3：如果severity >= high
        if any(f.get("severity") in ("high", "critical", "blocker") for f in group):
            return "keep"
        return "reject:hint_like_medium"

    # 规则7修复：verification_needed不应默认全True就触发
    if all_need_verification:
        # 放宽"强直接代码问题"条件：evidence >= 3即可（原来要求4）
        has_strong_evidence = (
            has_direct_evidence
            and severity in ("high", "critical", "blocker")
            and avg_confidence >= priority_threshold
            and evidence_count >= 3  # 从4降到3
        )
        if has_strong_evidence:
            return "keep"
```

### 问题2：forced observation候选绕过质量流水线 —— "高置信度"的伪直接证据

**根因**：`_build_forced_observation_candidates` 机制将 `loop_call_amplification`、`comment_contract_unimplemented` 等观察结果以 `direct_evidence: True`、`verification_needed: False`、confidence >= 0.84 直接注入finding列表。这些finding绕过了整个质量流水线，因为它们看起来已经是"已验证的直接缺陷"。

**真实损失**：
- `loop_call_amplification` 用700字符窗口检测循环内的外部调用，可能误报：`forEach` 遍历内存列表后500字符处出现 `repository` 调用，实际不在循环内
- `comment_contract_unimplemented` 将TODO/FIXME标记为"未实现的承诺"，但很多TODO是技术债务标记，不是违约

**修复方案**：

```python
# review_runner.py — forced observation候选的evidence降级

def _build_forced_observation_candidates(self, observations, expert_id):
    for obs in observations:
        candidate = {
            # ... existing fields ...
            # 关键修改：observation-derived finding 不应声称 direct_evidence
            "direct_evidence": False,  # 从True改为False
            "verification_needed": True,  # 从False改为True，让它走正常验证流程
            "confidence": min(obs.get("confidence", 0.8), 0.78),  # cap at 0.78，不自动给0.84+
            "evidence_source": "observation_signal",  # 标记来源，供后续规则使用
        }
```

### 问题3：confidence校准缺基准 —— LLM说0.9就是0.9

**根因**：整个confidence流水线做的是加减法调整（共识+0.08，证据+0.06，假设惩罚-0.12，FP filter -0.3），但base值来自未校准的LLM输出。LLM confidence普遍聚集在0.7-0.85，且与真实准确率无对应关系。

**修复方案**：引入基于quality_profiles的先验校准

```python
# 新增：confidence校准器
class ConfidenceCalibrator:
    """基于历史反馈数据校准LLM原始confidence"""

    def calibrate(self, raw_confidence: float, expert_id: str,
                  finding_type: str, normalized_issue_type: str,
                  quality_profiles: dict) -> float:
        """
        根据历史数据校准confidence：
        - 如果某专家某类型的历史accept率 < 50%，则惩罚
        - 如果某专家某类型的历史accept率 > 80%，则轻微提升
        """
        profile_key = f"{expert_id}:{normalized_issue_type}"
        profile = quality_profiles.get(profile_key, {})

        accept_rate = profile.get("accept_rate", 0.5)  # 默认0.5（无数据时不偏移）
        sample_count = profile.get("sample_count", 0)

        if sample_count < 5:
            # 数据不足，不做大幅校准，但做保守处理
            return min(raw_confidence, 0.85)  # cap at 0.85 for unknown experts

        # 线性映射：accept_rate 0.3 → -0.15, 0.5 → 0, 0.8 → +0.05
        adjustment = (accept_rate - 0.5) * 0.3
        adjustment = max(-0.15, min(0.05, adjustment))

        return max(0.3, min(0.95, raw_confidence + adjustment))
```

### 问题4：关键问题类型检测盲区

| 盲区 | 当前状态 | 业界做法 | 修复方案 |
|------|---------|---------|---------|
| **并发/竞态条件** | 无任何检测 | CodeRabbit task graph包含concurrency子任务 | correctness_business prompt增加并发检查要求；新增`concurrent_access_risk`观察信号 |
| **幂等性违反** | 仅MQ专家提到"幂等" | Qodo检查API幂等性 | correctness_business prompt增加幂等性检查：重复请求是否导致数据不一致 |
| **API契约破坏** | cross_file_impact有签名变更检测，但不参与issue | Qodo跨仓API兼容性检查 | 将cross_file_impact的签名变更检测结果作为expert context注入correctness_business和architecture_design |
| **配置驱动行为** | 完全无视 | Greptile RAG检索配置引用 | 新增`config_driven_behavior`观察信号：检测@Value/@ConfigurationProperties引用 |
| **SSRF/反序列化/加密误用** | security prompt提了一句，但不具体 | SAST工具擅长 | SAST预扫描注入为验证目标（见方案5） |

### 问题5：design_concern系统性被压制

**根因**：confidence评分中 `design_concern` 权重仅0.55，加上 `design_concern_only` reject规则，导致真正的架构回归几乎不可能被提升为issue。

**修复方案**：
- `design_concern` 权重从0.55提升到 **0.65**（仍低于direct_defect的1.0，但不再是二等公民）
- `design_concern_only` reject规则：当severity >= high时，不做reject

---

## 三、多检问题分析（False Positives —— 不该报的报了）

### 问题1：专家间overlap导致同一问题重复报出

**根因**：`apply_java_signal_expert_retention` 中，`exception_swallowed` 同时触发 `security_compliance`(0.76) 和 `maintainability_code_health`(0.72)；`loop_call_amplification` 同时触发 `performance_reliability`(0.84) 和 `database_analysis`(0.8)。

**修复方案**：在detect_conflicts中增加管辖权去重

```python
# detect_conflicts.py — 跨专家管辖权去重

# 定义每类问题的"管辖专家"（谁拥有最终决定权）
ISSUE_JURISDICTION = {
    "exception_swallowed": "maintainability_code_health",  # 空catch块→代码健康
    "exception_semantics_weakened": "correctness_business", # 异常语义弱化→业务正确性
    "injection_risk": "security_compliance",               # 注入风险→安全
    "missing_auth_check": "security_compliance",           # 鉴权缺失→安全
    "loop_call_amplification": "performance_reliability",  # 循环内外部调用→性能
    "unbounded_query_risk": "database_analysis",           # 无界查询→数据库
    "query_semantics_weakened": "database_analysis",       # 查询语义弱化→数据库
    "comment_contract_unimplemented": "correctness_business", # 契约未实现→业务正确性
    "naming_convention_violation": "maintainability_code_health",
    "magic_value_literal": "maintainability_code_health",
}

def _dedup_cross_expert_findings(self, findings):
    """合并同一位置+同一问题的跨专家finding，保留管辖专家"""
    grouped = {}
    for f in findings:
        key = (f["file_path"], f.get("line_start"), f.get("normalized_issue_type"))
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(f)

    result = []
    for key, group in grouped.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        jurisdiction = ISSUE_JURISDICTION.get(key[2])
        if jurisdiction:
            # 优先保留管辖专家的finding
            primary = next((f for f in group if f["expert_id"] == jurisdiction), group[0])
            # 其他专家的finding作为supporting_evidence追加
            for f in group:
                if f != primary:
                    primary.setdefault("supporting_experts", []).append({
                        "expert_id": f["expert_id"],
                        "evidence": f.get("evidence", []),
                    })
            result.append(primary)
        else:
            # 无管辖权定义，保留confidence最高的
            result.append(max(group, key=lambda f: f.get("confidence", 0)))

    return result
```

### 问题2：共识bonus不看"是否同意"只看"是否同位置"

**根因**：`_score_issue_confidence` 的共识bonus只看 `participant_count`，不管两个专家是否对问题性质达成一致。专家A说"安全问题"，专家B说"命名问题"，在同一行 → 也给共识+0.05。

**修复方案**：

```python
def _consensus_bonus(self, group):
    participant_count = len(set(f["expert_id"] for f in group))

    # 新增：检查专家是否对问题性质达成一致
    issue_types = set(f.get("normalized_issue_type") for f in group if f.get("normalized_issue_type"))
    same_verdict = len(issue_types) <= 1  # 同一issue_type才算真共识

    if participant_count >= 2 and same_verdict:
        return min(0.08, 0.03 + 0.02 * (participant_count - 2))
    elif participant_count >= 2 and not same_verdict:
        return min(0.04, 0.01 * participant_count)  # 不同意见的"弱共识"，bonus减半
    return 0.0
```

### 问题3：`verification_needed` 默认True导致过多issue进入conditional_conclusion陷阱

**根因**：专家prompt的JSON输出模板中 `verification_needed` 是一个必须输出的字段，LLM倾向于输出True（保守选择）。

**修复方案**：修改专家prompt，明确指导何时设为False

```markdown
<!-- 在每个专家prompt的输出格式说明中 -->

- verification_needed:
  - 设为 false：当你在diff中直接看到了问题代码（如空catch块、硬编码密钥、缺少null检查）
  - 设为 true：当你推测问题存在但无法从diff直接确认（如"可能影响下游调用方"）
  - **默认应设为 false**——如果你能指出具体的问题代码行，就不需要额外验证
```

### 问题4：`LOW_RISK_HINT_TOKENS` 过于宽泛

**当前token列表**："代码健康"、"提醒"、"提示性"、"命名"、"日志补充"、"格式化"、"建议性"等

**问题**：一个关于"日志中泄露了PII"的issue可能因为包含"日志"二字就被过滤。一个关于"异常处理缺少关键日志导致生产问题无法排查"的issue包含"日志"和"异常"也被过滤。

**修复方案**：缩小token列表 + 增加反过滤例外

```python
# 收窄hint token — 只保留纯风格/格式类
LOW_RISK_HINT_TOKENS = {
    # 保留：纯风格/格式
    "格式化", "代码风格", "排版", "对齐", "空行",
    # 保留：纯命名建议（但不含安全性命名问题）
    "命名建议", "命名优化",
    # 删除："代码健康"(太宽)、"日志补充"(可能含安全)、"提醒"(太宽)
}

# 增加反过滤：如果issue含高优先级token，即使有hint token也不过滤
HIGH_PRIORITY_OVERRIDE_TOKENS = {
    "泄露", "注入", "鉴权", "权限", "安全", "并发", "竞态",
    "死锁", "空指针", "NPE", "超时", "熔断", "降级",
    "数据丢失", "不一致", "幂等", "重入",
}
```

---

## 四、新增：SAST预扫描层（减少漏检的关键手段）

**业界证明**：SAST交叉引用提升漏检率至96.9%，SAST-Genius减少91%的SAST误报。这是投入产出比最高的优化。

### 架构

```
diff输入
  ↓
SAST预扫描 (Semgrep/ESLint/Bandit，秒级)
  ↓
SAST发现 → 作为"验证目标"注入专家prompt
  ↓
专家LLM检视 (结合SAST发现做深度确认)
  ↓
交叉验证：
  - SAST发现 + LLM确认 → confidence >= 0.90 (直接通过)
  - SAST发现 + LLM否认 → needs_human (人工判断)
  - LLM发现 + SAST确认 → confidence + 0.10
  - LLM发现 + SAST未覆盖 → 正常流程
```

### 实现要点

```python
# 新增服务：sast_preflight_service.py
class SastPreflightService:
    """SAST预扫描服务：在LLM检视前运行确定性规则扫描"""

    def scan(self, changed_files: list[str], repo_path: str) -> list[SastFinding]:
        findings = []

        # 1. Semgrep (多语言安全+质量规则)
        if self._semgrep_available():
            findings.extend(self._run_semgrep(changed_files, repo_path))

        # 2. ESLint (JS/TS)
        js_ts_files = [f for f in changed_files if f.endswith(('.js', '.ts', '.tsx'))]
        if js_ts_files and self._eslint_available():
            findings.extend(self._run_eslint(js_ts_files, repo_path))

        # 3. Bandit (Python)
        py_files = [f for f in changed_files if f.endswith('.py')]
        if py_files and self._bandit_available():
            findings.extend(self._run_bandit(py_files, repo_path))

        # 4. 过滤：只保留changed_files内的发现
        return [f for f in findings if f.file_path in set(changed_files)]
```

### 注入专家prompt

```python
def _inject_sast_findings_as_verification_targets(self, expert_prompt, sast_findings, expert_id):
    """将SAST发现注入专家prompt作为验证目标"""
    relevant_findings = [f for f in sast_findings if self._is_relevant(f, expert_id)]
    if not relevant_findings:
        return expert_prompt

    sast_section = "## 静态分析预扫描结果（需要你确认）\n\n"
    sast_section += "以下问题由静态分析工具发现，请在检视中重点确认：\n\n"

    for f in relevant_findings:
        sast_section += f"- [{f.tool}:{f.rule_id}] {f.file_path}:{f.line} — {f.message}\n"
        sast_section += f"  请验证：此发现是否为真实问题？\n"

    return expert_prompt + "\n\n" + sast_section
```

---

## 五、新增：验证Agent（减少误报的关键手段）

### 设计原则

- **不是第三层judge**，而是"独立复核员"——用不同的视角重新审视
- **只对灰色地带触发**：confidence 0.65-0.80 或 risk_hypothesis 类型
- **可以推翻judge**：如果验证Agent明确reject，即使judge已经accept也可以降级

### 触发条件

```python
def should_verify(issue) -> bool:
    """验证Agent触发条件"""
    # 条件1：灰色地带confidence
    if 0.65 <= issue.confidence <= 0.80:
        return True
    # 条件2：高severity但无工具验证
    if issue.severity in ("blocker", "critical") and not issue.tool_verified:
        return True
    # 条件3：risk_hypothesis + 跨文件
    if issue.finding_type == "risk_hypothesis" and issue.cross_file_evidence:
        return True
    # 条件4：单专家 + 无直接证据
    if issue.participant_count == 1 and not issue.direct_evidence:
        return True
    return False
```

### 验证Agent的prompt策略

```
你是一个代码检视验证Agent。你的任务是独立复核以下issue是否为真实问题。

**你必须**：
1. 重新阅读diff，独立判断问题是否存在（不要受原专家的推理影响）
2. 检查修复建议是否可行（不会引入新问题）
3. 检查是否遗漏了关键上下文（调用方、配置、版本兼容性）

**你不要**：
- 重新做一遍检视（只复核给定的issue）
- 猜测你没有看到的代码

输出JSON：
{
  "verdict": "confirmed" | "rejected" | "needs_context",
  "reason": "具体原因",
  "confidence_adjustment": -0.15 ~ +0.05
}
```

### 集成位置

```
judge_and_merge
    ↓
verification_agent (新增：只对should_verify=True的issue)
    ↓
human_gate
```

---

## 六、优化总结：检视质量

| 优化项 | 类型 | 预期效果 | 优先级 |
|--------|------|---------|--------|
| 放宽单专家medium reject规则 | 减少漏检 | 单专家真问题不再被系统性杀掉 | P0 |
| forced observation降级 | 减少多检 | 观察信号不再伪装成直接证据 | P0 |
| SAST预扫描 | 减少漏检 | SAST交叉引用提升检测率至96.9% | P0 |
| 管辖权去重 | 减少多检 | 同一问题不再被两个专家重复报 | P1 |
| confidence先验校准 | 减少漏检+多检 | LLM置信度与真实准确率对齐 | P1 |
| verification_needed默认改False | 减少漏检 | 解除conditional_conclusion陷阱 | P1 |
| 收窄LOW_RISK_HINT_TOKENS | 减少漏检 | 日志/异常类issue不再被误过滤 | P1 |
| 共识bonus看同意度 | 减少多检 | 不同意见的弱共识不再获得同等bonus | P2 |
| design_concern权重提升 | 减少漏检 | 架构回归不再被系统性压制 | P2 |
| 验证Agent | 减少多检 | 灰色地带issue二次独立复核 | P2 |
| 并发/幂等/API契约盲区补全 | 减少漏检 | 覆盖当前完全无法检测的问题类型 | P2 |

---

---

# 第二部分：GitNexus关联影响分析优化

## 一、当前系统的完整链路（已有的好设计）

```
符号提取(正则) → MCP查询(list_repos/detect_changes/context/impact)
    → 报告归一化 → LLM综合 → Markdown报告
```

GitNexus MCP集成是国内独有的代码图谱影响分析方案。以下是在此基础上的提升方向。

---

## 二、影响分析的核心问题

### 问题1：MCP每次查询都新建子进程 —— 性能瓶颈

**现状**：每次 `_call_tool` 都 spawn 一个新的 `gitnexus mcp` 进程，发送 `initialize → initialized → tools/call`，然后杀掉。一个MR最多触发 1(list_repos) + 1(detect_changes) + 8(context) + 8(impact) = **18次进程创建**。

**影响**：
- 每次进程创建有冷启动开销
- 12-target / 8-query 的硬上限正是因为进程开销而设
- 总耗时可能超过60秒

**修复方案**：MCP持久连接

```python
# mcp_stdio_client.py — 增加持久连接模式

class McpStdioClient:
    def __init__(self, command, persistent=True):
        self._command = command
        self._persistent = persistent
        self._process = None
        self._request_id = 0

    async def _ensure_connection(self):
        """确保MCP连接存活"""
        if self._process and self._process.poll() is None:
            return  # 连接仍然存活

        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # 只需initialize一次
        await self._send_initialize()

    async def call_tool(self, tool_name, arguments):
        """复用持久连接调用tool"""
        await self._ensure_connection()
        return await self._send_tool_call(tool_name, arguments)

    async def close(self):
        """显式关闭连接"""
        if self._process:
            self._process.terminate()
            self._process = None
```

**效果**：去除进程开销后，可以将上限从12 targets / 8 queries 提升到 **30 targets / 20 queries**，大型MR覆盖更完整。

### 问题2：符号提取仅靠正则 —— 覆盖率和准确性不足

**当前覆盖**：Java/Kotlin/JS/TS/Python（且仅方法声明和类声明）

**关键遗漏**：
- 接口/抽象方法声明被过滤（以分号结尾的声明被丢弃）
- 注解驱动的入口点（`@GetMapping`、`@RabbitListener`、`@Scheduled`）不可见
- MyBatis XML mapper的SQL ID未提取（但报告模板有MyBatis专区）
- Go `func (receiver) Name()`、Rust `pub fn`、C# `public async Task<T>` 不支持
- **删除的符号不可见**（只看 `+` 行）—— 删除一个方法调用的影响无法追踪

**修复方案**：分层符号提取

```
Level 0: GitNexus detect_changes (最权威，优先使用)
    ↓ 补充
Level 1: AST解析 (tree-sitter，新增)
    ↓ 补充
Level 2: 正则提取 (当前实现，兜底)
```

```python
# 新增：ast_symbol_extractor.py

class AstSymbolExtractor:
    """基于tree-sitter的多语言符号提取器"""

    # 优先级0：直接用GitNexus的detect_changes结果
    # 优先级1：tree-sitter AST解析
    # 优先级2：正则（当前逻辑）

    def extract(self, file_path: str, diff_hunks: list) -> list[ChangedSymbol]:
        # 对每个hunk的added lines做AST解析
        # 对每个hunk的deleted lines做AST解析（标记为deleted）
        # 合并去重
        pass
```

**关键增强：追踪删除的符号**

```python
def _extract_deleted_symbols(self, diff: str) -> list[Symbol]:
    """提取被删除/修改签名的符号"""
    # 删除的方法调用 → 其调用方可能仍在调用旧签名
    # 删除的字段 → 引用该字段的代码会编译失败
    # 这对"谁还在引用旧接口"的影响分析至关重要
```

### 问题3：target选择12个上限 —— 大型MR覆盖不足

**现状**：排序后截断到12个target，其中只有前8个会获得context和impact查询。

**问题**：
- 排序仅靠关键词打分（controller +18, service +14, repository +10），无语义理解
- `detect_changes` 的结果权重(30)低于diff正则的 `container.symbol`(50)，但GitNexus的发现更可靠
- target列表在查询前就固定，如果context查询发现重要新线索也无法追加

**修复方案**：

```python
# 1. 提升detect_changes来源的权重
# 从30提升到45，接近container.symbol的50
DETECT_CHANGES_SOURCE_PRIORITY = 45  # was 30

# 2. 提升上限（依赖MCP持久连接解决性能问题）
MAX_GITNEXUS_TARGETS = 25  # was 12
MAX_GITNEXUS_CONTEXT_QUERIES = 15  # was 8
MAX_GITNEXUS_IMPACT_QUERIES = 15  # was 8

# 3. 动态target发现：context查询后追加新target
async def _adaptive_target_expansion(self, initial_targets, context_results):
    """根据context查询结果动态追加重要target"""
    expanded = list(initial_targets)

    for result in context_results:
        # 如果发现当前target调用了另一个重要模块，追加为target
        for outgoing in result.get("outgoing", []):
            target_name = outgoing.get("symbol")
            if target_name and target_name not in expanded:
                # 如果被调用方是controller/service级别，追加
                if self._is_high_priority_path(outgoing.get("file", "")):
                    expanded.append(target_name)

    return expanded[:MAX_GITNEXUS_TARGETS]
```

### 问题4：影响报告与实际开发决策脱节 —— 不够actionable

**现状问题**：
- `must_run_tests` 输出泛泛的"mvn test or ./gradlew test"，开发者无法直接复制执行
- 报告不区分"确认影响"（图中有直接链路）和"可能影响"（需要人工确认）
- 无历史回归关联："上次改这个文件，测试X/Y/Z失败了"
- 无配置文件影响分析：`application.yml` 变更完全不可见

**修复方案**：

#### 4.1 精确测试命令

```python
def _generate_specific_test_commands(self, impact_report, repo_path):
    """生成可复制执行的精确测试命令"""
    commands = []
    for test_scope in impact_report.test_scopes:
        # Java: mvn test -pl module -Dtest=TestClass
        if test_scope.test_file.endswith(".java"):
            module = self._infer_maven_module(test_scope.test_file, repo_path)
            class_name = os.path.splitext(os.path.basename(test_scope.test_file))[0]
            commands.append(f"mvn test -pl {module} -Dtest={class_name}")
        # Python: pytest path/to/test_file.py::TestClass::test_method
        elif test_scope.test_file.endswith(".py"):
            commands.append(f"pytest {test_scope.test_file} -v")
    return commands
```

#### 4.2 确认影响 vs 候选影响标记

```python
# 在ImpactPath中明确区分
class ImpactPath:
    path: list[str]
    confidence: str  # 新增: "confirmed" | "candidate" | "inferred"
    confirmation_reason: str  # 新增: 为什么是confirmed/candidate

    # confirmed: GitNexus图中有直接边连接
    # candidate: GitNexus图中有2跳以内路径，但无直接边
    # inferred: 基于命名约定/文件路径推断，无图数据支持
```

#### 4.3 配置文件影响分析

```python
def _analyze_config_impact(self, changed_files, repo_path):
    """分析配置文件变更的影响"""
    config_patterns = {
        "application.yml": "Spring配置",
        "application.properties": "Spring配置",
        ".env": "环境变量",
        "docker-compose.yml": "容器编排",
        "Dockerfile": "构建镜像",
    }

    config_changes = []
    for f in changed_files:
        basename = os.path.basename(f)
        if basename in config_patterns:
            # 检查哪些代码引用了这些配置项
            config_keys = self._extract_config_keys(os.path.join(repo_path, f))
            referencing_code = self._find_config_references(config_keys, repo_path)
            config_changes.append({
                "file": f,
                "type": config_patterns[basename],
                "changed_keys": config_keys,
                "affected_code": referencing_code,
            })

    return config_changes
```

### 问题5：cross_file_impact.py 的签名变更检测未流入影响报告

**现状**：`cross_file_impact.py` 有优秀的签名变更检测能力（参数数量变化、返回类型变化、throws变更），但这些结果只作为专家prompt中的hint，未流入ImpactReport。

**修复方案**：将签名变更检测结果集成到影响分析流程

```python
# gitnexus_impact_service.py — 在analyze()中调用cross_file_impact

def analyze(self, subject, runtime_settings):
    # ... existing flow ...

    # 新增：调用cross_file_impact提取签名变更
    from .cross_file_impact import CrossFileImpact
    cross_file = CrossFileImpact()
    sig_changes = cross_file.detect_signature_changes(
        subject.unified_diff, subject.changed_files
    )

    # 将签名变更添加到impact report的manual_verification
    for change in sig_changes:
        if change["type"] == "parameter_count_changed":
            report.manual_verification.append({
                "item": f"方法签名变更: {change['method']} 参数从{change['old_count']}变为{change['new_count']}",
                "reason": "调用方可能仍使用旧参数数量",
                "affected_callers": change.get("unchanged_callers", []),
                "priority": "high",
            })
```

### 问题6：影响分析无反馈闭环

**现状**：影响分析结果产出后，没有任何机制收集"这个影响预测是否准确"的反馈。

**修复方案**：在ImpactReport中增加反馈收集

```python
class ImpactReport:
    # ... existing fields ...

    # 新增：反馈收集
    feedback_tokens: list[FeedbackToken] = []  # 每个影响点附带一个token

class FeedbackToken:
    token_id: str          # 唯一ID
    impact_item: str       # 对应的影响项
    status: str            # "pending" | "confirmed" | "false_positive" | "irrelevant"
    confirmed_by: str      # 确认人
    confirmed_at: str      # 确认时间

# 前端：每个影响项旁加"确认/误报"按钮
# 后端API：POST /api/reviews/{id}/impact-feedback
```

---

## 三、影响分析优化总结

| 优化项 | 预期效果 | 优先级 | 依赖 |
|--------|---------|--------|------|
| MCP持久连接 | 去除18次进程创建开销，提升查询上限 | P0 | 无 |
| 提升target/query上限 | 大型MR覆盖更完整 | P0 | 依赖MCP持久连接 |
| tree-sitter AST符号提取 | 多语言覆盖、接口/注解/删除符号 | P1 | tree-sitter依赖 |
| 签名变更检测流入影响报告 | "方法签名变了，3个调用方没更新" | P1 | 无 |
| 精确测试命令 | 开发者可直接复制执行 | P1 | 无 |
| 确认影响 vs 候选影响标记 | 影响可信度一目了然 | P1 | 无 |
| 配置文件影响分析 | yml/properties变更的影响可见 | P2 | 无 |
| 动态target发现 | context查询后追加重要target | P2 | 依赖MCP持久连接 |
| 影响反馈闭环 | 积累数据提升未来预测准确率 | P2 | 前端UI改造 |
| detect_changes权重提升 | GitNexus发现优先于正则猜测 | P2 | 无 |

---

# 附录：两个功能的协作界面

虽然两个功能隔离，但它们应该有**轻量级的信息交换接口**，而不是完全互不通信：

```
┌─────────────────────┐          ┌─────────────────────┐
│   代码检视Agent      │          │   影响分析Agent       │
│                     │          │                     │
│  输入: diff + 上下文  │          │  输入: diff + GitNexus │
│  输出: findings/issues│         │  输出: ImpactReport   │
│                     │          │                     │
│  ← 接收: 影响摘要     │←— 只读 ──│  (产出一个精简的影响   │
│    (受影响模块+风险级) │          │   摘要供检视参考)     │
│                     │          │                     │
│  → 发送: 检视发现摘要  │── 只读 ──→│  ← 接收: 检视发现摘要  │
│    (哪些文件有问题)   │          │    (交叉引用影响链路)   │
└─────────────────────┘          └─────────────────────┘
```

**交换内容**：
1. 影响分析 → 检视：一个精简的 `ImpactSummary`（受影响模块列表 + 风险级别 + 关键影响链路），作为context注入专家prompt，**不驱动专家选择**（隔离原则）
2. 检视 → 影响分析：哪些文件被检视出问题，在影响报告中标记"已检视"/"未检视"状态

这种轻量级交换保持了功能隔离，又让两个Agent的产出互为补充。
