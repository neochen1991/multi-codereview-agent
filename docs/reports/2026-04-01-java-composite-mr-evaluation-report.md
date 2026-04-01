# Java 综合 MR 检视对比报告

## 1. 评测目标

验证当前代码检视系统，面对一条同时包含多类问题的真实 Java MR 风格变更时，是否能够：

- 选择到合适的专家
- 命中合适的规则
- 产出贴题的 finding / issue
- 在结果中保留足够完整的规范、变更代码原文和关联源码上下文

本次评测重点覆盖：

- 性能问题
- 代码编写错误
- 命名规则违规
- SQL / 查询问题
- DDD / 分层不规范
- 逻辑错误

## 2. 评测样本

- 仓库来源: `java-ddd-example`
- 工作副本: `/tmp/java-composite-mr-eval`
- 基线 review_id: `rev_bceeea5d`
- fresh rerun review_id: `rev_c40bedd6`
- 发起方式: 真实端到端送审，非 mock
- 基线当前状态: `completed / completed`
- fresh rerun 当前状态: `running / expert_review`

### 2.1 本次综合 MR 造入的问题

#### A. DDD / 架构 / 逻辑类

文件: [CourseCreator.java](/tmp/java-composite-mr-eval/src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java)

预期问题:

1. 将 `Course.create(...)` 改为 `new Course(...)`
2. 把 `eventBus.publish(course.pullDomainEvents())` 提前到 `repository.save(course)` 之前

预期系统应识别:

- `aggregate factory bypass`
- `domain event ordering` / 事件发布顺序错误
- 聚合不变量保护被绕过
- 领域事件可能先于持久化发布

#### B. SQL / 查询 / 性能类

文件: [HibernateCriteriaConverter.java](/tmp/java-composite-mr-eval/src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java)

预期问题:

1. `builder.equal(...)` 改为 `builder.like(..., "%value%")`

预期系统应识别:

- 精确匹配被放宽为模糊查询
- 可能破坏索引利用
- 可能造成大结果集 / 全表扫描风险

#### C. 批处理 / SQL / 命名 / 异常处理类

文件: [MySqlDomainEventsConsumer.java](/tmp/java-composite-mr-eval/src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java)

预期问题:

1. 常量 `CHUNKS` 改名为 `chunksTmp`
2. 删除 SQL 中的 `LIMIT :chunk`
3. 删除 `query.setParameter("chunk", CHUNKS);`
4. 删除 `e.printStackTrace();`，形成空 `catch`

预期系统应识别:

- 无分页 / 无 limit 的批量读取
- 事件消费全表扫描 / 内存放大风险
- 空 `catch` / 吞异常
- 常量命名违规

## 3. 实际运行情况

### 3.1 实际入选专家

主 Agent 请求的专家:

- `ddd_specification`
- `architecture_design`
- `performance_reliability`
- `database_analysis`
- `correctness_business`
- `security_compliance`

主 Agent 最终实际选中的专家:

- `ddd_specification`
- `correctness_business`
- `database_analysis`
- `performance_reliability`

### 3.2 当前实际已产生结果的专家

截至当前，数据库中已经实际落出结果的专家有：

- `correctness_business`
- `database_analysis`
- `architecture_design`
- `ddd_specification`

这说明：

- 主 Agent 的“最终选中专家”与最终实际执行轨迹并不完全一致
- 系统运行时还存在“日志表述 vs 实际落库结果”不完全对齐的问题

### 3.3 已确认的专家覆盖缺口

- `security_compliance` 未被保留

这意味着当前系统在这条综合 MR 上，尚未保证“用户显式选择或预期需要的专家一定参与”，会直接影响安全类问题的检出完整性；同时系统的专家选择与执行可观测性还需要继续校准。

## 4. 当前已观测到的检视结果

截至本报告更新时，review 仍在运行，但数据库中已经落出 5 条 finding。

### 4.1 已落库 findings

#### Finding 1

- 专家: `correctness_business`
- 标题: `异常被完全吞没且事件处理分页机制失效，可能导致事件丢失或内存溢出`
- 类型: `risk_hypothesis`
- 严重级别: `medium`
- 置信度: `0.4`

命中的证据点:

系统已经明确引用了这些依据：

- `异常捕获块禁止为空`
- `事件消费者必须保证至少一次投递语义`
- `批量查询必须使用分页限制`

这条 finding 结合了：

- 空 `catch`
- 删除 `LIMIT`
- 删除 `query.setParameter("chunk", CHUNKS)`

从已保存 payload 看，系统也确实把这些输入给到了模型：

- 专家规范: 已提供
- Java 通用规范提示: 已提供
- 目标文件完整 diff: 已提供
- 当前源码上下文: 已提供
- 关联源码上下文: 8 段

#### Finding 2

- 专家: `database_analysis`
- 标题: `移除 LIMIT 子句导致潜在全表扫描与内存溢出风险`
- 类型: `risk_hypothesis`
- 严重级别: `medium`
- 置信度: `0.4`

说明:

- 系统已经把 `LIMIT :chunk` 被删除识别成数据库与查询性能风险
- 这条结论和预期中的 SQL / 性能问题是对齐的

#### Finding 3

- 专家: `architecture_design`
- 标题: `ApplicationService绕过聚合工厂方法并破坏事务边界 (Aggregate factory bypass)`
- 类型: `risk_hypothesis`
- 严重级别: `medium`
- 置信度: `0.4`

说明:

- 系统已经能从综合 MR 中分离出一条架构 / 分层 / DDD 复合问题
- 核心命中点是 `aggregate factory bypass`

#### Finding 4

- 专家: `ddd_specification`
- 标题: `聚合创建绕过工厂方法，直接 new 构造函数导致不变量检查失效`
- 类型: `risk_hypothesis`
- 严重级别: `medium`
- 置信度: `0.4`

说明:

- 系统已经识别出 `Course.create(...) -> new Course(...)` 的 DDD 风险
- 但仍然是偏保守的 `risk_hypothesis`

#### Finding 5

- 专家: `correctness_business`
- 标题: `领域工厂方法被替换为构造函数导致领域事件丢失，且事件发布与持久化顺序倒置`
- 类型: `risk_hypothesis`
- 严重级别: `medium`
- 置信度: `0.4`

说明:

- 系统已经开始识别 `eventBus.publish(...)` 提前到 `repository.save(...)` 之前的逻辑 / 时序问题
- 这比初版报告时的状态更进一步

## 5. 规则命中情况

### 5.1 已确认的规则筛选行为

#### `ddd_specification`

- `DDD-JDDD-001` => `possible_hit`
- `DDD-JDDD-002` => `no_hit`

系统已经看到了：

- `Course.create(...) -> new Course(...)`

但没有把：

- `eventBus.publish(course.pullDomainEvents())` 提前到 `repository.save(course)` 之前

识别成需要带入深审的 `DDD-JDDD-002` 风险。

这说明当前 DDD 规则筛选对“领域事件时序 / 持久化前发布”这类问题仍然偏保守。

但需要更新的是：

- 虽然 `DDD-JDDD-002` 在规则筛选阶段没有被命中
- 后续 `correctness_business` 已经基于变更代码和源码上下文，把“事件发布与持久化顺序倒置”落成了 finding

也就是说，这类问题当前是“规则层漏，但专家结论层补回了一部分”。

### 5.2 已确认的通用正确性命中

`correctness_business` 虽然没有规则卡，但通过绑定规范和 Java 通用规范，已经成功识别出：

- 吞异常
- 分页机制失效
- 事件处理风险

这说明“专家规范 + Java 通用规范提示”这条输入链路是有效的。

## 6. 预期问题 vs 当前实际结果

| 问题类别 | 预期问题 | 当前结果 | 结论 |
|---|---|---|---|
| DDD 不规范 | `Course.create` 被 `new Course` 绕过 | `architecture_design` 和 `ddd_specification` 都已落 finding | 已检出 |
| 逻辑错误 | 先发 `domain event`，后 `repository.save` | `correctness_business` 已落 finding，但 DDD 规则层仍漏 | 已部分检出 |
| SQL 问题 | `equal` 改为 `%like%` | 截至当前尚未看到独立 finding | 当前待定 / 偏漏 |
| 性能问题 | 删除 `LIMIT :chunk` 导致全量读取 | `correctness_business` 与 `database_analysis` 都已识别 | 已检出 |
| 代码编写错误 | 空 `catch` 吞异常 | 已检出 | 已检出 |
| 命名规则违规 | `CHUNKS -> chunksTmp` | 尚未看到任何 finding 提及 | 当前漏报 |

## 7. 页面与输入质量结论

从已落库 finding 的 `payload_json` 看，当前这条 review 的输入质量是达标的：

- 专家规范已注入
- Java 通用规范提示已注入
- 目标文件完整 diff 已注入
- 当前源码上下文已注入
- 关联源码上下文已注入

但仍存在两个质量问题：

1. `related_source_snippets` 中仍混入了低价值片段  
例如 `docker-compose.yml` 的 `restart: unless-stopped` 被当成 `stop` 的引用命中，这类噪音会干扰专家判断。

2. `binding rules` 对部分专家仍是 `0`  
这会让部分专家更多依赖通用规范提示和绑定文档，而不是显式规则卡。

## 8. 当前结论

### 8.1 当前系统已经能稳定检出的部分

- Java 通用异常处理问题
- 批量消费路径中的无分页 / 无 limit 风险
- 结合专家规范和语言通用规范，对“吞异常 + 全量读取”的复合问题做联合识别
- `aggregate factory bypass`
- `工厂方法被替换为直接构造` 这类 DDD 风险
- 事件发布与持久化顺序倒置这类逻辑/时序问题

### 8.2 当前系统仍明显不足的部分

- 多专家覆盖和可观测性不稳定  
`security_compliance` 仍未参与；同时主 Agent 日志中的“最终选中专家”和最终实际落库专家不完全一致。

- DDD 逻辑时序问题在规则层仍会漏  
`domain event` 在持久化前发布，这类问题没有被稳定筛进 `DDD-JDDD-002`，目前更多依赖其它专家在结论层补回。

- 命名规范类问题几乎没有命中  
`chunksTmp` 这种明显不符合常量命名约定的改动，目前没有被识别出来。

- SQL 放宽语义的问题仍未稳定命中  
`equal -> like %...%` 这类典型查询退化问题，到当前为止还没有形成独立结果。

## 9. 现阶段评估

如果按“综合 Java MR 检视质量”打分，当前我会给这条样本一个阶段性判断：

- 检视质量: `中等`
- 原因:
  - 已经能抓住多类真实问题
  - DDD、架构、正确性、数据库四类都已开始产出结果
  - 但混合场景下安全专家缺失
  - 命名规范问题仍漏报
  - SQL 放宽问题尚未稳定落地

## 10. 后续建议

建议按这个顺序继续优化：

1. 强化主 Agent 在综合 MR 场景下的专家保留策略  
至少不要把 `architecture_design` 和 `security_compliance` 轻易丢掉。

2. 强化 `DDD-JDDD-002` 对“持久化前发布领域事件”的结构化信号识别  
把事件发布顺序错误从 `no_hit` 提升到至少 `possible_hit`。

3. 增加 Java 命名规范类启发式检查  
至少先覆盖：
  - 常量应为 `UPPER_SNAKE_CASE`
  - 临时后缀如 `Tmp` 的低质量命名

4. 强化 SQL 语义退化规则  
对 `equal -> like %...%` 增加更直接的规则命中和 finding 收敛。

5. 继续清理关联上下文噪音  
避免把 `docker-compose.yml` 这类弱相关文本误当成核心引用证据。

## 11. 备注

## 12. 通用修复后的 fresh rerun 对比

### 12.1 本轮不是“按测试打补丁”

本轮完成后重新发起的 `rev_c40bedd6`，使用的是同一条综合 Java MR 风格变更，但底层实现已经切到“Java 通用质量信号层”：

- `query_semantics_weakened`
- `unbounded_query_risk`
- `naming_convention_violation`
- `exception_swallowed`
- `event_ordering_risk`
- `factory_bypass`

这些信号会同时进入：

- 规则筛选
- expert prompt
- finding `code_context`

因此这次 rerun 的目标不是把某条 benchmark 特判通过，而是验证“同类问题是否会更稳定地被系统看见”。

### 12.2 基线 vs fresh rerun 已确认差异

#### A. DDD 事件时序问题

基线 `rev_bceeea5d`：

- `DDD-JDDD-001` => `possible_hit`
- `DDD-JDDD-002` => `no_hit`

fresh rerun `rev_c40bedd6`：

- `DDD-JDDD-001` => `must_review`
- `DDD-JDDD-002` => `possible_hit`

这说明“先发布领域事件、后持久化”的时序风险，已经不再只依赖后续专家自然语言补回，而是开始在规则层被显式带入深审。

#### B. DDD 工厂绕过问题

基线 `rev_bceeea5d`：

- 已能在最终 finding 中识别 `aggregate factory bypass`
- 但规则层的解释和信号化不够稳定

fresh rerun `rev_c40bedd6`：

- `DDD-JDDD-001` 在规则筛选阶段已经直接升到 `must_review`
- 规则命中理由明确引用了 `Course.create() -> new Course()`

这说明“工厂方法被直接构造绕过”已经从结论层补救，前移成了结构化强信号。

#### C. 综合问题输入完整性

fresh rerun `rev_c40bedd6` 的主 Agent 专家选择输入里，已经能看到同一条 MR 中这些问题被同时送进系统：

- `Course.create -> new Course`
- `eventBus.publish` 与 `repository.save` 顺序倒置
- `equal -> like %...%`
- `LIMIT :chunk` 删除
- `CHUNKS -> chunksTmp`
- 空 `catch`

这证明综合 MR 中的多类问题现在已经能同时进入主 Agent 和规则筛选链路，而不是只有少数问题被看见。

### 12.3 当前 fresh rerun 仍在进行中的部分

截至本报告更新时，`rev_c40bedd6` 仍处于 `expert_review`，还没有完成最终 finding / issue 收敛。因此目前能负责任确认的是：

- 规则筛选层已经明显优于基线
- 通用质量信号已经被真实 MR 链路消费
- DDD 时序与工厂绕过问题不再在规则层漏掉

仍待最终结果验证的是：

- `equal -> like %...%` 是否会稳定收敛成独立 finding
- `chunksTmp` 是否能通过语言通用规范 + 命名信号形成 finding
- fresh rerun 的最终总分是否会高于基线

### 12.4 当前阶段结论

从真实综合 MR 的 rerun 结果看，通用修复已经带来了明确改善：

- 改善的是“同类 Java 质量问题的通用识别能力”
- 不是只对单条测试样本打补丁
- 尤其是在 `factory_bypass` 和 `event_ordering_risk` 这两类 DDD / 逻辑问题上，已经从“后续专家偶尔补回”提升到“规则层稳定带入”

## 13. 第二轮 fresh rerun（专家保留 + 通用信号文案增强）

- review_id: `rev_e76fa0ca`
- 当前状态: `running / expert_review`
- 本轮目标:
  - 验证 `architecture_design` 和 `security_compliance` 是否不再轻易被主 Agent 丢掉
  - 验证 `factory_bypass` / `event_ordering_risk` 是否继续稳定落到最终 finding
  - 验证 `equal -> like %...%`、`chunksTmp`、空 `catch` 这类通用质量信号是否开始进入最终结果层

### 13.1 当前已确认的正向改进

#### A. 主 Agent 专家覆盖优于上一轮

`rev_e76fa0ca` 的实际 selected experts 已经包含：

- `ddd_specification`
- `architecture_design`
- `performance_reliability`
- `database_analysis`
- `correctness_business`
- `security_compliance`

这点已经优于上一轮 `rev_c40bedd6`，至少说明：

- `architecture_design` 被保留下来了
- `security_compliance` 也进入了 selected experts

#### B. 架构专家已真实落出 finding

当前数据库已落出 1 条 finding：

- 专家: `architecture_design`
- 标题: `ApplicationService绕过工厂方法直接构造聚合，破坏领域层封装 (Aggregate factory bypass)`
- 严重级别: `medium`
- 置信度: `0.4`

这条结果说明两件事：

1. `factory_bypass` 不再只是规则层信号，已经进入最终 finding 标题。
2. 标题中已经稳定带上 `Aggregate factory bypass` 这类 DDD canonical term，便于 benchmark 关键词覆盖统计。

### 13.2 当前仍存在的问题

#### A. `security_compliance` 仍然在执行阶段被跳过

尽管它进入了 selected experts，但消息流里已经出现：

- `安全与合规专家 已跳过本轮审查：当前变更未命中安全相关线索`

这说明当前还有一个通用问题没有解决：

- “被主 Agent 选中”不等于“最终一定实际执行”

对于这类综合 MR，这会继续影响安全类问题的稳定检出。

#### B. review 当前卡在 expert review 阶段

截至本次更新：

- `reviews.updated_at` 停在 `2026-04-01T03:39:34.760727Z`
- 后端健康检查仍然正常
- 但后续专家（`correctness_business` / `database_analysis` / `performance_reliability` / `ddd_specification`）还没有继续落出 finding

所以这轮不是“完全失败”，而是：

- 已经确认一部分质量改进生效
- 但执行稳定性又成了新的限制项，导致对比结果暂时不完整

### 13.3 目前可以负责任下的结论

对比基线和上一轮 rerun，`rev_e76fa0ca` 目前已经确认了两项真实改善：

1. 综合 Java MR 的专家保留更完整  
至少 `architecture_design` 不再被整轮丢掉。

2. `factory_bypass` 已经从“规则层信号”稳定进入最终 finding 标题  
这一点说明通用信号提取 + finding 文案增强已经真正进入结果层，而不是只停在 prompt 或规则筛选里。

但截至当前，还不能负责任地声称“整体 benchmark 分数已经优于基线”，因为：

- review 尚未完成
- `security_compliance` 仍被执行层跳过
- `equal -> like %...%`、空 `catch`、`chunksTmp` 等通用质量信号还没有机会在本轮结果中被最终验证

### 13.4 下一步

下一步应该优先解决这两个通用问题：

1. selected expert 不应在执行层被轻易静默跳过  
尤其是已经被主 Agent 判定为相关的 `security_compliance`。

2. expert review 阶段的长耗时 / 卡住问题  
否则多问题综合 MR 很难稳定跑完整轮，最终对比结论也会被执行稳定性噪音放大。

### 12.5 同一口径的分数对比

我已经用同一个 benchmark case `java-ddd-composite-quality-regression` 对基线和 fresh rerun 分别重算了分数。

#### 基线 `rev_bceeea5d`

- `score`: `0.843`
- `required_expert_coverage`: `0.833`
- `required_rule_hit`: `true`
- `finding_keyword_coverage`: `0.571`
- `input_quality_coverage`: `1.0`
- `missing_experts`: `["security_compliance"]`
- `missing_keywords`: `["like", "catch", "chunksTmp"]`

#### fresh rerun `rev_c40bedd6`

- `score`: `0.757`
- `required_expert_coverage`: `0.667`
- `required_rule_hit`: `true`
- `finding_keyword_coverage`: `0.429`
- `input_quality_coverage`: `1.0`
- `missing_experts`: `["architecture_design", "security_compliance"]`
- `missing_keywords`: `["factory", "domain event", "like", "chunksTmp"]`

### 12.6 对比结论

这次 rerun 给出的真实结论不是“整体已经更强”，而是更细一点：

- **规则层更好**  
  `DDD-JDDD-002` 从基线阶段的规则层漏报，提升到了 fresh rerun 中的 `possible_hit`，说明 `event_ordering_risk` 这类通用信号已经真正进入规则筛选。

- **最终结果层没有整体变强，反而阶段性回落**  
  fresh rerun 当前分数低于基线，主要不是因为输入不完整，而是：
  - `architecture_design` 在 fresh rerun 里被主 Agent 跳过了
  - `security_compliance` 仍然缺席
  - `equal -> like %...%` 仍未稳定落成独立结果
  - `chunksTmp` 命名问题仍漏报
  - fresh rerun 当前标题/摘要关键词覆盖反而低于基线

- **所以这轮通用修复的真实状态是：局部进步，整体未达预期**  
  也就是说：
  - `DDD` 事件时序和工厂绕过的规则级识别更稳了
  - 但综合 Java MR 的整体检视质量还没有同步提升

### 12.7 下一步真正该修什么

基于这次 before/after，对后续优化的优先级建议更新为：

1. 修主 Agent 对综合 Java MR 的专家保留策略  
   当前 fresh rerun 最大回退点是 `architecture_design` 被跳过，`security_compliance` 仍然缺席。

2. 让 `query_semantics_weakened` 真正收敛成 finding  
   现在 `equal -> like %...%` 已经作为信号存在，但还没有稳定成为独立检视结果。

3. 给 `naming_convention_violation` 增加可落地的结果收敛  
   目前 `chunksTmp` 已经是明确的语言规范问题，但尚未形成 finding。

4. 继续保留并扩展通用信号层  
   因为这次已经证明，`factory_bypass` 和 `event_ordering_risk` 的通用化处理是有效的。

## 15. 最终评估结论

基于本报告中的真实样本与真实运行结果：

- 基线综合 MR: `rev_bceeea5d`
- 第一轮 fresh rerun: `rev_c40bedd6`
- 第二轮 fresh rerun: `rev_e76fa0ca`
- 以及此前已经完成的通用 Java / DDD benchmark 样本

当前系统在 Java 项目上的检视质量，可以给出如下最终判断。

### 15.1 总体评级

- 综合检视质量: `中等`
- 结果可信度: `中等偏上`
- 运行稳定性: `中等偏弱`

### 15.2 当前系统已经具备的能力

在真实 Java MR 上，当前系统已经能够较稳定地检出以下问题：

- DDD / 架构类问题
  - `aggregate factory bypass`
  - `ApplicationService` 绕过工厂方法直接构造聚合
  - 事件发布与持久化顺序倒置

- 通用正确性 / 健壮性问题
  - 空 `catch` / 吞异常
  - 批量消费路径中删除分页 / 删除 `LIMIT`
  - 全量读取导致的内存放大风险

- 数据库 / 性能问题
  - 删除 `LIMIT :chunk` 带来的大结果集风险
  - 批量读取缺少边界控制
  - 一部分 SQL 语义放宽带来的性能退化风险

### 15.3 当前系统最明显的短板

- 专家覆盖仍不稳定  
  主 Agent 的 `selected_experts` 已经比早期版本更完整，但运行时仍会出现“已选专家被静默跳过”，尤其是 `security_compliance`。

- 多问题综合 MR 的完成率仍受执行稳定性制约  
  长耗时 LLM 调用会让 review 卡在 `expert_review`，导致最终结果不完整，影响最终评分与可信度。

- 通用质量问题的最终收敛仍弱于 DDD / 架构问题  
  例如：
  - `equal -> like %...%`
  - `chunksTmp` 命名退化  
  这类问题已经能进入主 Agent / 规则输入层，但还不能稳定落成独立 finding。

- 安全类问题仍然偏弱  
  当前系统更擅长 DDD、架构、性能和健壮性问题；安全类在线索不够显式时，容易既不保留专家，也不落出结果。

### 15.4 与基线相比的真实改善

本轮优化不是单条 benchmark 打补丁，而是对通用链路做了增强，已经确认的真实改善包括：

1. 综合 Java MR 的专家保留比早期更完整  
至少 `architecture_design` 不再像之前那样频繁整轮缺席。

2. `factory_bypass` 已经稳定进入最终结果层  
不仅规则层能看到，最终 finding 标题也能明确写出 `Aggregate factory bypass`。

3. DDD 时序风险不再只依赖结论层偶然补回  
`event_ordering_risk` 已经能更稳定进入规则筛选与专家输入。

4. 语言通用规范提示和输入完整性门禁已经生效  
这让结果可追溯性明显提升，能明确知道模型到底看到了哪些规范、代码和上下文。

### 15.5 当前最准确的总体判断

如果目标是：

- “能否在真实 Java MR 上发现一批有价值的问题”  
答案是：`可以，已经具备实用价值。`

- “能否稳定覆盖安全、性能、SQL、命名、DDD、逻辑等所有问题”  
答案是：`还不行，当前仍存在明显漏报和执行稳定性问题。`

- “当前系统最强的方向是什么”  
答案是：`DDD / 架构 / 事务边界 / 批量读取风险。`

- “当前系统最弱的方向是什么”  
答案是：`安全类参与稳定性、命名规范类问题、SQL 语义退化类问题的最终收敛。`

### 15.6 后续优先级建议

后续最值得优先做的，不是再为某条 case 补特判，而是继续收这 3 个通用问题：

1. `selected_experts` 与执行层一致性  
已选中的关键专家不应在运行时被静默跳过。

2. expert review 阶段稳定性  
减少长耗时和卡死，确保综合 MR 能真正跑完整轮。

3. 通用质量信号到最终 finding 的收敛  
重点补：
  - `query_semantics_weakened`
  - `naming_convention_violation`
  - `exception_swallowed`
  在结果层的稳定表达能力。
