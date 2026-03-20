# 性能与可靠性专家超长参考规范

> 绑定专家: `performance_reliability`
> 文档目标: 作为 Java 代码审查中的性能与稳定性长版参考，覆盖 JVM、并发、I/O、数据库、缓存、消息、批处理、容量治理与上线守卫。
> 使用约束: 审查时必须优先按章节命中检索，不允许把整篇文档整体注入 prompt。

## 文档使用原则

- 只对性能、容量、可靠性和资源安全问题给出结论。
- 看到数据库执行计划细节时，结论要联动数据库专家。
- 看到 Redis key/TTL 设计时，结论要联动 Redis 专家。
- 看到 MQ 投递语义、消费确认和死信时，结论要联动 MQ 专家。
- 没有线上压测、监控、指标或代码路径证据时，只能下“待验证风险”。
- 输出必须落到具体文件、具体代码行、具体放大链路和具体修复动作。

## 全局核对清单

- 请求路径是否存在超时边界。
- 重试是否会放大下游压力或重复写入。
- 线程池、连接池、队列、批量窗口是否具备容量上限。
- 缓存、消息、数据库与批处理是否会形成级联放大。
- 降级、熔断、限流、背压和回滚是否完整。
- 指标、日志、trace 与告警是否足以定位退化根因。

## 数据库访问与连接池治理

### HikariCP 连接池容量规划

- 风险定义: 连接池默认值直接沿用到高并发链路，导致等待队列过长、应用线程堆积、数据库雪崩放大。
- 审查重点:
  - 是否根据峰值 QPS、单请求持有连接时间、数据库核数和慢 SQL 上限反推 maxPoolSize。
  - 是否设置 connectionTimeout、validationTimeout、idleTimeout、maxLifetime，并与数据库侧超时协调。
  - 是否把在线查询与批量写入共用一个 DataSource。
  - 是否存在连接泄漏检测和池耗尽告警。
- 正例:
```java
@Bean
public DataSource orderQueryDataSource() {
    HikariConfig config = new HikariConfig();
    config.setPoolName("order-query-pool");
    config.setMaximumPoolSize(32);
    config.setMinimumIdle(8);
    config.setConnectionTimeout(800);
    config.setValidationTimeout(300);
    config.setLeakDetectionThreshold(5000);
    return new HikariDataSource(config);
}
```
- 反例:
```java
private final HikariDataSource dataSource = new HikariDataSource();

public OrderRepository() {
    dataSource.setMaximumPoolSize(200);
    dataSource.setConnectionTimeout(30000);
}
```
- 指标与告警:
  - hikaricp.connections.active
  - hikaricp.connections.pending
  - db.sql.slow
  - http.server.requests.p95
- 修复建议:
  - 按业务池拆分 DataSource。
  - 把等待时间和池大小与数据库连接上限联动建模。
  - 给池耗尽、慢 SQL、借还连接失败建立告警。

## 数据库访问与连接池治理

### JDBC batch 写入窗口控制

- 风险定义: 批量写入窗口过大时，会导致单事务时间过长、锁持有时间过长、内存占用上升和回滚成本失控。
- 审查重点:
  - 是否显式限定 batch size、flush 频率和单事务记录数。
  - 是否在批量任务中与在线流量共用连接池和数据库写热点索引。
  - 失败重试是否按幂等键切片，而不是整批重放。
  - 是否输出 batch 体积、耗时、失败条数和回滚条数指标。
- 正例:
```java
public void saveBatch(List<OrderRow> rows) {
    Lists.partition(rows, 200).forEach(chunk -> transactionTemplate.executeWithoutResult(status -> {
        jdbcTemplate.batchUpdate(SQL, new BatchPreparedStatementSetter() {
            @Override public int getBatchSize() { return chunk.size(); }
        });
    }));
}
```
- 反例:
```java
public void saveBatch(List<OrderRow> rows) {
    transactionTemplate.executeWithoutResult(status -> {
        jdbcTemplate.batchUpdate(SQL, rows, rows.size(), this::bind);
    });
}
```
- 指标与告警:
  - batch.write.size
  - batch.write.cost
  - db.lock.wait
  - db.transaction.rollback.count
- 修复建议:
  - 按分片窗口提交。
  - 把批量失败重试改成子批次补偿。
  - 拆离线池，避免挤占在线写路径。

## 数据库访问与连接池治理

### 慢 SQL 退化隔离

- 风险定义: 慢 SQL 在连接池耗尽、线程池堵塞和上游超时级联中起到放大器作用。
- 审查重点:
  - 是否对 SQL 耗时、返回行数和扫描行数设置观测。
  - 是否在热点接口上限制返回列、返回行数和排序字段。
  - 是否存在分页缺失、函数包裹索引列或 join 爆炸。
  - 是否把慢 SQL 失败隔离成降级结果，而不是把上游线程全部拖死。
- 正例:
```java
public Page<OrderView> query(OrderQuery query) {
    int pageSize = Math.min(query.pageSize(), 100);
    return mapper.selectByStatus(query.status(), pageSize, query.cursor());
}
```
- 反例:
```java
public List<OrderView> query(OrderQuery query) {
    return mapper.selectAllByStatus(query.status());
}
```
- 指标与告警:
  - db.query.rows
  - db.query.cost
  - db.query.scan.rows
  - app.thread.blocked
- 修复建议:
  - 给热点查询加分页和列裁剪。
  - 加超时与失败隔离。
  - 对 SQL 退化建立自动告警。

## Java 21 虚拟线程与结构化并发

### 虚拟线程 pinning 风险正反例

- 风险定义: 虚拟线程在 synchronized、native 阻塞或长时间持有监视器时会发生 pinning，导致载体线程被长期占住。
- 审查重点:
  - 是否在虚拟线程中进入粗粒度 synchronized 代码块。
  - 是否在持锁期间执行 JDBC、HTTP 或文件 I/O。
  - 是否使用 JFR pinned thread 事件监控 Loom 退化。
  - 是否把结构化并发与阻塞旧组件混用。
- 正例:
```java
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    Future<Order> orderFuture = scope.fork(() -> orderClient.fetch(orderId));
    Future<Customer> customerFuture = scope.fork(() -> customerClient.fetch(customerId));
    scope.join().throwIfFailed();
    return aggregate(orderFuture.resultNow(), customerFuture.resultNow());
}
```
- 反例:
```java
synchronized (sharedMonitor) {
    Thread.sleep(2000);
    return jdbcTemplate.queryForObject(SQL, mapper, id);
}
```
- 指标与告警:
  - jdk.virtualThreadPinned
  - executor.carrier.busy
  - http.client.latency
  - jfr.monitor.blocked
- 修复建议:
  - 缩小监视器范围。
  - 避免在持锁路径执行阻塞 I/O。
  - 对虚拟线程专用链路补 JFR 观测。

## 专题 1: JVM 启动与预热

- 专题摘要: 预热不足会把首波流量暴露给冷启动成本。
- 关键词: classloading / jit / tiered compilation / warmup
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T01-S01: 热点路径基线核对

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S01
- 风险概述: JVM 启动与预热 场景下若忽视“热点路径基线核对”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
List<List<JVM启动与预热Record>> partitions = Lists.partition(records, 200);
for (List<JVM启动与预热Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (JVM启动与预热Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S02: 线程池隔离策略

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S02
- 风险概述: JVM 启动与预热 场景下若忽视“线程池隔离策略”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    return client.query(request);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S03: 超时预算切分

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S03
- 风险概述: JVM 启动与预热 场景下若忽视“超时预算切分”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S04: 重试放大抑制

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S04
- 风险概述: JVM 启动与预热 场景下若忽视“重试放大抑制”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
List<List<JVM启动与预热Record>> partitions = Lists.partition(records, 200);
for (List<JVM启动与预热Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (JVM启动与预热Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S05: 限流与背压协同

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S05
- 风险概述: JVM 启动与预热 场景下若忽视“限流与背压协同”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    return client.query(request);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S06: 批量窗口与分片

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S06
- 风险概述: JVM 启动与预热 场景下若忽视“批量窗口与分片”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S07: 慢查询与慢调用旁路

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S07
- 风险概述: JVM 启动与预热 场景下若忽视“慢查询与慢调用旁路”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
List<List<JVM启动与预热Record>> partitions = Lists.partition(records, 200);
for (List<JVM启动与预热Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (JVM启动与预热Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S08: 缓存雪崩与击穿保护

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S08
- 风险概述: JVM 启动与预热 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    return client.query(request);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S09: 对象分配与复制控制

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S09
- 风险概述: JVM 启动与预热 场景下若忽视“对象分配与复制控制”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S10: 序列化与日志成本治理

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S10
- 风险概述: JVM 启动与预热 场景下若忽视“序列化与日志成本治理”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
List<List<JVM启动与预热Record>> partitions = Lists.partition(records, 200);
for (List<JVM启动与预热Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (JVM启动与预热Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S11: 指标与告警最小闭环

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S11
- 风险概述: JVM 启动与预热 场景下若忽视“指标与告警最小闭环”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public JVM启动与预热Result handle(JVM启动与预热Query request) {
    return client.query(request);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S12: 发布前容量守卫

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S12
- 风险概述: JVM 启动与预热 场景下若忽视“发布前容量守卫”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T01-S13: 故障恢复与回滚准则

- 主题: JVM 启动与预热
- 场景编号: PERF-T01-S13
- 风险概述: JVM 启动与预热 场景下若忽视“故障恢复与回滚准则”，常见后果是 预热不足会把首波流量暴露给冷启动成本。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 classloading / jit / tiered compilation
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 JVM 启动与预热 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.1.latency.p95
  - app.1.timeout.count
  - app.1.queue.depth
  - app.1.error.ratio
- 正例:
```java
List<List<JVM启动与预热Record>> partitions = Lists.partition(records, 200);
for (List<JVM启动与预热Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (JVM启动与预热Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - JVM 启动与预热 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 JVM 启动与预热 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 2: GC 与堆内存治理

- 专题摘要: 不合理的堆与对象分配会引起停顿放大。
- 关键词: g1 gc / young gc / mixed gc / heap
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T02-S01: 热点路径基线核对

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S01
- 风险概述: GC 与堆内存治理 场景下若忽视“热点路径基线核对”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    return client.query(request);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S02: 线程池隔离策略

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S02
- 风险概述: GC 与堆内存治理 场景下若忽视“线程池隔离策略”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S03: 超时预算切分

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S03
- 风险概述: GC 与堆内存治理 场景下若忽视“超时预算切分”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
List<List<GC与堆内存治理Record>> partitions = Lists.partition(records, 200);
for (List<GC与堆内存治理Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (GC与堆内存治理Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S04: 重试放大抑制

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S04
- 风险概述: GC 与堆内存治理 场景下若忽视“重试放大抑制”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    return client.query(request);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S05: 限流与背压协同

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S05
- 风险概述: GC 与堆内存治理 场景下若忽视“限流与背压协同”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S06: 批量窗口与分片

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S06
- 风险概述: GC 与堆内存治理 场景下若忽视“批量窗口与分片”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
List<List<GC与堆内存治理Record>> partitions = Lists.partition(records, 200);
for (List<GC与堆内存治理Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (GC与堆内存治理Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S07: 慢查询与慢调用旁路

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S07
- 风险概述: GC 与堆内存治理 场景下若忽视“慢查询与慢调用旁路”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    return client.query(request);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S08: 缓存雪崩与击穿保护

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S08
- 风险概述: GC 与堆内存治理 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S09: 对象分配与复制控制

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S09
- 风险概述: GC 与堆内存治理 场景下若忽视“对象分配与复制控制”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
List<List<GC与堆内存治理Record>> partitions = Lists.partition(records, 200);
for (List<GC与堆内存治理Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (GC与堆内存治理Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S10: 序列化与日志成本治理

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S10
- 风险概述: GC 与堆内存治理 场景下若忽视“序列化与日志成本治理”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    return client.query(request);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S11: 指标与告警最小闭环

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S11
- 风险概述: GC 与堆内存治理 场景下若忽视“指标与告警最小闭环”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S12: 发布前容量守卫

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S12
- 风险概述: GC 与堆内存治理 场景下若忽视“发布前容量守卫”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
List<List<GC与堆内存治理Record>> partitions = Lists.partition(records, 200);
for (List<GC与堆内存治理Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (GC与堆内存治理Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T02-S13: 故障恢复与回滚准则

- 主题: GC 与堆内存治理
- 场景编号: PERF-T02-S13
- 风险概述: GC 与堆内存治理 场景下若忽视“故障恢复与回滚准则”，常见后果是 不合理的堆与对象分配会引起停顿放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 g1 gc / young gc / mixed gc
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 GC 与堆内存治理 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.2.latency.p95
  - app.2.timeout.count
  - app.2.queue.depth
  - app.2.error.ratio
- 正例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public GC与堆内存治理Result handle(GC与堆内存治理Query request) {
    return client.query(request);
}
```
- 评审追问:
  - GC 与堆内存治理 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 GC 与堆内存治理 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 3: 线程池与执行器隔离

- 专题摘要: 公共池混用会导致关键链路饿死。
- 关键词: threadpool / executor / queue / reject
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T03-S01: 热点路径基线核对

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S01
- 风险概述: 线程池与执行器隔离 场景下若忽视“热点路径基线核对”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S02: 线程池隔离策略

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S02
- 风险概述: 线程池与执行器隔离 场景下若忽视“线程池隔离策略”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
List<List<线程池与执行器隔离Record>> partitions = Lists.partition(records, 200);
for (List<线程池与执行器隔离Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (线程池与执行器隔离Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S03: 超时预算切分

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S03
- 风险概述: 线程池与执行器隔离 场景下若忽视“超时预算切分”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S04: 重试放大抑制

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S04
- 风险概述: 线程池与执行器隔离 场景下若忽视“重试放大抑制”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S05: 限流与背压协同

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S05
- 风险概述: 线程池与执行器隔离 场景下若忽视“限流与背压协同”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
List<List<线程池与执行器隔离Record>> partitions = Lists.partition(records, 200);
for (List<线程池与执行器隔离Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (线程池与执行器隔离Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S06: 批量窗口与分片

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S06
- 风险概述: 线程池与执行器隔离 场景下若忽视“批量窗口与分片”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S07: 慢查询与慢调用旁路

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S07
- 风险概述: 线程池与执行器隔离 场景下若忽视“慢查询与慢调用旁路”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S08: 缓存雪崩与击穿保护

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S08
- 风险概述: 线程池与执行器隔离 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
List<List<线程池与执行器隔离Record>> partitions = Lists.partition(records, 200);
for (List<线程池与执行器隔离Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (线程池与执行器隔离Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S09: 对象分配与复制控制

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S09
- 风险概述: 线程池与执行器隔离 场景下若忽视“对象分配与复制控制”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S10: 序列化与日志成本治理

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S10
- 风险概述: 线程池与执行器隔离 场景下若忽视“序列化与日志成本治理”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S11: 指标与告警最小闭环

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S11
- 风险概述: 线程池与执行器隔离 场景下若忽视“指标与告警最小闭环”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
List<List<线程池与执行器隔离Record>> partitions = Lists.partition(records, 200);
for (List<线程池与执行器隔离Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (线程池与执行器隔离Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S12: 发布前容量守卫

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S12
- 风险概述: 线程池与执行器隔离 场景下若忽视“发布前容量守卫”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 线程池与执行器隔离Result handle(线程池与执行器隔离Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T03-S13: 故障恢复与回滚准则

- 主题: 线程池与执行器隔离
- 场景编号: PERF-T03-S13
- 风险概述: 线程池与执行器隔离 场景下若忽视“故障恢复与回滚准则”，常见后果是 公共池混用会导致关键链路饿死。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 threadpool / executor / queue
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 线程池与执行器隔离 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.3.latency.p95
  - app.3.timeout.count
  - app.3.queue.depth
  - app.3.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 线程池与执行器隔离 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 线程池与执行器隔离 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 4: 锁竞争与并发控制

- 专题摘要: 锁粒度过大时延迟会在峰值期放大。
- 关键词: synchronized / lock / cas / contention
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T04-S01: 热点路径基线核对

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S01
- 风险概述: 锁竞争与并发控制 场景下若忽视“热点路径基线核对”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
List<List<锁竞争与并发控制Record>> partitions = Lists.partition(records, 200);
for (List<锁竞争与并发控制Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (锁竞争与并发控制Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S02: 线程池隔离策略

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S02
- 风险概述: 锁竞争与并发控制 场景下若忽视“线程池隔离策略”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S03: 超时预算切分

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S03
- 风险概述: 锁竞争与并发控制 场景下若忽视“超时预算切分”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S04: 重试放大抑制

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S04
- 风险概述: 锁竞争与并发控制 场景下若忽视“重试放大抑制”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
List<List<锁竞争与并发控制Record>> partitions = Lists.partition(records, 200);
for (List<锁竞争与并发控制Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (锁竞争与并发控制Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S05: 限流与背压协同

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S05
- 风险概述: 锁竞争与并发控制 场景下若忽视“限流与背压协同”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S06: 批量窗口与分片

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S06
- 风险概述: 锁竞争与并发控制 场景下若忽视“批量窗口与分片”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S07: 慢查询与慢调用旁路

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S07
- 风险概述: 锁竞争与并发控制 场景下若忽视“慢查询与慢调用旁路”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
List<List<锁竞争与并发控制Record>> partitions = Lists.partition(records, 200);
for (List<锁竞争与并发控制Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (锁竞争与并发控制Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S08: 缓存雪崩与击穿保护

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S08
- 风险概述: 锁竞争与并发控制 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S09: 对象分配与复制控制

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S09
- 风险概述: 锁竞争与并发控制 场景下若忽视“对象分配与复制控制”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S10: 序列化与日志成本治理

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S10
- 风险概述: 锁竞争与并发控制 场景下若忽视“序列化与日志成本治理”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
List<List<锁竞争与并发控制Record>> partitions = Lists.partition(records, 200);
for (List<锁竞争与并发控制Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (锁竞争与并发控制Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S11: 指标与告警最小闭环

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S11
- 风险概述: 锁竞争与并发控制 场景下若忽视“指标与告警最小闭环”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 锁竞争与并发控制Result handle(锁竞争与并发控制Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S12: 发布前容量守卫

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S12
- 风险概述: 锁竞争与并发控制 场景下若忽视“发布前容量守卫”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T04-S13: 故障恢复与回滚准则

- 主题: 锁竞争与并发控制
- 场景编号: PERF-T04-S13
- 风险概述: 锁竞争与并发控制 场景下若忽视“故障恢复与回滚准则”，常见后果是 锁粒度过大时延迟会在峰值期放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 synchronized / lock / cas
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 锁竞争与并发控制 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.4.latency.p95
  - app.4.timeout.count
  - app.4.queue.depth
  - app.4.error.ratio
- 正例:
```java
List<List<锁竞争与并发控制Record>> partitions = Lists.partition(records, 200);
for (List<锁竞争与并发控制Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (锁竞争与并发控制Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 锁竞争与并发控制 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 锁竞争与并发控制 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 5: 对象分配与逃逸分析

- 专题摘要: 热点路径对象抖动会制造 GC 压力。
- 关键词: allocation / escape analysis / boxing / copy
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T05-S01: 热点路径基线核对

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S01
- 风险概述: 对象分配与逃逸分析 场景下若忽视“热点路径基线核对”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S02: 线程池隔离策略

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S02
- 风险概述: 对象分配与逃逸分析 场景下若忽视“线程池隔离策略”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S03: 超时预算切分

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S03
- 风险概述: 对象分配与逃逸分析 场景下若忽视“超时预算切分”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
List<List<对象分配与逃逸分析Record>> partitions = Lists.partition(records, 200);
for (List<对象分配与逃逸分析Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (对象分配与逃逸分析Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S04: 重试放大抑制

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S04
- 风险概述: 对象分配与逃逸分析 场景下若忽视“重试放大抑制”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S05: 限流与背压协同

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S05
- 风险概述: 对象分配与逃逸分析 场景下若忽视“限流与背压协同”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S06: 批量窗口与分片

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S06
- 风险概述: 对象分配与逃逸分析 场景下若忽视“批量窗口与分片”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
List<List<对象分配与逃逸分析Record>> partitions = Lists.partition(records, 200);
for (List<对象分配与逃逸分析Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (对象分配与逃逸分析Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S07: 慢查询与慢调用旁路

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S07
- 风险概述: 对象分配与逃逸分析 场景下若忽视“慢查询与慢调用旁路”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S08: 缓存雪崩与击穿保护

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S08
- 风险概述: 对象分配与逃逸分析 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S09: 对象分配与复制控制

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S09
- 风险概述: 对象分配与逃逸分析 场景下若忽视“对象分配与复制控制”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
List<List<对象分配与逃逸分析Record>> partitions = Lists.partition(records, 200);
for (List<对象分配与逃逸分析Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (对象分配与逃逸分析Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S10: 序列化与日志成本治理

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S10
- 风险概述: 对象分配与逃逸分析 场景下若忽视“序列化与日志成本治理”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S11: 指标与告警最小闭环

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S11
- 风险概述: 对象分配与逃逸分析 场景下若忽视“指标与告警最小闭环”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S12: 发布前容量守卫

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S12
- 风险概述: 对象分配与逃逸分析 场景下若忽视“发布前容量守卫”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
List<List<对象分配与逃逸分析Record>> partitions = Lists.partition(records, 200);
for (List<对象分配与逃逸分析Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (对象分配与逃逸分析Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T05-S13: 故障恢复与回滚准则

- 主题: 对象分配与逃逸分析
- 场景编号: PERF-T05-S13
- 风险概述: 对象分配与逃逸分析 场景下若忽视“故障恢复与回滚准则”，常见后果是 热点路径对象抖动会制造 GC 压力。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 allocation / escape analysis / boxing
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 对象分配与逃逸分析 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.5.latency.p95
  - app.5.timeout.count
  - app.5.queue.depth
  - app.5.error.ratio
- 正例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 对象分配与逃逸分析Result handle(对象分配与逃逸分析Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 对象分配与逃逸分析 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 对象分配与逃逸分析 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 6: HTTP 客户端与连接复用

- 专题摘要: 连接复用不足和超时失配会拖慢出站调用。
- 关键词: http client / keepalive / dns / tls
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T06-S01: 热点路径基线核对

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S01
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“热点路径基线核对”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S02: 线程池隔离策略

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S02
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“线程池隔离策略”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
List<List<HTTP客户端与连接复用Record>> partitions = Lists.partition(records, 200);
for (List<HTTP客户端与连接复用Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (HTTP客户端与连接复用Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S03: 超时预算切分

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S03
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“超时预算切分”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    return client.query(request);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S04: 重试放大抑制

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S04
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“重试放大抑制”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S05: 限流与背压协同

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S05
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“限流与背压协同”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
List<List<HTTP客户端与连接复用Record>> partitions = Lists.partition(records, 200);
for (List<HTTP客户端与连接复用Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (HTTP客户端与连接复用Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S06: 批量窗口与分片

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S06
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“批量窗口与分片”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    return client.query(request);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S07: 慢查询与慢调用旁路

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S07
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“慢查询与慢调用旁路”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S08: 缓存雪崩与击穿保护

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S08
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
List<List<HTTP客户端与连接复用Record>> partitions = Lists.partition(records, 200);
for (List<HTTP客户端与连接复用Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (HTTP客户端与连接复用Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S09: 对象分配与复制控制

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S09
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“对象分配与复制控制”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    return client.query(request);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S10: 序列化与日志成本治理

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S10
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“序列化与日志成本治理”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S11: 指标与告警最小闭环

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S11
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“指标与告警最小闭环”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
List<List<HTTP客户端与连接复用Record>> partitions = Lists.partition(records, 200);
for (List<HTTP客户端与连接复用Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (HTTP客户端与连接复用Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S12: 发布前容量守卫

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S12
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“发布前容量守卫”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public HTTP客户端与连接复用Result handle(HTTP客户端与连接复用Query request) {
    return client.query(request);
}
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T06-S13: 故障恢复与回滚准则

- 主题: HTTP 客户端与连接复用
- 场景编号: PERF-T06-S13
- 风险概述: HTTP 客户端与连接复用 场景下若忽视“故障恢复与回滚准则”，常见后果是 连接复用不足和超时失配会拖慢出站调用。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 http client / keepalive / dns
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 HTTP 客户端与连接复用 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.6.latency.p95
  - app.6.timeout.count
  - app.6.queue.depth
  - app.6.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - HTTP 客户端与连接复用 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 HTTP 客户端与连接复用 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 7: 文件 I/O 与零拷贝

- 专题摘要: 大文件读写若未做分块和限速会冲垮磁盘。
- 关键词: nio / mmap / zero copy / disk io
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T07-S01: 热点路径基线核对

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S01
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“热点路径基线核对”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
List<List<文件I/O与零拷贝Record>> partitions = Lists.partition(records, 200);
for (List<文件I/O与零拷贝Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (文件I/O与零拷贝Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S02: 线程池隔离策略

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S02
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“线程池隔离策略”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S03: 超时预算切分

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S03
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“超时预算切分”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S04: 重试放大抑制

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S04
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“重试放大抑制”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
List<List<文件I/O与零拷贝Record>> partitions = Lists.partition(records, 200);
for (List<文件I/O与零拷贝Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (文件I/O与零拷贝Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S05: 限流与背压协同

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S05
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“限流与背压协同”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S06: 批量窗口与分片

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S06
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“批量窗口与分片”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S07: 慢查询与慢调用旁路

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S07
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“慢查询与慢调用旁路”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
List<List<文件I/O与零拷贝Record>> partitions = Lists.partition(records, 200);
for (List<文件I/O与零拷贝Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (文件I/O与零拷贝Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S08: 缓存雪崩与击穿保护

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S08
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S09: 对象分配与复制控制

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S09
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“对象分配与复制控制”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S10: 序列化与日志成本治理

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S10
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“序列化与日志成本治理”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
List<List<文件I/O与零拷贝Record>> partitions = Lists.partition(records, 200);
for (List<文件I/O与零拷贝Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (文件I/O与零拷贝Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S11: 指标与告警最小闭环

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S11
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“指标与告警最小闭环”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 文件I/O与零拷贝Result handle(文件I/O与零拷贝Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S12: 发布前容量守卫

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S12
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“发布前容量守卫”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T07-S13: 故障恢复与回滚准则

- 主题: 文件 I/O 与零拷贝
- 场景编号: PERF-T07-S13
- 风险概述: 文件 I/O 与零拷贝 场景下若忽视“故障恢复与回滚准则”，常见后果是 大文件读写若未做分块和限速会冲垮磁盘。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 nio / mmap / zero copy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 文件 I/O 与零拷贝 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.7.latency.p95
  - app.7.timeout.count
  - app.7.queue.depth
  - app.7.error.ratio
- 正例:
```java
List<List<文件I/O与零拷贝Record>> partitions = Lists.partition(records, 200);
for (List<文件I/O与零拷贝Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (文件I/O与零拷贝Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 文件 I/O 与零拷贝 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 文件 I/O 与零拷贝 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 8: 序列化与压缩

- 专题摘要: 不必要的序列化层级会推高 CPU。
- 关键词: json / protobuf / snappy / gzip
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T08-S01: 热点路径基线核对

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S01
- 风险概述: 序列化与压缩 场景下若忽视“热点路径基线核对”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S02: 线程池隔离策略

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S02
- 风险概述: 序列化与压缩 场景下若忽视“线程池隔离策略”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S03: 超时预算切分

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S03
- 风险概述: 序列化与压缩 场景下若忽视“超时预算切分”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
List<List<序列化与压缩Record>> partitions = Lists.partition(records, 200);
for (List<序列化与压缩Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (序列化与压缩Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S04: 重试放大抑制

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S04
- 风险概述: 序列化与压缩 场景下若忽视“重试放大抑制”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S05: 限流与背压协同

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S05
- 风险概述: 序列化与压缩 场景下若忽视“限流与背压协同”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S06: 批量窗口与分片

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S06
- 风险概述: 序列化与压缩 场景下若忽视“批量窗口与分片”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
List<List<序列化与压缩Record>> partitions = Lists.partition(records, 200);
for (List<序列化与压缩Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (序列化与压缩Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S07: 慢查询与慢调用旁路

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S07
- 风险概述: 序列化与压缩 场景下若忽视“慢查询与慢调用旁路”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S08: 缓存雪崩与击穿保护

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S08
- 风险概述: 序列化与压缩 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S09: 对象分配与复制控制

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S09
- 风险概述: 序列化与压缩 场景下若忽视“对象分配与复制控制”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
List<List<序列化与压缩Record>> partitions = Lists.partition(records, 200);
for (List<序列化与压缩Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (序列化与压缩Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S10: 序列化与日志成本治理

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S10
- 风险概述: 序列化与压缩 场景下若忽视“序列化与日志成本治理”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S11: 指标与告警最小闭环

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S11
- 风险概述: 序列化与压缩 场景下若忽视“指标与告警最小闭环”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S12: 发布前容量守卫

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S12
- 风险概述: 序列化与压缩 场景下若忽视“发布前容量守卫”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
List<List<序列化与压缩Record>> partitions = Lists.partition(records, 200);
for (List<序列化与压缩Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (序列化与压缩Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T08-S13: 故障恢复与回滚准则

- 主题: 序列化与压缩
- 场景编号: PERF-T08-S13
- 风险概述: 序列化与压缩 场景下若忽视“故障恢复与回滚准则”，常见后果是 不必要的序列化层级会推高 CPU。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 json / protobuf / snappy
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 序列化与压缩 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.8.latency.p95
  - app.8.timeout.count
  - app.8.queue.depth
  - app.8.error.ratio
- 正例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 序列化与压缩Result handle(序列化与压缩Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 序列化与压缩 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 序列化与压缩 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 9: 缓存一致性与热点保护

- 专题摘要: 热点数据保护缺失会让后端雪崩。
- 关键词: cache / hot key / stampede / ttl
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T09-S01: 热点路径基线核对

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S01
- 风险概述: 缓存一致性与热点保护 场景下若忽视“热点路径基线核对”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S02: 线程池隔离策略

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S02
- 风险概述: 缓存一致性与热点保护 场景下若忽视“线程池隔离策略”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
List<List<缓存一致性与热点保护Record>> partitions = Lists.partition(records, 200);
for (List<缓存一致性与热点保护Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (缓存一致性与热点保护Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S03: 超时预算切分

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S03
- 风险概述: 缓存一致性与热点保护 场景下若忽视“超时预算切分”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S04: 重试放大抑制

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S04
- 风险概述: 缓存一致性与热点保护 场景下若忽视“重试放大抑制”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S05: 限流与背压协同

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S05
- 风险概述: 缓存一致性与热点保护 场景下若忽视“限流与背压协同”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
List<List<缓存一致性与热点保护Record>> partitions = Lists.partition(records, 200);
for (List<缓存一致性与热点保护Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (缓存一致性与热点保护Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S06: 批量窗口与分片

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S06
- 风险概述: 缓存一致性与热点保护 场景下若忽视“批量窗口与分片”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S07: 慢查询与慢调用旁路

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S07
- 风险概述: 缓存一致性与热点保护 场景下若忽视“慢查询与慢调用旁路”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S08: 缓存雪崩与击穿保护

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S08
- 风险概述: 缓存一致性与热点保护 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
List<List<缓存一致性与热点保护Record>> partitions = Lists.partition(records, 200);
for (List<缓存一致性与热点保护Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (缓存一致性与热点保护Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S09: 对象分配与复制控制

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S09
- 风险概述: 缓存一致性与热点保护 场景下若忽视“对象分配与复制控制”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S10: 序列化与日志成本治理

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S10
- 风险概述: 缓存一致性与热点保护 场景下若忽视“序列化与日志成本治理”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S11: 指标与告警最小闭环

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S11
- 风险概述: 缓存一致性与热点保护 场景下若忽视“指标与告警最小闭环”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
List<List<缓存一致性与热点保护Record>> partitions = Lists.partition(records, 200);
for (List<缓存一致性与热点保护Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (缓存一致性与热点保护Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S12: 发布前容量守卫

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S12
- 风险概述: 缓存一致性与热点保护 场景下若忽视“发布前容量守卫”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 缓存一致性与热点保护Result handle(缓存一致性与热点保护Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T09-S13: 故障恢复与回滚准则

- 主题: 缓存一致性与热点保护
- 场景编号: PERF-T09-S13
- 风险概述: 缓存一致性与热点保护 场景下若忽视“故障恢复与回滚准则”，常见后果是 热点数据保护缺失会让后端雪崩。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 cache / hot key / stampede
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 缓存一致性与热点保护 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.9.latency.p95
  - app.9.timeout.count
  - app.9.queue.depth
  - app.9.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 缓存一致性与热点保护 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 缓存一致性与热点保护 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 10: 消息消费与背压

- 专题摘要: 消费端扩缩容不当会形成堆积。
- 关键词: mq / consumer lag / backpressure / rebalance
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T10-S01: 热点路径基线核对

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S01
- 风险概述: 消息消费与背压 场景下若忽视“热点路径基线核对”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
List<List<消息消费与背压Record>> partitions = Lists.partition(records, 200);
for (List<消息消费与背压Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (消息消费与背压Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S02: 线程池隔离策略

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S02
- 风险概述: 消息消费与背压 场景下若忽视“线程池隔离策略”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S03: 超时预算切分

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S03
- 风险概述: 消息消费与背压 场景下若忽视“超时预算切分”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S04: 重试放大抑制

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S04
- 风险概述: 消息消费与背压 场景下若忽视“重试放大抑制”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
List<List<消息消费与背压Record>> partitions = Lists.partition(records, 200);
for (List<消息消费与背压Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (消息消费与背压Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S05: 限流与背压协同

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S05
- 风险概述: 消息消费与背压 场景下若忽视“限流与背压协同”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S06: 批量窗口与分片

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S06
- 风险概述: 消息消费与背压 场景下若忽视“批量窗口与分片”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S07: 慢查询与慢调用旁路

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S07
- 风险概述: 消息消费与背压 场景下若忽视“慢查询与慢调用旁路”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
List<List<消息消费与背压Record>> partitions = Lists.partition(records, 200);
for (List<消息消费与背压Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (消息消费与背压Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S08: 缓存雪崩与击穿保护

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S08
- 风险概述: 消息消费与背压 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S09: 对象分配与复制控制

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S09
- 风险概述: 消息消费与背压 场景下若忽视“对象分配与复制控制”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S10: 序列化与日志成本治理

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S10
- 风险概述: 消息消费与背压 场景下若忽视“序列化与日志成本治理”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
List<List<消息消费与背压Record>> partitions = Lists.partition(records, 200);
for (List<消息消费与背压Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (消息消费与背压Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S11: 指标与告警最小闭环

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S11
- 风险概述: 消息消费与背压 场景下若忽视“指标与告警最小闭环”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 消息消费与背压Result handle(消息消费与背压Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S12: 发布前容量守卫

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S12
- 风险概述: 消息消费与背压 场景下若忽视“发布前容量守卫”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T10-S13: 故障恢复与回滚准则

- 主题: 消息消费与背压
- 场景编号: PERF-T10-S13
- 风险概述: 消息消费与背压 场景下若忽视“故障恢复与回滚准则”，常见后果是 消费端扩缩容不当会形成堆积。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 mq / consumer lag / backpressure
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 消息消费与背压 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.10.latency.p95
  - app.10.timeout.count
  - app.10.queue.depth
  - app.10.error.ratio
- 正例:
```java
List<List<消息消费与背压Record>> partitions = Lists.partition(records, 200);
for (List<消息消费与背压Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (消息消费与背压Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 消息消费与背压 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 消息消费与背压 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 11: 批处理与离线任务

- 专题摘要: 离线任务抢占在线资源会损伤 SLA。
- 关键词: batch / window / scheduler / cron
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T11-S01: 热点路径基线核对

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S01
- 风险概述: 批处理与离线任务 场景下若忽视“热点路径基线核对”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S02: 线程池隔离策略

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S02
- 风险概述: 批处理与离线任务 场景下若忽视“线程池隔离策略”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S03: 超时预算切分

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S03
- 风险概述: 批处理与离线任务 场景下若忽视“超时预算切分”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
List<List<批处理与离线任务Record>> partitions = Lists.partition(records, 200);
for (List<批处理与离线任务Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (批处理与离线任务Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S04: 重试放大抑制

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S04
- 风险概述: 批处理与离线任务 场景下若忽视“重试放大抑制”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S05: 限流与背压协同

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S05
- 风险概述: 批处理与离线任务 场景下若忽视“限流与背压协同”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S06: 批量窗口与分片

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S06
- 风险概述: 批处理与离线任务 场景下若忽视“批量窗口与分片”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
List<List<批处理与离线任务Record>> partitions = Lists.partition(records, 200);
for (List<批处理与离线任务Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (批处理与离线任务Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S07: 慢查询与慢调用旁路

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S07
- 风险概述: 批处理与离线任务 场景下若忽视“慢查询与慢调用旁路”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S08: 缓存雪崩与击穿保护

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S08
- 风险概述: 批处理与离线任务 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S09: 对象分配与复制控制

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S09
- 风险概述: 批处理与离线任务 场景下若忽视“对象分配与复制控制”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
List<List<批处理与离线任务Record>> partitions = Lists.partition(records, 200);
for (List<批处理与离线任务Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (批处理与离线任务Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S10: 序列化与日志成本治理

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S10
- 风险概述: 批处理与离线任务 场景下若忽视“序列化与日志成本治理”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S11: 指标与告警最小闭环

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S11
- 风险概述: 批处理与离线任务 场景下若忽视“指标与告警最小闭环”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S12: 发布前容量守卫

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S12
- 风险概述: 批处理与离线任务 场景下若忽视“发布前容量守卫”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
List<List<批处理与离线任务Record>> partitions = Lists.partition(records, 200);
for (List<批处理与离线任务Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (批处理与离线任务Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T11-S13: 故障恢复与回滚准则

- 主题: 批处理与离线任务
- 场景编号: PERF-T11-S13
- 风险概述: 批处理与离线任务 场景下若忽视“故障恢复与回滚准则”，常见后果是 离线任务抢占在线资源会损伤 SLA。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 batch / window / scheduler
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 批处理与离线任务 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.11.latency.p95
  - app.11.timeout.count
  - app.11.queue.depth
  - app.11.error.ratio
- 正例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 批处理与离线任务Result handle(批处理与离线任务Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 批处理与离线任务 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 批处理与离线任务 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 12: 限流熔断与降级

- 专题摘要: 保护策略失配会让故障被重试放大。
- 关键词: ratelimit / circuit breaker / fallback / bulkhead
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T12-S01: 热点路径基线核对

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S01
- 风险概述: 限流熔断与降级 场景下若忽视“热点路径基线核对”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S02: 线程池隔离策略

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S02
- 风险概述: 限流熔断与降级 场景下若忽视“线程池隔离策略”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
List<List<限流熔断与降级Record>> partitions = Lists.partition(records, 200);
for (List<限流熔断与降级Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (限流熔断与降级Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S03: 超时预算切分

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S03
- 风险概述: 限流熔断与降级 场景下若忽视“超时预算切分”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S04: 重试放大抑制

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S04
- 风险概述: 限流熔断与降级 场景下若忽视“重试放大抑制”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S05: 限流与背压协同

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S05
- 风险概述: 限流熔断与降级 场景下若忽视“限流与背压协同”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
List<List<限流熔断与降级Record>> partitions = Lists.partition(records, 200);
for (List<限流熔断与降级Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (限流熔断与降级Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S06: 批量窗口与分片

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S06
- 风险概述: 限流熔断与降级 场景下若忽视“批量窗口与分片”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S07: 慢查询与慢调用旁路

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S07
- 风险概述: 限流熔断与降级 场景下若忽视“慢查询与慢调用旁路”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S08: 缓存雪崩与击穿保护

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S08
- 风险概述: 限流熔断与降级 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
List<List<限流熔断与降级Record>> partitions = Lists.partition(records, 200);
for (List<限流熔断与降级Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (限流熔断与降级Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S09: 对象分配与复制控制

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S09
- 风险概述: 限流熔断与降级 场景下若忽视“对象分配与复制控制”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S10: 序列化与日志成本治理

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S10
- 风险概述: 限流熔断与降级 场景下若忽视“序列化与日志成本治理”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S11: 指标与告警最小闭环

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S11
- 风险概述: 限流熔断与降级 场景下若忽视“指标与告警最小闭环”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
List<List<限流熔断与降级Record>> partitions = Lists.partition(records, 200);
for (List<限流熔断与降级Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (限流熔断与降级Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S12: 发布前容量守卫

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S12
- 风险概述: 限流熔断与降级 场景下若忽视“发布前容量守卫”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 限流熔断与降级Result handle(限流熔断与降级Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T12-S13: 故障恢复与回滚准则

- 主题: 限流熔断与降级
- 场景编号: PERF-T12-S13
- 风险概述: 限流熔断与降级 场景下若忽视“故障恢复与回滚准则”，常见后果是 保护策略失配会让故障被重试放大。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 ratelimit / circuit breaker / fallback
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 限流熔断与降级 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.12.latency.p95
  - app.12.timeout.count
  - app.12.queue.depth
  - app.12.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 限流熔断与降级 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 限流熔断与降级 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 13: 日志与可观测性成本

- 专题摘要: 观测体系本身也可能成为性能负担。
- 关键词: logging / trace / metric / cardinality
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T13-S01: 热点路径基线核对

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S01
- 风险概述: 日志与可观测性成本 场景下若忽视“热点路径基线核对”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
List<List<日志与可观测性成本Record>> partitions = Lists.partition(records, 200);
for (List<日志与可观测性成本Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (日志与可观测性成本Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S02: 线程池隔离策略

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S02
- 风险概述: 日志与可观测性成本 场景下若忽视“线程池隔离策略”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S03: 超时预算切分

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S03
- 风险概述: 日志与可观测性成本 场景下若忽视“超时预算切分”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S04: 重试放大抑制

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S04
- 风险概述: 日志与可观测性成本 场景下若忽视“重试放大抑制”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
List<List<日志与可观测性成本Record>> partitions = Lists.partition(records, 200);
for (List<日志与可观测性成本Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (日志与可观测性成本Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S05: 限流与背压协同

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S05
- 风险概述: 日志与可观测性成本 场景下若忽视“限流与背压协同”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S06: 批量窗口与分片

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S06
- 风险概述: 日志与可观测性成本 场景下若忽视“批量窗口与分片”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S07: 慢查询与慢调用旁路

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S07
- 风险概述: 日志与可观测性成本 场景下若忽视“慢查询与慢调用旁路”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
List<List<日志与可观测性成本Record>> partitions = Lists.partition(records, 200);
for (List<日志与可观测性成本Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (日志与可观测性成本Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S08: 缓存雪崩与击穿保护

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S08
- 风险概述: 日志与可观测性成本 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S09: 对象分配与复制控制

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S09
- 风险概述: 日志与可观测性成本 场景下若忽视“对象分配与复制控制”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S10: 序列化与日志成本治理

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S10
- 风险概述: 日志与可观测性成本 场景下若忽视“序列化与日志成本治理”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
List<List<日志与可观测性成本Record>> partitions = Lists.partition(records, 200);
for (List<日志与可观测性成本Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (日志与可观测性成本Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S11: 指标与告警最小闭环

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S11
- 风险概述: 日志与可观测性成本 场景下若忽视“指标与告警最小闭环”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 日志与可观测性成本Result handle(日志与可观测性成本Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S12: 发布前容量守卫

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S12
- 风险概述: 日志与可观测性成本 场景下若忽视“发布前容量守卫”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T13-S13: 故障恢复与回滚准则

- 主题: 日志与可观测性成本
- 场景编号: PERF-T13-S13
- 风险概述: 日志与可观测性成本 场景下若忽视“故障恢复与回滚准则”，常见后果是 观测体系本身也可能成为性能负担。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 logging / trace / metric
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 日志与可观测性成本 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.13.latency.p95
  - app.13.timeout.count
  - app.13.queue.depth
  - app.13.error.ratio
- 正例:
```java
List<List<日志与可观测性成本Record>> partitions = Lists.partition(records, 200);
for (List<日志与可观测性成本Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (日志与可观测性成本Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 日志与可观测性成本 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 日志与可观测性成本 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 14: 数据库索引与访问模式

- 专题摘要: 查询模式失控会让连接池与线程池一起退化。
- 关键词: jdbc / index / slow sql / pagination
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T14-S01: 热点路径基线核对

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S01
- 风险概述: 数据库索引与访问模式 场景下若忽视“热点路径基线核对”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S02: 线程池隔离策略

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S02
- 风险概述: 数据库索引与访问模式 场景下若忽视“线程池隔离策略”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S03: 超时预算切分

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S03
- 风险概述: 数据库索引与访问模式 场景下若忽视“超时预算切分”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
List<List<数据库索引与访问模式Record>> partitions = Lists.partition(records, 200);
for (List<数据库索引与访问模式Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (数据库索引与访问模式Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S04: 重试放大抑制

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S04
- 风险概述: 数据库索引与访问模式 场景下若忽视“重试放大抑制”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S05: 限流与背压协同

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S05
- 风险概述: 数据库索引与访问模式 场景下若忽视“限流与背压协同”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S06: 批量窗口与分片

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S06
- 风险概述: 数据库索引与访问模式 场景下若忽视“批量窗口与分片”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
List<List<数据库索引与访问模式Record>> partitions = Lists.partition(records, 200);
for (List<数据库索引与访问模式Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (数据库索引与访问模式Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S07: 慢查询与慢调用旁路

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S07
- 风险概述: 数据库索引与访问模式 场景下若忽视“慢查询与慢调用旁路”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S08: 缓存雪崩与击穿保护

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S08
- 风险概述: 数据库索引与访问模式 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S09: 对象分配与复制控制

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S09
- 风险概述: 数据库索引与访问模式 场景下若忽视“对象分配与复制控制”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
List<List<数据库索引与访问模式Record>> partitions = Lists.partition(records, 200);
for (List<数据库索引与访问模式Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (数据库索引与访问模式Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S10: 序列化与日志成本治理

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S10
- 风险概述: 数据库索引与访问模式 场景下若忽视“序列化与日志成本治理”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S11: 指标与告警最小闭环

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S11
- 风险概述: 数据库索引与访问模式 场景下若忽视“指标与告警最小闭环”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S12: 发布前容量守卫

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S12
- 风险概述: 数据库索引与访问模式 场景下若忽视“发布前容量守卫”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
List<List<数据库索引与访问模式Record>> partitions = Lists.partition(records, 200);
for (List<数据库索引与访问模式Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (数据库索引与访问模式Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T14-S13: 故障恢复与回滚准则

- 主题: 数据库索引与访问模式
- 场景编号: PERF-T14-S13
- 风险概述: 数据库索引与访问模式 场景下若忽视“故障恢复与回滚准则”，常见后果是 查询模式失控会让连接池与线程池一起退化。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 jdbc / index / slow sql
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 数据库索引与访问模式 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.14.latency.p95
  - app.14.timeout.count
  - app.14.queue.depth
  - app.14.error.ratio
- 正例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 数据库索引与访问模式Result handle(数据库索引与访问模式Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 数据库索引与访问模式 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 数据库索引与访问模式 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 15: Redis 与内存数据结构

- 专题摘要: 缓存层误用会把压力反推到核心链路。
- 关键词: redis / pipeline / lua / scan
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T15-S01: 热点路径基线核对

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S01
- 风险概述: Redis 与内存数据结构 场景下若忽视“热点路径基线核对”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S02: 线程池隔离策略

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S02
- 风险概述: Redis 与内存数据结构 场景下若忽视“线程池隔离策略”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
List<List<Redis与内存数据结构Record>> partitions = Lists.partition(records, 200);
for (List<Redis与内存数据结构Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Redis与内存数据结构Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S03: 超时预算切分

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S03
- 风险概述: Redis 与内存数据结构 场景下若忽视“超时预算切分”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S04: 重试放大抑制

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S04
- 风险概述: Redis 与内存数据结构 场景下若忽视“重试放大抑制”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S05: 限流与背压协同

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S05
- 风险概述: Redis 与内存数据结构 场景下若忽视“限流与背压协同”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
List<List<Redis与内存数据结构Record>> partitions = Lists.partition(records, 200);
for (List<Redis与内存数据结构Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Redis与内存数据结构Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S06: 批量窗口与分片

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S06
- 风险概述: Redis 与内存数据结构 场景下若忽视“批量窗口与分片”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S07: 慢查询与慢调用旁路

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S07
- 风险概述: Redis 与内存数据结构 场景下若忽视“慢查询与慢调用旁路”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S08: 缓存雪崩与击穿保护

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S08
- 风险概述: Redis 与内存数据结构 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
List<List<Redis与内存数据结构Record>> partitions = Lists.partition(records, 200);
for (List<Redis与内存数据结构Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Redis与内存数据结构Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S09: 对象分配与复制控制

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S09
- 风险概述: Redis 与内存数据结构 场景下若忽视“对象分配与复制控制”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S10: 序列化与日志成本治理

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S10
- 风险概述: Redis 与内存数据结构 场景下若忽视“序列化与日志成本治理”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S11: 指标与告警最小闭环

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S11
- 风险概述: Redis 与内存数据结构 场景下若忽视“指标与告警最小闭环”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
List<List<Redis与内存数据结构Record>> partitions = Lists.partition(records, 200);
for (List<Redis与内存数据结构Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Redis与内存数据结构Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S12: 发布前容量守卫

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S12
- 风险概述: Redis 与内存数据结构 场景下若忽视“发布前容量守卫”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Redis与内存数据结构Result handle(Redis与内存数据结构Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T15-S13: 故障恢复与回滚准则

- 主题: Redis 与内存数据结构
- 场景编号: PERF-T15-S13
- 风险概述: Redis 与内存数据结构 场景下若忽视“故障恢复与回滚准则”，常见后果是 缓存层误用会把压力反推到核心链路。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 redis / pipeline / lua
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Redis 与内存数据结构 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.15.latency.p95
  - app.15.timeout.count
  - app.15.queue.depth
  - app.15.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Redis 与内存数据结构 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Redis 与内存数据结构 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 16: Netty 与事件循环模型

- 专题摘要: 把阻塞逻辑塞进事件循环会击穿吞吐。
- 关键词: netty / event loop / channel / epoll
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T16-S01: 热点路径基线核对

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S01
- 风险概述: Netty 与事件循环模型 场景下若忽视“热点路径基线核对”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
List<List<Netty与事件循环模型Record>> partitions = Lists.partition(records, 200);
for (List<Netty与事件循环模型Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Netty与事件循环模型Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S02: 线程池隔离策略

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S02
- 风险概述: Netty 与事件循环模型 场景下若忽视“线程池隔离策略”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S03: 超时预算切分

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S03
- 风险概述: Netty 与事件循环模型 场景下若忽视“超时预算切分”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S04: 重试放大抑制

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S04
- 风险概述: Netty 与事件循环模型 场景下若忽视“重试放大抑制”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
List<List<Netty与事件循环模型Record>> partitions = Lists.partition(records, 200);
for (List<Netty与事件循环模型Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Netty与事件循环模型Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S05: 限流与背压协同

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S05
- 风险概述: Netty 与事件循环模型 场景下若忽视“限流与背压协同”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S06: 批量窗口与分片

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S06
- 风险概述: Netty 与事件循环模型 场景下若忽视“批量窗口与分片”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S07: 慢查询与慢调用旁路

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S07
- 风险概述: Netty 与事件循环模型 场景下若忽视“慢查询与慢调用旁路”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
List<List<Netty与事件循环模型Record>> partitions = Lists.partition(records, 200);
for (List<Netty与事件循环模型Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Netty与事件循环模型Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S08: 缓存雪崩与击穿保护

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S08
- 风险概述: Netty 与事件循环模型 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S09: 对象分配与复制控制

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S09
- 风险概述: Netty 与事件循环模型 场景下若忽视“对象分配与复制控制”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S10: 序列化与日志成本治理

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S10
- 风险概述: Netty 与事件循环模型 场景下若忽视“序列化与日志成本治理”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
List<List<Netty与事件循环模型Record>> partitions = Lists.partition(records, 200);
for (List<Netty与事件循环模型Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Netty与事件循环模型Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S11: 指标与告警最小闭环

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S11
- 风险概述: Netty 与事件循环模型 场景下若忽视“指标与告警最小闭环”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Netty与事件循环模型Result handle(Netty与事件循环模型Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S12: 发布前容量守卫

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S12
- 风险概述: Netty 与事件循环模型 场景下若忽视“发布前容量守卫”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T16-S13: 故障恢复与回滚准则

- 主题: Netty 与事件循环模型
- 场景编号: PERF-T16-S13
- 风险概述: Netty 与事件循环模型 场景下若忽视“故障恢复与回滚准则”，常见后果是 把阻塞逻辑塞进事件循环会击穿吞吐。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 netty / event loop / channel
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Netty 与事件循环模型 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.16.latency.p95
  - app.16.timeout.count
  - app.16.queue.depth
  - app.16.error.ratio
- 正例:
```java
List<List<Netty与事件循环模型Record>> partitions = Lists.partition(records, 200);
for (List<Netty与事件循环模型Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Netty与事件循环模型Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Netty 与事件循环模型 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Netty 与事件循环模型 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 17: Reactive 与异步编排

- 专题摘要: 阻塞桥接和线程切换过多会抵消响应式收益。
- 关键词: reactive / mono / flux / scheduler
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T17-S01: 热点路径基线核对

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S01
- 风险概述: Reactive 与异步编排 场景下若忽视“热点路径基线核对”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S02: 线程池隔离策略

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S02
- 风险概述: Reactive 与异步编排 场景下若忽视“线程池隔离策略”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S03: 超时预算切分

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S03
- 风险概述: Reactive 与异步编排 场景下若忽视“超时预算切分”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
List<List<Reactive与异步编排Record>> partitions = Lists.partition(records, 200);
for (List<Reactive与异步编排Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Reactive与异步编排Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S04: 重试放大抑制

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S04
- 风险概述: Reactive 与异步编排 场景下若忽视“重试放大抑制”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S05: 限流与背压协同

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S05
- 风险概述: Reactive 与异步编排 场景下若忽视“限流与背压协同”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S06: 批量窗口与分片

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S06
- 风险概述: Reactive 与异步编排 场景下若忽视“批量窗口与分片”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
List<List<Reactive与异步编排Record>> partitions = Lists.partition(records, 200);
for (List<Reactive与异步编排Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Reactive与异步编排Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S07: 慢查询与慢调用旁路

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S07
- 风险概述: Reactive 与异步编排 场景下若忽视“慢查询与慢调用旁路”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S08: 缓存雪崩与击穿保护

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S08
- 风险概述: Reactive 与异步编排 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S09: 对象分配与复制控制

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S09
- 风险概述: Reactive 与异步编排 场景下若忽视“对象分配与复制控制”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
List<List<Reactive与异步编排Record>> partitions = Lists.partition(records, 200);
for (List<Reactive与异步编排Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Reactive与异步编排Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S10: 序列化与日志成本治理

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S10
- 风险概述: Reactive 与异步编排 场景下若忽视“序列化与日志成本治理”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S11: 指标与告警最小闭环

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S11
- 风险概述: Reactive 与异步编排 场景下若忽视“指标与告警最小闭环”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S12: 发布前容量守卫

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S12
- 风险概述: Reactive 与异步编排 场景下若忽视“发布前容量守卫”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
List<List<Reactive与异步编排Record>> partitions = Lists.partition(records, 200);
for (List<Reactive与异步编排Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (Reactive与异步编排Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T17-S13: 故障恢复与回滚准则

- 主题: Reactive 与异步编排
- 场景编号: PERF-T17-S13
- 风险概述: Reactive 与异步编排 场景下若忽视“故障恢复与回滚准则”，常见后果是 阻塞桥接和线程切换过多会抵消响应式收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 reactive / mono / flux
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 Reactive 与异步编排 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.17.latency.p95
  - app.17.timeout.count
  - app.17.queue.depth
  - app.17.error.ratio
- 正例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public Reactive与异步编排Result handle(Reactive与异步编排Query request) {
    return client.query(request);
}
```
- 评审追问:
  - Reactive 与异步编排 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 Reactive 与异步编排 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 18: 容器与 JVM 参数

- 专题摘要: 容器资源识别错误会造成误判和过度扩容。
- 关键词: container / cpu quota / memory limit / oom
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T18-S01: 热点路径基线核对

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S01
- 风险概述: 容器与 JVM 参数 场景下若忽视“热点路径基线核对”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S02: 线程池隔离策略

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S02
- 风险概述: 容器与 JVM 参数 场景下若忽视“线程池隔离策略”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
List<List<容器与JVM参数Record>> partitions = Lists.partition(records, 200);
for (List<容器与JVM参数Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (容器与JVM参数Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S03: 超时预算切分

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S03
- 风险概述: 容器与 JVM 参数 场景下若忽视“超时预算切分”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S04: 重试放大抑制

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S04
- 风险概述: 容器与 JVM 参数 场景下若忽视“重试放大抑制”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S05: 限流与背压协同

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S05
- 风险概述: 容器与 JVM 参数 场景下若忽视“限流与背压协同”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
List<List<容器与JVM参数Record>> partitions = Lists.partition(records, 200);
for (List<容器与JVM参数Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (容器与JVM参数Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S06: 批量窗口与分片

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S06
- 风险概述: 容器与 JVM 参数 场景下若忽视“批量窗口与分片”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S07: 慢查询与慢调用旁路

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S07
- 风险概述: 容器与 JVM 参数 场景下若忽视“慢查询与慢调用旁路”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S08: 缓存雪崩与击穿保护

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S08
- 风险概述: 容器与 JVM 参数 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
List<List<容器与JVM参数Record>> partitions = Lists.partition(records, 200);
for (List<容器与JVM参数Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (容器与JVM参数Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S09: 对象分配与复制控制

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S09
- 风险概述: 容器与 JVM 参数 场景下若忽视“对象分配与复制控制”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S10: 序列化与日志成本治理

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S10
- 风险概述: 容器与 JVM 参数 场景下若忽视“序列化与日志成本治理”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S11: 指标与告警最小闭环

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S11
- 风险概述: 容器与 JVM 参数 场景下若忽视“指标与告警最小闭环”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
List<List<容器与JVM参数Record>> partitions = Lists.partition(records, 200);
for (List<容器与JVM参数Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (容器与JVM参数Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S12: 发布前容量守卫

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S12
- 风险概述: 容器与 JVM 参数 场景下若忽视“发布前容量守卫”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 容器与JVM参数Result handle(容器与JVM参数Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T18-S13: 故障恢复与回滚准则

- 主题: 容器与 JVM 参数
- 场景编号: PERF-T18-S13
- 风险概述: 容器与 JVM 参数 场景下若忽视“故障恢复与回滚准则”，常见后果是 容器资源识别错误会造成误判和过度扩容。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 container / cpu quota / memory limit
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 容器与 JVM 参数 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.18.latency.p95
  - app.18.timeout.count
  - app.18.queue.depth
  - app.18.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 容器与 JVM 参数 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 容器与 JVM 参数 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 19: 压测方法与容量建模

- 专题摘要: 没有容量模型就无法解释优化收益。
- 关键词: load test / stress / slo / capacity
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T19-S01: 热点路径基线核对

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S01
- 风险概述: 压测方法与容量建模 场景下若忽视“热点路径基线核对”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
List<List<压测方法与容量建模Record>> partitions = Lists.partition(records, 200);
for (List<压测方法与容量建模Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (压测方法与容量建模Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S02: 线程池隔离策略

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S02
- 风险概述: 压测方法与容量建模 场景下若忽视“线程池隔离策略”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S03: 超时预算切分

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S03
- 风险概述: 压测方法与容量建模 场景下若忽视“超时预算切分”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S04: 重试放大抑制

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S04
- 风险概述: 压测方法与容量建模 场景下若忽视“重试放大抑制”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
List<List<压测方法与容量建模Record>> partitions = Lists.partition(records, 200);
for (List<压测方法与容量建模Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (压测方法与容量建模Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S05: 限流与背压协同

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S05
- 风险概述: 压测方法与容量建模 场景下若忽视“限流与背压协同”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S06: 批量窗口与分片

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S06
- 风险概述: 压测方法与容量建模 场景下若忽视“批量窗口与分片”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S07: 慢查询与慢调用旁路

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S07
- 风险概述: 压测方法与容量建模 场景下若忽视“慢查询与慢调用旁路”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
List<List<压测方法与容量建模Record>> partitions = Lists.partition(records, 200);
for (List<压测方法与容量建模Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (压测方法与容量建模Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S08: 缓存雪崩与击穿保护

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S08
- 风险概述: 压测方法与容量建模 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S09: 对象分配与复制控制

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S09
- 风险概述: 压测方法与容量建模 场景下若忽视“对象分配与复制控制”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S10: 序列化与日志成本治理

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S10
- 风险概述: 压测方法与容量建模 场景下若忽视“序列化与日志成本治理”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
List<List<压测方法与容量建模Record>> partitions = Lists.partition(records, 200);
for (List<压测方法与容量建模Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (压测方法与容量建模Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S11: 指标与告警最小闭环

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S11
- 风险概述: 压测方法与容量建模 场景下若忽视“指标与告警最小闭环”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 压测方法与容量建模Result handle(压测方法与容量建模Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S12: 发布前容量守卫

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S12
- 风险概述: 压测方法与容量建模 场景下若忽视“发布前容量守卫”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T19-S13: 故障恢复与回滚准则

- 主题: 压测方法与容量建模
- 场景编号: PERF-T19-S13
- 风险概述: 压测方法与容量建模 场景下若忽视“故障恢复与回滚准则”，常见后果是 没有容量模型就无法解释优化收益。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 load test / stress / slo
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 压测方法与容量建模 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.19.latency.p95
  - app.19.timeout.count
  - app.19.queue.depth
  - app.19.error.ratio
- 正例:
```java
List<List<压测方法与容量建模Record>> partitions = Lists.partition(records, 200);
for (List<压测方法与容量建模Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (压测方法与容量建模Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 压测方法与容量建模 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 压测方法与容量建模 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

## 专题 20: 发布、回滚与故障演练

- 专题摘要: 上线与回滚路径若不演练，风险会在生产集中暴露。
- 关键词: canary / rollback / chaos / feature flag
- 审核员提醒:
  - 先定位受影响的请求入口、后台任务入口或消费入口。
  - 再找线程池、连接池、队列、批量窗口和降级配置。
  - 最后判断问题会不会在高峰、重试、故障恢复时级联放大。

### 规则 PERF-T20-S01: 热点路径基线核对

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S01
- 风险概述: 发布、回滚与故障演练 场景下若忽视“热点路径基线核对”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 热点路径基线核对 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 热点路径基线核对 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 热点路径基线核对 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S02: 线程池隔离策略

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S02
- 风险概述: 发布、回滚与故障演练 场景下若忽视“线程池隔离策略”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 线程池隔离策略 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 线程池隔离策略 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 线程池隔离策略 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S03: 超时预算切分

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S03
- 风险概述: 发布、回滚与故障演练 场景下若忽视“超时预算切分”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 超时预算切分 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
List<List<发布、回滚与故障演练Record>> partitions = Lists.partition(records, 200);
for (List<发布、回滚与故障演练Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (发布、回滚与故障演练Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 超时预算切分 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 超时预算切分 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S04: 重试放大抑制

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S04
- 风险概述: 发布、回滚与故障演练 场景下若忽视“重试放大抑制”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 重试放大抑制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 重试放大抑制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 重试放大抑制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S05: 限流与背压协同

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S05
- 风险概述: 发布、回滚与故障演练 场景下若忽视“限流与背压协同”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 限流与背压协同 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 限流与背压协同 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 限流与背压协同 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S06: 批量窗口与分片

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S06
- 风险概述: 发布、回滚与故障演练 场景下若忽视“批量窗口与分片”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 批量窗口与分片 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
List<List<发布、回滚与故障演练Record>> partitions = Lists.partition(records, 200);
for (List<发布、回滚与故障演练Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (发布、回滚与故障演练Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 批量窗口与分片 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 批量窗口与分片 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S07: 慢查询与慢调用旁路

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S07
- 风险概述: 发布、回滚与故障演练 场景下若忽视“慢查询与慢调用旁路”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 慢查询与慢调用旁路 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 慢查询与慢调用旁路 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 慢查询与慢调用旁路 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S08: 缓存雪崩与击穿保护

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S08
- 风险概述: 发布、回滚与故障演练 场景下若忽视“缓存雪崩与击穿保护”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 缓存雪崩与击穿保护 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 缓存雪崩与击穿保护 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 缓存雪崩与击穿保护 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S09: 对象分配与复制控制

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S09
- 风险概述: 发布、回滚与故障演练 场景下若忽视“对象分配与复制控制”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 对象分配与复制控制 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
List<List<发布、回滚与故障演练Record>> partitions = Lists.partition(records, 200);
for (List<发布、回滚与故障演练Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (发布、回滚与故障演练Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 对象分配与复制控制 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 对象分配与复制控制 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S10: 序列化与日志成本治理

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S10
- 风险概述: 发布、回滚与故障演练 场景下若忽视“序列化与日志成本治理”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 序列化与日志成本治理 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 序列化与日志成本治理 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 序列化与日志成本治理 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S11: 指标与告警最小闭环

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S11
- 风险概述: 发布、回滚与故障演练 场景下若忽视“指标与告警最小闭环”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 指标与告警最小闭环 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
ExecutorService isolatedExecutor = new ThreadPoolExecutor(
    8, 16, 60, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new NamedThreadFactory("perf-guard"),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```
- 反例:
```java
CompletableFuture.supplyAsync(() -> heavyCall(request));
CompletableFuture.supplyAsync(() -> anotherHeavyCall(request));
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 指标与告警最小闭环 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 指标与告警最小闭环 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S12: 发布前容量守卫

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S12
- 风险概述: 发布、回滚与故障演练 场景下若忽视“发布前容量守卫”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 发布前容量守卫 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
List<List<发布、回滚与故障演练Record>> partitions = Lists.partition(records, 200);
for (List<发布、回滚与故障演练Record> chunk : partitions) {
    repository.saveChunk(chunk);
}
```
- 反例:
```java
for (发布、回滚与故障演练Record record : records) {
    repository.save(record);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 发布前容量守卫 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 发布前容量守卫 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。

### 规则 PERF-T20-S13: 故障恢复与回滚准则

- 主题: 发布、回滚与故障演练
- 场景编号: PERF-T20-S13
- 风险概述: 发布、回滚与故障演练 场景下若忽视“故障恢复与回滚准则”，常见后果是 上线与回滚路径若不演练，风险会在生产集中暴露。
- 适用代码信号:
  - 类名、方法名、配置项或日志中出现 canary / rollback / chaos
  - 变更中出现 executor、timeout、batch、cache、queue、metric、fallback 等语义。
  - 目标文件位于 service、worker、consumer、repository、scheduler、client 等路径。
- 必查项:
  - 是否为 发布、回滚与故障演练 相关热点路径建立独立资源边界。
  - 是否把 故障恢复与回滚准则 的阈值、容量或异常策略写死在代码中。
  - 是否在失败场景中出现重试叠加、队列堆积、连接池耗尽或线程阻塞。
  - 是否有指标、日志、trace 和告警支持快速定位。
- 推荐指标:
  - app.20.latency.p95
  - app.20.timeout.count
  - app.20.queue.depth
  - app.20.error.ratio
- 正例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    TimeoutBudget budget = timeoutBudget.split("downstream", 120);
    return bulkhead.executeSupplier(() -> client.query(request, budget));
}
```
- 反例:
```java
public 发布、回滚与故障演练Result handle(发布、回滚与故障演练Query request) {
    return client.query(request);
}
```
- 评审追问:
  - 发布、回滚与故障演练 的容量预算是谁维护，是否跟 SLO 对齐。
  - 故障恢复与回滚准则 的阈值上线后是否可以热更新。
  - 当下游持续抖动 5 到 10 分钟时，系统是快速失败还是持续堆积。
  - 若本次变更回滚，是否需要额外的缓存清理、批次补偿或指标回收。
- 修复建议:
  - 给 发布、回滚与故障演练 相关链路补独立线程池、连接池、队列或资源上限。
  - 将 故障恢复与回滚准则 的关键参数外置为配置并建立监控。
  - 把失败路径改成限次重试、快速失败、旁路降级或按分片补偿。
  - 为核心优化点补压测对照数据和回滚守卫。
- 上线前核验:
  - 检查压测报告是否覆盖稳态、突刺、故障注入和恢复阶段。
  - 检查 dashboard 是否具备延迟、错误率、队列深度、线程池、连接池面板。
  - 检查告警阈值是否基于历史分位值，而不是随意拍脑袋。
