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
      "repo_context_search"
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
```

当前结果：

- 后端测试：`17 passed`
- 前端构建：`vite build` 通过
