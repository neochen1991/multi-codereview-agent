# multi-codereview-agent

多专家协同代码审核工具，后端基于 FastAPI，前端基于 React / Ant Design。

本文只说明本工具运行所需的前置条件、安装步骤、配置入口和启动方式。系统能力、专家边界、GitNexus 影响分析和质量治理说明见文末文档链接。

## 运行前置条件

请先在运行机器上准备：

- Python `>= 3.11`
- Node.js + npm
- Git
- 可访问模型服务的网络与 API Key
- 可访问待审核代码仓的 Git Token 或本地代码仓路径
- macOS / Linux 需要 `bash`、`curl`
- Windows 需要 PowerShell，建议使用 Python Launcher `py`
- 可选：GitNexus，用于生成 MR 关联影响分析报告

后端启动会用到 `uvicorn`。如果是全新环境，请按下面安装步骤安装。

## 首次安装

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e . "uvicorn[standard]>=0.30"
npm --prefix frontend install
```

Windows：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -e . "uvicorn[standard]>=0.30"
cd frontend
npm install
```

## 基础配置

项目根目录的 `config.json` 是默认配置入口，前端设置页 `/settings` 读写的也是这份文件。

至少需要确认：

- `server.backend_port`：默认 `8011`
- `server.frontend_port`：默认 `5174`
- `llm.default_base_url`
- `llm.default_model`
- `llm.default_api_key_env` 或 `llm.default_api_key`
- `git.repo_access_token`
- `code_repo.local_path` 或 `code_repositories[*].local_path`
- `code_repo.default_branch` 或 `code_repositories[*].default_branch`
- `network.verify_ssl` / `network.use_system_trust_store` / `network.ca_bundle_path`

如果使用环境变量放模型 Key，例如：

```bash
export DASHSCOPE_API_KEY="your-api-key"
```

Windows PowerShell：

```powershell
$env:DASHSCOPE_API_KEY="your-api-key"
```

## 启动工具

macOS / Linux：

```bash
bash scripts/start-all.sh
```

Windows：

```bat
scripts\start-all.bat
```

启动成功后访问：

- 前端：`http://127.0.0.1:5174`
- 后端健康检查：`http://127.0.0.1:8011/health`

日志位置：

```text
logs/backend.log
logs/frontend.log
```

## 停止工具

macOS / Linux：

```bash
bash scripts/stop-all.sh
```

Windows：

```bat
scripts\stop-all.bat
```

## 单独启动

后端：

```bash
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8011
```

Windows 后端：

```bat
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8011
```

前端：

```bash
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

## 可选：开启 GitNexus

如果需要 MR 关联影响分析，先在机器上安装并确认可执行：

```bash
gitnexus analyze
gitnexus mcp
```

再设置：

```bash
export GITNEXUS_INDEX_ENABLED=true
export GITNEXUS_INDEX_INTERVAL_SECONDS=3600
export GITNEXUS_INDEX_TIMEOUT_SECONDS=900
export GITNEXUS_ANALYZE_COMMAND="gitnexus analyze"
export GITNEXUS_MCP_COMMAND="gitnexus mcp"
```

Windows 或安装目录包含空格时，推荐：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "mcp"]'
$env:GITNEXUS_BIN="C:\Program Files\GitNexus\gitnexus.exe"
```

详细说明见 [GitNexus 关联影响分析说明](docs/architecture/2026-05-01-gitnexus-impact-analysis.md)。

## 常用验证

后端聚焦测试：

```bash
.venv/bin/python -m pytest backend/tests/services/test_gitnexus_impact_service.py
```

质量门禁：

```bash
bash scripts/check_review_quality.sh
```

采样真实 GitNexus MCP fixture：

```bash
PYTHONPATH=backend .venv/bin/python scripts/capture_gitnexus_mcp_fixture.py --repo-path /path/to/repo --preflight-only
```

前端：

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## 常见问题

如果后端启动失败，先看：

```text
logs/backend.log
```

如果前端启动失败，先看：

```text
logs/frontend.log
```

Windows 下 HTTPS 证书校验失败时，优先检查 `config.json`：

- `network.verify_ssl`
- `network.use_system_trust_store`
- `network.ca_bundle_path`

推荐先保持 `verify_ssl=true`，再配置系统证书或企业 CA。只有排障时才临时关闭证书校验。

## 说明文档

- [系统能力说明](docs/architecture/2026-05-01-system-capabilities.md)
- [GitNexus 关联影响分析说明](docs/architecture/2026-05-01-gitnexus-impact-analysis.md)
- [专家 Agent 职责边界手册](docs/architecture/2026-04-19-expert-agent-boundary-handbook.md)
- [Review Quality Eval Baseline](docs/architecture/2026-05-01-review-quality-eval-baseline.md)
- [Repo Review Policy](docs/architecture/2026-05-01-repo-review-policy.md)
- [系统运行与代码地图](docs/architecture/code-wiki.md)
