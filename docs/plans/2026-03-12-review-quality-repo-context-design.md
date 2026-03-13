# 多专家代码审核质量增强与代码仓上下文检索设计

**背景**

基于真实 GitHub PR `calcom/cal.com#28378` 的端到端评测，当前系统已经具备真实 PR patch 拉取、真实 `kimi-k2.5` 调用、多专家对话流和结构化报告能力，但仍存在以下关键问题：

- 专家容易基于局部 diff 进行推测，产生误报
- 主 Agent 派工上下文过窄，导致跨文件真实风险漏报
- Judge 会把推测性结论包装成高置信、已验证的问题
- 复杂 PR 采用串行派工，完成时间偏长
- 专家主要依赖 PR/MR/commit diff，缺少目标分支源码上下文

本设计聚焦于提升代码审核结果的真实性、可解释性和可依赖性，同时新增系统级代码仓上下文检索能力，让所有专家能够结合目标分支源码进行判断。

## 目标

1. 降低基于局部 diff 脑补导致的误报
2. 提升跨文件、跨层链路问题的命中率
3. 让正式 Code Review 报告能够区分“直接问题”和“待验证风险”
4. 让所有专家都支持面向目标分支源码的上下文检索
5. 在保证质量的前提下改善复杂 PR 的协作效率

## 非目标

- 不重写现有 `FastAPI + file repository + LangGraph-style runtime + React workbench` 骨架
- 不引入重量级代码索引系统或向量数据库
- 不改变前端主信息架构
- 不在本轮中引入新的外部 SaaS 依赖

## 总体方案

本轮改进分四层推进：

1. **主 Agent 派工升级**
   - 从“按文件单行派工”升级到“按变更链路派工”
   - 把 PR diff 切分为 hunk，并聚合出关联文件链路，例如：
     - `migration.sql -> schema.prisma -> output DTO -> service -> transformer`
   - 每位专家拿到的不仅是主审片段，还包括关联片段、符号和预期检查点

2. **专家输出协议升级**
   - 从自由文本升级为结构化输出
   - 每条专家结论必须带问题类型、直接证据、跨文件证据、假设项和验证计划
   - 显式区分：
     - `direct_defect`
     - `risk_hypothesis`
     - `test_gap`
     - `design_concern`

3. **Judge 与报告收口升级**
   - Judge 改为证据驱动裁决，不再因为多专家重复意见就自动升置信
   - 报告按三类收口：
     - 阻塞问题
     - 待验证风险
     - 测试与回归缺口

4. **代码仓上下文检索能力**
   - 新增系统级代码仓配置
   - 所有专家都可基于目标分支源码进行检索
   - 检索方式采用本地轻量方案：`glob + rg + 文件片段读取`

## 主 Agent 设计

### 派工输入

主 Agent 在执行前先构建变更图：

- `primary_hunks`: 当前 diff 中的主变更片段
- `supporting_hunks`: 同一链路上的关联变更片段
- `changed_symbols`: 变更涉及的类型、函数、类、schema 字段
- `related_files`: 由 import、类型引用、路径模式推导出的关联文件
- `expected_checks`: 针对专家角色的必查项
- `disallowed_inference`: 明确禁止的推断方式

### 派工原则

- 不再只依据文件名和某一行新增内容派工
- 对跨文件链路强制补充上下文
- 高风险链路允许多个专家共同拿到同一组上下文，但关注点不同
- 对纯推测性线索，不直接派成“高优先级问题”，而是派成“待验证任务”

## 专家协议设计

### 统一输出结构

每位专家的最终输出统一包含：

- `claim`
- `finding_type`
- `file_path`
- `line_start`
- `line_end`
- `evidence_snippets`
- `cross_file_evidence`
- `assumptions`
- `suggested_fix`
- `confidence`
- `verification_needed`
- `verification_plan`
- `context_files`

### 输出约束

- 没有直接代码证据时，不允许输出高置信 `direct_defect`
- 仅依赖 import、命名、路径线索时，只能输出 `risk_hypothesis`
- 若引用了目标分支源码，必须明确写出 `context_files`
- 高置信问题必须说明：
  - 主证据来自哪个 diff hunk
  - 补充证据来自哪些目标分支文件

## Judge 设计

Judge 不再把“多专家提到同一件事”视为高置信，而改为按证据裁决：

- 具备直接证据 + 可复核定位 -> `accepted`
- 有风险线索但证据不足 -> `needs_verification`
- 多专家重复薄弱线索 -> 仍保持 `needs_verification`
- 与工具结果冲突 -> 降级为 `needs_human` 或 `comment`
- 纯设计建议 -> `comment`

正式 issue 状态统一收口为：

- `accepted`
- `needs_verification`
- `needs_human`
- `rejected`
- `comment`

## 报告设计

最终 Code Review 报告分三层：

1. **阻塞问题**
   - 直接证据明确，建议 `Request changes`

2. **待验证风险**
   - 有线索但证据不足，需要人工确认或追加工具验证

3. **测试与回归缺口**
   - 单独展示测试覆盖、断言薄弱、验证步骤遗漏

报告中不再把所有 finding 混成同一类“问题”。

## 代码仓上下文检索设计

### 系统配置

新增运行时配置项：

- `code_repo_clone_url`
- `code_repo_local_path`
- `code_repo_default_branch`
- `code_repo_access_token`
- `code_repo_auto_sync`

### 后端能力

新增 `RepositoryContextService`，负责：

- 确保本地存在目标代码仓
- 可选同步目标分支
- 提供源码检索能力：
  - `glob` 查找文件
  - `rg` 查找关键词、符号、调用点
  - 读取命中文件片段
  - 返回上下文文件列表和片段

### 专家使用方式

所有专家都具备 `repo_search` 能力，但默认检索策略不同：

- 正确性专家：调用链、类型定义、边界条件
- 架构专家：模块边界、依赖方向、跨层调用
- 可维护性专家：重复逻辑、复杂度、相似实现
- 安全专家：鉴权入口、权限校验、租户隔离
- 性能专家：热路径、缓存、事务、批处理
- 测试专家：测试文件、夹具、回归覆盖
- 数据库专家：schema、migration、repository、查询路径
- Redis / MQ / DDD 等专家同样基于角色选择检索重点

所有高置信结论如依赖源码仓上下文，必须记录引用文件。

## 协作效率设计

在质量提升后，再补以下效率增强：

- 互不依赖的专家并行派工
- `tool / skill / repository retrieval / knowledge retrieval` 结果缓存
- 仅对存在冲突或高风险推测项触发 debate

## 风险与应对

- **风险：专家拿到更多上下文后 prompt 过长**
  - 解决：限制 supporting hunks 数量，优先给精简片段而不是整文件

- **风险：代码仓检索导致运行变慢**
  - 解决：缓存检索结果，并为每类专家限制最大上下文条数

- **风险：Judge 过度保守，导致问题过少**
  - 解决：保留 `待验证风险` 区域，不因为证据不足直接丢弃线索

## 成功标准

- 真实复杂 PR 评测中，明显误报数量下降
- 至少能识别跨文件链路中的关键上下文问题
- 结果页能区分“直接问题”和“待验证风险”
- 所有专家都具备代码仓上下文检索能力
- 审核过程页能展示源码仓检索带来的上下文引用
