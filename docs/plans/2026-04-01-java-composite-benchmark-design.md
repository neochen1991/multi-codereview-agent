# Java Composite Benchmark Design

## Goal

构造两条更接近真实业务 MR 的 Java 综合评测用例，验证当前检视系统在一条 MR 中同时识别多类问题的能力，并输出“预期问题 vs 实际检出”的对比报告。

## Approach

采用双用例方案：

1. `spring-petclinic` 综合 MR
   - 偏通用 Java/Spring 业务项目
   - 重点覆盖：输入校验回退、SQL/查询性能、逻辑一致性、命名规范、代码编写错误

2. `java-ddd-example` 综合 MR
   - 偏 DDD/分层项目
   - 重点覆盖：DDD 边界、领域事件、应用服务越权、事务/查询风险、命名与逻辑错误

## Evaluation Dimensions

- 性能问题
- 代码编写错误
- 命名规则违规
- SQL / 查询问题
- DDD / 分层不规范
- 逻辑错误

## Expected Output

每条综合 MR 输出：

- 用例说明
- 人工预期问题清单
- 实际检出 findings / issues
- 专家覆盖情况
- 规则命中情况
- 漏报
- 误报
- 页面展示准确性

最终再生成一份汇总对比报告。

