# 大文件重构设计

## 背景

当前项目已经进入持续演进阶段，但几个核心文件已经明显超过可维护边界：

- `backend/app/services/review_runner.py`：9616 行
- `backend/app/services/main_agent_service.py`：1832 行
- `backend/app/services/review_service.py`：1719 行
- `backend/app/services/tool_gateway.py`：1459 行
- `frontend/src/pages/ReviewWorkbench/index.tsx`：1627 行
- `frontend/src/components/review/ReviewDialogueStream.tsx`：1584 行

这些文件的问题不是“行数好看不好看”，而是职责已经揉在一起，后续继续改质量、加专家、调上下文、改页面展示时，风险会越来越高。

## 目标

本轮重构目标有三件事：

1. 把巨石文件拆成职责清楚的协作模块
2. 不改变当前系统外部行为和接口
3. 在拆分后跑完整回归，确认质量没有被拆坏

本轮不追求一次性做“最终架构”，而是先把最难维护的部分切开，让后面继续演进时有稳定落点。

## 设计原则

### 1. 先按职责拆，不按工具人肉切行数

拆分标准不是“每个文件小于多少行”，而是：

- 一个模块只处理一类职责
- 模块名字能直接说明它干什么
- 测试能围绕这个职责写

### 2. 先抽纯逻辑，再抽流程胶水

优先抽这几类内容：

- prompt 组装
- observation / signal 后处理
- finding 稳定化
- 结果映射与格式化
- 前端展示前的数据转换

这些逻辑边界清楚，抽出去后最容易验证。

### 3. 旧入口尽量保持不变

例如：

- `ReviewRunner` 仍然作为审核执行总入口
- `MainAgentService` 仍然作为主 Agent 对外入口
- `ReviewWorkbench/index.tsx` 仍然保留页面容器角色

这样可以减少调用方改动范围，降低回归风险。

## 目标模块划分

## 后端

### 1. `review_runner.py`

保留：

- `ReviewRunner` 类
- 审核任务总流程编排
- 仓储、事件、图执行等主入口

拆出：

- `app/services/review_runner/expert_prompt_builder.py`
  - 专家 system/user prompt 组装
  - repository context 和知识上下文格式化
- `app/services/review_runner/observation_followup.py`
  - observation 收集
  - uncovered observation 追补
  - follow-up prompt 构建
  - forced candidate 生成
- `app/services/review_runner/finding_stabilizer.py`
  - finding 强化
  - Java 高价值 signal 补强
  - input completeness 质量门控
  - candidate merge / dedupe
- `app/services/review_runner/repository_context_formatter.py`
  - repo context summary
  - related context/source snippet 格式化

### 2. `main_agent_service.py`

保留：

- `MainAgentService` 类
- 对外 `select_review_experts` / `build_routing_plan` / `build_command`

拆出：

- `app/services/main_agent/routing_prompt_builder.py`
  - 选专家 prompt
  - 派工 prompt
  - 主责专家速查文案
- `app/services/main_agent/route_hint_builder.py`
  - baseline route
  - candidate hunk 汇总
  - related files / route hint 组装

### 3. `review_service.py`

保留：

- `ReviewService` 对外 API 入口

拆出：

- `app/services/review_service/report_builder.py`
  - report 聚合
  - findings/issues/filter decisions 映射
  - LLM usage summary 汇总
- `app/services/review_service/task_lifecycle.py`
  - create / cancel / retry / close / status 相关辅助逻辑

### 4. `tool_gateway.py`

保留：

- `ReviewToolGateway` 对外入口

拆出：

- `app/services/tool_gateway/repo_context_tools.py`
  - `repo_context_search`
  - related source snippet / symbol context / filter 逻辑
- `app/services/tool_gateway/knowledge_tools.py`
  - `knowledge_search`
  - 规则文档、设计文档相关工具封装
- `app/services/tool_gateway/datasource_tools.py`
  - `pg_schema_context`
  - 其他数据源型工具

## 前端

### 5. `ReviewWorkbench/index.tsx`

保留：

- 页面容器
- 顶层 tab 切换
- 顶层数据请求协作

拆出：

- `frontend/src/pages/ReviewWorkbench/helpers.ts`
  - review / replay / issue / report 相关纯函数
  - routing summary / selection summary / filter decision 等解析器
- `frontend/src/pages/ReviewWorkbench/useReviewWorkbenchState.ts`
  - 轮询
  - 过程事件累积
  - tab 与 workspace 状态

### 6. `ReviewDialogueStream.tsx`

保留：

- 组件渲染

拆出：

- `frontend/src/components/review/reviewDialogueFormatters.ts`
  - tool result 转展示结构
  - knowledge / issue / routing / replay 相关格式化
- `frontend/src/components/review/reviewDialogueTypes.ts`
  - 本组件私有展示类型

## 实施顺序

### 第一批

- `review_runner.py`
- `main_agent_service.py`
- `ReviewWorkbench/index.tsx`
- `ReviewDialogueStream.tsx`

理由：

- 这是当前最影响质量迭代速度的主链
- 边界最清楚
- 已有测试基础最好

### 第二批

- `review_service.py`
- `tool_gateway.py`

理由：

- 会影响 API 返回和 runtime tool，适合在第一批稳定后继续拆

### 第三批

- `llm_chat_service.py`
- `knowledge_rule_screening_service.py`
- `detect_conflicts.py`
- `java_quality_signal_extractor.py`
- `Settings/index.tsx`
- `api.ts`

理由：

- 这些文件虽然大，但当前问题没有第一批那么阻塞主链开发

## 验证策略

每一批都按同样节奏执行：

1. 先抽模块，不改行为
2. 跑该模块直接相关测试
3. 再跑审核主链回归
4. 最后跑前端构建 / 类型检查

本轮最终验收要包含：

- 后端关键服务回归通过
- 前端构建通过
- 没有新增专家边界、finding 归因、结果展示回归

## 风险与应对

### 风险 1：拆分后循环依赖

应对：

- helper 模块只依赖 domain model 和小型服务
- 总入口文件反向依赖 helper，不反过来

### 风险 2：私有方法过多，迁移后行为变化

应对：

- 先迁移纯函数和弱状态逻辑
- 对迁移点补回归测试

### 风险 3：前端拆分后状态散落

应对：

- 页面仍保留容器角色
- 状态型逻辑进入 hook
- 纯解析逻辑进入 helpers

## 结论

这轮重构的核心不是“代码瘦身”，而是给后续质量优化、专家治理、页面演进腾出稳定结构。先把最大、最常改、最容易出风险的巨石切开，再跑完整回归，后续再继续向下治理剩余大文件。
