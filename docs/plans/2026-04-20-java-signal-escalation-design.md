# Java 高价值信号直出设计

## 背景

当前系统已经能提取两类高价值 Java 质量信号：

- `loop_call_amplification`
- `comment_contract_unimplemented`

但在真实检视中，这两类问题仍然容易漏掉。根因不是完全没有信号，而是信号命中后，仍然可能在专家生成、后处理或结果收敛阶段被降成弱提示，最终没有稳定产出 finding。

## 目标

对以下两类问题，改为“命中明显信号就默认要求专家产出 finding”：

1. 循环性能问题
   - 循环里查库
   - 循环里调远程
   - 循环里发消息
   - 批量路径逐条处理

2. 注释承诺未实现
   - TODO / 注释 / 接口说明 / 方法意图明确承诺某行为
   - 当前实现明显没有对应动作

## 方案

### 1. 提升为高优先级结构化观察点

保留现有 `java_quality_signal_extractor`，但强化这两类信号的观察点语义：

- `loop_call_amplification` -> `control_flow_with_external_call`
- `comment_contract_unimplemented` -> `declared_intent_without_implementation`

要求后续专家提示和 finding 补强都优先引用这些观察点。

### 2. 在主 Agent 路由阶段明确补入主责专家

保持当前主责关系不变，但把这两类信号提升为强触发：

- `loop_call_amplification`
  - 主责：`performance_reliability`
  - 协同：`database_analysis`
- `comment_contract_unimplemented`
  - 主责：`correctness_business`

### 3. 在专家后处理阶段强制补强为可输出 finding

在 `review_runner` 的 Java 质量信号补强逻辑中，对这两类信号做更强约束：

- 如果 `performance_reliability` 命中 `loop_call_amplification`
  - 自动补强标题、claim、summary、evidence
  - 若当前 finding 置信度偏低，则至少提升到可输出 finding 的范围

- 如果 `correctness_business` 命中 `comment_contract_unimplemented`
  - 自动补强标题、claim、summary、evidence
  - 默认视为高价值正确性问题，而不是普通注释建议

### 4. 避免再次被后处理压成弱提示

在 finding 后处理阶段，对这两类信号增加“不可轻易降级”的判断：

- `loop_call_amplification`
  - 不再因为 wording 偏保守就自动压成普通 `medium/0.4` 提示

- `comment_contract_unimplemented`
  - 不再被当成“注释风格 / 可维护性建议”
  - 优先作为 `correctness_business` 的行为不一致问题输出

## 验证

新增回归测试：

1. 命中 `loop_call_amplification` 时，`performance_reliability` 的 finding 会被补强为明确的循环调用放大问题
2. 命中 `comment_contract_unimplemented` 时，`correctness_business` 的 finding 会被补强为“承诺未落地/承诺与实现不一致”

## 不做的事

本轮不引入新的静态规则引擎，不额外新增专家，不重写 merge 逻辑，只做最小增强，让现有信号能稳定产出高价值 finding。
