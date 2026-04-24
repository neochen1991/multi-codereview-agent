# Code Review Agent 质量优化建议：不漏检 × 不多检

> 基于业界最佳实践（BitsAI-CR、Ellipsis、CodeRabbit、Greptile、PR-Agent/Qodo 等）与本项目代码深度分析，提出针对性优化建议。

---

## 一、当前架构优势（应保留）

项目已具备多专家协同、LangGraph 管线、Finding→Issue 分层、置信度评分等良好基础，以下建议基于此框架做增量优化。

---

## 二、关键问题诊断与优化建议

### 问题 1：证据验证层形同虚设（漏检+多检的根源）

**现状**：`evidence_verification.py` 调用的三个工具是纯存根：

- `local_diff_tool`：仅检查 `changed_files` 是否非空，返回硬编码 score=0.82
- `coverage_diff_tool`：仅检查文件名含 "test"/"spec"，返回硬编码 score
- `schema_diff_tool`：仅检查文件名含 "migration"/".sql"，返回硬编码 score

这些工具**没有真正验证 issue 的证据**，只看了文件名，对证据可信度零影响。

**业界对标**：
- BitsAI-CR 的 ReviewFilter 阶段：用 LLM 判定每条评论是否准确（精度优先于召回）
- Ellipsis 的 LLM-as-judge：用另一个 LLM 对每条 finding 做质量评分后过滤
- Datadog：用 LLM 过滤 SAST 工具的误报

**优化建议**：

```
方案 A：LLM-as-False-Positive-Filter（推荐，立即可做）
在 evidence_verification 节点中，对每个 issue 调用 LLM 做二阶段审查：
- 输入：issue 的 title + summary + evidence + 对应 diff hunk
- 提示："给定以下代码变更和检视意见，判断该意见是否为真实问题。
         输出 JSON: {is_true_positive: bool, confidence: 0-1, reason: str}"
- 置信度低于阈值（如 0.7）的 issue 降级为 finding 或标记为疑似

方案 B：引入静态分析工具作为 RAG 上下文（中期）
在专家 review 前注入 Semgrep/CodeQL 扫描结果到 prompt 中：
- 利用 RAG 方式将 KBS 发现注入 LLM prompt（arXiv 2502.06633 证明最有效）
- KBS 高精度 + LLM 高覆盖率 = 互补
```

**涉及文件**：
- `backend/app/services/orchestrator/nodes/evidence_verification.py`
- `backend/app/services/tools/local_diff_tool.py`
- `backend/app/services/tools/coverage_diff_tool.py`
- `backend/app/services/tools/schema_diff_tool.py`
- `backend/app/services/evidence_verifier_service.py`

---

### 问题 2：专家 Prompt 过于简陋（漏检主因）

**现状**：专家 prompt 仅 10-15 行自然语言，例如 `correctness_business/prompt.md`：

```
你是正确性与业务专家。
你的核心职责：
- 判断业务规则、状态流转...
不要做的事：...
```

缺少：结构化输出要求、Chain-of-Thought 引导、少样本示例、负样本示例、置信度自评指令。

**业界对标**：
- Ellipsis："让 LLM 的问题更容易"——每个 Comment Generator 的 prompt 聚焦一个维度、含明确输出 schema
- BitsAI-CR：RuleChecker 使用结构化审查规则分类体系
- PR-Agent：每个工具有独立优化的 prompt，支持 `extra_instructions` 注入

**优化建议**：

```markdown
# 增强后的 prompt 结构（以 correctness_business 为例）

## 1. 角色 + 职责（已有，保留）
## 2. 审查步骤（Chain-of-Thought，新增）
请按以下步骤逐步分析：
  Step 1: 识别本次变更的业务意图（从方法名、注释、commit message 推断）
  Step 2: 检查状态流转是否完整（每个状态是否有合法前驱和后继）
  Step 3: 核对注释/TODO/接口承诺是否在代码中落地
  Step 4: 检查边界条件、空值、异常分支是否处理
  Step 5: 验证返回值语义与调用方期望是否一致

## 3. 必须检查的规则清单（新增，结构化）
- [ ] 状态机：新增/修改的状态是否有完整的状态流转路径
- [ ] 契约落地：TODO/注释承诺的行为是否有对应实现
- [ ] 异常分支：catch 块是否吞异常或返回语义错误
- [ ] 幂等性：重试场景下是否可能重复执行副作用
- [ ] 边界值：0、空集合、null、超长字符串等边界是否处理

## 4. 反模式（不要报告的，新增）
- 纯命名风格问题 → 交给 architecture_design
- 纯索引/SQL 细节 → 交给 database_analysis
- 纯日志格式 → 交给 architecture_design
- 只有推测没有代码证据 → 只能提"需验证"，不能断言

## 5. 输出格式（新增，强制 JSON Schema）
每个 finding 必须包含以下字段：
{
  "title": "问题一句话描述",
  "summary": "详细分析",
  "finding_type": "direct_defect | test_gap | risk_hypothesis | design_concern",
  "severity": "blocker | critical | high | medium | low",
  "confidence": 0.0-1.0,
  "evidence": ["代码行证据1", "代码行证据2"],
  "cross_file_evidence": ["跨文件证据"],
  "assumptions": ["假设前提，若为空则表示无假设"],
  "verification_needed": true/false,
  "remediation_suggestion": "修复建议",
  "file_path": "...",
  "line_start": 123,
  "matched_rules": ["状态机完整性", "契约落地"]
}

## 6. 置信度自评指南（新增）
- 0.9+：能在 diff 中直接看到缺陷代码（如 catch 吞异常、状态遗漏）
- 0.7-0.9：有强代码证据但需确认运行时上下文
- 0.5-0.7：有间接证据但存在合理反驳空间
- <0.5：纯推测，应标记 verification_needed=true
```

**涉及文件**：
- `backend/app/builtin_experts/correctness_business/prompt.md`
- `backend/app/builtin_experts/security_compliance/prompt.md`
- `backend/app/builtin_experts/performance_reliability/prompt.md`
- 其余 9 个专家的 `prompt.md`

---

### 问题 3：辩论机制名不副实（漏检+多检）

**现状**：`run_targeted_debate.py` 仅 25 行，只设 `needs_debate` 标志，**没有实际 LLM 辩论**：

```python
needs_debate = participant_count > 1 or confidence < 0.8
issue["status"] = "debating" if needs_debate else "open"
```

**业界对标**：
- BitsAI-CR：ReviewFilter 独立阶段用 LLM 过滤低质量评论
- Ellipsis：confidence threshold + LLM-as-judge 双层过滤
- Atlassian：ML 排序器训练于历史 review 数据

**优化建议**：

```
方案：实现真正的定向辩论（Targeted Debate）

当多个专家对同一代码位置有分歧时：
1. 收集所有 expert_views，提取共识点和分歧点
2. 构造辩论 prompt，让每个专家看到其他专家的观点
3. 让 LLM 扮演"裁判"角色：
   - 输入：所有专家观点 + 代码上下文
   - 输出：{consensus: str, dissent: str, final_verdict: "accept|reject|needs_human", confidence_adjustment: float}
4. 裁判结论更新 issue 的 confidence 和 status

伪代码：
for issue in conflicts:
    if len(participant_expert_ids) > 1:
        debate_result = llm_debate(
            expert_views=issue.expert_views,
            code_context=issue.evidence + issue.cross_file_evidence,
            debate_rounds=config.debate_rounds  # 默认1轮
        )
        issue.confidence = adjust_confidence(issue.confidence, debate_result)
        if debate_result.verdict == "reject":
            issue.status = "rejected_after_debate"
```

**涉及文件**：
- `backend/app/services/orchestrator/nodes/run_targeted_debate.py`
- `backend/app/services/llm_chat_service.py`（增加 debate 专用调用）

---

### 问题 4：Issue 去重依赖 Token 匹配（多检+漏检）

**现状**：`_is_same_problem_type()` 使用 token 交集 >=2 判断是否同一问题。问题：
- 不同问题提到"异常处理" → 误合并
- 同一问题一个用"空指针"另一个用"NPE" → 漏合并
- 中文/英文同义词无法匹配

**业界对标**：
- Greptile：构建代码图谱，基于代码位置+语义做去重
- CodeRabbit：向量数据库做语义相似度检索

**优化建议**：

```
方案 A：基于语义嵌入的去重（推荐）
1. 对每个 finding 的 title+summary 生成 embedding
2. 同一文件位置的 findings 之间计算余弦相似度
3. 相似度 > 0.85 视为同一问题，合并

方案 B：LLM 辅助去重（轻量替代）
对同一文件位置的 findings 组，调用 LLM：
"以下 N 条检视意见是否描述了同一个代码问题？
 输出: {same_issue: bool, merged_title: str}"

方案 C：增强 token 匹配（最小改动）
1. 扩展同义词表：{"空指针": "NPE", "null": "空值", ...}
2. 加入 finding_type 权重：finding_type 不同的不合并
3. 加入 severity 一致性检查：severity 差异 >= 2 级的不合并
```

**涉及文件**：
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`（`_is_same_problem_type`, `_build_problem_token_set`）

---

### 问题 5：Expert 路由过于简单（漏检主因）

**现状**：`route_experts.py` 仅基于 2 个 risk_hint 做补充：

```python
if "security_surface" in risk_hints: selected.append("security_compliance")
if "database_migration" in risk_hints: selected.append("performance_reliability")
```

MainAgent 的专家选择依赖单次 LLM 调用，若 LLM 遗漏某类风险，该专家不会被激活。

**业界对标**：
- Ellipsis：每个维度都有独立的 Comment Generator，保证覆盖
- BitsAI-CR：RuleChecker 对结构化规则分类体系逐一检查
- Qodo v2：多 Agent 并行审查不同维度

**优化建议**：

```
方案 A：规则兜底路由（推荐，最小改动）
在 route_experts 节点中加入基于 diff 内容的确定性规则：

SECURITY_KEYWORDS = {"auth", "password", "token", "secret", "encrypt", "decrypt",
                     "permission", "role", "tenant", "sql", "inject", "xss"}
DATABASE_KEYWORDS = {"sql", "query", "index", "migration", "schema", "transaction", "ddl"}
MQ_KEYWORDS = {"mq", "kafka", "rabbitmq", "message", "consumer", "producer", "queue"}
REDIS_KEYWORDS = {"redis", "cache", "ttl", "lua"}
FRONTEND_KEYWORDS = {".tsx", ".jsx", ".vue", ".css", "accessibility", "aria-"}

for keyword_set, expert_id in [
    (SECURITY_KEYWORDS, "security_compliance"),
    (DATABASE_KEYWORDS, "database_analysis"),
    (MQ_KEYWORDS, "mq_analysis"),
    (REDIS_KEYWORDS, "redis_analysis"),
    (FRONTEND_KEYWORDS, "frontend_accessibility"),
]:
    if any(kw in diff_text_lower for kw in keyword_set) and expert_id not in selected:
        selected.append(expert_id)

方案 B：保底全量 + 精简 prompt（激进）
所有 12 个专家全部激活，但通过 prompt 控制每个专家只报告自己维度的问题。
不相关的专家自然不会产出 finding。这样保证零漏检，代价是 token 消耗增加。
```

**涉及文件**：
- `backend/app/services/orchestrator/nodes/route_experts.py`
- `backend/app/services/main_agent_prompt_builder.py`

---

### 问题 6：仅支持 Java 质量信号提取（漏检主因）

**现状**：`JavaQualitySignalExtractor` 仅处理 `.java` 文件。Python/Go/TypeScript 等语言无确定性预分析。

**优化建议**：

```
实现 Language-Agnostic Quality Signal Extractor：

1. 提取通用信号（与语言无关）：
   - exception_swallowed: catch/except 块为空或仅 print/log
   - comment_contract_unimplemented: TODO/FIXME/HACK 标记
   - magic_value_literal: 硬编码数字/字符串在条件判断中
   - dead_code_after_return: return 后仍有代码

2. 按语言扩展：
   Python: except: pass, bare except, mutable default args
   Go: unchecked error return, goroutine leak
   TypeScript: any type, non-null assertion, missing error boundary

3. 这些信号注入到专家 prompt 中，让专家优先关注已检测到的风险点
```

**涉及文件**：
- `backend/app/services/java_quality_signal_extractor.py`
- `backend/app/services/code_observation_extractor.py`

---

### 问题 7：没有反馈飞轮（长期质量无法提升）

**现状**：`feedback_learner_service.py` 存在但未与检视质量闭环。没有从人工标注中学习哪些类型的 finding 是误报/漏检。

**业界对标**：
- BitsAI-CR 的 Data Flywheel：持续从 code style guide 挖掘规则，从已解决 review comment 构建训练集
- CodeRabbit Knowledge Base：从历史 review 中积累学习
- Greptile：从工程师的 PR comment 中推断编码标准

**优化建议**：

```
1. 在前端增加"误报标记"功能：
   - 用户可以对 issue 标记"误报"并填写原因
   - 标记数据存入 feedback_labels 表

2. 定期分析误报模式：
   - 哪个 expert 的误报率最高？
   - 哪种 finding_type 最容易误报？
   - 哪些 token/关键词触发了误报？

3. 用误报模式动态调整：
   - 高误报专家的 confidence 加权降低
   - 高误报 finding_type 的置信度阈值提升
   - 常见误报关键词加入 NON_CODE_REVIEW_SCOPE_TOKENS
```

**涉及文件**：
- `backend/app/services/feedback_learner_service.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`（读取误报模式调整阈值）
- `frontend/src/components/review/IssueDetailPanel.tsx`（增加误报标记 UI）

---

### 问题 8：Judge 逻辑缺乏 LLM 推理（多检主因）

**现状**：`judge_and_merge.py` 是纯规则逻辑（if-else 链），不看代码内容，只看元数据字段。例如：

```python
if finding_type == "risk_hypothesis" and not direct_evidence:
    if speculative_issue and confidence <= 0.5:
        status = "needs_verification"
```

这无法判断"这个 risk_hypothesis 是否有道理"。

**优化建议**：

```
在 judge_and_merge 阶段对低置信度 issue 加入 LLM 裁决：

if confidence < 0.7 and finding_type in ("risk_hypothesis", "design_concern"):
    llm_verdict = llm_judge(
        title=issue.title,
        summary=issue.summary,
        evidence=issue.evidence,
        diff_hunk=对应代码片段,
        prompt="这是一个低置信度检视意见。请判断：
                1. 该意见是否指出了真实风险？
                2. 证据是否充分？
                3. 建议状态：accept/reject/needs_human
                输出JSON: {is_real_issue, evidence_sufficient, recommendation, reasoning}"
    )
    if llm_verdict.recommendation == "reject":
        issue.status = "rejected_by_judge"
        issue.resolution = "llm_judge_rejected"
        continue  # 不进入最终报告
```

**涉及文件**：
- `backend/app/services/orchestrator/nodes/judge_and_merge.py`
- `backend/app/services/llm_chat_service.py`

---

### 问题 9：跨文件影响分析浮于表面

**现状**：`cross_file_impact.py` 仅生成文字提示（"当前改动会联动 N 个关联文件"），不做实际调用链/依赖分析。

**业界对标**：
- Greptile：构建完整代码图谱（函数、类、依赖关系），查询跨文件影响
- Tanagram：三种图（词汇、引用、依赖），确定性查询+选择性 LLM

**优化建议**：

```
方案 A：AST 级跨文件分析（推荐，针对 Java/Python）
1. 使用 tree-sitter 解析变更文件和关联文件的 AST
2. 提取函数签名变更 → 检查调用方是否同步修改
3. 提取接口/契约变更 → 检查实现类是否同步修改
4. 结果作为 cross_file_evidence 注入专家 prompt

方案 B：基于仓库搜索的轻量替代
1. 对变更的 public 方法/类名，在仓库中搜索引用
2. 若引用方不在 changed_files 中，标记为潜在跨文件风险
3. 已有 repository_context_service 可复用，增加 "callers not changed" 信号
```

**涉及文件**：
- `backend/app/services/cross_file_impact.py`
- `backend/app/services/repository_context_service.py`

---

### 问题 10：专家产出 Finding 缺少强制结构化

**现状**：Finding 模型有字段定义，但 LLM 输出解析可能不完整。缺少 `matched_rules`（匹配的审查规则）、`normalized_issue_type`（归一化问题类型）等字段的强制填写。

**优化建议**：

```
1. 在 expert prompt 中强制要求输出 JSON 数组，每条 finding 必须包含：
   - matched_rules: 命中了哪些审查规则（从 review_spec.md 的规则清单中选）
   - normalized_issue_type: 从预定义枚举中选择

2. 在 review_runner 中对 LLM 输出做 schema 校验：
   - 缺少必填字段 → 补为默认值 + 标记 confidence 降低
   - severity 不在枚举内 → 降为 "medium"
   - confidence 未填写 → 根据 finding_type 推算默认值

3. 预定义 normalized_issue_type 枚举：
   exception_swallowed, state_transition_incomplete, contract_unimplemented,
   sql_injection_risk, missing_auth_check, n_plus_one_query,
   missing_test, magic_value, naming_violation, ...
```

**涉及文件**：
- `backend/app/services/review_runner.py`（LLM 输出解析与校验）
- `backend/app/domain/models/finding.py`（增加字段约束）
- 所有 `backend/app/builtin_experts/*/prompt.md`（增加输出格式要求）

---

## 三、优先级排序

| 优先级 | 优化项 | 影响面 | 实施难度 | 预期收益 |
|--------|--------|--------|----------|----------|
| **P0** | #1 证据验证层升级（LLM-as-Judge） | 不多检 | 中 | 减少 30-50% 误报 |
| **P0** | #2 专家 Prompt 增强 | 不漏检+不多检 | 低 | 提升 finding 质量基线 |
| **P0** | #5 Expert 路由规则兜底 | 不漏检 | 低 | 防止关键专家被遗漏 |
| **P1** | #8 Judge 加入 LLM 推理 | 不多检 | 中 | 低置信度 issue 质量提升 |
| **P1** | #3 实现定向辩论 | 不多检+不漏检 | 中 | 多专家冲突时决策质量 |
| **P1** | #10 强制结构化输出 | 不漏检+不多检 | 低 | 下游处理质量保障 |
| **P2** | #4 Issue 去重升级 | 不多检 | 中 | 减少重复 issue |
| **P2** | #6 多语言质量信号 | 不漏检 | 中 | 非 Java 项目覆盖 |
| **P2** | #9 跨文件影响分析 | 不漏检 | 高 | 跨文件问题检出 |
| **P3** | #7 反馈飞轮 | 长期质量 | 高 | 持续进化能力 |

---

## 四、核心原则总结

1. **精度优先于召回**（BitsAI-CR 的核心教训）——一条误报比一条漏检更伤害开发者信任
2. **分解问题**（Ellipsis 的核心原则）——每个专家/Agent 只聚焦一个维度，prompt 小而精准
3. **多层过滤**——Expert → Finding → Issue → Judge → LLM-as-Judge，每层过滤掉一层噪音
4. **确定性规则 + LLM 推理互补**——用规则保底（不漏检），用 LLM 做上下文推理（不多检）
5. **数据飞轮**——从误报反馈中持续学习，阈值和规则动态调整

---

## 五、业界参考来源

| 项目/论文 | 核心启发 |
|-----------|----------|
| BitsAI-CR (ByteDance, arXiv 2501.15134) | 4 阶段管线 + ReviewFilter + 精度优先 + Data Flywheel |
| Ellipsis (ellipsis.dev) | 多 Comment Generator 并行 + confidence 过滤 + LLM-as-judge |
| CodeRabbit (coderabbit.ai) | Knowledge Base + LanceDB 向量检索 + Pre-Merge Checks |
| Greptile (greptile.com) | 代码图谱 + 跨文件影响 + 团队学习 |
| PR-Agent / Qodo v2 (CodiumAI) | 多 Agent 审查 + Rule System + extra_instructions |
| Amazon CodeGuru | 程序分析 + ML + 低误报设计目标 |
| Tanagram | 确定性图查询 + 选择性 LLM（混合架构） |
| arXiv 2502.06633 | KBS+LLM 混合：RAG 注入静态分析结果最有效 |
| Atlassian Comment Ranker | ML 排序器过滤低质量评论，减少 30% PR 周期 |
| Datadog LLM Filter | 用 LLM 过滤 SAST 误报 |
