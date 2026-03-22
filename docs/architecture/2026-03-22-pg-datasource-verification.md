# PostgreSQL 数据源接入验证说明

## 目标

验证数据库分析专家是否会按代码仓匹配 PostgreSQL 数据源，并在真实审核任务中读取：

- 命中表
- 表字段元信息
- 约束
- 索引
- 轻量统计信息

## 前置条件

1. 后端服务已启动，健康检查可访问：

```bash
curl http://127.0.0.1:8011/health
```

2. 设置页或 `config.json` 已配置当前代码仓对应的 `database_sources`。

示例：

```json
[
  {
    "repo_url": "https://github.com/example/repo.git",
    "provider": "postgres",
    "enabled": true,
    "host": "127.0.0.1",
    "port": 5432,
    "database": "review_db",
    "user": "readonly",
    "password_env": "PG_REVIEW_PASSWORD",
    "schema_allowlist": ["public"],
    "ssl_mode": "prefer",
    "connect_timeout_seconds": 5,
    "statement_timeout_ms": 3000
  }
]
```

3. 本地环境已设置对应密码环境变量，例如：

```bash
export PG_REVIEW_PASSWORD='your-password'
```

4. 待验证的 MR/PR 需要包含数据库相关变更，例如：

- `migration`
- `schema`
- `repository`
- `dao`
- SQL 语句

## 验证脚本

项目已提供脚本：

- [scripts/smoke_pg_review.py](/Users/neochen/multi-codereview-agent/scripts/smoke_pg_review.py)

执行方式：

```bash
.venv/bin/python scripts/smoke_pg_review.py \
  --mr-url "https://github.com/your-org/your-repo/pull/123" \
  --title "PG datasource smoke review"
```

可选参数：

- `--analysis-mode standard|light`
- `--timeout-seconds 240`

## 预期输出

脚本会输出 JSON，重点看 `pg_evidence`：

```json
{
  "review_id": "rev_xxx",
  "status": "completed",
  "phase": "completed",
  "pg_evidence": {
    "pg_tool_message_count": 1,
    "data_source_database": "review_db",
    "data_source_host": "127.0.0.1",
    "matched_tables": ["orders"],
    "pg_summary": "已从 PostgreSQL 数据源拉取 1 张表的结构与统计元信息。",
    "database_finding_count": 1,
    "database_finding_titles": ["orders.status 新增列缺少默认值与回填策略"]
  }
}
```

## 如何判断链路生效

至少满足以下条件：

1. `pg_tool_message_count > 0`
2. `matched_tables` 非空
3. `data_source_database` 有值
4. 数据库专家消息流里存在 `pg_schema_context`
5. `database_finding_count >= 0`

## 常见失败原因

### 1. `data_source_not_configured`

说明当前代码仓 URL 没有匹配到 PG 数据源配置。

检查：

- `repo_url` 是否和当前审核任务代码仓一致
- 是否使用了统一的 clone URL 口径

### 2. `password_env_missing`

说明已匹配到数据源，但本地没有设置密码环境变量。

### 3. `table_not_detected`

说明当前 MR 没有提取出明显的表名信号。

建议使用带有：

- SQL 语句
- migration 文件
- repository/entity 命名

的 MR 进行验证。

### 4. `query_failed`

说明已连接到 PG 元信息查询阶段，但查询失败。

重点检查：

- 只读账号权限
- schema 白名单
- `statement_timeout_ms`
- 网络连通性

## 当前实现边界

第一版只读拉取：

- 表字段
- 约束
- 索引
- 行数估计
- 表大小 / 索引大小
- vacuum / analyze 时间

默认不会扫全库，只会针对当前 MR 命中的表进行查询。
