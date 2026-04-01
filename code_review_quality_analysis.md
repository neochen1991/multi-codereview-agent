# 多Agent代码检视质量提升手段分析

## 概述

本项目是一个基于FastAPI + LangGraph + React的多专家协同代码审核系统。通过深入分析代码实现，本文梳理了系统在提升代码检视质量方面的多种手段。

---

## 一、多专家协作架构 (Multi-Agent Orchestration)

### 1.1 专业化专家分工

系统预置了6类专业审核专家：

| 专家ID | 职责 | 关注维度 |
|--------|------|----------|
| `correctness_business` | 业务逻辑正确性 | 业务流程、领域模型、业务规则 |
| `correctness_technical` | 技术实现正确性 | 算法、数据结构、API使用 |
| `security_compliance` | 安全合规检查 | 注入攻击、敏感数据、越权访问 |
| `performance_reliability` | 性能与可靠性 | 性能瓶颈、并发安全、资源泄漏 |
| `maintainability_code_health` | 可维护性与代码健康 | 代码异味、复杂度、测试覆盖 |
| `test_verification` | 测试验证 | 测试完整性、边界条件、Mock合理性 |

### 1.2 主Agent智能派工

`MainAgentService`负责基于MR信息和专家画像确定参与审核的专家：

```python
# 核心流程
selection_plan = self.main_agent_service.select_review_experts(
    review.subject,           # MR/PR信息
    enabled_experts,          # 已启用的专家
    effective_runtime_settings,  # 运行时配置
    requested_expert_ids=requested_selected_ids,  # 用户指定的专家
)
```

派工策略考虑因素：
- 文件变更类型（新增/修改/删除）
- 文件路径模式（Controller/Service/Repository等）
- 专家适用文件类型匹配
- 代码语义分析（命名、注解、调用关系）

### 1.3 动态专家跳过机制

当变更未命中专家的有效审查线索时，系统会动态跳过该专家：

```python
if not bool(command.get("routeable", True)):
    skip_reason = str(command.get("skip_reason") or "当前变更未命中该专家的有效审查线索")
    skipped_experts.append({
        "expert_id": expert_id,
        "expert_name": expert.name_zh,
        "reason": skip_reason,
    })
```

---

## 二、知识规则驱动的精准审查

### 2.1 规则预筛查机制

`KnowledgeRuleScreeningService` 对专家绑定的全部规则做预筛查：

```python
def screen(
    self,
    expert_id: str,
    review_context: dict[str, object],
    runtime_settings: RuntimeSettings | None = None,
    analysis_mode: str = "standard",
) -> dict[str, object]:
    rules = self._repository.list_for_expert(expert_id)
    # 选择筛查策略：LLM辅助 or 启发式
    if runtime_settings.rule_screening_mode == "llm":
        return self._screen_with_llm(...)
    return self._screen_with_heuristic(expert_id, review_context, rules)
```

### 2.2 启发式筛查维度

```python
def _screen_rule(
    self,
    rule: KnowledgeReviewRule,
    signal_payload: dict[str, object],
) -> dict[str, object]:
    # 1. 关键词匹配（代码片段、文件路径）
    matched_terms = [t for t in rule.keywords if t in signal_payload["text_blob"]]
    
    # 2. 文件路径模式匹配
    path_matched = any(
        fnmatch.fnmatch(file_path, pattern) 
        for pattern in rule.path_patterns
    )
    
    # 3. 变更类型匹配（新增/修改/删除）
    change_type_matched = rule.change_types & signal_payload["change_types"]
    
    # 4. 代码语义信号（命名、注解、调用关系）
    semantic_signals = self._extract_semantic_signals(signal_payload["snippet"])
    semantic_matched = bool(rule.semantic_patterns & semantic_signals)
```

### 2.3 筛查结果分级

| 分级 | 决策 | 说明 |
|------|------|------|
| `must_review` | 必须审查 | 高置信度匹配，规则必须进入本轮审查 |
| `possible_hit` | 可能命中 | 中等置信度，建议进入审查 |
| `no_hit` | 未命中 | 低置信度，本轮跳过该规则 |

### 2.4 LLM辅助筛查

当启发式筛查无法确定时，启用LLM辅助判断：

```python
def _screen_with_llm(
    self,
    *,
    expert_id: str,
    rules: list[KnowledgeReviewRule],
    review_context: dict[str, object],
    runtime_settings: RuntimeSettings,
) -> dict[str, object] | None:
    # 分批处理，每批4-24条规则
    batch_size = max(4, min(24, int(runtime_settings.rule_screening_batch_size or 12)))
    
    # 构建LLM Prompt
    system_prompt = self._build_screening_system_prompt(expert_id)
    user_prompt = self._build_screening_user_prompt(rules, review_context)
    
    # 调用LLM判断每条规则的相关性
    response = self._llm.complete_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        resolution=resolution,
        timeout_seconds=timeout_seconds,
    )
    
    # 解析LLM返回的决策结果
    return self._parse_screening_result(response, rules)
```

---

## 三、深度上下文组装

### 3.1 Java DDD 上下文组装器

`JavaDddContextAssembler` 为Java项目补充固定结构的审查上下文：

```python
def build_context_pack(
    self,
    service: RepositoryContextService,
    *,
    file_path: str,
    line_start: int,
    primary_context: dict[str, Any],
    related_files: list[str],
    symbol_contexts: list[dict[str, Any]],
    excerpt: str,
) -> dict[str, object]:
    return {
        # 当前类上下文
        "current_class_context": self._build_current_class_context(...),
        # 父接口/抽象类上下文
        "parent_contract_contexts": self._find_parent_contracts(...),
        # 调用者上下文（谁调用了当前代码）
        "caller_contexts": self._find_callers(...),
        # 被调用者上下文（当前代码调用了谁）
        "callee_contexts": self._find_callees(...),
        # 领域模型上下文
        "domain_model_contexts": self._find_domain_models(...),
        # 事务上下文
        "transaction_context": self._find_transaction_context(...),
        # 持久化上下文
        "persistence_contexts": self._find_persistence_contexts(...),
    }
```

### 3.2 调用链分析

**查找调用者** (`_find_callers`):
- 从当前方法名提取符号
- 搜索代码仓中引用该符号的位置
- 过滤出 Controller/Application/Listener 等调用方路径
- 推断调用者类型（HTTP入口、消息监听、定时任务等）

**查找被调用者** (`_find_callees`):
- 从代码片段提取依赖令牌（Repository/Service/Mapper/Gateway等后缀）
- 从方法调用提取被调用方法名
- 搜索代码仓中这些符号的定义
- 推断被调用者类型（数据访问、领域服务、外部网关等）

### 3.3 领域模型关联

```python
def _find_domain_models(
    self,
    service: RepositoryContextService,
    *,
    file_path: str,
    primary_context: dict[str, Any],
    excerpt: str,
) -> list[dict[str, Any]]:
    # 提取领域模型令牌（Aggregate/Entity/ValueObject/Event等后缀）
    candidates = self._extract_dependency_tokens(
        f"{primary_context.get('snippet') or ''}\n{excerpt}",
        suffixes=("Aggregate", "Entity", "ValueObject", "Event", "DomainEvent"),
    )
    # 搜索领域模型定义，要求路径包含 domain/aggregate/valueobject/event
    # 推断领域模型类型（聚合根、实体、值对象、领域事件）
```

### 3.4 事务边界分析

```python
def _find_transaction_context(
    self,
    file_path: str,
    current_class_context: dict[str, Any],
    caller_contexts: list[dict[str, Any]],
    callee_contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    # 检查当前类和方法的 @Transactional 注解
    # 分析事务传播行为（REQUIRED/REQUIRES_NEW/NESTED等）
    # 检查调用者事务上下文（调用方是否已开启事务）
    # 检查被调用者事务行为（被调用方如何参与事务）
    # 识别潜在事务问题（长事务、事务传播冲突、事务边界不一致等）
```

---

## 四、置信度驱动的Issue升级机制

### 4.1 分层置信度模型

```
┌─────────────────────────────────────────────────────────────┐
│                     Issue 置信度计算                         │
├─────────────────────────────────────────────────────────────┤
│  base_weighted_confidence                                   │
│  = Σ(confidence_i × weight_i) / Σ(weight_i)                  │
│                                                             │
│  权重映射：                                                  │
│  • direct_defect    → 1.00  (直接缺陷，最高权重)              │
│  • test_gap         → 0.80  (测试覆盖缺口)                   │
│  • risk_hypothesis  → 0.65  (风险假设，需验证)                │
│  • design_concern   → 0.55  (设计关注，偏建议)                │
├─────────────────────────────────────────────────────────────┤
│  + consensus_bonus                                            │
│  = min(0.08, 0.03 + 0.02 × (participant_count - 2))          │
│  (多专家一致时增加，最多+0.08)                                │
├─────────────────────────────────────────────────────────────┤
│  + evidence_bonus                                             │
│  = min(0.06, min(evidence_count, 4) × 0.01                   │
│        + (0.02 if direct_evidence else 0))                   │
│  (证据越充分加分越多，直接证据额外+0.02)                       │
├─────────────────────────────────────────────────────────────┤
│  + verification_bonus (预留，当前为0)                         │
├─────────────────────────────────────────────────────────────┤
│  - hypothesis_penalty                                         │
│  = 0.05  (若全为risk_hypothesis)                             │
│    + 0.03 (若单专家)                                         │
│    + 0.02 (若证据信号≤3)                                    │
│  ≤ 0.12 (纯假设无直接证据时最高扣0.12)                         │
├─────────────────────────────────────────────────────────────┤
│  final_confidence                                             │
│  = round(clamp(0.01, 0.99, base + bonus - penalty), 2)      │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Issue升级治理规则

```python
# _classify_issue_candidate 函数实现的多层过滤

def _classify_issue_candidate(items, config):
    # 1. 低风险提示过滤
    if highest_severity == "low" and config.suppress_low_risk_hint_issues:
        return {
            "decision": "保留为finding",
            "reason": "整体风险较低，仅保留在findings中提示，不升级为issue"
        }
    
    # 2. 非代码检视范围过滤
    if non_code_review_scope and not direct_evidence:
        return {
            "decision": "不升级为issue",
            "reason": "追问业务背景、需求说明或产品上下文，不属于代码检视应升级处理的issue"
        }
    
    # 3. 优先级阈值检查
    min_priority_level = config.issue_min_priority_level  # 默认P2
    if highest_priority_rank > min_priority_rank:
        return {
            "decision": "仅保留为finding",
            "reason": f"最高仅达到{highest_severity}，低于配置的issue升级阈值{min_priority_level}"
        }
    
    # 4. 设计关注项过滤
    if finding_types <= {"design_concern"} and highest_severity in {"low", "medium"}:
        return {
            "decision": "保留为finding",
            "reason": "仅属于设计关注或建议项，缺少需要进入debate的直接风险证据"
        }
    
    # 5. 提示性问题过滤（多条件综合判断）
    if (highest_severity == "medium" 
        and not direct_evidence 
        and participant_count <= 1 
        and all_need_verification
        and average_confidence < config.hint_issue_confidence_threshold  # 默认0.85
        and evidence_strength <= config.hint_issue_evidence_cap  # 默认2
        and hint_like):  # 包含命名、注释、风格等提示性关键词
        return {
            "decision": "保留为finding",
            "reason": "更偏命名、注释、风格、日志补充等提示性建议，证据较弱且置信度未达到升级阈值"
        }
    
    # 6. P级置信度阈值检查
    priority_confidence_threshold = _priority_confidence_threshold(config, priority_label)
    if average_confidence < priority_confidence_threshold:
        return {
            "decision": "保留为finding",
            "reason": f"已达到{priority_label}，但平均置信度{average_confidence:.2f}低于阈值{priority_confidence_threshold:.2f}"
        }
    
    # 通过所有过滤，允许升级为issue
    return None
```

### 4.3 置信度解释与可追溯性

每个issue包含详细的置信度分解：

```python
{
    "confidence": 0.92,
    "confidence_breakdown": {
        "base_weighted_confidence": 0.78,  # 基础加权分
        "consensus_bonus": 0.05,            # 多专家一致性加分
        "evidence_bonus": 0.06,            # 证据链加分
        "verification_bonus": 0.0,         # 验证加分（预留）
        "hypothesis_penalty": 0.0,           # 纯假设扣分
        "final_confidence": 0.92
    },
    "participant_count": 3,      # 参与专家数
    "evidence_signal_count": 12  # 证据信号数
}
```

---

## 五、定向辩论与冲突解决

### 5.1 辩论触发条件

```python
def run_targeted_debate(state: ReviewState) -> ReviewState:
    for conflict in next_state.get("conflicts", []):
        participant_count = len(issue.get("participant_expert_ids", []))
        confidence = float(issue.get("confidence", 0.0))
        
        # 辩论触发条件：多专家命中 OR 置信度低于阈值
        needs_debate = participant_count > 1 or confidence < 0.8
        
        issue["needs_debate"] = needs_debate
        issue["status"] = "debating" if needs_debate else "open"
```

### 5.2 辩论价值与机制

| 场景 | 辩论价值 |
|------|----------|
| 多专家命中 | 不同视角碰撞，发现潜在边界情况 |
| 置信度低 | 通过辩论澄清疑问，提升确定性 |
| 观点冲突 | 暴露风险的不同侧面，形成全面判断 |
| 证据不足 | 通过辩论识别需要补充验证的假设 |

---

## 六、设计一致性检查

### 6.1 Skill机制扩展

```yaml
# extensions/skills/design-consistency-check/SKILL.md

Purpose: 检查详细设计文档与代码实现是否一致

When To Use:
  - 当前专家为 correctness_business
  - 本次review绑定了design_spec类型文档
  - 改动命中 service/usecase/handler/workflow/transformer/output 等业务实现文件

Required Tools:
  - diff_inspector          # Diff深度分析
  - repo_context_search     # 代码仓上下文检索
  - design_spec_alignment   # 设计规范对齐检查

Rules:
  - 不允许脱离设计文档做需求猜测
  - 不允许只根据命名推断实现完成度
  - 证据不足时只能输出"待验证风险"
  - 必须同时对照diff、源码仓上下文和设计文档结构化结果

Output Contract:
  - design_alignment_status          # 对齐状态
  - matched_implementation_points  # 已匹配的实现点
  - missing_implementation_points    # 缺失的实现点
  - extra_implementation_points      # 额外的实现点
  - design_conflicts                 # 设计冲突
```

### 6.2 检查维度

| 维度 | 检查内容 |
|------|----------|
| API定义一致性 | URL路径、HTTP方法、Request/Response DTO字段 |
| 入参/出参字段 | 字段名、类型、必填约束、校验规则 |
| 表结构定义 | 表名、字段、索引、外键约束 |
| 业务逻辑时序 | 步骤顺序、条件分支、循环处理 |
| 性能要求 | 响应时间、并发量、资源限制 |
| 安全要求 | 鉴权、敏感数据脱敏、操作审计 |

---

## 七、分层Diff上下文策略

### 7.1 三层视角设计

```
┌─────────────────────────────────────────────────────────────────┐
│ 第一层：前端 Diff Preview                                        │
├─────────────────────────────────────────────────────────────────┤
│ • 展示完整的 ReviewSubject.unified_diff                         │
│ • 用于人工浏览和核对原始变更                                      │
│ • 不做任何截断或摘要处理                                          │
└─────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────┐
│ 第二层：主 Agent Prompt                                          │
├─────────────────────────────────────────────────────────────────┤
│ • 主要业务文件：提供完整diff                                      │
│ • 其他变更文件：提供摘要（文件路径、变更类型、影响行数）            │
│ • 候选hunk列表：供专家选择重点关注                                │
│                                                                 │
│ 设计目的：让主Agent掌握全局信号，但不被超长diff冲垮token限制        │
└─────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────┐
│ 第三层：专家 Agent Prompt                                        │
├─────────────────────────────────────────────────────────────────┤
│ • 目标文件：完整diff（确保专家看到完整上下文，避免误判）            │
│ • 其他变更文件：摘要（了解相关变更）                              │
│ • 目标hunk：定位具体变更位置                                      │
│ • 代码仓上下文：跨文件定义/引用关系                               │
│ • 运行时工具结果：diff深度分析、设计规范检查等                      │
│                                                                 │
│ 设计目的：确保专家有足够上下文做出准确判断                          │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 设计动机与价值

| 层级 | 核心问题 | 解决策略 | 价值 |
|------|----------|----------|------|
| 前端预览 | 人工核对原始变更 | 展示完整diff | 确保变更透明度 |
| 主Agent | 掌握全局信号但token有限 | 核心业务文件完整+其他摘要 | 平衡全局视野与token限制 |
| 专家Agent | 避免局部excerpt误判 | 目标文件完整diff+上下文 | 确保判断准确性 |

### 7.3 Diff摘录服务

`DiffExcerptService` 提供精细的diff操作能力：

```python
class DiffExcerptService:
    def extract_file_diff(self, unified_diff: str, file_path: str) -> str:
        """提取某个文件在unified diff中的完整diff block"""
        
    def list_hunks(self, unified_diff: str, file_path: str) -> list[dict]:
        """列出某个文件在diff中的全部hunk"""
        
    def find_best_hunk(self, unified_diff: str, file_path: str, target_line: int) -> dict | None:
        """根据目标行号找到最接近的hunk"""
        
    def extract_excerpt(self, unified_diff: str, file_path: str, target_line: int, 
                       *, context_lines: int = 2) -> str:
        """提取目标行附近最相关的diff代码片段"""
```

---

## 八、运行时工具扩展

### 8.1 工具类型与职责

| 工具ID | 名称 | 职责 | 输出 |
|--------|------|------|------|
| `diff_inspector` | Diff深度分析器 | 分析diff中的代码变更模式、识别潜在风险点 | 变更模式报告、风险标记 |
| `repo_context_search` | 代码仓上下文检索 | 跨文件搜索定义和引用 | 符号定义、引用位置、依赖关系 |
| `design_spec_alignment` | 设计规范对齐检查 | 比对代码实现与设计文档 | 对齐状态、缺失点、冲突点 |
| `local_diff` | 本地Diff对比 | 对比本地文件版本 | 差异报告 |
| `schema_diff` | 数据库Schema对比 | 对比数据库结构变更 | Schema变更报告 |
| `coverage_diff` | 测试覆盖率对比 | 对比测试覆盖率变化 | 覆盖率变化报告 |

### 8.2 工具执行模式

```python
# ReviewToolGateway 动态调用工具
class ReviewToolGateway:
    def execute(self, tool_id: str, params: dict) -> ToolResult:
        tool_config = self._load_tool_config(tool_id)
        
        # Subprocess方式执行
        if tool_config.execution_mode == "subprocess":
            return self._execute_subprocess(tool_config, params)
        
        # Python函数方式执行（内置工具）
        if tool_config.execution_mode == "python_function":
            return self._execute_python_function(tool_config, params)
        
        # HTTP API方式执行（外部服务）
        if tool_config.execution_mode == "http_api":
            return self._execute_http_api(tool_config, params)

    def _execute_subprocess(self, tool_config, params):
        # stdin写入JSON参数
        # stdout读取JSON结果
        # stderr捕获错误
        # 超时控制
```

### 8.3 工具结果集成

工具结果被整合到专家Prompt中：

```python
# 专家Prompt结构
expert_prompt = f"""
## 代码变更
{file_diff}

## 代码仓上下文
{repo_context}

## 工具分析结果
{tool_results}

## 审查任务
请基于以上信息，对代码变更进行审查，输出findings。
"""
```

---

## 九、反馈学习机制

### 9.1 反馈采集

```python
# FeedbackLabel 模型
class FeedbackLabel:
    review_id: str          # 审核ID
    issue_id: str           # 问题ID
    expert_id: str          # 专家ID
    feedback_type: str      # 反馈类型：confirm / reject / unclear
    severity_adjustment: int # 严重度调整：-2 ~ +2
    comment: str            # 人工评语
    created_at: datetime    # 创建时间
```

### 9.2 反馈类型与处理

| 反馈类型 | 含义 | 系统动作 |
|----------|------|----------|
| `confirm` | 确认问题有效 | 增加该专家/规则权重，类似问题提升置信度 |
| `reject` | 拒绝问题（误报） | 降低该专家/规则权重，记录为假阳性 |
| `unclear` | 不清晰 | 标记为需要更多上下文，优化Prompt |

### 9.3 闭环优化

```python
class FeedbackLearnerService:
    def process_feedback(self, feedback: FeedbackLabel):
        # 1. 更新专家权重
        self._update_expert_weights(feedback)
        
        # 2. 更新规则权重
        self._update_rule_weights(feedback)
        
        # 3. 更新置信度模型参数
        self._update_confidence_model(feedback)
        
        # 4. 生成优化建议
        suggestions = self._generate_suggestions(feedback)
        
        return suggestions
    
    def _update_expert_weights(self, feedback):
        # 根据反馈调整专家在特定场景下的权重
        if feedback.feedback_type == "confirm":
            self.expert_weights[feedback.expert_id]["success_count"] += 1
        elif feedback.feedback_type == "reject":
            self.expert_weights[feedback.expert_id]["false_positive_count"] += 1
```

---

## 十、总结：质量提升手段分层

| 层级 | 手段 | 核心价值 | 实现文件 |
|------|------|----------|----------|
| **架构层** | 多专家协作 | 专业分工，避免单一视角盲区 | `expert_registry.py`, `main_agent_service.py` |
| **策略层** | 知识规则驱动 | 精准匹配审查规则，减少噪音 | `knowledge_rule_screening_service.py` |
| **数据层** | 深度上下文组装 | 提供充足语义信息支撑判断 | `java_ddd_context_assembler.py`, `repository_context_service.py` |
| **算法层** | 置信度计算 | 量化问题确定性，优先高置信度问题 | `detect_conflicts.py` |
| **协作层** | 定向辩论 | 多视角碰撞，提升问题准确性 | `run_targeted_debate.py` |
| **验证层** | 设计一致性检查 | 对齐设计文档，确保实现符合预期 | `extensions/skills/design-consistency-check/` |
| **工程层** | 分层Diff策略 | 平衡token限制与上下文完整性 | `diff_excerpt_service.py` |
| **学习层** | 反馈闭环 | 持续优化专家权重和规则准确性 | `feedback_learner_service.py` |

---

## 附录：关键代码文件索引

| 文件路径 | 职责 |
|----------|------|
| `backend/app/services/orchestrator/graph.py` | LangGraph状态机定义 |
| `backend/app/services/orchestrator/nodes/detect_conflicts.py` | Issue置信度计算 |
| `backend/app/services/orchestrator/nodes/run_targeted_debate.py` | 定向辩论触发 |
| `backend/app/services/expert_registry.py` | 专家注册与管理 |
| `backend/app/services/java_ddd_context_assembler.py` | Java DDD上下文组装 |
| `backend/app/services/repository_context_service.py` | 代码仓上下文检索 |
| `backend/app/services/knowledge_rule_screening_service.py` | 知识规则预筛查 |
| `backend/app/services/diff_excerpt_service.py` | Diff摘录服务 |
| `backend/app/services/review_runner.py` | 审核执行引擎 |
| `extensions/skills/design-consistency-check/SKILL.md` | 设计一致性检查Skill |
