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
