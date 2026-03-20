# 专家代码检视规则 Markdown 模板

> 用途：把产品规则写成可解析、可绑定到专家、可由 LLM 预筛并进入正式审查的规则卡。
> 约束：每条规则必须以 `## RULE:` 开头；字段只保留产品规则约定的 9 个部分。

## RULE: PERF-SQL-001 大结果集查询必须显式分页或限流

### 一级场景
数据库访问

### 二级场景
查询性能

### 三级场景
大结果集分页缺失

### 描述
当本次 MR 修改了查询接口、仓储实现或 SQL 生成逻辑，且存在一次性返回大量数据的风险时，需要启用这条规则。重点关注未分页查询、未限制返回条数、热路径全表扫描、批量拉取后再内存过滤等问题。

### 问题代码示例
```java
List<OrderEntity> orders = orderRepository.findAll();
return orders.stream()
    .filter(OrderEntity::isActive)
    .toList();
```

### 问题代码行
List<OrderEntity> orders = orderRepository.findAll();

### 误报代码
```java
Page<OrderEntity> orders = orderRepository.findAll(PageRequest.of(0, 50));
return orders.getContent();
```

### 语言
java

### 问题级别
P1

---

## 编写建议

1. `一级场景 / 二级场景 / 三级场景` 要能稳定表达规则适用范围，避免多个独立问题混在同一条规则里。
2. `描述` 只写规则语义和适用条件，给预筛阶段的 LLM 使用，不要堆过多样例代码。
3. `问题代码示例 / 问题代码行 / 误报代码` 会在正式审查阶段提供给专家 LLM，用于判断是否真实违规和避免误报。
4. `问题级别` 统一使用 `P0 / P1 / P2 / P3`。
5. `语言` 建议写成单值；如果确实跨语言，可写成逗号分隔，系统会自动拆分。
