# GitNexus 关联影响分析说明

## 目标

普通 code review 更关注“这几行代码有没有问题”。GitNexus 关联影响分析关注的是：

```text
这次 MR 改了这些文件以后，可能波及哪些入口、模块、调用链，以及应该回归哪些测试？
```

本项目通过 `change_impact_analysis` 专家消费 GitNexus 图谱结果。它不参与普通 issue/finding 收敛链路，只输出独立的 `impact_report`。

## 运行模型

整体分两段：

| 阶段 | 组件 | 输出 |
|---|---|---|
| 后台建图 | `GitNexusIndexScheduler` | `.gitnexus/` 图谱和 `index_status.json` |
| MR 影响分析 | `GitNexusImpactService` + `change_impact_analysis` | `ReviewReport.impact_report` |

默认原则：

- 每个 MR 默认带上 `change_impact_analysis`
- 运行时优先使用后台已建好的 GitNexus 图谱
- 只有拿到 GitNexus MCP 正式结果才展示关联影响报告正文
- 图谱未就绪、registry 未识别、MCP 调用失败时，影响分析步骤标记失败，但不阻断主审核
- 系统不会生成伪造的 fallback 影响报告

## 后台建图

开启：

```bash
export GITNEXUS_INDEX_ENABLED=true
export GITNEXUS_INDEX_INTERVAL_SECONDS=3600
export GITNEXUS_INDEX_TIMEOUT_SECONDS=900
export GITNEXUS_ANALYZE_COMMAND="gitnexus analyze"
```

单仓配置可以使用：

```json
{
  "code_repo": {
    "local_path": "/data/repos/your-project",
    "default_branch": "main"
  }
}
```

多仓配置建议使用：

```json
{
  "default_repository_id": "ipc-fnd-service",
  "code_repositories": [
    {
      "repository_id": "ipc-fnd-service",
      "name": "IPC FND Service",
      "provider": "codehub",
      "clone_url": "https://codehub.example.com/ipc/ipc-fnd-service.git",
      "web_url_prefixes": ["https://codehub.example.com/ipc/ipc-fnd-service"],
      "local_path": "D:/workspace/ipc-fnd-service",
      "default_branch": "master",
      "enabled": true,
      "auto_review_enabled": true,
      "auto_review_poll_interval_seconds": 120,
      "auto_sync": false,
      "gitnexus_enabled": true,
      "database_source_ids": []
    }
  ]
}
```

建图状态文件：

```text
backend/app/storage/gitnexus/index_status.json
backend/app/storage/gitnexus/{repository_id}/index_status.json
```

成功状态示例：

```json
{
  "state": "ready",
  "repo_path": "/data/repos/your-project",
  "repo_name": "your-project",
  "indexed_at": "2026-04-27T00:00:00+00:00",
  "commit": "当前仓库 HEAD commit",
  "graph_dir": "/data/repos/your-project/.gitnexus",
  "graph_dir_exists": true,
  "registry_path": "/home/service/.gitnexus/registry.json",
  "registry_registered": true
}
```

`registry_registered=true` 表示当前仓库已经被 GitNexus 官方 registry 识别。若为 `false`，说明本地建图成功但还没有完全走通 registry / MCP 发现流程。

## MR 审核时的调用顺序

```text
读取 review.subject.metadata.gitnexus_impact_report
  -> 如果已有外部预计算结果，直接使用
否则读取 GitNexus index_status.json
  -> state=ready 且本地仓存在 .gitnexus/，继续
  -> 否则影响分析失败，主审核继续
使用 GitNexus MCP 查询图谱
  -> list_repos：确认仓库已按官方流程注册
  -> detect_changes(scope=all)：识别整体变更影响
  -> context：围绕关键变更类/方法补齐上下游语义
  -> impact：对 diff 中提取出的变更类/方法/函数做符号级影响分析
标准化为 ReviewReport.impact_report
```

当前默认 MCP 命令：

```bash
gitnexus mcp
```

可配置为：

```bash
export GITNEXUS_ANALYZE_COMMAND="gitnexus analyze"
export GITNEXUS_MCP_COMMAND="gitnexus mcp"
export GITNEXUS_MCP_TIMEOUT_SECONDS=45
```

## Windows 可行性

Windows 或安装路径包含空格时，推荐使用 JSON array：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "mcp"]'
```

也可以只指定二进制路径：

```powershell
$env:GITNEXUS_BIN="C:\Program Files\GitNexus\gitnexus.exe"
```

当前适配层已经处理：

- `GITNEXUS_ANALYZE_COMMAND` JSON array 解析
- `GITNEXUS_MCP_COMMAND` JSON array 解析
- 带空格 exe 路径的引号解析
- `GITNEXUS_BIN` + `analyze/mcp` 自动拼接
- Windows 盘符大小写等价
- `\` 与 `/` 等价
- 尾部分隔符等价
- 传给 `git diff` / `git show` 的变更文件路径统一转换为 Git 风格 `/`

## Preflight 诊断

`GitNexusImpactService.preflight(...)` 会返回结构化诊断，便于部署排障。

检查项包括：

- `git_binary`
- `gitnexus_command`
- `repo_path`
- `git_repository`
- `gitnexus_registry`
- `registry_repo_match`
- `graph_status`
- `recommended_actions`

典型用途：

- Windows 机器上确认 `GITNEXUS_BIN` 或 `GITNEXUS_MCP_COMMAND` 是否可解析
- 确认 `code_repo.local_path` / `code_repositories[*].local_path` 是否指向真实 Git 仓
- 确认 GitNexus registry 中的路径是否和本系统配置路径等价
- 确认 `.gitnexus` 图谱和 `index_status.json` 是否 ready

## 查询质量优化

为了减少无效 MCP 查询，影响分析会对候选 target 做排序、去重和限流。

优先级更高的 target：

- 变更容器 + 符号组合，例如 `OrderService.createOrder`
- Controller / API 入口
- Service 应用服务或领域服务
- Repository / Mapper 数据访问路径
- 函数或方法级符号
- 有明确行号的符号

优先级更低的 target：

- DTO / Request / Response
- config / constant
- Java 关键字或无效符号
- 重复 target

默认限制：

- `MAX_GITNEXUS_TARGETS = 12`
- `MAX_GITNEXUS_CONTEXT_QUERIES = 8`
- `MAX_GITNEXUS_IMPACT_QUERIES = 8`

同一轮分析中，重复的 `context` / `impact` target 会走缓存，避免重复 MCP 调用。

## 结果页口径

结果页“关联影响报告”主要看：

| 字段 | 含义 |
|---|---|
| `graph_status` | `ready` 表示使用图谱 |
| `risk_level` | 图谱和变更面综合后的影响风险 |
| `changed_files` | 本次 MR 直接修改的文件 |
| `impacted_files` | 图谱识别出的候选受影响文件 |
| `impacted_modules` | 受影响模块 |
| `recommended_test_scope` | 建议回归范围 |
| `must_run_tests` | 建议执行的测试命令或测试类型 |
| `limitations` | 图谱状态和分析边界 |

它不是缺陷清单。缺陷是否成立仍以有效问题清单为准。

## Smoke Test

脚本：

```text
scripts/smoke_gitnexus_impact_demo.py
```

运行：

```bash
PYTHONPATH=backend .venv/bin/python scripts/smoke_gitnexus_impact_demo.py
```

这条脚本会走：

```text
ReviewService.create_review
-> ReviewService.start_review
-> ReviewRunner impact_analysis
-> GitNexusImpactService.analyze
-> ReviewReport.impact_report
```

它会覆盖两条路径：

- 图谱 ready，GitNexus 查询成功
- 图谱 ready，但 GitNexus MCP 查询失败

默认使用 fake client 模拟成功/失败，不依赖真实外网 GitNexus 服务。若要验证真实 `gitnexus analyze` 和 MCP，需要先在部署机器预装 GitNexus 并完成真实建图。

## 真实 MCP Payload 采样

如果部署机器已经安装 GitNexus，并且目标仓库已经执行过 `gitnexus analyze`，可以采样真实 MCP 输出：

```bash
PYTHONPATH=backend .venv/bin/python scripts/capture_gitnexus_mcp_fixture.py \
  --repo-path /data/repos/order-service \
  --repo-name order-service \
  --source-ref feature/order-create \
  --target-ref main \
  --changed-file src/main/java/com/example/order/OrderController.java \
  --output backend/tests/fixtures/gitnexus_mcp/order-service-real.json
```

Windows PowerShell 示例：

```powershell
$env:GITNEXUS_BIN="C:\Program Files\GitNexus\gitnexus.exe"
$env:PYTHONPATH="backend"
.venv\Scripts\python.exe scripts\capture_gitnexus_mcp_fixture.py `
  --repo-path D:\workspace\order-service `
  --repo-name order-service `
  --source-ref feature/order-create `
  --target-ref main `
  --changed-file src/main/java/com/example/order/OrderController.java `
  --output backend/tests/fixtures/gitnexus_mcp/order-service-real.json
```

只做环境诊断：

```bash
PYTHONPATH=backend .venv/bin/python scripts/capture_gitnexus_mcp_fixture.py \
  --repo-path /data/repos/order-service \
  --repo-name order-service \
  --preflight-only
```

采样文件可以作为 fixture 加入 `backend/tests/fixtures/gitnexus_mcp/`，再补一条标准化测试，防止真实 GitNexus 输出结构变化时解析器静默退化。

## 相关代码

- `backend/app/services/gitnexus_impact_service.py`
- `backend/app/services/gitnexus_index_scheduler.py`
- `backend/app/services/review_runner.py`
- `scripts/capture_gitnexus_mcp_fixture.py`
- `backend/tests/services/test_gitnexus_impact_service.py`
