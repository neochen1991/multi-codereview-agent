# 分级上下文压缩设计

## 背景

当前轻量模式的上下文压缩仍以文本截断为主，典型实现包括：

- `review_runner.py` 中的 `_compact_prompt_block()`
- `review_runner.py` 中的 `_trim_prompt_repository_context_for_light()`

这种方式能快速降低 prompt 长度，但裁剪单位是整段文本，不是结构化证据块，存在两个明显风险：

1. 关键代码证据与说明性文本被同等对待，容易误伤检视质量。
2. 不同专家应优先看到的上下文不同，但当前裁剪更多是统一截断，缺少职责驱动的优先级控制。

本设计目标是在不影响检视质量的前提下，把上下文改造成“结构化 block + 统一总预算 + 分级裁剪”的机制。

## 目标

1. 以整次 LLM 请求为单位统一管理上下文预算。
2. 先保证专家职责、规范、变更代码原文、关键上下文不丢。
3. 压缩单位从文本改为结构化 block。
4. 不同专家按职责优先保留不同上下文。
5. 为每次 prompt 生成保留可观测元数据，方便回查漏检原因。

## 非目标

1. 不改变专家选择逻辑。
2. 不改变标准模式与轻量模式的业务语义。
3. 不以“更短 prompt”为第一目标，不牺牲检出率换速度。

## 核心策略

### 1. 统一总预算

上下文预算按整次 LLM 请求统一计算，而不是按模块固定配额。

原因：

1. 不同审核任务中，真正重要的信息分布不固定。
2. 固定模块预算容易让低价值模块占住预算，反而挤掉关键证据。
3. 多文件、多 hunk、多专家场景下，统一预算更容易做动态最优保留。

### 2. 上下文 Block 化

所有输入给专家 LLM 的上下文都拆成结构化 block。每个 block 至少带以下字段：

- `block_id`
- `type`
- `priority`
- `expert_relevance`
- `evidence_strength`
- `must_keep`
- `compression_level`
- `token_cost`
- `file_path`
- `line_start`
- `line_end`
- `summary`
- `content`
- `tags`
- `related_rule_ids`
- `related_observation_ids`

### 3. 优先级分层

#### P0 必留

- 专家职责定义
- 专家绑定规范
- 语言通用规范
- 目标文件变更 hunk
- 当前问题行附近源码
- 命中的强规则
- 关键 observation
- 输出格式与文件行号约束

规则：

1. 只能去重和轻度格式清洗。
2. 不允许删除。

#### P1 高优先级

- 同文件其他变更 hunk
- 当前类实现片段
- 调用方上下文
- 被调方上下文
- 事务边界
- 持久化上下文
- 命中的绑定文档片段
- 强相关运行时工具证据

规则：

1. 默认保留原文片段。
2. 预算紧张时才允许缩短 snippet。

#### P2 中优先级

- 领域模型上下文
- 父接口 / 抽象类
- 关联源码片段
- symbol definitions / references
- 规则筛选摘要
- 设计文档摘要
- 同批次其他文件的高相关摘要

规则：

1. 可压缩为结构化摘要。
2. 默认不先删除。

#### P3 低优先级

- 代码仓上下文摘要
- 其他文件 diff 摘要
- observation 解释性文本
- 重复工具结果摘要
- 弱相关辅助上下文

规则：

1. 优先去重。
2. 其次摘要化。

#### P4 可淘汰

- 已被更高优先级 block 覆盖的重复文本
- 泛化说明文字
- 对当前专家帮助较低的弱相关上下文
- 低相关其他文件补充块

规则：

1. 仅在超预算时淘汰。

## 专家职责加权

在基础优先级之上，再按专家职责上调相关 block：

### 安全专家

上调：

- `caller_context`
- `persistence_context`
- `runtime_tool_evidence`
- 输入边界 / 鉴权 / 查询边界相关 observation

### 性能专家

上调：

- `transaction_context`
- `persistence_context`
- `callee_context`
- 循环 / 批处理 / 调用放大相关 observation

### DDD 专家

上调：

- `domain_model_context`
- `current_class_context`
- `caller_context`
- 聚合 / 工厂 / 领域事件相关 observation

### 架构专家

上调：

- `caller_context`
- `callee_context`
- `parent_contract_context`
- 跨层依赖 / 边界相关 observation

### 可维护性专家

上调：

- `current_class_context`
- `same_file_other_hunks`
- 命名 / 魔法值 / 空实现相关 observation

### 正确性专家

上调：

- `current_code`
- `same_file_other_hunks`
- `caller_context`
- 承诺未实现 / 逻辑不一致相关 observation

## 压缩等级

### L0 原文保留

适用于：

- P0
- 高证据强相关 P1

### L1 去重和格式清洗

适用于：

- 重复 diff
- 重复摘要
- 同源重复 block

### L2 片段缩短

适用于：

- P1
- P2

做法：

1. 保留头部说明
2. 保留目标代码附近片段
3. 删除中间低价值行

### L3 结构化摘要

适用于：

- P2
- P3

做法：

1. 保留路径、符号、行号、命中理由
2. 将大段源码降成结构化要点

### L4 淘汰

适用于：

- P4
- 极少数超预算仍无法收敛的 P3

## 统一预算算法

1. 生成全量 context blocks
2. 给每个 block 打分
3. 删除重复 block
4. 锁定全部 `must_keep`
5. 计算剩余预算
6. 按得分从高到低装入
7. 超预算时先升级压缩等级，不直接删除
8. 只有在 L3 后仍超预算，才开始淘汰 P4，再考虑极少量 P3

## 可观测性

每次 prompt 生成后记录：

- `prompt_budget_total`
- `prompt_budget_used`
- `must_keep_blocks`
- `kept_blocks`
- `compressed_blocks`
- `dropped_blocks`
- `compression_level_by_block`

用途：

1. 回查漏检是否因为关键 block 未进入 prompt
2. 比较不同模式下实际保留的上下文差异
3. 为后续质量评测提供依据

## 实施顺序

1. 定义 `ContextBlock` 结构
2. 定义 block 优先级和专家加权规则
3. 把 `review_runner.py` 当前上下文组装逻辑映射到 block
4. 增加统一预算规划器
5. 先接入轻量模式
6. 跑真实 Java 用例回归，确认检出率不下降
7. 再评估是否扩展到标准模式
