# multi-codereview-agent

基于 FastAPI + LangGraph-style Runtime + React/Ant Design 的多专家协同代码审核系统。

当前实现刻意参考了 `/Users/neochen/multi-agent-cli_v2/` 的前后端组织方式：

- 后端沿用 `api / services / repositories / runtime(orchestrator)` 分层
- 前端沿用 V1 的 `Header + Sider + page shell + module-card` 交互与布局
- 工作台延续“过程流 + 争议议题 + 最终报告 + 人工裁决”的控制台形式，只是把故障分析域替换成了代码审核域

## 当前能力

- 创建 `MR / Branch` 两种审核任务，并通过平台适配器归一化为 `ReviewSubject`
- 本地文件存储 review / event / finding / issue / message
- 内置专家注册表
- 审核启动后生成事件流、finding、争议议题、judge 摘要和人工 gate 状态
- `Review Workbench / History / Experts / Knowledge / Settings` 五个 V1 风格页面骨架
- SSE 事件回放接口
- LangGraph 风格 graph shim 与 orchestrator 子图节点
- 人工裁决 API 与工作台控制面板

## 目录

```text
backend/
  app/
    api/routes/
    domain/models/
    repositories/
    services/
frontend/
  src/
    components/common/
    components/review/
    pages/
docs/plans/
```

## 一键启动

```bash
bash scripts/start-all.sh
```

停止：

```bash
bash scripts/stop-all.sh
```

Windows:

```bat
scripts\start-all.bat
scripts\stop-all.bat
```

Windows 启动脚本会在启动前自动检查：

- `.\.venv\Scripts\python.exe` 是否可用
- `node` / `npm` 是否已安装并在 `PATH`
- `frontend\node_modules` 是否存在

其中前端依赖缺失时会自动执行 `npm install`。如果 Python 虚拟环境缺失，脚本会提示你先创建：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

## 统一配置

项目根目录提供一份用户可直接编辑的配置文件：

- [`config.json`](/Users/neochen/multi-codereview-agent/config.json)

这份文件是当前默认的全局配置入口，主要包含：

- 默认大模型配置
- Git / 代码仓 Access Token
- 代码仓 clone 地址、本地路径和目标分支
- 工具、skill、agent allowlist
- 默认辩论轮次和人工裁决开关
- 前后端默认端口

设置页 `/settings` 读写的也是这份 `config.json`。如果你想手工改配置，优先修改它，而不是去改散落的运行时文件。

当前 `config.json` 结构示例：

```json
{
  "server": {
    "backend_port": 8011,
    "frontend_port": 5174
  },
  "llm": {
    "default_provider": "dashscope-openai-compatible",
    "default_base_url": "https://coding.dashscope.aliyuncs.com/v1",
    "default_model": "kimi-k2.5",
    "default_api_key_env": "DASHSCOPE_API_KEY",
    "default_api_key": "your-api-key"
  },
  "git": {
    "repo_access_token": "your-git-token"
  },
  "code_repo": {
    "clone_url": "",
    "local_path": "",
    "default_branch": "main",
    "auto_sync": false
  },
  "runtime": {
    "default_target_branch": "main",
    "allow_llm_fallback": false,
    "allow_human_gate": true,
    "default_max_debate_rounds": 2
  },
  "allowlist": {
    "tools": ["local_diff", "schema_diff", "coverage_diff"],
    "skills": [
      "knowledge_search",
      "diff_inspector",
      "test_surface_locator",
      "dependency_surface_locator",
      "repo_context_search"
    ],
    "mcp": [],
    "agents": []
  }
}
```

## 后端单独启动

```bash
.venv/bin/pytest backend/tests -q
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8011
```

## 前端启动

```bash
cd frontend
npm install
npm run dev
```

默认前端运行在 `http://127.0.0.1:5174`，并通过 Vite 代理把 `/api/*` 指向 `http://localhost:8011`。

## 已验证

```bash
.venv/bin/pytest backend/tests -q
cd frontend && npm run build
```

当前结果：

- 后端测试：`17 passed`
- 前端构建：`vite build` 通过
