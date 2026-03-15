# Plugin Skill + Tool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为代码审核系统新增可插拔的 `skill + tool` 扩展机制，并以“详细设计文档一致性检查”为首个落地示例。

**Architecture:** 采用双层扩展架构：`skill` 负责声明何时触发、依赖哪些 tools、如何约束专家输出；`tool` 负责实际执行检索、提取、比对和结构化证据。详细设计文档在审核启动页上传并绑定到本次 review，由 `correctness_business` 专家在运行时按需加载 `design-consistency-check` skill，并调用 `design_spec_alignment` tool 完成设计一致性检查。

**Tech Stack:** FastAPI, Pydantic, React, TypeScript, Ant Design, 本地文件仓储, Python 子进程工具插件

---

### Task 1: 定义 review 级设计文档绑定数据模型

**Files:**
- Modify: `backend/app/domain/models/review.py`
- Modify: `backend/app/api/routes/reviews.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `frontend/src/services/api.ts`

**Step 1: 为 review subject metadata 增加 design_docs 约定**

在 `ReviewSubject.metadata` 中约定保存本次审核绑定的设计文档：
- `doc_id`
- `title`
- `filename`
- `content`
- `doc_type=design_spec`

**Step 2: 扩展创建审核接口请求体**

让创建审核接口支持提交 `design_docs` 数组，并在后端写入 `review.subject.metadata.design_docs`。

**Step 3: 前端 API 类型同步**

在 `frontend/src/services/api.ts` 增加 `ReviewDesignDocumentInput` 之类的类型，并在 `reviewApi.create()` 请求载荷里透传。

**Step 4: 跑接口回归测试**

Run: `.venv/bin/pytest backend/tests/api -q`
Expected: PASS

### Task 2: 重构审核启动页输入

**Files:**
- Modify: `frontend/src/components/review/ReviewOverviewPanel.tsx`
- Modify: `frontend/src/pages/ReviewWorkbench/index.tsx`
- Modify: `frontend/src/styles/global.css`

**Step 1: 移除无关启动字段**

从审核启动页移除：
- `access_token`
- `repo_id`
- `project_id`

只保留：
- 代码链接
- 源分支
- 目标分支
- 分析模式
- 专家选择
- 详细设计文档上传

**Step 2: 增加设计文档上传入口**

在启动页增加 `.md` 多文件上传区，只允许本次审核上下文使用。

**Step 3: 将上传文档回填到创建请求**

让启动页把上传后的文档内容与元数据一起放入 `reviewApi.create()`。

**Step 4: 运行前端构建验证**

Run: `npm run build`
Expected: PASS

### Task 3: 设计 skill 插件目录与加载协议

**Files:**
- Create: `backend/app/domain/models/review_skill.py`
- Create: `backend/app/services/review_skill_registry.py`
- Create: `backend/app/services/review_skill_loader.py`
- Create: `extensions/skills/design-consistency-check/SKILL.md`
- Create: `extensions/skills/design-consistency-check/metadata.json`

**Step 1: 定义 skill 数据模型**

数据模型至少包含：
- `skill_id`
- `description`
- `applicable_experts`
- `required_tools`
- `required_doc_types`
- `activation_hints`
- `required_context`
- `allowed_modes`
- `output_contract`
- `prompt_body`

**Step 2: 实现 skill 目录扫描**

扫描：
- `extensions/skills/*/SKILL.md`
- `extensions/skills/*/metadata.json`

并将两者合并成统一 `ReviewSkillProfile`。

**Step 3: 约定 SKILL.md 读取规则**

运行时读取 `SKILL.md` 正文作为附加规则 prompt，不再把 skill 当成工具。

**Step 4: 为 design-consistency-check 创建首个 skill**

写出标准 `SKILL.md` 和 `metadata.json`。

**Step 5: 补 skill registry 单测**

Run: `.venv/bin/pytest backend/tests/services -q`
Expected: PASS

### Task 4: 设计 tool 插件目录与执行协议

**Files:**
- Create: `backend/app/domain/models/review_tool_plugin.py`
- Modify: `backend/app/services/tool_gateway.py`
- Create: `backend/app/services/tool_plugin_loader.py`
- Create: `extensions/tools/design_spec_alignment/tool.json`
- Create: `extensions/tools/design_spec_alignment/run.py`

**Step 1: 定义 tool 插件元数据模型**

至少包含：
- `tool_id`
- `runtime`
- `entry`
- `timeout_seconds`
- `allowed_experts`
- `input_schema`
- `output_schema`

**Step 2: 实现插件扫描**

扫描：
- `extensions/tools/*/tool.json`

**Step 3: 实现 Python tool 子进程执行协议**

使用：
- stdin 传 JSON
- stdout 回 JSON
- stderr + 非 0 exit code 表示失败

**Step 4: 保持现有内建 tools 兼容**

`knowledge_search`、`repo_context_search` 等内建 tools 继续可用；插件 tool 作为新增能力层。

**Step 5: 补 tool 执行单测**

Run: `.venv/bin/pytest backend/tests/services -q`
Expected: PASS

### Task 5: 实现 skill 激活规则

**Files:**
- Create: `backend/app/services/review_skill_activation_service.py`
- Modify: `backend/app/services/review_runner.py`

**Step 1: 定义 skill 激活公式**

运行时按下述规则判断是否激活：

```text
expert_has_skill_binding
AND required_doc_types_matched
AND changed_files_match
AND required_context_available
AND analysis_mode_allowed
```

**Step 2: 在专家执行前解析本轮激活 skills**

在 `ReviewRunner._run_expert_from_command()` 前半段，先计算：
- 本轮命中的 skills
- skill 需要调用的 tools

**Step 3: 把激活的 skill 信息写入消息 metadata**

便于前端过程页和调试日志展示。

**Step 4: 补 skill activation 单测**

Run: `.venv/bin/pytest backend/tests/services -q`
Expected: PASS

### Task 6: 实现 design_spec_alignment tool

**Files:**
- Create: `extensions/tools/design_spec_alignment/run.py`
- Create: `extensions/tools/design_spec_alignment/README.md`
- Modify: `backend/app/services/tool_gateway.py`

**Step 1: 实现设计文档结构化提取**

优先提取：
- `api_definitions`
- `request_fields`
- `response_fields`
- `table_definitions`
- `business_sequences`
- `performance_requirements`
- `security_requirements`

**Step 2: 实现一致性比对**

输入：
- 本次绑定设计文档
- diff
- changed_files
- repo context

输出：
- `matched_implementation_points`
- `missing_implementation_points`
- `extra_implementation_points`
- `conflicting_implementation_points`
- `uncertain_points`

**Step 3: 把 tool 结果接入统一 runtime tool 输出**

工具调用成功后，和其他 tool 一样进入 expert 上下文和消息流。

**Step 4: 补 tool 单测**

Run: `.venv/bin/pytest backend/tests/services -q`
Expected: PASS

### Task 7: 给正确性与业务专家接入 design-consistency-check

**Files:**
- Modify: `backend/app/builtin_experts/correctness_business/expert.yaml`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/domain/models/finding.py`
- Modify: `frontend/src/services/api.ts`

**Step 1: 为正确性专家增加 skill 绑定**

新增：
- `design-consistency-check`

**Step 2: 扩展 finding 结构**

新增字段：
- `design_alignment_status`
- `matched_design_points`
- `missing_design_points`
- `extra_implementation_points`
- `design_conflicts`
- `design_doc_titles`

**Step 3: 在 expert prompt 中注入 skill 和 tool 结果**

让 `correctness_business` 能明确依据：
- 设计文档
- design_spec_alignment 结果
- repo context
- diff

输出正式结论。

**Step 4: 补解析和稳定化测试**

Run: `.venv/bin/pytest backend/tests/services -q`
Expected: PASS

### Task 8: 结果页与过程页展示设计一致性结果

**Files:**
- Modify: `frontend/src/components/review/ReviewDialogueStream.tsx`
- Modify: `frontend/src/components/review/CodeReviewConclusionPanel.tsx`
- Modify: `frontend/src/styles/global.css`

**Step 1: 过程页展示 skill/tool 激活信息**

在正确性专家消息里展示：
- 激活的 skill
- 设计文档标题
- 设计一致性状态

**Step 2: 结果页展示详细设计一致性区块**

展示：
- 设计文档
- 命中的设计点
- 缺失实现
- 超出设计实现
- 设计冲突

**Step 3: 跑前端构建**

Run: `npm run build`
Expected: PASS

### Task 9: 文档与端到端验证

**Files:**
- Modify: `docs/architecture/code-wiki.md`
- Modify: `README.md`

**Step 1: 补充开发者文档**

说明：
- skill 目录结构
- tool 目录结构
- 如何新增自定义 skill
- 如何新增 Python tool
- 如何在启动页上传详细设计文档

**Step 2: 端到端跑一个公开 PR**

步骤：
- 修改 `config.json` 指向公开仓库
- 在启动页绑定详细设计文档
- 启动审核
- 验证 `correctness_business` 是否触发 `design-consistency-check`
- 验证结果页是否有设计一致性信息

**Step 3: 记录验证结论**

输出：
- 是否激活 skill
- 是否调用 tool
- 是否读取 repo context
- 是否得到高质量问题清单

