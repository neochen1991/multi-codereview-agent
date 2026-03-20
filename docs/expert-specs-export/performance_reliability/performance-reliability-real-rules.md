# 性能与可靠性专家真实规则文档

> 适用专家：`performance_reliability`
> 适用语言：`java`
> 用途：用于真实 MR 审核时的规则预筛与正式审查。

## RULE: PERF-POOL-001 连接池扩容必须配套容量评估

### 一级场景
数据库访问

### 二级场景
连接池配置

### 三级场景
连接池扩容缺少容量评估

### 描述
当 MR 调整 Hikari 或其他连接池的容量、空闲连接数、连接超时或校验超时时，需要启用这条规则。重点关注扩容是否有数据库最大连接数、服务实例数、峰值流量和回滚策略作为依据，避免把放大连接池当成掩盖慢 SQL 或连接泄漏的手段。

### 问题代码示例
```java
config.setMaximumPoolSize(256);
config.setMinimumIdle(1);
config.setConnectionTimeout(30000);
config.setValidationTimeout(30000);
```

### 问题代码行
config.setMaximumPoolSize(256);

### 误报代码
```java
config.setMaximumPoolSize(32);
config.setMinimumIdle(8);
config.setConnectionTimeout(1500);
config.setValidationTimeout(1000);
```

### 语言
java

### 问题级别
P1

## RULE: PERF-SQL-001 大结果集查询必须显式分页或限流

### 一级场景
数据库访问

### 二级场景
查询性能

### 三级场景
大结果集分页缺失

### 描述
当 MR 修改 repository、mapper、导出接口或批量读取逻辑，且存在一次性返回大集合的风险时，需要启用这条规则。重点关注 `findAll`、全量 list、热路径全表扫描、导出接口无分页、内存过滤替代数据库过滤等问题。

### 问题代码示例
```java
List<Order> orders = orderRepository.findAll();
return orders.stream().filter(Order::active).toList();
```

### 问题代码行
List<Order> orders = orderRepository.findAll();

### 误报代码
```java
Page<Order> page = orderRepository.findByStatus(status, PageRequest.of(0, 200));
return page.getContent();
```

### 语言
java

### 问题级别
P1

## RULE: PERF-SQL-002 N+1 查询风险必须在服务层被识别

### 一级场景
数据库访问

### 二级场景
聚合装配

### 三级场景
循环内逐条查询

### 描述
当 MR 修改服务层循环、stream 链或 DTO 组装逻辑，并在循环体内访问 repository、DAO 或 mapper 时，需要启用这条规则。重点识别 N+1 查询、重复懒加载和按条查询关联数据的风险。

### 问题代码示例
```java
orders.forEach(order -> order.setItems(itemRepository.findByOrderId(order.getId())));
```

### 问题代码行
itemRepository.findByOrderId(order.getId())

### 误报代码
```java
Map<Long, List<OrderItem>> itemMap = itemRepository.findByOrderIds(orderIds);
orders.forEach(order -> order.setItems(itemMap.get(order.getId())));
```

### 语言
java

### 问题级别
P1

## RULE: PERF-THREAD-001 线程池扩容必须说明背压与拒绝策略

### 一级场景
并发与线程池

### 二级场景
线程池容量配置

### 三级场景
线程池扩容缺少背压策略

### 描述
当 MR 调整线程池核心线程数、最大线程数、队列容量或拒绝策略时，需要启用这条规则。重点检查是否同步说明背压、拒绝策略、上游限流和下游 DB/HTTP/MQ 的承载能力。

### 问题代码示例
```java
executor.setCorePoolSize(32);
executor.setMaxPoolSize(512);
executor.setQueueCapacity(20000);
executor.setRejectedExecutionHandler(new ThreadPoolExecutor.AbortPolicy());
```

### 问题代码行
executor.setMaxPoolSize(512);

### 误报代码
```java
executor.setCorePoolSize(16);
executor.setMaxPoolSize(32);
executor.setQueueCapacity(500);
executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
```

### 语言
java

### 问题级别
P1

## RULE: PERF-LOCK-001 粗粒度锁和长临界区必须谨慎引入

### 一级场景
并发与线程池

### 二级场景
锁粒度

### 三级场景
锁内执行重操作

### 描述
当 MR 新增 `synchronized`、`ReentrantLock` 或其他显式锁，并且临界区内出现数据库访问、远程调用、磁盘 IO 或批量处理时，需要启用这条规则。重点关注吞吐下降、线程阻塞和潜在死锁风险。

### 问题代码示例
```java
synchronized (this) {
    orderRepository.save(order);
    remoteClient.notify(order);
}
```

### 问题代码行
synchronized (this) {

### 误报代码
```java
lock.lock();
try {
    state.increment();
} finally {
    lock.unlock();
}
```

### 语言
java

### 问题级别
P2

## RULE: PERF-HTTP-001 远程调用必须显式超时与重试边界

### 一级场景
远程调用

### 二级场景
超时控制

### 三级场景
远程调用缺少隔离

### 描述
当 MR 新增或修改 Feign、WebClient、RestTemplate 等远程调用时，需要启用这条规则。重点检查 connect/read timeout、重试次数、幂等边界、熔断和 fallback 是否明确。

### 问题代码示例
```java
return feignClient.createOrder(request);
```

### 问题代码行
return feignClient.createOrder(request);

### 误报代码
```java
Request.Options options = new Request.Options(1000, 2000);
return feignClient.createOrder(request);
```

### 语言
java

### 问题级别
P1

## RULE: PERF-CACHE-001 缓存大对象必须评估序列化与过期策略

### 一级场景
缓存

### 二级场景
序列化与过期

### 三级场景
大对象缓存缺少体积控制

### 描述
当 MR 引入新的缓存对象、修改 TTL 或把聚合对象直接放入 Redis/本地缓存时，需要启用这条规则。重点关注大 key、热点失效、雪崩击穿和序列化带宽放大。

### 问题代码示例
```java
redisTemplate.opsForValue().set("orders", fullOrderAggregateList);
```

### 问题代码行
redisTemplate.opsForValue().set("orders", fullOrderAggregateList);

### 误报代码
```java
redisTemplate.opsForValue().set(cacheKey, summaryDto, Duration.ofMinutes(5));
```

### 语言
java

### 问题级别
P2

## RULE: PERF-BATCH-001 批处理写入必须控制批大小与事务范围

### 一级场景
数据库访问

### 二级场景
批处理

### 三级场景
批处理事务范围过大

### 描述
当 MR 修改 `saveAll`、flush、chunk 消费或离线迁移逻辑时，需要启用这条规则。重点关注超大事务、一次性加载全部数据、失败重试缺少幂等、资源占用失控等问题。

### 问题代码示例
```java
repository.saveAll(allRecords);
```

### 问题代码行
repository.saveAll(allRecords);

### 误报代码
```java
Lists.partition(records, 200).forEach(batch -> repository.saveAll(batch));
```

### 语言
java

### 问题级别
P1

## RULE: PERF-JSON-001 大型对象序列化路径必须避免重复拷贝

### 一级场景
序列化

### 二级场景
JSON 热路径

### 三级场景
重复序列化拷贝

### 描述
当 MR 修改请求响应组装、缓存落盘或日志打印路径，并且多次对同一个大对象做 JSON 转换、深拷贝或字符串拼接时，需要启用这条规则。重点关注热路径中的重复序列化和大对象完整日志输出。

### 问题代码示例
```java
String payload = objectMapper.writeValueAsString(order);
String copy = objectMapper.writeValueAsString(order);
logger.info("payload={}", payload);
```

### 问题代码行
String copy = objectMapper.writeValueAsString(order);

### 误报代码
```java
String payload = objectWriter.writeValueAsString(summaryDto);
```

### 语言
java

### 问题级别
P2
