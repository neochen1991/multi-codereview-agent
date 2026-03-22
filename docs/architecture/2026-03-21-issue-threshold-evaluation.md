# Issue 阈值规则真实用例评估报告

## 背景

本轮评估目标是验证以下机制是否真正有价值：

1. `issue_min_priority_level` 是否能有效阻止低级别问题进入有效问题流。
2. `issue_confidence_threshold_p0 ~ issue_confidence_threshold_p3` 是否能稳定压制低置信度噪音问题。
3. 在压制噪音的同时，是否会误伤真正有价值的高风险问题。

评估对象均为真实 GitHub Java PR，用系统当前真实审核链路发起，不使用伪造 findings。

## 评估结论

当前这套阈值规则是有价值的，主要价值体现在：

1. 可以稳定压掉当前大量 `confidence=0.4` 的中低质量 findings。
2. 能显著降低“所有 findings 都进入 issue 流”的噪音。
3. 但 `P1=0.90` 明显偏严，会把已经比较有价值的 `high / 0.85` 问题也压掉。

基于本轮真实样本，建议先采用如下配置：

- `issue_min_priority_level = P1`
- `issue_confidence_threshold_p0 = 0.95`
- `issue_confidence_threshold_p1 = 0.85`
- `issue_confidence_threshold_p2 = 0.75`
- `issue_confidence_threshold_p3 = 0.70`

## 评估方法

### 真实运行方式

所有样本均通过本地真实审核 API 发起：

- 审核对象：真实 GitHub PR
- 审核执行：系统现有主 Agent + 专家 Agent + LLM 实时调用
- 统计口径：
  - `findings` 数量
  - `issues` 数量
  - 每条 finding 的 `severity`
  - 每条 finding 的 `confidence`
  - 是否出现 `issue_filter_applied`

### 重点观察项

每个样本重点观察：

1. 是否存在大量 `medium / 0.4` 这类噪音 finding。
2. 这些问题是否被成功拦截在 finding 层，没有进入有效问题流。
3. 是否存在 `high / 0.85` 这类更值得保留的问题。
4. 这些问题在不同阈值下是否被误伤。

## 阈值配置组

### 配置组 A：严格

- `issue_min_priority_level = P2`
- `P0 = 0.99`
- `P1 = 0.98`
- `P2 = 0.95`
- `P3 = 0.90`

### 配置组 B：当前候选推荐

- `issue_min_priority_level = P1`
- `P0 = 0.95`
- `P1 = 0.85`
- `P2 = 0.75`
- `P3 = 0.70`

### 配置组 C：历史偏严基线

用于对照的老思路：

- `issue_min_priority_level = P2`
- `P0 = 0.95`
- `P1 = 0.90`
- `P2 = 0.85`
- `P3 = 0.80`

## 样本一：PR #88

- PR: [CodelyTV/java-ddd-example #88](https://github.com/CodelyTV/java-ddd-example/pull/88)
- 标题: `feat: use generic event publisher for mysql consumer`

### 运行结果

#### 样本 1A

- `review_id = rev_d22618d2`
- 配置组：A（严格）
- 结果：
  - `finding_count = 5`
  - `issue_count = 0`
  - 全部被拦截在 finding 层

代表性 findings：

- `architecture_design | medium | 0.4`
- `correctness_business | medium | 0.4`
- `database_analysis | medium | 0.4`
- `mq_analysis | medium | 0.4`
- `performance_reliability | medium | 0.4`

#### 样本 1B

- `review_id = rev_57d6d7bf`
- 配置组：C（历史偏严基线）
- 结果：
  - `finding_count = 5`
  - `issue_count = 0`

代表性 findings：

- `architecture_design | high | 0.85`
- 其余 4 条均为 `medium | 0.4`

关键现象：

- `high / 0.85` 的架构问题也被压掉了。
- 这说明 `P1 = 0.90` 对当前模型输出偏严。

#### 样本 1C

- `review_id = rev_798addf6`
- 配置组：较宽松实验
- 结果：
  - `finding_count = 5`
  - `issue_count = 0`

关键现象：

- 即便放宽，`medium / 0.4` 这类问题仍然不应进入 issue。
- 说明当前模型在该 PR 上给出的中风险问题整体置信度偏低。

### 样本一结论

PR #88 明确说明两件事：

1. `0.4` 级别的中风险问题需要被稳定过滤。
2. `P1 = 0.90` 会误伤 `0.85` 的高价值问题。

## 样本二：PR #96

- PR: [CodelyTV/java-ddd-example #96](https://github.com/CodelyTV/java-ddd-example/pull/96)
- 标题: `Modify the StringValueObject base class`
- `review_id = rev_67999852`

### 运行结果

- `finding_count = 3`
- `issue_count = 0`

3 条 findings 全部为：

- `medium | 0.4`

具体包括：

- `correctness_business | medium | 0.4`
- `ddd_specification | medium | 0.4`
- `maintainability_code_health | medium | 0.4`

### 样本二结论

这条样本说明阈值规则不是只对 PR #88 偶然有效，而是对另一个真实 Java PR 也同样能稳定压制低置信度噪音。

## 样本三：PR #66

- PR: [CodelyTV/java-ddd-example #66](https://github.com/CodelyTV/java-ddd-example/pull/66)
- 标题: `Add compatibility in HibernateCriteriaConverter.java with Value Objects`
- `review_id = rev_8b80c7fb`
- 模式：`light`

### 运行结果

- `finding_count = 3`
- `issue_count = 0`

findings：

- `correctness_business | Value Object 相等性判断引入空指针与类型安全风险 | medium | 0.4`
- `ddd_specification | 基础设施层代码风格变更，无 DDD 职责违规 | low | 0.4`
- `maintainability_code_health | 中间变量引入无实质收益，增加认知负担 | low | 0.4`

### 样本三结论

这条样本说明：

1. 当前阈值能挡住“中低价值代码味道类”问题。
2. 低价值 `low / 0.4` 发现不会误进 issue。
3. 这类提示更适合作为 findings 留给结果页参考，而不是进入有效问题流。

## 样本四：PR #64

- PR: [CodelyTV/java-ddd-example #64](https://github.com/CodelyTV/java-ddd-example/pull/64)
- 标题: `Hexagonal architecture create use case that uses application and infrastructure service for twitter notifications`
- `review_id = rev_3af9c85a`
- 模式：`light`

### 运行结果

- `finding_count = 4`
- `issue_count = 0`

findings：

- `architecture_design | Application层跨模块直接依赖具体CommandHandler，破坏分层边界 | medium | 0.4`
- `correctness_business | CommandHandler 缺少 handle 方法实现，业务逻辑不完整 | medium | 0.4`
- `ddd_specification | 应用服务直接依赖异上下文CommandHandler导致上下文边界模糊 | medium | 0.4`
- `maintainability_code_health | 应用服务直接依赖具体CommandHandler导致职责耦合与测试困难 | medium | 0.4`

### 样本四结论

这条样本进一步说明：

1. 当前模型会产出一些“像问题、但置信度仍然偏低”的结构性意见。
2. 这些问题虽然语义上有一定价值，但在 `confidence=0.4` 时，不应直接进入有效问题流。

## 无效样本：PR #69

- PR: [CodelyTV/java-ddd-example #69](https://github.com/CodelyTV/java-ddd-example/pull/69)
- `review_id = rev_50885fe1`

### 结果

- 状态：`failed`
- 失败原因：
  - `request_transport_error:connect_error:[Errno 61] Connection refused`

### 结论

这条样本是环境失败，不应用于阈值规则判断。

## 关键观察

### 观察一：当前主要噪音集中在 `confidence=0.4`

本轮所有已完成有效样本中，绝大多数被过滤的问题都集中在：

- `severity = medium`
- `confidence = 0.4`

这说明：

1. 模型当前经常会产出“有一定语义合理性，但证据不够强”的问题。
2. 这类问题可以保留为 findings，但不应该进入有效问题流。

### 观察二：`P1 = 0.90` 会误伤高价值问题

PR #88 中出现了一条：

- `architecture_design | high | 0.85`

这条问题相比 `0.4` 噪音明显更有价值，但在 `P1 = 0.90` 时仍被过滤。

因此：

- `P1 = 0.90` 过严
- `P1 = 0.85` 更合理

### 观察三：`issue_min_priority_level = P1` 很有必要

因为当前大量噪音问题集中在：

- `P2 / P3`
- `medium / low`
- `confidence = 0.4`

将最低 issue 级别直接提高到 `P1`，可以进一步减少无意义争议流和人工负担。

## 推荐配置

建议默认值：

- `issue_min_priority_level = P1`
- `issue_confidence_threshold_p0 = 0.95`
- `issue_confidence_threshold_p1 = 0.85`
- `issue_confidence_threshold_p2 = 0.75`
- `issue_confidence_threshold_p3 = 0.70`

## 推荐原因

### 为什么不是 `P1 = 0.90`

因为已经有真实样本证明：

- `high / 0.85` 的高价值问题会被误伤。

### 为什么不是更低的 `P2 = 0.60`

因为当前多条真实样本说明：

- `confidence = 0.4` 的 `medium` 问题数量很多
- 如果 `P2` 再降，很容易把噪音放进 issue 流

### 为什么 `issue_min_priority_level` 要设成 `P1`

因为当前 issue 流更适合承接：

- 高优先级
- 高置信度
- 需要争议收敛或人工裁决

而不是承接所有语义上“看起来像问题”的提示项。

## 后续观察建议

建议用推荐配置继续观察后续 `5-10` 个真实 MR，重点看：

1. 是否还能稳定挡住 `0.4` 级别噪音。
2. 是否开始出现 `0.8-0.9` 区间的高价值问题进入有效问题流。
3. 若有效问题仍然过少，可尝试把 `P1` 从 `0.85` 微调到 `0.83`。
4. 若噪音开始增加，可尝试把 `P1` 回调到 `0.88`。

## 最终建议

先采用如下配置作为当前生产建议值：

```json
{
  "issue_min_priority_level": "P1",
  "issue_confidence_threshold_p0": 0.95,
  "issue_confidence_threshold_p1": 0.85,
  "issue_confidence_threshold_p2": 0.75,
  "issue_confidence_threshold_p3": 0.70
}
```

这是目前基于真实 Java PR 对照样本得到的最平衡结果：

- 能压住噪音
- 不会像 `P1=0.90` 那样明显误伤高价值问题
- 也不会把大量中低质量 findings 错推进有效问题流
