# PostgreSQL 数据源接入数据库分析专家设计

## 目标

为 `database_analysis` 专家接入按代码仓配置的 PostgreSQL 数据源，让数据库专家在审核时不仅能看 diff、schema/migration 和源码上下文，还能结合真实库表元信息与轻量统计信息做判断。

## 设计原则

1. 数据源按代码仓绑定，而不是全局单例。
2. 只读访问，不允许写库。
3. 默认只查询命中的表，不扫全库。
4. 元信息获取失败时，不阻塞整个审核流程，只让数据库专家降级。
5. 系统必要配置仍然放 `config.json`，不放 SQLite。

## 方案

### 方案 A：数据库专家内硬编码 PG 连接

优点：

- 开发快

缺点：

- 无法支持多代码仓
- 无法扩展到多个项目/环境
- 配置边界混乱

### 方案 B：按代码仓配置 PostgreSQL 数据源映射

优点：

- 与现有“按代码仓审核”的产品模型一致
- 后续可扩 MySQL/Oracle
- 可以在创建审核后根据 `repo_url/clone_url` 自动匹配

缺点：

- 需要新增配置模型、元信息服务和工具接入

### 推荐方案

采用方案 B。

## 配置设计

在 `config.json` 中新增按代码仓配置的数据源块，挂在系统配置主路径。

建议结构：

```json
{
  "database_sources": [
    {
      "repo_url": "https://github.com/org/repo.git",
      "provider": "postgres",
      "host": "127.0.0.1",
      "port": 5432,
      "database": "app",
      "user": "readonly_user",
      "password_env": "APP_PG_PASSWORD",
      "schema_allowlist": ["public"],
      "ssl_mode": "prefer",
      "connect_timeout_seconds": 5,
      "statement_timeout_ms": 3000,
      "enabled": true
    }
  ]
}
```

说明：

- `repo_url` 用于匹配当前审核任务所属代码仓
- `password_env` 不把密码明文落在配置文件里
- `schema_allowlist` 控制只查允许的 schema
- `statement_timeout_ms` 防止数据库查询拖慢审核

## 审核链路设计

### 1. 审核任务拿到代码仓

当前审核任务已能拿到：

- `subject.repo_url`
- `subject.mr_url`
- `runtime.code_repo_clone_url`

数据库元信息服务通过这些字段匹配对应数据源。

### 2. 数据库专家执行前新增 PG 元信息探测

新增一个数据库工具，例如：

- `pg_schema_context`

它只对 `database_analysis` 专家开放。

### 3. 工具行为

输入：

- 当前审核对象代码仓 URL
- 当前文件路径
- 当前 diff hunk
- `changed_files`
- 已提取的 query terms

处理：

1. 解析候选表名
2. 匹配代码仓对应的数据源
3. 用只读连接查询 PG 元信息
4. 产出结构化摘要

输出：

- `matched_tables`
- `table_columns`
- `constraints`
- `indexes`
- `table_stats`
- `meta_queries`
- `data_source_summary`

### 4. 候选表名提取策略

第一版采用启发式即可：

- 从 `.sql / migration / schema / repository / dao / entity / mapper` 文件路径推测
- 从 diff 中提取：
  - `CREATE TABLE`
  - `ALTER TABLE`
  - `JOIN`
  - `FROM`
  - `INSERT INTO`
  - `UPDATE`
  - `DELETE FROM`
- 从 Java 仓储类、ORM 注解、Prisma schema 中提取表名/实体名

### 5. PostgreSQL 查询内容

第一版查询：

- 表基本信息：
  - schema
  - table name
  - comment
- 列信息：
  - column name
  - data type
  - nullable
  - default
- 约束：
  - primary key
  - unique
  - foreign key
- 索引：
  - index name
  - indexed columns
  - unique / partial
- 统计：
  - `reltuples` 行数估计
  - 表大小
  - 索引大小
  - 最近 vacuum/analyze

### 6. 给大模型的形式

不把完整 catalog 生硬塞给 LLM，而是压成结构化文本：

- 命中的表
- 每个表的关键列
- 关键约束
- 关键索引
- 表规模与统计摘要
- 异常或缺失提示

数据库专家的 prompt 中增加一段：

- `PostgreSQL 元信息上下文`

### 7. 前端展示

审核过程页中，数据库专家的工具调用需要展示：

- 匹配到的数据源
- 命中的表
- 查询到了哪些元信息
- 是否拿到了统计信息

不默认展开所有列明细，只展示摘要，支持展开。

## 错误处理

### 数据源未配置

输出：

- 已跳过 PG 元信息检索
- 原因：当前代码仓未绑定 PostgreSQL 数据源

### 环境变量缺失

输出：

- 已跳过 PG 元信息检索
- 原因：缺少密码环境变量

### 查询超时/连接失败

输出：

- 已降级
- 原因：数据库连接失败或超时

### 表名未命中

输出：

- 未从本次变更中提取到明确的表名

## 安全要求

1. 强制只读连接。
2. 不允许执行 DDL/DML。
3. SQL 必须是固定模板，不能把 LLM 输出直接拼成 SQL。
4. 表名只允许走白名单过滤和安全 quoting。
5. 日志中不打印密码和连接串。

## 测试策略

### 单元测试

- 配置模型解析
- 代码仓到 PG 数据源匹配
- 表名提取
- PG 元信息结果压缩
- 失败降级输出

### 集成测试

- 伪造 PG 查询服务返回元信息
- 验证数据库专家工具结果被带入 prompt
- 验证过程页能显示 PG 元信息摘要

## 结论

推荐以“按代码仓配置 PostgreSQL 数据源 + 数据库专家专属工具接入”的方式落地。该方案最符合现有配置边界、产品模型和后续扩展方向。
