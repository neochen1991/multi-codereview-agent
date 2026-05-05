# multi-codereview-agent

多专家协同代码审核工具，后端基于 FastAPI，前端基于 React / Ant Design。

本文只说明本工具运行所需的前置条件、安装步骤、配置入口和启动方式。系统能力、专家边界、GitNexus 影响分析和质量治理说明见文末文档链接。

## 运行前置条件

请先在运行机器上准备：

- Python `>= 3.11`
- Node.js `>= 18` + npm
- Git，可在命令行执行 `git --version`
- 可访问模型服务的网络与 API Key，例如通义千问、OpenAI 兼容网关或公司内部模型网关
- 可访问待审核代码仓的 Git Token，或者已经克隆好的本地代码仓路径
- macOS / Linux：需要 `bash`、`curl`
- Windows：需要 PowerShell，建议安装 Python Launcher `py`
- 可选：GitNexus，用于生成 MR 关联影响分析报告
- 推荐：Tree-sitter Python 依赖，用于 Java 代码图谱和更准确的关联上下文

Python 依赖会通过 `pip install -e ".[code-graph]"` 安装，前端依赖会通过 `npm install` 安装。GitNexus 属于可选增强能力；Tree-sitter 不需要单独安装系统命令，本工具通过 Python 包 `tree-sitter` 和 `tree-sitter-language-pack` 使用它。

后端启动会用到 `uvicorn`。如果是全新环境，请按下面安装步骤安装。

## 首次安装

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
npm --prefix frontend install
```

Windows：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
cd frontend
npm install
```

安装后可以先检查：

```bash
python --version
node --version
npm --version
git --version
```

Windows 如果使用 `py`：

```bat
py -3.11 --version
node --version
npm --version
git --version
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

Windows 一键启动脚本会自动检查并补装：

- 后端基础依赖
- Tree-sitter 代码图谱依赖：`tree-sitter`、`tree-sitter-language-pack`、`networkx`
- 前端 `node_modules`

Windows 下后端日志默认写入 `logs/backend.log`，不再默认持续写控制台，避免 cmd/PowerShell 控制台输出阻塞导致页面假死。确实需要控制台日志调试时，可先设置：

```bat
set CODE_REVIEW_CONSOLE_LOG=true
```

启动后进入设置页，在“Tree-sitter 代码图谱”区块中为目标代码仓点击“建立/刷新图谱”。图谱生成后会写入目标仓库：

```text
<repo>/.code-review-graph/graph.db
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

如果需要 MR 关联影响分析，先安装 GitNexus CLI。GitNexus 依赖 Node.js，建议先确认 `node --version` 为 `18` 或更高。

推荐安装为全局命令：

```bash
npm install -g gitnexus
```

Windows PowerShell 同样使用：

```powershell
npm install -g gitnexus
```

如果不想全局安装，也可以使用 `npx` 临时运行：

```bash
npx -y gitnexus@latest analyze
```

安装后进入目标代码仓根目录，执行一次建图并确认仓库已注册：

```bash
cd /path/to/your/repo
gitnexus analyze
gitnexus list
gitnexus status
```

Windows：

```powershell
cd D:\workspace\your-repo
gitnexus analyze
gitnexus list
gitnexus status
```

本工具默认调用全局命令：

```bash
export GITNEXUS_INDEX_ENABLED=true
export GITNEXUS_INDEX_INTERVAL_SECONDS=3600
export GITNEXUS_INDEX_TIMEOUT_SECONDS=1800
export GITNEXUS_ANALYZE_COMMAND="gitnexus analyze"
export GITNEXUS_MCP_COMMAND="gitnexus mcp"
```

Windows 或安装目录包含空格时，推荐：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "mcp"]'
$env:GITNEXUS_BIN="C:\Program Files\GitNexus\gitnexus.exe"
```

如果使用 `npx` 而不是全局安装，推荐显式配置：

```bash
export GITNEXUS_ANALYZE_COMMAND='["npx", "-y", "gitnexus@latest", "analyze"]'
export GITNEXUS_MCP_COMMAND='["npx", "-y", "gitnexus@latest", "mcp"]'
```

Windows PowerShell：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["npx", "-y", "gitnexus@latest", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["npx", "-y", "gitnexus@latest", "mcp"]'
```

如果遇到 GitNexus 原生依赖加载错误，通常先检查 Node.js 版本，然后重新执行 `npm install -g gitnexus`。

大仓库或 Windows 机器较慢时，可以适当调大 MR 快照和 worktree 建图时间：

```powershell
$env:REVIEW_WORKSPACE_FETCH_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_WORKTREE_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_MERGE_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_APPLY_TIMEOUT_SECONDS="600"
$env:GITNEXUS_INDEX_TIMEOUT_SECONDS="1800"
$env:GITNEXUS_REVIEW_WORKSPACE_INDEX_TIMEOUT_SECONDS="1800"
```

其中 `GITNEXUS_INDEX_TIMEOUT_SECONDS` 控制设置页/后台建图最长等待时间，`GITNEXUS_REVIEW_WORKSPACE_INDEX_TIMEOUT_SECONDS` 控制检视任务中针对 MR 快照 worktree 自动执行 `gitnexus analyze` 的最长等待时间，默认都是 `1800` 秒。

详细说明见 [GitNexus 关联影响分析说明](docs/architecture/2026-05-01-gitnexus-impact-analysis.md)。

## 可选：安装 Tree-sitter 依赖

本项目参考 `code-review-graph` 引入了 Tree-sitter 本地代码图谱，用于给 Java 检视提供更准确的调用方、被调方、接口实现、测试影响和最小上下文。安装后会优先使用 Tree-sitter 图谱检索关联上下文，没有命中或不可用时会自动退化为关键词搜索。

注意：本工具使用的是 Python 包，不需要在 Windows 上安装 `tree-sitter.exe`，也不需要执行 `npm install -g tree-sitter-cli`。

Windows 推荐安装方式：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[code-graph]"
.venv\Scripts\python.exe -c "import tree_sitter, tree_sitter_language_pack; from tree_sitter_language_pack import get_language; get_language('java'); print('tree-sitter java ok')"
```

也可以直接运行一键启动脚本，它会自动做同样的依赖检查和安装：

```bat
scripts\start-all.bat
```

安装完成后，进入前端设置页 `/settings`，在“Tree-sitter 代码图谱”区块中点击目标仓库的“建立/刷新图谱”。成功后页面会展示：

- 图谱数据库路径：`<repo>/.code-review-graph/graph.db`
- 图谱规模：已索引文件数、节点数、关系数
- 最近更新时间
- 本次索引、跳过和失败文件数量

macOS / Linux 安装方式：

```bash
.venv/bin/python -m pip install -e ".[code-graph]"
.venv/bin/python -c "import tree_sitter, tree_sitter_language_pack; from tree_sitter_language_pack import get_language; get_language('java'); print('tree-sitter java ok')"
```

如果 Windows 使用公司内网 PyPI 镜像，确认镜像里有以下包：

- `tree-sitter`
- `tree-sitter-language-pack`
- `networkx`

例如需要临时指定镜像：

```bat
set PIP_INDEX_URL=https://your-internal-pypi/simple
.venv\Scripts\python.exe -m pip install -e ".[code-graph]"
```

PowerShell：

```powershell
$env:PIP_INDEX_URL="https://your-internal-pypi/simple"
.venv\Scripts\python.exe -m pip install -e ".[code-graph]"
```

如果 pip 尝试从源码编译并失败，优先升级 pip / wheel：

```bat
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
```

说明：

- 不需要单独安装 Tree-sitter CLI。
- `tree-sitter-language-pack` 提供常用语言 grammar，减少 Windows 下本地编译成本。
- 这些依赖已放在 `pyproject.toml` 的 `code-graph` 可选依赖组中。

方案说明见 [借鉴 code-review-graph 的关联上下文优化方案](docs/plans/2026-05-05-code-review-graph-context-optimization.md)。

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
- [借鉴 code-review-graph 的关联上下文优化方案](docs/plans/2026-05-05-code-review-graph-context-optimization.md)
- [专家 Agent 职责边界手册](docs/architecture/2026-04-19-expert-agent-boundary-handbook.md)
- [Review Quality Eval Baseline](docs/architecture/2026-05-01-review-quality-eval-baseline.md)
- [Repo Review Policy](docs/architecture/2026-05-01-repo-review-policy.md)
- [系统运行与代码地图](docs/architecture/code-wiki.md)
