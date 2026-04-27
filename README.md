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
- `extensions/skills` + `extensions/tools` 可插拔扩展机制
- 审核启动页可上传本次审核专属的详细设计文档（Markdown）
- 正确性与业务专家可通过 `design-consistency-check` 检查代码与详细设计是否一致
- 每个 MR 都会输出关联影响报告；GitNexus 图谱可用时使用图谱结果，不可用时使用 diff/路径规则降级生成测试范围建议

## 专家体系与职责边界

当前系统的目标不是“让更多专家同时发声”，而是让每一类问题都有明确主责专家，避免不同专家对同一类问题重复提意见。

下面这张表是面向研发团队的快速说明版：

| 专家 | 主要职责范围 | 典型能检出的问题 | 不负责什么 |
|---|---|---|---|
| `architecture_design` | 通用编码规范、Java 常规写法质量、工程一致性 | 命名不规范、魔法值、判空写法危险、异常处理不稳、日志缺关键信息、集合与泛型使用不规范、代码风格不一致 | 不判断业务规则对错，不判断 DDD 边界，不看 SQL/事务/索引，不看系统级性能问题 |
| `ddd_architecture` | DDD 边界、聚合职责、上下文边界、分层职责、依赖方向 | 应用服务堆业务逻辑、聚合边界被绕过、跨层直连、依赖方向错误、基础设施实现上浮、职责放错层 | 不评论命名、日志、魔法值等写法问题，不判断业务行为对错，不看 SQL/索引/性能细节 |
| `correctness_business` | 业务规则、状态流转、输入输出一致性、边界条件、承诺落地情况 | 状态流转错误、边界条件漏处理、返回值与副作用不一致、注释/TODO/接口承诺未实现、业务语义偏差 | 不主提命名和风格问题，不主提分层边界问题，不主提索引事务和系统级性能问题 |
| `maintainability_code_health` | 长期演化成本、复杂度、重复代码、结构清晰度 | 函数过长、分支太深、复制粘贴、职责揉在一起、抽象不清、后续维护成本高 | 不判断业务规则是否错误，不判断职责边界是否放错，不主提通用编码规范和专项性能/安全问题 |
| `database_analysis` | SQL 语义、事务边界、锁、Schema 演进、索引与约束 | 慢 SQL、事务边界不完整、锁范围不合理、Schema 兼容性问题、索引缺失、默认值/约束不当 | 不评论命名和可读性，不主提通用架构问题，不负责系统级稳定性结论 |
| `redis_analysis` | 缓存一致性、TTL、热点 key、Lua/事务、多 key 原子性 | 脏读、缓存穿透/击穿/雪崩、热点 key、TTL 不合理、Lua 破坏原子性 | 不看消息链路问题，不看前端体验，不主提通用性能结论 |
| `mq_analysis` | 消息顺序、幂等、ack、重试、补偿、死信、堆积治理 | 重复消费、顺序错乱、死信处理缺失、消息丢失风险、堆积治理缺口 | 不看 SQL 和索引，不评论前端问题，不主提通用代码风格问题 |
| `performance_reliability` | 热点路径、并发稳定性、资源效率、超时重试、失败恢复 | 批处理过大、锁竞争放大、同步阻塞、资源泄漏、超时重试缺口、局部故障放大成系统压力 | 不主提索引设计和 SQL 字段问题，不主提命名和风格问题 |
| `security_compliance` | 鉴权授权、输入校验、敏感数据、合规边界 | 权限绕过、输入校验绕过、敏感信息泄露、合规风险 | 不主提一般边界条件问题，不判断普通性能瓶颈，不评论可读性 |
| `test_verification` | 自动化测试覆盖、断言质量、回归保护、验证步骤 | 缺测试、断言太弱、风险路径无保护、缺集成测试、缺回归脚本或人工验证清单 | 不判断业务规则本身是否正确，不评论命名风格，不主提架构边界问题 |
| `change_impact_analysis` | 本次 MR 的影响范围、调用链影响、建议测试范围 | 识别直接变更文件、候选受影响文件、入口点、数据访问路径、必须回归的测试范围；GitNexus 不可用时说明降级边界 | 不裁决业务逻辑是否正确，不判断 SQL 性能/事务问题是否成立，不评价测试断言质量 |
| `frontend_accessibility` | 前端 a11y、关键交互可达性、基础渲染可达性 | a11y 回归、关键交互不可达、基础渲染或可达性退化 | 不看后端业务逻辑，不看数据库、中间件，不评论通用 Java 编码规范 |

理解这张表时，可以先按这个顺序判断问题归属：

1. 这是行为错误，还是职责边界错误，还是写法不规范。
2. 如果多个专家都能“看见”，最终只保留一个主责专家。
3. 结果收敛时优先遵循：行为正确性 > DDD 边界 > 编码规范 > 可维护性。

更完整的边界说明和归因规则见：

- [专家 Agent 职责边界手册](/Users/neochen/multi-codereview-agent/docs/architecture/2026-04-19-expert-agent-boundary-handbook.md)

## 结果页语义约定

为了让研发同学一眼看懂结果，当前结果页统一按下面三层表达：

| 结果层级 | 含义 | 会展示什么 |
|---|---|---|
| 审核发现 | 专家提出的原始 finding，允许保留待验证风险 | 专家、证据、代码位置、摘要 |
| 有效问题 | 已通过当前 issue 过滤规则，可以直接进入整改或人工裁决的问题 | 主责专家、参与专家、严重度、置信度、修复建议 |
| 被过滤的问题 | 仍保留在结果里，但不会升级为有效问题 | 过滤规则、过滤原因、原始 expert、文件和行号 |

当前会明确展示的几类过滤原因包括：

- `below_issue_priority_threshold`
- `below_priority_confidence_threshold`
- `conditional_conclusion`
- `removed_line_only`

其中：

- `conditional_conclusion` 表示这条结论还依赖额外条件或上下文确认，只能保留为 finding
- `removed_line_only` 表示问题只命中了待删除代码，属于无效问题

同时，所有进入“有效问题清单”的 issue 都会带一个唯一的 `primary_expert_id`，表示这条问题最终归属于哪个主责专家。

## GitNexus 关联影响分析

### 这个能力解决什么问题

普通 code review 更关注“这几行代码有没有问题”。关联影响分析更关注另一件事：

```text
这次 MR 改了这些文件以后，可能波及哪些入口、模块、调用链，以及应该回归哪些测试？
```

本项目为此新增了 `change_impact_analysis` 专家。它不会替代正确性、数据库、性能或测试专家报缺陷，而是为每个 MR 输出一份 `impact_report`，帮助研发同学快速确认影响范围和测试范围。

当前报告会展示在结果页的“关联影响报告”区域，也会进入后端 `ReviewReport.impact_report` 字段和 artifact 快照。

### GitNexus 在这里怎么用

[GitNexus](https://github.com/abhigyanpatwari/GitNexus) 是代码图谱/影响分析工具。它的定位是提前把代码仓建成图谱，MR 审核时再基于图谱回答“改动会影响哪些调用方、被调用方和测试范围”。

本项目把 GitNexus 接成两段，并且运行顺序是固定的：

| 阶段 | 谁负责 | 什么时候执行 | 输出 |
|---|---|---|---|
| 后台建图 | `GitNexusIndexScheduler` | 定时对配置的本地代码仓执行 | `.gitnexus/` 图谱和 `index_status.json` |
| MR 影响报告 | `GitNexusImpactService` + `change_impact_analysis` 专家 | 每个 MR 请求都执行 | 基于图谱的 `impact_report` |

设计原则是：后台先把配置仓库建好图谱；每次 MR 关联影响分析必须先尝试使用这份图谱。只有图谱未就绪、MCP 调用失败或本地仓未配置时，才会退回到 diff/路径规则，并在 `limitations` 里写明原因。

### 默认行为：先保证每个 MR 有报告

如果 GitNexus 图谱不可用，系统会自动降级：

- 从 `changed_files` 和 `unified_diff` 识别直接变更文件
- 从 diff 中提取新增/修改的类、方法、函数等符号
- 按路径规则识别 Controller/API、Repository/Mapper/DAO、SQL/Migration、Job/Consumer 等入口或数据访问面
- 推导候选测试文件和建议测试范围
- 在报告里明确写出 `graph_status = fallback`

这份降级报告不声称自己有完整调用链，只用于给研发同学一个最低限度的影响范围和测试范围提示。

### 开启 GitNexus 后台建图

后台建图默认关闭。需要接入 GitNexus 时，先确保公共机器上可以执行：

```bash
npx gitnexus analyze
```

然后在启动后端前设置环境变量：

```bash
export GITNEXUS_INDEX_ENABLED=true
export GITNEXUS_INDEX_INTERVAL_SECONDS=3600
export GITNEXUS_INDEX_TIMEOUT_SECONDS=900
```

同时在 `config.json` 或设置页里配置本地代码仓路径：

```json
{
  "code_repo": {
    "local_path": "/data/repos/your-project",
    "default_branch": "main"
  }
}
```

后端启动后，`GitNexusIndexScheduler` 会按间隔在该仓库目录执行：

```bash
npx gitnexus analyze
```

建图状态会写到：

```text
backend/app/storage/gitnexus/index_status.json
```

建图成功时，状态文件会包含：

```json
{
  "state": "ready",
  "repo_path": "/data/repos/your-project",
  "repo_name": "your-project",
  "indexed_at": "2026-04-27T00:00:00+00:00",
  "commit": "当前仓库 HEAD commit",
  "graph_dir": "/data/repos/your-project/.gitnexus",
  "graph_dir_exists": true
}
```

如果没有配置 `code_repo.local_path`、机器上没有 `npx`、或者 GitNexus 执行失败，调度器只记录 `skipped / failed` 状态，不影响 MR 审核。

### MR 审核时如何使用 GitNexus 结果

审核执行时，`GitNexusImpactService` 会按下面的顺序执行：

```text
读取 review.subject.metadata.gitnexus_impact_report
  -> 如果已有外部预计算结果，直接使用
否则读取 backend/app/storage/gitnexus/index_status.json
  -> state=ready 且本地仓存在 .gitnexus/，继续
  -> 否则 fallback
使用 GitNexus MCP 查询图谱
  -> detect_changes：识别整体变更影响
  -> impact：对 diff 中提取出的变更类/方法/函数做符号级影响分析
标准化为 ReviewReport.impact_report
  -> graph_status=ready
MCP 调用失败
  -> graph_status=fallback，并在 limitations 写明失败原因
```

也就是说，每次 MR 请求都会先尝试使用后台建好的 GitNexus 图谱。不是等专家自由发挥，也不是只看文件路径。

当前代码默认使用 GitNexus MCP stdio server：

```bash
npx -y gitnexus@latest mcp
```

如需改成本地固定命令，可以设置：

```bash
export GITNEXUS_MCP_COMMAND="npx -y gitnexus@latest mcp"
export GITNEXUS_MCP_TIMEOUT_SECONDS=45
```

### 如何做一条真实链路 smoke test

仓库里带了一条可直接运行的 smoke 脚本：

- [smoke_gitnexus_impact_demo.py](/Users/neochen/multi-codereview-agent/scripts/smoke_gitnexus_impact_demo.py)

这条脚本不会只测单个 helper，而是会走一条接近真实的后端链路：

```text
ReviewService.create_review
-> ReviewService.build_report
-> GitNexusImpactService.analyze
-> ReviewReport.impact_report
```

脚本会自动：

1. 创建一个最小可运行的本地 Java git 仓
2. 伪造一条真实感较强的 MR diff
3. 写入 `index_status.json`，模拟“后台图谱已经 ready”
4. 跑两组对照用例：
   - 图谱 ready，GitNexus 查询成功
   - 图谱 ready，但 GitNexus MCP 查询失败

运行方式：

```bash
PYTHONPATH=backend .venv/bin/python scripts/smoke_gitnexus_impact_demo.py
```

预期你会看到两组 JSON 结果：

- `graph_ready_success`
  - `graph_status = ready`
  - 有 `impact_paths`
  - `impacted_files` 中能看到图谱识别出的上游/下游文件
  - `recommended_test_scope` 中能看到图谱给出的测试建议
- `graph_ready_but_mcp_failed`
  - `graph_status = fallback`
  - `limitations` 第一条会明确写出 GitNexus MCP 调用失败原因

这条 smoke test 的价值是：

- 验证“后台图谱 ready 时，MR 是否优先走 GitNexus”
- 验证“查询失败时，系统是否明确降级而不是静默退回”
- 验证结果页和 `ReviewReport.impact_report` 使用的是同一份标准化结构

注意：

- 这条脚本默认不依赖真实外网 GitNexus 服务，而是用 fake client 模拟图谱成功/失败结果，适合本地和 CI 快速回归
- 如果要验证真实 `npx gitnexus analyze` 和真实 MCP，请在能访问 npm/GitHub 的机器上再跑一轮完整环境测试

审核流程里有两个入口会消费这份分析结果：

1. `ReviewRunner`
   - 在任务收尾阶段调用 `GitNexusImpactService`
   - 把报告写入 `review.subject.metadata.impact_report`
   - artifact 快照也会带上 `impact_report`
2. `change_impact_analysis` 专家
   - 通过运行时工具 `gitnexus_impact_analysis` 获取同一份报告
   - 专家只围绕影响范围和测试范围输出，不和其他专家重复报缺陷

如果后续在平台侧预先调用 GitNexus MCP，并把结果随 MR 一起传入，只需要把 GitNexus 输出标准化写入：

```text
review.subject.metadata.gitnexus_impact_report
```

`GitNexusImpactService` 会优先读取这份预计算结果。前端、报告模型和专家工具都不需要再改。

### 配置白名单

`gitnexus_impact_analysis` 是系统必需的内置运行时工具。为了兼容旧配置，后端会自动把它补进 `runtime_tool_allowlist`。

如果手工维护 `config.json`，建议也显式加入：

```json
{
  "allowlist": {
    "runtime_tools": [
      "knowledge_search",
      "diff_inspector",
      "test_surface_locator",
      "dependency_surface_locator",
      "repo_context_search",
      "gitnexus_impact_analysis"
    ]
  }
}
```

### 结果页怎么看

结果页“关联影响报告”主要看这几项：

| 字段 | 含义 |
|---|---|
| `graph_status` | `ready` 表示使用图谱；`fallback` 表示降级分析 |
| `risk_level` | 根据变更文件、入口类型、数据访问面、符号数量估算的影响风险 |
| `changed_files` | 本次 MR 直接修改的文件 |
| `impacted_files` | 直接变更文件和候选受影响文件，包括候选测试文件 |
| `impacted_modules` | 按路径推导的受影响模块 |
| `recommended_test_scope` | 建议研发回归的测试范围 |
| `must_run_tests` | 建议执行的测试命令或测试类型 |
| `limitations` | 图谱状态和降级分析边界说明 |

判断口径很简单：

- `graph_status = ready` 时，可以把报告当成图谱增强后的影响分析来看
- `graph_status = fallback` 时，说明本次没有成功使用图谱，只能当成保守提示，需要研发结合业务场景确认
- 任何情况下，它都不是“缺陷清单”，缺陷是否成立仍看有效问题清单

## 审核状态节点

当前系统里和“状态”相关的概念有两层，建议分开理解：

- `LangGraph 编排节点`
  - 表示审核流程在 orchestrator 里的执行步骤
- `ReviewTask.status / phase / human_review_status`
  - 表示一条审核任务当前对外展示的生命周期状态

### 1. LangGraph 编排节点

状态图定义在：

- [graph.py](/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/graph.py)

共享状态结构定义在：

- [state.py](/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/state.py)

当前图中的节点顺序如下：

1. `ingest_subject`
   - 装载审核输入，初始化图状态
   - 对应 `phase = ingest`
2. `slice_change`
   - 把 `changed_files` 切成最小变更片段 `change_slices`
   - 对应 `phase = slice_change`
3. `expand_context`
   - 从文件名和变更范围提取高层风险提示 `risk_hints`
   - 对应 `phase = expand_context`
4. `route_experts`
   - 基于风险提示补充必要专家
   - 对应 `phase = route_experts`
5. `run_independent_reviews`
   - 表示进入专家独立审查阶段
   - 对应 `phase = expert_review`
6. `detect_conflicts`
   - 对 findings 做冲突检测、同类聚合、问题归并
   - 对应 `phase = detect_conflicts`
7. `run_targeted_debate`
   - 针对多专家冲突或低置信问题组织定向辩论
   - 对应 `phase = run_targeted_debate`
8. `evidence_verification`
   - 根据 issue 类型选择 verifier，对证据做本地核验
   - 对应 `phase = evidence_verification`
9. `judge_and_merge`
   - 基于证据强度、严重级别、人工门禁要求做最终裁决收敛
   - 对应 `phase = judge_and_merge`
10. `human_gate`
    - 判断是否需要人工裁决
    - 对应 `phase = human_gate`
11. `publish_report`
    - 生成报告摘要与结果产物摘要
    - 对应 `phase = publish_report`
12. `persist_feedback`
    - 为反馈学习与后续治理保留统一出口
    - 对应 `phase = persist_feedback`

说明：

- 这条图主要负责“findings -> conflicts -> issues -> human gate -> report”这条后半段编排链路
- 专家实际跑 LLM、拼装上下文、调用运行时 tools/skills，主要仍由 `ReviewRunner` 主控

### 2. ReviewTask 生命周期状态

任务模型定义在：

- [review.py](/Users/neochen/multi-codereview-agent/backend/app/domain/models/review.py)

#### `status`

`status` 表示任务对外展示的主状态，当前主要有：

- `pending`
  - 任务已创建，但还没开始执行
- `running`
  - 审核执行中
- `waiting_human`
  - 自动审核已收敛完成，但仍有 issue 需要人工裁决
- `completed`
  - 审核完全结束，不再等待人工处理
- `failed`
  - 审核失败
- `closed`
  - 被用户强制结束

#### `phase`

`phase` 表示任务当前所处的内部阶段，既可能来自主执行链，也可能来自图节点。当前常见值包括：

- `pending`
- `queued`
- `intake`
- `coordination`
- `expert_review`
- `human_gate`
- `completed`
- `failed`
- `ingest`
- `slice_change`
- `expand_context`
- `route_experts`
- `detect_conflicts`
- `run_targeted_debate`
- `evidence_verification`
- `judge_and_merge`
- `publish_report`
- `persist_feedback`

理解上可以把它看成“比 status 更细的执行阶段”。

#### `human_review_status`

`human_review_status` 表示人工裁决子流程状态，当前主要有：

- `not_required`
  - 当前任务不需要人工裁决
- `requested`
  - 已进入人工裁决队列
- `approved`
  - 人工已确认问题成立
- `rejected`
  - 人工已判定为误报或暂不采纳

### 3. 一次完整审核的大致状态流转

典型流转顺序可以概括为：

```text
pending
-> queued
-> running / expert_review
-> ingest
-> slice_change
-> expand_context
-> route_experts
-> expert_review
-> detect_conflicts
-> run_targeted_debate
-> evidence_verification
-> judge_and_merge
-> human_gate
-> publish_report
-> persist_feedback
-> completed
```

如果存在高风险议题需要人工确认，则会变成：

```text
pending
-> queued
-> running
-> ...
-> judge_and_merge
-> human_gate
-> waiting_human
-> human reviewer approve / reject
-> completed
```

如果执行中断或失败，则可能进入：

```text
running -> failed
running -> closed
```

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

Windows 启动脚本还会检查后端依赖是否完整，尤其会校验 `httpx>=0.27`。如果依赖缺失或版本过旧，会自动执行：

```bat
.venv\Scripts\python.exe -m pip install -e .
```

这可以直接修复类似 `unexpected keyword argument verify` 这种由旧版 `httpx` 引起的问题。

## 统一配置

项目根目录提供一份用户可直接编辑的配置文件：

- [`config.json`](/Users/neochen/multi-codereview-agent/config.json)

这份文件是当前默认的全局配置入口，主要包含：

- 默认大模型配置
- Git / 代码仓 Access Token
- HTTPS 证书校验、系统证书库和 CA Bundle 路径
- 代码仓 clone 地址、本地路径和目标分支
- 通用 tools、运行时 tools、agent allowlist
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
  "network": {
    "verify_ssl": true,
    "use_system_trust_store": true,
    "ca_bundle_path": ""
  },
  "allowlist": {
    "tools": ["local_diff", "schema_diff", "coverage_diff"],
    "runtime_tools": [
      "knowledge_search",
      "diff_inspector",
      "test_surface_locator",
      "dependency_surface_locator",
      "repo_context_search",
      "gitnexus_impact_analysis"
    ],
    "mcp": [],
    "agents": []
  }
}
```

## Skill + Tool 插件扩展

当前系统的专家扩展能力已经拆成两层：

- `skill`
  - 上层能力包
  - 使用目录式 `SKILL.md`
  - 负责定义“什么时候触发、依赖哪些运行时工具、要求专家如何输出”
- `tool`
  - 下层执行插件
  - 默认使用 Python 实现
  - 负责真正执行检索、结构化提取和一致性比对

### 目录约定

```text
extensions/
  skills/
    design-consistency-check/
      SKILL.md
      metadata.json
  tools/
    design_spec_alignment/
      tool.json
      run.py
      README.md
```

### skill 如何绑定到专家

skill 绑定优先从 extension 目录读取，不需要再改内置专家源码。

绑定入口：

- `extensions/skills/<skill>/metadata.json`

关键字段：

- `bound_experts`

例如：

- [extensions/skills/design-consistency-check/metadata.json](/Users/neochen/multi-codereview-agent/extensions/skills/design-consistency-check/metadata.json)

会把 `design-consistency-check` 绑定到：

- `correctness_business`

### skill 什么时候会被激活

skill 不是在专家启动时全量加载，而是由 runtime 按规则激活。

当前激活规则大致是：

```text
expert 已绑定该 skill
AND 当前 expert 在 applicable_experts 内
AND 当前分析模式允许
AND required_doc_types 满足
AND changed_files 命中 activation_hints
AND 必要上下文存在
=> 激活 skill
```

这意味着：

- 是否加载 skill，不由 LLM 主观决定
- 而是由后端根据 review 上下文稳定判断

### tool 如何实现

第一版 extension tool 统一使用 Python，约定：

- `tool.json`
  - 描述 tool id、名称、入口脚本、超时、输入输出 schema
- `run.py`
  - 从 stdin 读取 JSON
  - 向 stdout 输出 JSON
  - 出错时通过 stderr + 非 0 exit code 返回

这样新增 tool 时，不需要改主审核流程源码。

## Diff 上下文策略

系统内部现在区分了三种 diff 视角，不能混用：

- 前端 `Diff Preview`
  - 直接展示完整的 `ReviewSubject.unified_diff`
  - 用于人工浏览和核对原始变更
- 主 Agent prompt
  - 不再直接塞一整段裸 `unified_diff[:12000]`
  - 改为：
    - 主要业务文件完整 diff
    - 其他变更文件摘要
    - 候选 hunk
- 专家 Agent prompt
  - 不再只看 `target_hunk` 和 `code_excerpt`
  - 改为：
    - 目标文件完整 diff
    - 其他变更文件摘要
    - 目标 hunk
    - 代码仓上下文
    - 运行时工具结果

这样做的原因很直接：

- 前端预览需要完整原始 diff
- 主 Agent 需要足够多的全局信号做派工，但不能被超长 diff 直接冲垮 token
- 专家 Agent 至少必须看到“目标文件完整 diff”，否则会把局部 excerpt 误判成“diff 不完整”

当前约束是：

- 专家审查时，目标文件完整 diff 是必带上下文
- 其他文件只做摘要补充，不默认把整个 MR 全量灌给每个专家
- 如果后续要调整 prompt，优先改“文件级完整 diff + 其他文件摘要”的结构，不要退回到“只给 excerpt”或“直接截断整份 unified_diff”

## 轻量模式上下文窗口与智能压缩

轻量模式现在支持单独配置 LLM 上下文预算，目的不是一刀切缩短 prompt，而是在不明显影响检视质量的前提下，尽量避免触发模型输入上限。

### 可配置项

设置页和运行时配置里新增了两个字段：

- `light_llm_max_input_tokens`
  - 轻量模式单次请求的 token 预算
- `light_llm_max_prompt_chars`
  - 轻量模式单次请求的字符级兜底预算

其中：

- `token` 预算控制大方向，防止模型输入超过上限
- `char` 预算作为二次兜底，解决中英文混合、diff、日志、SQL 等场景下 token 估算与真实计数存在偏差的问题

### 智能压缩怎么做

轻量模式不是直接把 prompt 从尾部截断，而是分三步处理：

1. 先按固定区块拆分 prompt
2. 再按专家类型和当前 hunk 锚点做定向提纯
3. 再按区块重要性做压缩
4. 最后用 token 和字符双预算做严格兜底

系统会优先识别这些区块：

- `规范提要`
- `已绑定参考文档`
- `规则遍历结果`
- `目标 hunk`
- `目标文件完整 diff`
- `关键源码上下文`
- `当前代码片段`
- `JSON 字段要求`

### 压缩优先级

轻量模式的核心原则是“保规则、保问题、保代码、保上下文，优先压外围材料”。

高优先保留：

- `目标文件完整 diff`
- `关键源码上下文`
- `当前代码片段`
- `目标 hunk`
- `规则遍历结果`
- `JSON 字段要求`

中优先保留：

- `语言通用规范提示`
- `代码仓上下文`
- `必查项`

优先压缩：

- `已激活技能`
- `本次审核绑定的详细设计文档`
- `运行时工具调用结果`
- `其他变更文件摘要`

### 不同专家使用不同压缩优先级

轻量模式现在不是所有专家共用一套完全相同的保留策略，而是会根据当前 `expert_id` 做差异化保留：

- 安全专家
  - 更保留 SQL、鉴权、外部输入、请求头、租户、密钥、敏感字段相关上下文
- 性能专家
  - 更保留 循环、集合、批处理、查询、缓存、并发、线程池、分页相关上下文
- 数据库专家
  - 更保留 SQL、Mapper、JPA/MyBatis、索引、事务、分页、Repository 相关上下文
- DDD/架构专家
  - 更保留 聚合、实体、领域服务、应用服务、边界、依赖方向、详细设计文档相关上下文
- 测试专家
  - 更保留 测试代码、断言、mock、异常分支、边界条件相关上下文

也就是说，轻量模式下虽然都在压缩，但不同专家保留下来的“核心证据”并不一样。

### 基于当前 hunk 的精准上下文裁剪

除了固定区块权重，轻量模式还会从当前审核目标里提取一组“锚点”：

- 目标 hunk 中的类名、方法名、关键标识符
- 当前代码片段中的核心调用和字段名
- 目标文件 diff 里的关键 token

然后在这些大块上下文里优先保留：

- 同方法
- 同类
- 同调用链邻近代码
- 同实体 / DTO / Repository / Mapper
- 与当前 hunk 命中的 SQL、事务、鉴权、缓存、查询、批处理等相关的代码

这样做的目标是：

- 不是平均裁每个区块
- 而是优先保留“和当前 hunk 最相关的那部分代码”

例如：

- 当前 hunk 命中了 `processOrder(...)`
  - 会优先保留 `processOrder` 同方法和邻近调用链上下文
- 当前 hunk 命中了 `jwtToken / Authorization / select ... from users`
  - 安全专家会优先保留请求头读取、鉴权校验、相关 SQL 和用户查询上下文

### 文档类内容如何压缩

规范文档、绑定参考文档、详细设计文档这类长文本，不会额外调用一个 LLM 先做摘要，而是本地规则式提炼：

- 保留开头的说明和上下文
- 优先保留包含“必须、禁止、应当、风险、错误、性能、安全、SQL、事务、DDD”等关键词的行
- 优先保留标题、编号条款和关键 bullet
- 压缩大量重复解释性内容

这样做的原因是：

- 不引入额外模型调用
- 不额外消耗 token
- 输出更稳定、可控
- 避免“摘要模型理解偏了”反过来伤害检视质量

### 双预算兜底机制

轻量模式的 prompt 预算控制是双层的：

1. 先估算系统 prompt 和用户 prompt 的总 token 数
2. 如果超出 `light_llm_max_input_tokens`，先做结构化智能压缩
3. 如果压缩后字符数仍超过 `light_llm_max_prompt_chars`，再做字符级兜底裁剪
4. 最终保证请求不会超过当前轻量模式配置的预算

这意味着：

- 先尽量“聪明地少丢信息”
- 再“硬性保证不超限”

### 当前边界

当前这套机制只作用于轻量模式，不影响标准模式。

如果后续继续优化，优先方向应是：

- 按专家类型做差异化压缩优先级
- 按方法级调用链和命中规则进一步收缩上下文

不要退回到：

- 直接按字符粗暴截断整段 prompt
- 只保留 excerpt，不带目标文件完整 diff
- 为了省 token 把规则规范、问题信息和关键源码上下文一起删掉

### 详细设计一致性检查示例

当前首个完整落地的 skill 是：

- `design-consistency-check`

它会在正确性与业务专家执行前，自动展开：

- `diff_inspector`
- `repo_context_search`
- `design_spec_alignment`

其中 `design_spec_alignment` 会先从本次审核上传的详细设计 Markdown 中提取：

- API 定义
- 入参字段定义
- 出参字段定义
- 表结构定义
- 业务逻辑时序
- 性能要求
- 安全要求

再结合 MR diff 和源码仓上下文，输出：

- `design_alignment_status`
- `matched_implementation_points`
- `missing_implementation_points`
- `extra_implementation_points`
- `conflicting_implementation_points`

## Issue 置信度计算模型

当前系统里，`finding` 和 `issue` 的置信度不是同一个概念：

- `finding.confidence`
  - 表示单个专家对单条结论的把握程度
  - 由专家基线分和专家 LLM 输出共同决定
- `issue.confidence`
  - 表示多个 findings 收敛为正式议题后的整体置信度
  - 由 orchestrator 在 issue 聚合阶段统一计算

### 计算入口

核心逻辑在：

- [detect_conflicts.py](/Users/neochen/multi-codereview-agent/backend/app/services/orchestrator/nodes/detect_conflicts.py)

系统会先按 `file_path + 行号窗口` 聚合 findings，再对每个 issue 候选计算一组新的 issue 级置信度。

### issue 置信度的组成

当前使用的是“加权基础分 + 修正项”模型，而不是简单平均。

公式可以理解为：

```text
issue_confidence
= base_weighted_confidence
+ consensus_bonus
+ evidence_bonus
+ verification_bonus
- hypothesis_penalty
```

其中：

- `base_weighted_confidence`
  - 对当前 issue 下的 findings 按类型加权平均
  - 权重如下：
    - `direct_defect`: `1.00`
    - `test_gap`: `0.80`
    - `risk_hypothesis`: `0.65`
    - `design_concern`: `0.55`
- `consensus_bonus`
  - 多个不同专家命中同一个 issue 时增加
  - 目前最多加到 `0.08`
- `evidence_bonus`
  - 根据证据条数、跨文件证据、上下文文件、命中规则、违反规范等信号增加
  - 目前最多加到 `0.06`
- `verification_bonus`
  - 预留给后续 verifier / 工具核验结果的正向修正
  - 当前 issue 聚合阶段先记为 `0.0`
- `hypothesis_penalty`
  - 如果一个 issue 全部由 `risk_hypothesis` 组成、仍需要验证、缺少直接证据，会做降权
  - 目前最多扣到 `0.12`

最终结果会被裁剪到 `0.01 ~ 0.99`，并四舍五入到两位小数。

### 为什么这样设计

这套模型主要是为了避免“issue 和 finding 内容几乎一样，但 issue 只是简单平均”的问题。

它会显式体现这些事实：

- `direct_defect` 比纯推测类 finding 更可信
- 多专家达成一致时，issue 应该比单专家更有把握
- 证据越充分、命中规范越多，issue 应该越稳定
- 单专家、纯提示、纯 hypothesis 的 issue 不应该被轻易抬得太高

### 输出给前端的解释字段

每个 issue 现在除了 `confidence`，还会附带：

- `confidence_breakdown`

用于解释：

- 基础加权分
- 一致性加分
- 证据加分
- 验证加分
- hypothesis 扣分

这能帮助前端详情页或后续排查直接回答：

- 为什么这个 issue 是 `0.95`
- 它是因为多专家一致，还是因为 direct defect + 证据更强

### 与 issue 升级阈值的关系

issue 是否能进入“正式问题清单”，仍然受设置页中的 P 级阈值控制：

- `issue_confidence_threshold_p0`
- `issue_confidence_threshold_p1`
- `issue_confidence_threshold_p2`
- `issue_confidence_threshold_p3`

也就是说：

- 先算出 issue 级置信度
- 再按 `P0 / P1 / P2 / P3` 阈值判断是否升级为正式问题
- 没达到阈值的，保留在 finding 或“被阈值过滤的发现清单”

### 审核启动页中的详细设计文档

详细设计文档现在直接在审核启动页上传，不需要先进入知识库。

推荐流程：

1. 在审核工作台填写 Git PR / MR / Commit 链接
2. 直接上传本次审核对应的详细设计 `.md`
3. 选择专家
4. 启动审核

这些文档会保存到本次 review 的：

- `review.subject.metadata.design_docs`

它们只参与本次审核，不会自动进入长期知识库。

### 后续如何扩展

如果后续开发者要新增一个专家能力，只需要改 `extensions/`：

1. 新增 `extensions/skills/<skill>/SKILL.md`
2. 新增 `extensions/skills/<skill>/metadata.json`
3. 在 `metadata.json` 里声明 `bound_experts`
4. 如需底层执行能力，再新增 `extensions/tools/<tool>/tool.json` 和 `run.py`

不需要再修改内置专家源码，也不需要改主审核流程。

Windows 下如果访问 GitHub、DashScope 等 HTTPS 链接出现证书校验失败，优先在 `config.json` 或设置页里调整这 3 个字段：

- `network.verify_ssl`
- `network.use_system_trust_store`
- `network.ca_bundle_path`

推荐顺序是：

- 先保持 `verify_ssl=true`
- 再开启 `use_system_trust_store=true`
- 如果是企业内网证书，再填写 `ca_bundle_path`
- 只有排障时才临时把 `verify_ssl` 设为 `false`

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
PYTHONPATH=backend .venv/bin/python scripts/smoke_gitnexus_impact_demo.py
.venv/bin/python -m pytest backend/tests/services/test_gitnexus_impact_service.py backend/tests/services/test_gitnexus_index_scheduler.py -q
```

当前结果：

- 后端测试：`17 passed`
- 前端构建：`vite build` 通过
- GitNexus 关联影响 smoke：可稳定复现 `graph_status = ready` 和 `graph_status = fallback` 两条路径
- GitNexus 相关聚焦测试：`5 passed`
