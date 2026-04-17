# Java Composite Quality Evaluation

评测时间：2026-04-17  
评测对象：`java-ddd-composite-quality-regression`  
评测目标：在不修改检视逻辑的前提下，评估当前系统对真实 Java 复合问题用例的检出质量与交付稳定性。

## 1. 用例说明

本用例来自真实风格的 Java DDD 仓库变更，预埋了 6 组问题标记、7 个关键词问题点：

- `aggregate`
- `factory`
- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

问题分布如下：

| 文件 | 预期问题 |
| --- | --- |
| `CourseCreator.java` | 聚合工厂绕过、领域事件顺序退化 |
| `HibernateCriteriaConverter.java` | `equal -> like` 导致查询语义放宽 |
| `MySqlDomainEventsConsumer.java` | 去掉 `LIMIT`、吞异常、命名退化 `chunksTmp` |

评测基线来自 [cases.json](/Users/neochen/multi-codereview-agent/backend/tests/fixtures/java_cases/cases.json) 中 `java-ddd-composite-quality-regression` 用例定义。

## 2. 对比任务

本次对比选取两条真实任务：

| 任务 | 日期 | 状态 | 说明 |
| --- | --- | --- | --- |
| `rev_424b7c86` | 2026-04-14 | `completed` | 历史完成任务，用于评估当前系统“可达到”的检视质量 |
| `rev_e08fc28e` | 2026-04-17 | `running` | 新代码链路下的实时任务，用于评估当前系统“当前实际交付”的质量 |

## 3. 结果对比

### 3.1 历史完成任务 `rev_424b7c86`

任务结果：

- `9 findings`
- `2 issues`
- `6` 个专家中 `2` 个完成，`4` 个超时失败
- 总耗时约 `2089s`，约 `34分49秒`

已明确命中的问题点：

- `aggregate`
- `factory`
- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

从最终报告内容看，主要命中包括：

- `CourseCreator` 直接 `new Course`，绕过工厂方法
- `CourseCreator` 中 `eventBus.publish` 与 `repository.save` 顺序调整，带来领域事件语义退化
- `HibernateCriteriaConverter` 将 `equal` 改成 `like`
- `MySqlDomainEventsConsumer` 去掉 `LIMIT`
- `MySqlDomainEventsConsumer` 吞掉 `catch` 中的异常处理
- `MySqlDomainEventsConsumer` 常量退化命名为 `chunksTmp`

阶段性评估：

- 关键词覆盖率：接近 `7/7`
- 问题标记覆盖率：接近 `6/6`
- 说明：虽然有 `4` 个专家失败，但系统仍然依靠已完成专家和部分保守兜底发现，把这条复合用例的大部分核心问题打出来了。

### 3.2 当前运行任务 `rev_e08fc28e`

当前结果：

- `1 finding`
- `0 issues`
- `6` 个专家中当前已有 `3` 个失败
- 当前状态仍为 `running`

当前只稳定命中了：

- `aggregate / factory` 相关问题

当前尚未稳定落库的问题点：

- `domain event`
- `LIMIT`
- `like`
- `catch`
- `chunksTmp`

阶段性评估：

- 当前可见关键词覆盖率：约 `1/7`
- 当前可见问题标记覆盖率：约 `1/6`

说明：

- 这不是“系统完全不会检”，而是任务尚未跑完，且已发生多专家超时失败，导致当前对外可见结果明显缩水。

## 4. 质量结论

### 4.1 当前系统的能力上限

参考 `rev_424b7c86`，系统在这条复合 Java 用例上具备较强的问题识别能力。

可以认为当前系统具备以下能力：

- 能识别 DDD 聚合工厂绕过
- 能识别领域事件顺序风险
- 能识别查询语义从 `equal` 放宽到 `like`
- 能识别 SQL 去掉 `LIMIT` 的风险
- 能识别吞异常
- 能识别命名退化问题

### 4.2 当前系统的真实交付质量

参考 `rev_e08fc28e`，当前系统的真实交付质量不稳定。

主要不是“规则不足”或“专家不会检”，而是：

- 专家执行阶段经常超时
- 专家一旦失败，只能留下低置信 fallback finding
- 最终报告的质量高度依赖专家是否真正跑完

所以当前系统应拆成两个维度来看：

- 检出能力：中等偏上
- 交付稳定性：偏低

## 5. 当前最大质量损失来源

本次评测暴露出的第一问题，不是提示词，也不是规则数量，而是运行稳定性。

具体体现在：

- `rule_screening` 单批 LLM 请求仍然较慢
- 专家失败后，结果被迫降级为保守风险
- 结果页最终展示的是“跑完后的剩余结果”，不是“理论上可检出的全部问题”

换句话说：

当前系统的质量短板，主要是“没完整跑完导致漏检”，而不是“模型完全识别不出问题”。

## 6. 阶段性判断

基于本次评测，当前系统可以做如下判断：

- 作为“问题发现系统”，已经具备较好的 Java 复合问题识别潜力
- 作为“稳定可交付的审核系统”，还不能认为质量稳定
- 若要继续提升真实质量，优先级最高的是提升专家执行成功率，而不是继续盲目扩规则

## 7. 后续建议

下一阶段建议重点跟踪 3 个指标：

- `问题覆盖率`
  评估最终结果中命中了多少预埋问题点

- `专家成功率`
  评估参与专家中有多少真正完成了深审

- `超时损失率`
  评估因专家失败而未交付的问题点占比

建议后续所有质量评测都沿用这三个指标，避免只看 `findings` 数量而误判质量。
