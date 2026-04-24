# 代码检视质量优化适配设计（基于当前项目实现）

## 背景

外部方案提出了几类值得吸收的方向：

- 统一严重性分级
- 规则优先、LLM 后置
- 异步错误与静默失败专项增强
- 协调器去重与结构化输出

这些方向本身是有价值的，但其文档假设的是另一套 agent 体系和文件结构：

- `code-reviewer`
- `silent-failure-hunter`
- `type-design-analyzer`
- `comment-analyzer`
- `review-coordinator`

而当前项目已经演进成一套不同的多专家代码审查系统：

- 以 `main_agent_service.py` 做选专家和派工
- 以 `review_runner.py` 做专家执行、上下文组装、observation 到 finding 的转化
- 以 LangGraph orchestrator 做 `detect_conflicts -> judge_and_merge -> human_gate -> publish_report`
- 以“问题类别 -> 主责专家 -> 冲突收敛”的方式控制重复意见

因此，这份设计的目标不是照搬外部方案，而是把其中有价值的思路，改写成**适配当前项目实现**的执行方案。

## 目标

这轮优化聚焦两个结果：

1. 审查质量更稳
   - 少漏检
   - 少重复
   - 少无效问题
   - 结果表达更确定

2. 前后端口径一致
   - 后端分级、过滤、主责归因清楚
   - 前端结果页、过程页、设置页能把这些信息准确表达出来

## 设计原则

### 1. 不引入第二套架构

当前项目已经有主 Agent、专家体系、signal/expert/tool/replay/report 这条完整链路。

本次优化不新建一套平行的 `review-coordinator`、`trigger-rules.yaml`、`severity-config.yaml` 体系，而是在现有模块上增强。

### 2. 规则和信号优先，LLM 做深分析

这条原则对当前项目最重要：

- 能通过规则、signal、observation 坐实的问题，优先走确定性链路
- 只有需要上下文理解和综合判断的问题，再交给 LLM 深度分析

这能同时改善：

- 漏检
- 不确定表述
- 执行效率

### 3. 问题类别必须有唯一主责专家

当前项目已经建立了“问题类别 -> 主责专家”的边界手册和 prompt 口径。

这轮优化必须延续这个方向，而不是重新退回“多个 agent 看同一类问题，再靠后面去重”。

### 4. 结果分层要清楚

不是所有问题都应该进入最终有效问题清单。

必须明确区分：

- `finding`
- `issue`
- `threshold_filtered`
- `conditional_conclusion`
- `removed_line_only`

这样研发同学看到的结果才不会混。

## 采纳外部方案的内容与映射

### A. 统一严重性分级标准

采纳方向：是  
直接照搬：否

当前项目已经存在：

- `P0-P3`
- `confidence threshold`
- `issue_min_priority_level`
- `verification_needed`
- `finding_type`

因此，不需要新增一个独立的 `severity-config.yaml`，而是要把现有分散规则收口。

#### 设计

统一形成一份项目内部分级标准，明确：

- `severity`
- `confidence`
- `finding_type`
- `verification_needed`
- 是否可升级为 issue

#### 建议落点

- `backend/app/domain/models/runtime_settings.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- `backend/app/services/review_runner.py`
- `README.md`
- `docs/architecture/*expert-boundary*`

#### 目标效果

研发团队、专家 prompt、结果页展示看到的是同一套口径。

### B. 异步错误与静默失败专项增强

采纳方向：是  
直接照搬：否

外部文档里这部分偏 JavaScript/Node 场景，当前项目不能原样照抄。但“异步错误处理、静默失败、资源清理、超时重试”这些风险类型，本身对我们有价值。

#### 设计

把这类风险纳入当前项目已有的 signal 和 expert 体系：

- `performance_reliability`
- `correctness_business`
- `java_quality_signal_extractor.py`

按照当前项目已有的 observation-driven 路线实现：

- signal 命中
- 形成 observation
- expert 补证据
- 必要时兜底转 finding

#### 本轮重点场景

- 循环中的外部调用放大
- 静默失败
- 注释/接口承诺未实现
- 条件化结论降级
- 删除代码误报过滤

#### 建议落点

- `backend/app/services/java_quality_signal_extractor.py`
- `backend/app/services/review_runner.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`

### C. 协调器去重与结构化报告

采纳方向：是  
直接照搬：否

当前项目已经有协调器能力，只是名字不是 `review-coordinator`。

现有对应模块：

- `main_agent_service.py`
- `backend/app/services/orchestrator/graph.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- `review_service.py`

#### 设计

继续强化现有协调器，而不是新增一个新 Agent。

重点增强：

- 主责专家归因
- 同类问题合并
- 结构化结果输出
- 冲突保留与去重策略

#### 建议落点

- `backend/app/services/main_agent_service.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- `backend/app/services/review_service.py`

### D. 规则引擎优先检查

采纳方向：是  
直接照搬：否

当前项目不适合再平行引入一套新的 rule engine DSL。更合理的做法是把“规则优先”吸收到现有两层：

- 规则筛选层
- Java quality signal 层

#### 设计

把规则和 signal 分成两类：

1. 确定性规则
   - 可直接形成 observation 或 finding
2. 上下文规则
   - 先决定是否拉相关专家
   - 再由专家结合上下文判断

#### 建议落点

- `backend/app/services/knowledge_rule_screening_service.py`
- `backend/app/services/java_quality_signal_extractor.py`
- `backend/app/services/main_agent_service.py`
- `backend/app/services/review_runner.py`

## 当前项目的目标架构

### 一、后端审查质量主链

后端主链统一为下面这条路径：

1. **MR 变更输入**
   - diff
   - changed files
   - repo context

2. **规则和信号预处理**
   - knowledge rule screening
   - java quality signals
   - observation 提取

3. **主 Agent 选专家与派工**
   - 按主责问题类别拉专家
   - 尽量少拉错专家

4. **专家执行**
   - prompt 组装
   - repo context / knowledge / tool / datasource 补证据
   - observation -> finding

5. **finding 质量门控**
   - removed line 过滤
   - conditional conclusion 降级
   - 强 observation 兜底
   - 主责边界收口

6. **冲突检测与 issue 收敛**
   - primary expert
   - normalized issue type
   - threshold filter
   - participant expert ids

7. **报告构建与前端输出**
   - findings
   - valid issues
   - threshold filtered findings
   - replay / metadata / tool results

### 二、前端配套链路

前端需要和后端统一以下口径：

1. **结果页**
   - 区分 `审核发现` 和 `有效问题清单`
   - 单独展示 `被阈值过滤的问题`
   - 展示主责专家和参与专家

2. **过程页**
   - 展示规则筛选
   - 展示 tool 调用
   - 展示知识命中
   - 展示主责专家归因

3. **设置页**
   - 展示统一分级标准
   - 展示 issue threshold 配置
   - 展示 skill / tool / data source 配置

4. **专家中心**
   - 展示专家职责边界
   - 展示该专家负责什么、不负责什么
   - 展示绑定文档、skill、tool

## 分阶段方案

### Phase 1：统一后端口径

目标：

- 把分级、过滤、主责归因统一下来
- 不再新增第二套规则体系

实施内容：

- 统一 severity / confidence / verification_needed 口径
- 统一 removed-line / conditional-conclusion / threshold-filter 规则
- 把主责专家表继续下沉到：
  - main agent 路由
  - expert prompt
  - detect_conflicts

重点文件：

- `backend/app/services/main_agent_service.py`
- `backend/app/services/review_runner.py`
- `backend/app/services/orchestrator/nodes/detect_conflicts.py`
- `backend/app/domain/models/runtime_settings.py`

### Phase 2：强化专项信号能力

目标：

- 提升当前最容易漏的高价值问题识别率

实施内容：

- 增强循环调用放大
- 增强注释/承诺未实现
- 增强静默失败与失败恢复缺口
- 增强异步/重试/资源清理类 observation

重点文件：

- `backend/app/services/java_quality_signal_extractor.py`
- `backend/app/services/review_runner.py`

### Phase 3：强化前端表达

目标：

- 把后端已经判断清楚的信息，准确地传达给研发同学

实施内容：

- 结果页补主责专家、过滤原因、问题分层
- 过程页补规则命中、工具调用、知识引用
- 设置页和专家中心补口径说明

重点文件：

- `frontend/src/pages/ReviewWorkbench/index.tsx`
- `frontend/src/components/review/*`
- `frontend/src/pages/Settings/*`
- `frontend/src/services/api.ts`

### Phase 4：基准验证与运营指标

目标：

- 不只改规则，要验证真实效果

实施内容：

- 跑真实 MR 和基准 case
- 对照以下指标：
  - 重复问题是否下降
  - 条件化问题是否进入 issue
  - 删除代码误报是否下降
  - 高价值场景是否更稳定被命中

## 本轮不做的事

以下内容当前不建议做：

1. 不新增一套外部 agent 文件体系
2. 不新增平行的 coordinator agent
3. 不引入一套和现有 signal/observation 冲突的新 DSL 规则引擎
4. 不为了追求“全自动”而取消 human gate 和 finding / issue 分层

## 风险与注意事项

### 风险 1：两套规则体系并存

如果把外部方案原样搬进来，会导致：

- 现有专家边界被打乱
- 现有路由逻辑和新 trigger 规则冲突
- 后端和前端口径再度分裂

因此必须坚持“映射到当前实现”，不能原样接入。

### 风险 2：规则优先过头，误报上升

规则优先是对的，但不能把所有问题都做成“硬编码规则直出”。需要坚持：

- 强确定性问题用规则
- 复杂问题仍然由专家结合上下文判断

### 风险 3：前端展示跟不上后端口径

如果后端已经引入：

- primary expert
- normalized issue type
- conditional filter reason

但前端还按旧口径展示，研发会继续误解结果。

## 推荐实施顺序

推荐顺序如下：

1. 先统一后端口径
2. 再增强专项 signal
3. 再补前端表达
4. 最后做基准和真实 MR 对照验证

## 成功标准

如果这套适配方案推进完成，至少应达到：

1. 同类问题重复主提明显下降
2. 删除代码误报不再进入最终结果
3. 条件化结论不再进入有效问题清单
4. 循环调用放大、注释未实现、静默失败等高价值问题命中更稳定
5. 前端页面能清楚告诉研发：
   - 这是不是最终有效问题
   - 主责专家是谁
   - 为什么被过滤

## 下一步

这份设计文档确认后，下一步应单独输出实现计划，拆成：

- 后端统一口径计划
- 专项 signal 增强计划
- 前端展示联动计划
- 基准与真实 MR 验证计划
