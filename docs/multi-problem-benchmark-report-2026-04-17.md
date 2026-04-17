# 多问题场景评测报告

评测时间：2026-04-17  
评测方式：基于现有 Java 评测集，对“一个用例内包含多个独立问题”的场景做持续验证  
评测目标：评估当前系统在多问题 Java MR 中的检出能力、交付稳定性，以及超时对最终质量的影响

## 1. 评测用例

本次选择评测集中的复合用例：

- `java-ddd-composite-quality-regression`

该用例来自 [cases.json](/Users/neochen/multi-codereview-agent/backend/tests/fixtures/java_cases/cases.json)，预埋问题如下：

### 1.1 文件级问题分布

| 文件 | 预埋问题 |
| --- | --- |
| `src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java` | 聚合工厂绕过、领域事件顺序退化 |
| `src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java` | `equal -> like` 查询语义放宽 |
| `src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java` | 去掉 `LIMIT`、吞异常、命名退化 `chunksTmp` |

### 1.2 问题点口径

本用例按关键词问题点统计为 `7` 个：

- `aggregate`
- `factory`
- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

本用例按问题标记统计为 `6` 组：

- `CourseCreator.java`：`aggregate + factory`
- `CourseCreator.java`：`domain event`
- `HibernateCriteriaConverter.java`：`equal + like`
- `MySqlDomainEventsConsumer.java`：`LIMIT`
- `MySqlDomainEventsConsumer.java`：`chunksTmp`
- `MySqlDomainEventsConsumer.java`：`catch`

## 2. 对比任务

为评估“当前系统实际质量”与“系统理论可达质量”，本次对比两条真实任务：

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| `rev_424b7c86` | `completed` | 历史已完成任务，用于观察当前系统在这条用例上的可达上限 |
| `rev_e08fc28e` | `running` | 最新链路下的实时任务，用于观察当前系统当前交付状态 |

## 3. 评测结果

### 3.1 历史完成任务 `rev_424b7c86`

任务结果：

- `9 findings`
- `2 issues`
- `6` 个专家中 `2` 个完成，`4` 个超时失败
- 总耗时约 `34分49秒`

### 3.1.1 已命中的问题

从最终报告内容可以确认，以下问题已经被识别：

- `aggregate`
- `factory`
- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

### 3.1.2 命中说明

主要已落出的内容包括：

- `CourseCreator` 直接 `new Course`，绕过工厂方法
- `CourseCreator` 中领域事件发布与持久化顺序调整
- `HibernateCriteriaConverter` 将 `equal` 改为 `like`
- `MySqlDomainEventsConsumer` 去掉 `LIMIT`
- `MySqlDomainEventsConsumer` 吞掉异常
- `MySqlDomainEventsConsumer` 常量命名退化为 `chunksTmp`

### 3.1.3 指标评估

| 指标 | 结果 |
| --- | --- |
| 问题点覆盖率 | 约 `7/7` |
| 问题标记覆盖率 | 约 `6/6` |
| 专家成功率 | `2/6 = 33.3%` |

### 3.1.4 结论

虽然只有 `2` 个专家真正完成，但最终结果仍覆盖了这条复合用例的大部分核心问题。这说明：

- 当前系统对多问题 Java MR 具备较强检出潜力
- 结果质量并不完全取决于所有专家都跑完
- 但这种“侥幸命中”并不稳定

## 4. 当前实时任务 `rev_e08fc28e`

当前结果：

- 状态：`running`
- 当前已产出：`1 finding`
- 当前 `0 issues`
- 当前已有 `3` 个专家失败：
  - `architecture_design`
  - `correctness_business`
  - `database_analysis`

### 4.1 当前已命中问题

当前只稳定命中了：

- `aggregate / factory`

### 4.2 当前尚未稳定落库的问题

当前尚未在对外结果中稳定体现：

- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

### 4.3 指标评估

| 指标 | 当前结果 |
| --- | --- |
| 当前可见问题点覆盖率 | 约 `1/7` |
| 当前可见问题标记覆盖率 | 约 `1/6` |
| 当前专家成功率 | `0/6` 已完成，`3/6` 已失败，其余仍在运行 |

## 5. 质量评估

### 5.1 检出能力

从 `rev_424b7c86` 可见，当前系统具备对复合问题用例的较强识别能力。  
至少在这条多问题用例上，系统已经能覆盖：

- DDD 问题
- 查询语义问题
- SQL 性能问题
- 异常处理问题
- 命名问题

### 5.2 交付稳定性

从 `rev_e08fc28e` 可见，当前系统的最终交付质量仍然不稳定。  
同一条用例，在最新实时任务里，目前只稳定落出 `1` 条发现。

这说明当前真正的质量短板不是“不会检”，而是：

- 专家执行阶段经常超时
- 专家失败后结果会退化成少量保守发现
- 多问题用例的最终交付结果对运行稳定性非常敏感

## 6. 关键结论

本次“多问题场景持续验证”的结论如下：

1. 当前系统对多问题 Java MR 具备较好的问题识别潜力  
   从历史完成任务看，系统已经能覆盖这条复合用例的大部分关键问题。

2. 当前系统的真实交付质量不稳定  
   新任务中专家失败会直接导致漏检，最终结果会大幅缩水。

3. 当前最大的质量损失来源不是规则数量不足  
   而是专家阶段超时，导致多问题场景下“理论可检出问题”无法稳定转化成“最终交付结果”。

## 7. 当前质量评级

基于本次评测，可做如下阶段性评级：

| 维度 | 评级 | 说明 |
| --- | --- | --- |
| 多问题检出潜力 | 中等偏上 | 历史任务已覆盖大部分核心问题 |
| 多问题交付稳定性 | 偏低 | 新任务中专家失败后，结果明显缩水 |
| 综合质量 | 中等 | 有能力，但不稳定 |

## 8. 后续建议

后续继续用评测集验证时，建议固定跟踪以下指标：

- `问题点覆盖率`
- `问题标记覆盖率`
- `专家成功率`
- `超时损失率`

其中最优先关注的是：

- `专家成功率`
- `超时损失率`

因为当前质量损失的首要来源，已经明确是“没跑完导致漏检”，而不是“完全识别不出问题”。
