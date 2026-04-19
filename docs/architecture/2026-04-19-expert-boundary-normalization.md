# 专家边界收口方案（解决重复提同类问题）

## 背景

当前专家体系已经能覆盖业务、架构、数据库、性能、安全、测试等多个方向，但在真实使用里有一个明显问题：

- 不同专家会对同一类问题同时发声
- 结果页会出现“同类问题重复表达”
- merge 层虽然会做去重，但根因并没有消失

最典型的例子是：

- 命名规范、可读性问题会被多个专家同时提到
- 业务正确性、DDD、架构设计会同时评论“职责放错层”
- 数据库专家和性能专家会同时指出批量读取、锁和事务问题

所以这次的目标不是“减少专家数量”，而是：

**给常见问题类别建立唯一主责专家，让其他专家只做协同参考，不再主提同一类问题。**

---

## 设计原则

### 1. 一个问题类别，只允许一个主责专家

例如：

- 命名规范 -> 只能由 `maintainability_code_health` 主提
- 注释或接口承诺未实现 -> 只能由 `correctness_business` 主提
- 聚合边界被绕过 -> 只能由 `ddd_specification` 主提

其他专家即使看到了，也只能作为背景判断，不应主提。

### 2. 专家按“问题责任边界”划分，而不是按技术名词堆角色

比如：

- `architecture_design` 负责通用分层与依赖方向
- `ddd_specification` 负责领域职责与聚合边界

这两者都会看到 service、application、repository，但评论角度必须不同。

### 3. merge 层只保留主责归因

如果多个专家都发现了同一个问题，最终结果层必须只保留一条，并且归因到唯一主责专家。

---

## 常见问题类别与主责专家映射

下面这张表是后续 prompt、专家路由、merge 归因的统一依据。

| 问题类别 | 主责专家 | 协同参考专家 | 说明 |
|---|---|---|---|
| 业务规则与状态流转 | `correctness_business` | `ddd_specification` | 先判断行为对不对 |
| 输入输出正确性与边界条件 | `correctness_business` | `security_compliance` | 空值、异常、边界输入、返回值一致性都归这里 |
| 注释 / TODO / 接口承诺与实现不一致 | `correctness_business` | `maintainability_code_health` | 这是实现正确性问题，不是代码风格问题 |
| 架构分层与依赖方向 | `architecture_design` | `ddd_specification` | 跨层直连、边界绕过、抽象泄漏归这里 |
| DDD 领域边界与职责归属 | `ddd_specification` | `architecture_design` | 聚合边界、领域纯度、应用服务编排越界归这里 |
| 数据库语义、事务、Schema、索引 | `database_analysis` | `performance_reliability` | SQL 正确性、事务边界、schema 兼容、索引归这里 |
| 缓存一致性与 Redis 使用 | `redis_analysis` | `performance_reliability` | TTL、热点 key、Lua、多 key 原子性归这里 |
| 消息队列可靠性与幂等 | `mq_analysis` | `correctness_business` | 顺序、幂等、重试、死信、堆积治理归这里 |
| 性能、并发、锁、失败恢复 | `performance_reliability` | `database_analysis` | 系统级性能、并发稳定性、故障恢复归这里 |
| 安全、鉴权、输入校验、敏感数据 | `security_compliance` | `correctness_business` | 攻击面、权限和敏感数据归这里 |
| 测试覆盖、断言质量、回归保护 | `test_verification` | `correctness_business` | 测试是否对这次改动形成保护归这里 |
| 可维护性、复杂度、命名、重复代码、可读性 | `maintainability_code_health` | `architecture_design` | 命名规范、可读性、复杂度统一归这里 |

---

## 专家主责边界

这一节的目的是把“这个专家应该主提什么，不应该主提什么”说死。

### 1. `correctness_business`

**主提范围**

- 业务规则错误
- 状态流转错误
- 输入输出与副作用不一致
- 边界条件和异常分支处理不完整
- 注释、方法名、接口说明、TODO 承诺的行为没有真正实现

**不要主提**

- 命名规范、复杂度、可读性
- 索引设计、事务细节
- 系统级性能和容量问题

**一句话**

它负责回答：**这段代码的行为有没有错。**

---

### 2. `architecture_design`

**主提范围**

- 分层错误
- 依赖方向错误
- 跨层直连
- 基础设施细节泄漏到高层
- 模块边界绕过
- 抽象层级退化

**不要主提**

- 聚合边界是否符合 DDD
- 命名和可读性
- 测试是否充足

**一句话**

它负责回答：**系统结构有没有被改坏。**

---

### 3. `ddd_specification`

**主提范围**

- 聚合边界被绕过
- 领域规则跑到应用层
- 应用服务堆业务逻辑
- 领域对象职责丢失
- 上下文边界和领域命名不一致

**不要主提**

- 泛化的“建议分层更清晰”
- SQL、索引、事务
- 纯性能问题

**一句话**

它负责回答：**领域职责有没有放错层。**

---

### 4. `database_analysis`

**主提范围**

- SQL 语义错误
- 事务、锁、回滚边界不完整
- Schema 变更兼容性问题
- 索引、默认值、约束不合理
- 迁移和数据模型演进风险

**不要主提**

- 系统级并发恢复问题
- 命名和代码风格
- DDD 分层

**一句话**

它负责回答：**数据库内部语义和边界有没有问题。**

---

### 5. `performance_reliability`

**主提范围**

- 热点路径退化
- 批处理过大
- 同步阻塞和重复 I/O
- 并发争用、锁放大、资源释放问题
- 超时、重试、回滚、降级、故障恢复缺口
- 局部故障放大成系统性压力

**不要主提**

- 具体索引建议
- SQL 字段设计
- 命名规范

**一句话**

它负责回答：**这段代码上线后稳不稳。**

---

### 6. `maintainability_code_health`

**主提范围**

- 命名规范
- 可读性差
- 重复代码
- 函数复杂度过高
- 职责揉在一起
- 抽象不清、难测试、难调试、演化成本高

**不要主提**

- 业务规则错误
- 架构分层错误
- 性能瓶颈
- 安全风险

**一句话**

它负责回答：**这段代码后面好不好维护。**

---

### 7. `security_compliance`

**主提范围**

- 鉴权授权
- 输入校验绕过
- 敏感数据处理
- 合规要求

**不要主提**

- 一般性的空值和边界处理
- 代码可读性
- 通用异常处理风格

**一句话**

它负责回答：**这段代码会不会破安全边界。**

---

### 8. `test_verification`

**主提范围**

- 缺测试
- 断言太弱
- 风险路径没有保护
- 回归保障不足
- 缺少自动化验证脚本或人工校验步骤

**不要主提**

- 功能逻辑本身错
- 命名不规范
- 架构设计问题

**一句话**

它负责回答：**这次改动有没有足够的保护网。**

---

### 9. `mq_analysis`

**主提范围**

- 幂等
- 顺序
- 重试
- 死信
- ack 和堆积治理

**一句话**

它负责回答：**消息链路会不会乱、丢、重。**

---

### 10. `redis_analysis`

**主提范围**

- 缓存一致性
- TTL 和失效策略
- 热点 key
- Lua / 事务 / 多 key 原子性

**一句话**

它负责回答：**缓存链路会不会脏、炸、放大。**

---

### 11. `frontend_accessibility`

当前这个专家定义还比较弱，建议先收窄成：

**主提范围**

- a11y
- 可访问性回归
- 关键渲染/交互可达性

在当前版本里，不建议让它泛化承担“前端所有体验问题”。

---

## 当前最容易重复的几类问题

### 1. 命名规范 / 可读性 / 重复代码

**统一归 `maintainability_code_health`**

其他专家不再主提：

- 命名不清晰
- 可读性一般
- 建议重构
- 方法太长
- 抽象不够优雅

除非这个命名问题**直接造成业务语义错误**，否则都不应由其他专家主提。

### 2. 注释 / TODO / 接口说明承诺未实现

**统一归 `correctness_business`**

这是“实现正确性”问题，不是文风问题，也不是可维护性问题。

### 3. 分层问题 vs DDD 问题

必须硬拆：

- 通用分层、依赖方向、跨层直连 -> `architecture_design`
- 聚合边界、领域职责、应用服务堆业务 -> `ddd_specification`

### 4. 数据库问题 vs 性能问题

必须硬拆：

- SQL、事务、schema、索引、DB 内部边界 -> `database_analysis`
- 批处理过大、并发争用、超时重试、故障放大 -> `performance_reliability`

---

## merge 层归因规则

即使多个专家都发现了，最终结果层也应该只保留**主责归因**。

建议采用如下规则：

| 如果问题核心是… | 最终归因到 |
|---|---|
| 行为错了、状态流转错了、承诺没实现 | `correctness_business` |
| 分层错了、依赖方向错了、跨层直连 | `architecture_design` |
| 聚合边界错了、领域职责错层 | `ddd_specification` |
| SQL / 事务 / schema / 索引问题 | `database_analysis` |
| 批处理、并发、失败恢复、系统压力问题 | `performance_reliability` |
| 命名、复杂度、可读性、重复代码 | `maintainability_code_health` |
| 权限、输入校验、敏感数据问题 | `security_compliance` |
| 测试覆盖、断言、回归保护问题 | `test_verification` |
| MQ 幂等、顺序、重试、死信问题 | `mq_analysis` |
| Redis 一致性、TTL、热点 key、Lua 原子性问题 | `redis_analysis` |

---

## 对 prompt 的改造建议

每个专家 prompt 里都建议补两段：

### A. 我负责什么

明确列出当前专家主提的问题类别。

### B. 这些问题不要主提

明确把容易重叠的问题交给其他专家。

例如 `correctness_business` 应补：

- 命名、复杂度和可读性问题交给 `maintainability_code_health`
- 索引与事务细节问题交给 `database_analysis`
- 系统级性能与并发瓶颈问题交给 `performance_reliability`

例如 `architecture_design` 应补：

- 聚合边界和领域职责交给 `ddd_specification`
- 命名和可读性问题交给 `maintainability_code_health`

---

## 优先落地顺序

这件事不要一次改太大，建议分三步。

### 第一步：先收口高频重复类别

优先改这 4 类：

1. 命名 / 可读性 / 重复代码 -> `maintainability_code_health`
2. 注释 / TODO / 接口承诺未实现 -> `correctness_business`
3. 架构分层 vs DDD -> `architecture_design` / `ddd_specification`
4. 数据库 vs 性能 -> `database_analysis` / `performance_reliability`

### 第二步：再收紧各专家 prompt

让专家知道：

- 我负责什么
- 我不负责什么
- 看到了也不要主提

### 第三步：最后改 merge 归因

即使上游还有少量重复，结果层也只保留主责归因，避免同类问题同时出现在结果页。

---

## 结论

这次要解决的不是“专家太多”，而是“同类问题没有唯一主责”。

**真正的解法不是先删专家，而是先把常见问题类别映射到唯一主责专家。**

这样后面不管是 prompt、专家路由，还是 merge 层，都有统一依据。
