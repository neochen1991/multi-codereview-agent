# multi-codereview-agent

多专家协同代码检视工具。后端基于 FastAPI，前端基于 React / Vite / Ant Design；支持本地仓库、MR/分支检视、Tree-sitter 代码图谱、GitNexus 影响分析、SAST/linter 预扫描和检视质量治理。

本文是运行手册，重点说明从零安装、配置和启动本工具的完整步骤，覆盖 macOS/Linux 与 Windows 两类环境。

## 快速路径

已经准备好 Python、Node.js、Git 和模型 API Key 时，可以直接按下面跑。

macOS / Linux：

```bash
git clone <this-repo-url>
cd multi-codereview-agent

python3 -m venv .venv
.venv/bin/python -m pip install -U pip setuptools wheel
.venv/bin/python -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
npm --prefix frontend install

export MINIMAX_API_KEY="your-api-key"
# 按需编辑 config.json，至少配置 llm、projects[*].repositories[*] 或 code_repo。

bash scripts/start-all.sh
```

Windows PowerShell：

```powershell
git clone <this-repo-url>
cd multi-codereview-agent

py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
cd frontend
npm install
cd ..

$env:MINIMAX_API_KEY="your-api-key"
# 按需编辑 config.json，至少配置 llm、projects[*].repositories[*] 或 code_repo。

scripts\start-all.bat
```

启动成功后访问：

- 前端工作台：`http://127.0.0.1:5174`
- 后端健康检查：`http://127.0.0.1:8011/health`
- 设置页：`http://127.0.0.1:5174/settings`

## 目录结构

```text
backend/                  FastAPI 后端、检视编排、存储、SAST 和 GitNexus 集成
frontend/                 React/Vite 前端
scripts/start-all.sh      macOS/Linux 一键启动
scripts/start-all.bat     Windows 一键启动
scripts/stop-all.sh       macOS/Linux 停止服务
scripts/stop-all.bat      Windows 停止服务
config.json               默认运行配置；前端设置页也会读写它
logs/                     启动后生成的后端/前端日志
backend/app/storage/      默认 SQLite 数据和运行时存储
```

## 运行前置条件

必需：

| 依赖 | 推荐版本 | 用途 | 验证命令 |
| --- | --- | --- | --- |
| Python | `3.11` 或更高 | 后端、检视编排、SAST Python 工具 | `python3 --version` 或 `py -3.11 --version` |
| Node.js + npm | Node `18` 或更高 | 前端、GitNexus 可选 CLI、ESLint 可选工具 | `node --version`、`npm --version` |
| Git | 任意现代版本 | 克隆本工具和目标代码仓、生成 MR 快照 | `git --version` |
| 模型 API Key | 取决于模型供应商 | 专家 Agent 调用 LLM | 见“模型配置” |
| 目标代码仓访问能力 | 本地路径或 Git Token | 拉取/检视代码 | `git clone` 或本地仓存在 |

推荐：

| 依赖 | 用途 |
| --- | --- |
| Tree-sitter Python 包 | Java 代码图谱、调用链、最小上下文检索 |
| Semgrep / PMD / Checkstyle / ESLint / Bandit | SAST/linter 预扫描候选信号 |
| GitNexus CLI | MR 关联影响分析 |
| Java / Maven / Gradle | 为 Java 项目生成 SpotBugs、ArchUnit、JaCoCo 等报告 |
| PostgreSQL | 需要持久化到 Postgres 时使用；默认 SQLite 不需要 |

## macOS / Linux 安装

1. 安装系统工具。

macOS 可使用 Homebrew：

```bash
brew install python@3.11 node git
```

Linux 可使用系统包管理器，例如 Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip nodejs npm git curl
```

2. 创建虚拟环境并安装后端依赖。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip setuptools wheel
.venv/bin/python -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
```

`.[code-graph]` 会安装 Tree-sitter 代码图谱依赖：

- `tree-sitter`
- `tree-sitter-language-pack`
- `networkx`
- `watchdog`

验证 Tree-sitter Java grammar：

```bash
.venv/bin/python -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke { void ok() {} }'); print('tree-sitter java ok')"
```

3. 安装前端依赖。

```bash
npm --prefix frontend install
```

4. 可选安装 SAST/linter 工具。

```bash
python3 -m pip install semgrep bandit
brew install pmd checkstyle
npm install -g eslint

semgrep --version
bandit --version
pmd --version
checkstyle --version
eslint --version
```

5. 可选安装 GitNexus。

```bash
npm install -g gitnexus
gitnexus --version
```

## Windows 安装

建议使用 PowerShell 或 cmd，路径尽量避免中文和过深目录。目标代码仓路径可以包含空格，但配置 GitNexus 或外部工具命令时要使用 JSON array 写法，见“GitNexus 配置”。

1. 安装必需工具。

- Python 3.11：安装时勾选 “Add python.exe to PATH”，并确认 `py` 可用。
- Node.js LTS：建议 Node 18 或更高。
- Git for Windows：安装后确认 PowerShell/cmd 能执行 `git`。
- 可选 Chocolatey：便于安装 PMD、Checkstyle。

验证：

```powershell
py -3.11 --version
node --version
npm --version
git --version
```

2. 创建虚拟环境并安装后端依赖。

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install -e ".[code-graph]" "uvicorn[standard]>=0.30"
```

验证 Tree-sitter Java grammar：

```powershell
.venv\Scripts\python.exe -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke { void ok() {} }'); print('tree-sitter java ok')"
```

如果公司内网 PyPI 镜像缺少 wheel，可先设置镜像，再重装：

```powershell
$env:PIP_INDEX_URL="https://your-internal-pypi/simple"
.venv\Scripts\python.exe -m pip install -U "tree-sitter>=0.23,<1" "tree-sitter-language-pack>=0.13,<1" "networkx>=3.2,<4"
```

3. 安装前端依赖。

```powershell
cd frontend
npm install
cd ..
```

4. 可选安装 SAST/linter 工具。

```powershell
py -m pip install semgrep bandit
npm install -g eslint

where semgrep
where bandit
where eslint
semgrep --version
bandit --version
eslint --version
```

PMD / Checkstyle 可通过 Chocolatey 安装，也可以下载发行包并把 `bin` 加入 PATH：

```powershell
choco install pmd
choco install checkstyle
where pmd
where checkstyle
```

5. 可选安装 GitNexus。

```powershell
npm install -g gitnexus
gitnexus --version
```

## 配置入口

本工具主要配置文件是项目根目录的 `config.json`。前端设置页 `/settings` 也会读写同一份配置。

后端还支持这些环境变量覆盖运行路径：

| 环境变量 | 作用 | 默认值 |
| --- | --- | --- |
| `CONFIG_PATH` | 指定配置文件路径 | `<project>/config.json` |
| `STORAGE_ROOT` | 指定后端存储目录 | `<project>/backend/app/storage` |
| `SQLITE_DB_PATH` | 指定 SQLite DB 文件 | `<storage_root>/app.db` |
| `CODE_REVIEW_CONSOLE_LOG` | Windows 调试时打开控制台日志 | 空，默认写入日志文件 |

示例：

macOS / Linux：

```bash
export CONFIG_PATH="/path/to/config.json"
export STORAGE_ROOT="/path/to/storage"
```

Windows PowerShell：

```powershell
$env:CONFIG_PATH="D:\workspace\multi-codereview-agent\config.json"
$env:STORAGE_ROOT="D:\workspace\multi-codereview-agent\storage"
```

## 模型配置

模型调用采用 OpenAI-compatible Chat Completions 协议。后端会把：

```text
llm.default_base_url + /chat/completions
```

拼成最终请求地址。因此 `default_base_url` 应填到 OpenAI-compatible API 根路径，例如 `/v1` 或供应商给出的兼容根路径，不要把 `/chat/completions` 一起填进去。

推荐使用环境变量保存 API Key，不推荐把密钥写进 `config.json`。

安全配置示例：

```json
{
  "llm": {
    "default_provider": "openai-compatible",
    "default_base_url": "https://your-provider.example.com/v1",
    "default_model": "your-model-name",
    "default_api_key_env": "MINIMAX_API_KEY",
    "default_api_key": null
  }
}
```

macOS / Linux：

```bash
export MINIMAX_API_KEY="your-api-key"
```

Windows PowerShell：

```powershell
$env:MINIMAX_API_KEY="your-api-key"
```

如果使用 MiniMax 2.7 / 火山方舟一类 OpenAI-compatible 端点，配置形态通常类似：

```json
{
  "llm": {
    "default_provider": "openai-compatible",
    "default_base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
    "default_model": "MiniMax-M2.7",
    "default_api_key_env": "MINIMAX_API_KEY",
    "default_api_key": null
  }
}
```

如果 API Key 有效但工具调不通，优先检查：

- 启动后端的同一个终端里是否设置了 `MINIMAX_API_KEY`。
- `default_api_key_env` 是否和环境变量名完全一致。
- `default_base_url` 是否多填了 `/chat/completions`。
- 模型名是否是供应商要求的精确名称。
- 公司代理或证书是否影响后端访问模型服务。
- Windows `.bat` 启动时是否继承了 PowerShell/cmd 当前环境变量。

可用 curl 或 PowerShell 先直连模型端点。

macOS / Linux：

```bash
export LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
export MINIMAX_API_KEY="your-api-key"

curl -sS "$LLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $MINIMAX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"MiniMax-M2.7","messages":[{"role":"user","content":"ping"}],"temperature":0.1}'
```

Windows PowerShell：

```powershell
$env:LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
$env:MINIMAX_API_KEY="your-api-key"

$body = @{
  model = "MiniMax-M2.7"
  messages = @(@{ role = "user"; content = "ping" })
  temperature = 0.1
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "$env:LLM_BASE_URL/chat/completions" `
  -Headers @{ Authorization = "Bearer $env:MINIMAX_API_KEY" } `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## 代码仓配置

至少配置一个目标仓库。推荐使用 `projects[*].repositories[*]`，旧版 `code_repo` 字段仍可兼容。

示例：

```json
{
  "projects": [
    {
      "project_id": "default",
      "name": "默认项目",
      "status": "active",
      "repositories": [
        {
          "repository_id": "demo-service",
          "name": "demo-service",
          "provider": "github",
          "clone_url": "https://github.com/your-org/demo-service.git",
          "web_url_prefixes": ["https://github.com/your-org/demo-service"],
          "local_path": "/path/to/demo-service",
          "default_branch": "main",
          "enabled": true,
          "auto_review_enabled": false,
          "auto_sync": false,
          "gitnexus_enabled": true
        }
      ]
    }
  ],
  "default_project_id": "default"
}
```

Windows 路径示例：

```json
{
  "local_path": "D:\\workspace\\demo-service",
  "default_branch": "main"
}
```

Git Token 配置：

```json
{
  "git": {
    "repo_access_token": null,
    "github_access_token": null,
    "gitlab_access_token": null,
    "codehub_access_token": null
  }
}
```

说明：

- 本地仓库存在时，优先使用 `local_path`。
- 需要拉取远端 MR/分支时，配置 `clone_url` 和对应平台 token。
- `repo_access_token` 是通用兜底 token；平台专用 token 优先级更高。
- Windows 路径在 JSON 中要使用双反斜杠。

## 运行时配置

常用字段：

| 字段 | 说明 |
| --- | --- |
| `runtime.default_analysis_mode` | 默认检视模式，`light` 更快，`standard` 更完整 |
| `runtime.storage_backend` | `sqlite` 或 `postgres`；默认 `sqlite` |
| `runtime.enable_sast_prescan` | 是否启用 SAST/linter 预扫描 |
| `runtime.enable_llm_issue_judge` | 是否启用 LLM issue 二次裁决 |
| `runtime.enable_llm_targeted_debate` | 是否启用定向辩论 |
| `runtime.allow_human_gate` | 是否允许需要人工确认的问题进入人工门禁 |
| `runtime.light_llm_timeout_seconds` | light 模式单次 LLM 超时 |
| `runtime.standard_llm_timeout_seconds` | standard 模式单次 LLM 超时 |
| `runtime.standard_max_parallel_experts` | standard 模式专家并发数 |

默认 SQLite 不需要额外数据库。切换 Postgres 时需要配置：

```json
{
  "runtime": {
    "storage_backend": "postgres",
    "storage_pg_url": "postgresql://host:5432/database",
    "storage_pg_schema": "public",
    "storage_pg_user": "user",
    "storage_pg_password": "password"
  }
}
```

## 网络与证书配置

`network` 控制后端访问模型服务、代码平台等外部服务时的 HTTPS 校验：

```json
{
  "network": {
    "verify_ssl": true,
    "use_system_trust_store": true,
    "ca_bundle_path": ""
  }
}
```

建议：

- 正常环境保持 `verify_ssl=true`。
- 公司内网证书优先配置系统信任或 `ca_bundle_path`。
- 只有排障时临时关闭 `verify_ssl`。
- 如果设置了代理，确保本地服务不走代理。

macOS / Linux：

```bash
export NO_PROXY="127.0.0.1,localhost"
```

Windows PowerShell：

```powershell
$env:NO_PROXY="127.0.0.1,localhost"
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

启动脚本会启动：

- 后端：`http://127.0.0.1:8011`
- 前端：`http://127.0.0.1:5174`

日志：

```text
logs/backend.log
logs/frontend.log
```

PID 文件：

| 环境 | PID 目录 |
| --- | --- |
| macOS / Linux | `${TMPDIR:-/tmp}/multi-codereview-agent` |
| Windows | `<project>\run` |

Windows 一键启动脚本会检查并尝试补装：

- 后端基础依赖
- Tree-sitter 代码图谱依赖
- 前端 `node_modules`

如果要单独启动后端：

macOS / Linux：

```bash
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8011 --reload
```

Windows：

```bat
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8011 --reload
```

如果要单独启动前端：

```bash
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174 --strictPort
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

如果端口被旧进程占用：

macOS / Linux：

```bash
lsof -i :8011
lsof -i :5174
kill <pid>
```

Windows：

```powershell
netstat -ano | findstr :8011
netstat -ano | findstr :5174
taskkill /PID <pid> /T /F
```

## 首次运行指引

1. 打开前端：`http://127.0.0.1:5174`
2. 进入设置页 `/settings`。
3. 检查模型配置是否正确。
4. 检查目标项目和代码仓配置。
5. 如果目标是 Java 项目，进入“Tree-sitter 代码图谱”区域，点击“建立/刷新图谱”。
6. 如果开启 SAST/linter，检查“静态分析工具”状态。
7. 进入检视工作台 `/review`。
8. 新建检视任务，选择项目、仓库、source/target 分支或提交。
9. 启动检视。
10. 在结果页查看正式问题、保留观察清单、未升级原因、证据链和影响分析。

也可以用 API 创建一条检视任务：

```bash
curl -sS -X POST http://127.0.0.1:8011/api/reviews \
  -H "Content-Type: application/json" \
  -d '{
    "subject_type": "mr",
    "analysis_mode": "light",
    "project_id": "default",
    "repo_id": "demo-service",
    "source_ref": "feature/demo",
    "target_ref": "main",
    "title": "manual smoke review"
  }'
```

返回 `review_id` 后启动：

```bash
curl -sS -X POST http://127.0.0.1:8011/api/reviews/<review_id>/start
curl -sS http://127.0.0.1:8011/api/reviews/<review_id>/report
```

## SAST/linter 预扫描

SAST/linter 预扫描用于提升检视召回率。工具输出不会直接升级为正式问题，而是进入 `tool_observations`，再交给专家 Agent 结合 diff、上下文、通用规范和项目规则判断。

开关：

```json
{
  "runtime": {
    "enable_sast_prescan": true
  }
}
```

命令类工具：

| 工具 | 适用 | 安装示例 macOS/Linux | 安装示例 Windows | 校验 |
| --- | --- | --- | --- | --- |
| Semgrep | 通用安全、Java/SQL/Python/JS/TS 规则 | `python3 -m pip install semgrep` | `py -m pip install semgrep` | `semgrep --version` |
| PMD | Java 复杂度、坏味道、空 catch 等 | `brew install pmd` | `choco install pmd` | `pmd --version` |
| Checkstyle | Java 规范、命名、导入、格式 | `brew install checkstyle` | `choco install checkstyle` | `checkstyle --version` |
| ESLint | JavaScript/TypeScript | `npm install -g eslint` | `npm install -g eslint` | `eslint --version` |
| Bandit | Python 安全 | `python3 -m pip install bandit` | `py -m pip install bandit` | `bandit --version` |

报告类工具不由本工具直接启动，而是读取目标项目已有 XML 报告：

| 工具 | 常见报告路径 | 作用 |
| --- | --- | --- |
| SpotBugs | `target/spotbugsXml.xml`、`target/spotbugs.xml`、`build/reports/spotbugs/main.xml` | 空指针、资源泄漏、并发、安全 bug pattern |
| ArchUnit | `target/surefire-reports/*.xml`、`target/failsafe-reports/*.xml`、`build/test-results/**/*.xml` | 分层依赖、DDD 边界、架构规则失败 |
| JaCoCo | `target/site/jacoco/jacoco.xml`、`build/reports/jacoco/test/jacocoTestReport.xml` | 覆盖率缺口候选信号 |

目标 Java 项目可以先运行：

```bash
mvn test
mvn spotbugs:spotbugs
mvn jacoco:report
```

或 Gradle：

```bash
./gradlew test spotbugsMain jacocoTestReport
```

Windows：

```powershell
mvn test
mvn spotbugs:spotbugs
mvn jacoco:report
```

工具状态含义：

| 状态 | 含义 |
| --- | --- |
| `available` | 后端进程 PATH 中能找到命令，或报告文件已存在 |
| `missing` | 命令类工具未安装或未加入后端进程 PATH |
| `requires_report` | 需要目标项目先生成 XML 报告 |
| `disabled` | 总开关关闭 |

## Tree-sitter 代码图谱

Tree-sitter 图谱用于 Java 检视的调用方、被调方、领域模型、测试影响和最小上下文检索。

安装依赖：

macOS / Linux：

```bash
.venv/bin/python -m pip install -e ".[code-graph]"
```

Windows：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[code-graph]"
```

验证：

```bash
.venv/bin/python -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke {}'); print('tree-sitter java ok')"
```

Windows：

```powershell
.venv\Scripts\python.exe -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke {}'); print('tree-sitter java ok')"
```

刷新图谱：

1. 启动前后端。
2. 打开 `/settings`。
3. 在“Tree-sitter 代码图谱”区域选择目标仓库。
4. 点击“建立/刷新图谱”。

图谱文件会写入目标仓：

```text
<repo>/.code-review-graph/graph.db
```

## GitNexus 影响分析

GitNexus 是可选增强能力。未安装时检视仍可运行，只是不会生成 GitNexus 关联影响分析。

安装：

```bash
npm install -g gitnexus
```

目标仓预建图：

macOS / Linux：

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

常用环境变量：

macOS / Linux：

```bash
export GITNEXUS_INDEX_ENABLED=true
export GITNEXUS_INDEX_INTERVAL_SECONDS=3600
export GITNEXUS_INDEX_TIMEOUT_SECONDS=1800
export GITNEXUS_ANALYZE_COMMAND="gitnexus analyze"
export GITNEXUS_MCP_COMMAND="gitnexus mcp"
```

Windows PowerShell，安装路径含空格时推荐 JSON array：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["C:\\Program Files\\GitNexus\\gitnexus.exe", "mcp"]'
$env:GITNEXUS_BIN="C:\Program Files\GitNexus\gitnexus.exe"
```

使用 `npx`：

```bash
export GITNEXUS_ANALYZE_COMMAND='["npx", "-y", "gitnexus@latest", "analyze"]'
export GITNEXUS_MCP_COMMAND='["npx", "-y", "gitnexus@latest", "mcp"]'
```

Windows PowerShell：

```powershell
$env:GITNEXUS_ANALYZE_COMMAND='["npx", "-y", "gitnexus@latest", "analyze"]'
$env:GITNEXUS_MCP_COMMAND='["npx", "-y", "gitnexus@latest", "mcp"]'
```

大仓库或 Windows 机器较慢时，可调大超时：

```powershell
$env:REVIEW_WORKSPACE_FETCH_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_WORKTREE_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_MERGE_TIMEOUT_SECONDS="600"
$env:REVIEW_WORKSPACE_APPLY_TIMEOUT_SECONDS="600"
$env:GITNEXUS_INDEX_TIMEOUT_SECONDS="1800"
$env:GITNEXUS_REVIEW_WORKSPACE_INDEX_TIMEOUT_SECONDS="1800"
```

更多说明见 [GitNexus 关联影响分析说明](docs/architecture/2026-05-01-gitnexus-impact-analysis.md)。

## 常用验证

后端测试：

```bash
.venv/bin/python -m pytest backend/tests
```

聚焦 smoke：

```bash
.venv/bin/python scripts/smoke_review.py
```

前端类型检查：

```bash
npm --prefix frontend run typecheck
```

前端生产构建：

```bash
npm --prefix frontend run build
```

服务健康：

```bash
curl -sS http://127.0.0.1:8011/health
curl -I http://127.0.0.1:5174
```

Windows PowerShell：

```powershell
Invoke-WebRequest http://127.0.0.1:8011/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:5174 -UseBasicParsing
```

## 常见问题

### 后端启动失败

先看日志：

```text
logs/backend.log
```

常见原因：

- `.venv` 未创建。
- 未安装 `uvicorn[standard]`。
- 8011 端口被占用。
- `config.json` JSON 格式错误。
- Windows 环境变量未在启动脚本所在终端设置。

### 前端启动失败

先看日志：

```text
logs/frontend.log
```

常见原因：

- 未安装 Node.js/npm。
- `frontend/node_modules` 缺失，重新执行 `npm --prefix frontend install`。
- 5174 端口被占用。

### 模型 API Key 有效但工具调不通

按顺序检查：

1. 后端进程是否继承了 API Key 环境变量。
2. `config.json` 中 `llm.default_api_key_env` 是否等于真实环境变量名。
3. `llm.default_base_url` 是否是兼容根路径，不含 `/chat/completions`。
4. `llm.default_model` 是否和供应商模型名一致。
5. 是否需要公司代理、系统证书或 CA bundle。
6. 日志里是否出现 `missing_api_key:<env>`、连接超时或 401/403。

### Windows 下静态工具显示 missing

原因通常是后端启动进程的 PATH 看不到该工具。处理方式：

1. 在同一个 PowerShell/cmd 中执行 `where semgrep`、`where eslint`。
2. 确认命令可执行后重启 `scripts\stop-all.bat` 和 `scripts\start-all.bat`。
3. 如果工具安装到用户目录，确认用户级 PATH 已刷新。
4. 如果工具在特殊目录，可配置额外扫描路径后重启后端：

```powershell
$env:CODE_REVIEW_SAST_TOOL_PATHS="C:\Tools\semgrep;C:\Tools\pmd\bin;C:\Users\<you>\AppData\Roaming\npm"
scripts\stop-all.bat
scripts\start-all.bat
```

5. PowerShell 临时 PATH 可用：

```powershell
$env:PATH="$env:USERPROFILE\AppData\Roaming\Python\Python311\Scripts;$env:PATH"
```

### Tree-sitter Java grammar 不可用

重新安装并验证：

```powershell
.venv\Scripts\python.exe -m pip install -U "tree-sitter>=0.23,<1" "tree-sitter-language-pack>=0.13,<1" "networkx>=3.2,<4"
.venv\Scripts\python.exe -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke {}'); print('tree-sitter java ok')"
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -U "tree-sitter>=0.23,<1" "tree-sitter-language-pack>=0.13,<1" "networkx>=3.2,<4"
.venv/bin/python -c "from tree_sitter_language_pack import get_parser; p=get_parser('java'); p.parse(b'class Smoke {}'); print('tree-sitter java ok')"
```

### HTTPS 证书失败

优先配置系统证书或 `network.ca_bundle_path`。只在排障时临时关闭：

```json
{
  "network": {
    "verify_ssl": false,
    "use_system_trust_store": false,
    "ca_bundle_path": ""
  }
}
```

### Windows 控制台输出卡住

Windows 启动脚本默认把后端日志写入 `logs/backend.log`，避免控制台输出阻塞。需要调试时再开启：

```bat
set CODE_REVIEW_CONSOLE_LOG=true
scripts\start-all.bat
```

## 相关文档

- [开发同学使用培训 Wiki](docs/wiki/developer-training-guide.md)
- [系统能力说明](docs/architecture/2026-05-01-system-capabilities.md)
- [GitNexus 关联影响分析说明](docs/architecture/2026-05-01-gitnexus-impact-analysis.md)
- [借鉴 code-review-graph 的关联上下文优化方案](docs/plans/2026-05-05-code-review-graph-context-optimization.md)
- [专家 Agent 职责边界手册](docs/architecture/2026-04-19-expert-agent-boundary-handbook.md)
- [Review Quality Eval Baseline](docs/architecture/2026-05-01-review-quality-eval-baseline.md)
- [Repo Review Policy](docs/architecture/2026-05-01-repo-review-policy.md)
- [系统运行与代码地图](docs/architecture/code-wiki.md)
