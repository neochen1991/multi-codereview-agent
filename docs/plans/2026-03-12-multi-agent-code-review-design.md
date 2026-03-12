# 多专家协同代码审核系统技术方案说明书

## 1. 文档信息

- 日期: 2026-03-12
- 适用范围: 类 GitHub 代码托管平台
- 接入方式: Access Token
- 审核对象: Merge Request 和 Git Branch
- 编排框架: LangGraph
- 文档目标: 设计一个由多个专家 Agent 协同工作的高置信度代码审核系统

## 2. 背景与目标

目标系统需要让多个具备不同角色和知识库的专家 Agent 同时审查同一批代码变更，例如:

- 数据库专家
- DDD 专家
- 性能专家
- 安全专家
- 代码规范专家
- 业务专家
- 架构专家

系统不只是“并行生成多份评论”，而是要让专家之间进行充分讨论、反驳、补证、交叉质询，最终沉淀为置信度更高、重复率更低、可执行性更强的审核意见。

这意味着系统设计不能停留在单轮 LLM Review，而应具备以下能力:

- 支持 MR 和 Branch 两种输入入口
- 支持基于 diff、文件上下文、仓库知识库、规则库进行联合判断
- 支持专家之间多轮讨论
- 支持仲裁、合并去重、置信度评估
- 支持人类 reviewer 介入
- 支持审计、回放、追踪与持续优化

## 3. 核心设计原则

参考 LangGraph 官方多 Agent 最佳实践以及业界开源项目经验，建议采用以下原则:

1. 专家职责单一，不让一个 Agent 同时承担过多审查维度。
2. 先证据后结论，所有批评意见都要绑定 diff、文件、规则或知识依据。
3. 先独立审查，再交叉辩论，最后仲裁收敛，避免群体从众。
4. 图编排优于简单链式调用，便于引入重试、人工介入、持久化与多轮状态控制。
5. 平台接入层与审查核心解耦，MR/Branch 只是不同的变更源适配器。
6. 大变更必须分块审查，避免单次上下文过载。
7. 最终输出应是“发现集合 + 证据 + 争议记录 + 置信度”，而不是仅一段自然语言总结。

## 4. GitHub 与 LangGraph 调研结论

### 4.1 可直接借鉴的开源项目

1. `qodo-ai/pr-agent`
   - 优点: 已验证的 PR 审查产品形态，支持 GitHub、GitLab、Bitbucket、Azure DevOps、Gitea；具备 PR compression、dynamic context、RAG context enrichment、self reflection 等能力。
   - 借鉴点: 变更压缩、平台适配、配置化审查规则、增量更新。
   - 局限: 核心仍偏单 agent / 单轮产出，不是“多专家激烈讨论后收敛”的架构。

2. `codedog-ai/codedog`
   - 优点: 明确支持 GitHub / GitLab，强调多模型和多维评分。
   - 借鉴点: 平台无关的审查入口、多维评分结构。
   - 局限: 公开信息里对多 Agent 协同机制描述较弱。

3. `tupizz/pr-code-analyzer`
   - 优点: 强调 diff 结构化分析、CLI 化接入、详细 review 模式。
   - 借鉴点: 先做结构化变更分析，再进入 LLM 审核。
   - 局限: 更像单体 CLI reviewer。

4. `Deb8flow`
   - 优点: 用 LangGraph 实现了辩论、事实校验、裁判仲裁、失败重试等流程控制。
   - 借鉴点: “先辩论、再核验、再裁决”的图式流程非常适合高置信代码审核。
   - 局限: 场景是通用辩论，不是代码审查。

5. `DebateLLM`
   - 优点: 研究了多种 debate protocol、consensus、agreement intensity 对结果质量的影响。
   - 借鉴点: 不同议题可能需要不同共识协议，不能假设单一投票机制总是最优。
   - 局限: 更偏研究框架，而非工程化审查系统。

### 4.2 LangGraph 官方最佳实践

1. Multi-agent network
   - 适合多个专家节点相互协作与转交任务。

2. Supervisor / Hierarchical teams
   - 适合由顶层协调者管理多个专家团队，特别适用于“专家数多、流程长、需要分层治理”的场景。

3. Checkpointer / thread-level persistence
   - 适合长流程、多轮讨论、人工介入、失败恢复、回放审计。

4. Human-in-the-loop / interrupt
   - 适合对高风险问题引入人工确认，例如安全阻断、数据库迁移风险、业务破坏性变更。

5. Long-term memory / store
   - 适合沉淀仓库规范、历史 review 偏好、误报案例、业务术语、架构约束等知识。

## 5. 方案候选

### 方案 A: 单 Supervisor + 多专家并行 + 一次汇总

流程:

1. Coordinator 解析变更
2. 各专家并行输出意见
3. 汇总器合并去重
4. 输出最终报告

优点:

- 结构最简单
- 响应快
- 易于 MVP 落地

缺点:

- 专家之间没有充分博弈
- 容易产生重复意见
- 难以提升置信度
- 对复杂争议问题无处理机制

适用:

- 轻量级首版验证

### 方案 B: 全网状多专家自由辩论

流程:

1. 每个专家独立审查
2. 任意专家都可质疑其他专家意见
3. 经过多轮对话后再投票或裁决

优点:

- 讨论最充分
- 有机会发现单专家遗漏

缺点:

- 成本高
- 路由复杂
- 讨论容易失控
- 对工程治理不友好

适用:

- 学术研究或小规模实验

### 方案 C: 分层式多专家审核图

流程:

1. 变更摄取与切片
2. 专家独立审查
3. 争议驱动的定向辩论
4. 证据核验
5. 裁判仲裁
6. 最终报告生成

优点:

- 兼顾质量、成本、可控性
- 适合 LangGraph 的 graph + state + checkpoint 模型
- 易于对 MR / Branch 统一抽象
- 易于接入人工确认、知识库和回放

缺点:

- 比单 supervisor 更复杂
- 需要更精细的状态设计和调度策略

适用:

- 生产级多专家代码审核系统

### 推荐结论

推荐采用方案 C: 分层式多专家审核图。

原因:

- 它继承了 PR-Agent 一类工程化产品的“平台适配、变更压缩、增量执行”优点。
- 它吸收了 Deb8flow / DebateLLM 一类方案的“辩论、核验、仲裁”优点。
- 它与 LangGraph 的 hierarchical teams、persistence、human-in-the-loop 能力天然匹配。

## 6. 总体架构设计

### 6.1 分层架构

建议采用五层架构:

1. 平台接入层
   - 负责对接类 GitHub 平台 API
   - 支持 MR 模式和 Branch 模式
   - 使用 Token 拉取 diff、文件、commit、评论、作者、目标分支信息

2. 变更理解层
   - 负责统一变更模型、差异切片、风险初筛、上下文扩展
   - 将 MR 和 Branch 都抽象为 `ReviewSubject`

3. 专家协作层
   - 由多个专家 Agent、讨论路由器、事实核验器、裁判 Agent 组成
   - 由 LangGraph 负责编排

4. 知识与规则层
   - 仓库知识库
   - 专家知识库
   - 组织规范库
   - 历史误报/漏报记忆

5. 输出与治理层
   - MR 评论 / Review Comment
   - Branch 审核报告
   - 审核 trace、指标、回放、人工介入、反馈闭环

### 6.2 统一输入模型

无论输入是 MR 还是 Branch，都先转换为统一对象:

```text
ReviewSubject
- subject_type: mr | branch
- repo_id
- project_id
- source_ref
- target_ref
- commits[]
- changed_files[]
- unified_diff
- metadata
```

统一抽象后，后续图流程无需感知入口差异。

### 6.3 Branch 与 MR 支持策略

#### MR 模式

- 直接读取平台 API 中的 merge request diff
- 获取目标分支、评审线程、作者信息、已有评论
- 支持增量审查，只分析新 commit 引入的变化

#### Branch 模式

- 通过 `source_ref` 与 `target_ref` 做 compare
- 若用户未指定 target，可默认主干分支，如 `main` 或 `master`
- 输出为“分支审核报告”或回写到平台的 commit comment / check run

统一本质:

- MR 是“平台已建模的 compare”
- Branch 是“系统自行构造的 compare”

## 7. LangGraph 图编排设计

### 7.1 顶层图

建议将系统建成一个顶层 `StateGraph`，核心节点如下:

1. `ingest_change`
   - 拉取 MR / Branch 元数据与 diff

2. `slice_change`
   - 按文件、模块、风险等级切片

3. `expand_context`
   - 拉取必要源码上下文、测试、配置、文档、历史关联文件

4. `route_experts`
   - 根据变更类型选择专家集合

5. `run_independent_reviews`
   - 各专家独立生成初步 findings

6. `detect_conflicts`
   - 识别冲突、重复、低证据、低置信意见

7. `run_targeted_debate`
   - 只对争议点发起定向辩论

8. `evidence_verification`
   - 对关键结论做规则、代码、知识库、测试证据核验

9. `judge_and_merge`
   - 仲裁、合并、打分、去重

10. `human_gate`
    - 对高风险项触发人工审核

11. `publish_report`
    - 输出最终意见和结构化报告

12. `persist_feedback`
    - 记录结果、用户反馈、误报标签

### 7.2 推荐子图设计

建议使用分层子图而不是把所有 Agent 平铺在一个大图上:

- 顶层协调图
- 专家审查子图
- 争议辩论子图
- 证据核验子图
- 输出生成子图

这样可以更容易做复用、测试和替换。

## 8. Agent 角色设计

### 8.1 专家划分原则

对标业界最佳实践，专家不应主要按“个人头衔”划分，而应优先按稳定的审查质量维度划分。

更合适的设计原则是:

- 核心专家按质量维度划分，如正确性、安全、性能、可靠性、可维护性、测试、兼容性、架构。
- 专项专家按特定技术域或方法论激活，如数据库、DDD、前端、语言生态。
- 能由确定性工具稳定覆盖的问题，不应占用高成本专家角色。
- 每个专家都要有清晰边界，避免对同一问题从不同名义重复评论。

### 8.2 核心专家 Agent

建议将以下角色设为系统默认常驻专家:

1. 功能正确性与业务逻辑专家 (`Correctness & Business Logic Reviewer`)
   - 关注功能正确性、边界条件、状态流转、业务规则一致性、异常路径。
   - 回答的问题是: “这次修改是否真的把需求做对了?”

2. 架构与设计专家 (`Architecture & Design Reviewer`)
   - 关注模块边界、抽象层级、依赖方向、耦合、扩展性、设计退化。
   - 回答的问题是: “这次修改是否破坏了系统设计?”

3. 安全与合规专家 (`Security & Compliance Reviewer`)
   - 关注注入、鉴权、越权、敏感数据处理、依赖风险、审计与合规约束。
   - 回答的问题是: “这次修改是否引入了安全或合规风险?”

4. 性能与资源效率专家 (`Performance & Resource Efficiency Reviewer`)
   - 关注时间复杂度、I/O、缓存命中、批处理、数据库访问模式、CPU/内存消耗。
   - 回答的问题是: “这次修改是否会显著拖慢系统或浪费资源?”

5. 可靠性与故障处理专家 (`Reliability & Failure Handling Reviewer`)
   - 关注超时、重试、幂等、回滚、异常处理、并发安全、容错行为。
   - 回答的问题是: “系统在失败、重试或并发场景下还能否稳定工作?”

6. 可维护性与代码健康专家 (`Maintainability & Code Health Reviewer`)
   - 关注可读性、复杂度、重复代码、可维护性、局部一致性、长期演化成本。
   - 回答的问题是: “这段代码未来是否容易维护和演进?”

7. 测试与验证专家 (`Test & Verification Reviewer`)
   - 关注测试覆盖、测试粒度、断言有效性、回归保护、mock 合理性。
   - 回答的问题是: “这次修改是否被充分验证，能否防止回归?”

8. 兼容性与变更影响专家 (`Compatibility & Change Impact Reviewer`)
   - 关注向后兼容、接口契约、Schema 变更、配置兼容、数据迁移、发布风险。
   - 回答的问题是: “这次修改是否会破坏已有调用方或上线流程?”

### 8.3 专项专家 Agent

以下角色不建议默认常驻，而应按变更特征动态激活:

1. 数据库专项专家 (`Database Specialist Reviewer`)
   - 关注事务边界、索引、锁、ORM 使用、迁移风险、SQL 性能、数据一致性。

2. DDD 领域建模专项专家 (`DDD Specialist Reviewer`)
   - 关注聚合边界、领域服务职责、仓储抽象、限界上下文、领域事件。
   - 仅在仓库明确采用 DDD 或变更明显触及领域模型时激活。

3. 前端体验与可访问性专项专家 (`Frontend UX / Accessibility Specialist Reviewer`)
   - 关注交互、可访问性、渲染性能、状态管理、组件边界。

4. API 契约专项专家 (`API Contract Specialist Reviewer`)
   - 关注 OpenAPI / gRPC / 事件契约、版本兼容、错误码、幂等语义。

5. 基础设施与运维专项专家 (`Infra / Ops Specialist Reviewer`)
   - 关注部署配置、资源配额、观测性、CI/CD、发布与回滚链路。

6. 语言与框架专项专家 (`Language / Framework Specialist Reviewer`)
   - 针对 Java / Go / Python / TypeScript / Spring / Django / React 等生态做专项检查。

### 8.4 不建议默认独立成专家的角色

以下角色常见但不建议默认独立设置:

1. 代码规范专家
   - 原因: 大量问题可以由 lint、formatter、静态分析稳定覆盖。
   - 建议: 将“风格与简单规范”下沉给工具，把“可维护性与代码健康”保留给高阶专家。

2. 业务专家
   - 原因: 如果定义过宽，容易与正确性专家、DDD 专家、架构专家重叠。
   - 建议: 将“需求正确性”归入“功能正确性与业务逻辑专家”；将“领域建模”归入“DDD 领域建模专项专家”。

3. DDD 专家
   - 原因: 并非所有仓库都适用，且常与架构、业务维度重叠。
   - 建议: 作为条件激活的专项专家，而不是全仓默认专家。

### 8.5 默认专家组合建议

#### MVP 默认专家

建议首版默认启用以下 6 个专家:

1. 功能正确性与业务逻辑专家
2. 架构与设计专家
3. 安全与合规专家
4. 性能与可靠性专家
5. 可维护性与代码健康专家
6. 测试与验证专家

说明:

- 为降低首版成本，可将 `Performance` 与 `Reliability` 暂时合并为一个专家。
- `Compatibility & Change Impact` 可在 API / Schema 变更多的仓库中提早升为默认专家。

#### 生产版默认专家

推荐逐步扩展为以下 8 个核心专家:

1. 功能正确性与业务逻辑专家
2. 架构与设计专家
3. 安全与合规专家
4. 性能与资源效率专家
5. 可靠性与故障处理专家
6. 可维护性与代码健康专家
7. 测试与验证专家
8. 兼容性与变更影响专家

### 8.6 专家定义模板

每个专家 Agent 由以下部分组成:

- 角色描述
- 关注点清单
- 专属知识库
- 输入约束
- 输出 schema
- 可调用工具

### 8.7 专家职责边界建议

为避免重复评论，建议明确边界:

1. 功能正确性与业务逻辑专家
   - 只判断功能语义和业务规则，不对架构优雅性做主判断。

2. 架构与设计专家
   - 只判断设计边界、依赖与抽象，不主导具体业务逻辑正确性。

3. 安全与合规专家
   - 只判断安全与合规风险，不对一般性能或可读性问题给出强结论。

4. 性能与资源效率专家
   - 只判断资源消耗和热点路径，不把所有数据库问题都纳入自己范围。

5. 可靠性与故障处理专家
   - 只判断失败恢复、并发、重试、幂等、超时等稳定性问题。

6. 可维护性与代码健康专家
   - 只判断长期维护成本，不处理纯格式化问题。

7. 测试与验证专家
   - 只判断验证充分性，不直接替代其他专家判定风险本身。

8. 兼容性与变更影响专家
   - 只判断变更对外部依赖、发布过程和存量数据的破坏性。

### 8.8 动态路由规则建议

建议由 `route_experts` 节点根据变更特征自动激活专项专家:

1. 涉及 `migration / schema / repository / transaction / SQL / ORM`
   - 激活 `数据库专项专家`

2. 涉及 `aggregate / domain / event / repository / application service`
   - 若仓库被标记为 DDD 项目，激活 `DDD 领域建模专项专家`

3. 涉及 `openapi / protobuf / controller / rpc / sdk / public interface`
   - 激活 `API 契约专项专家`

4. 涉及 `Dockerfile / helm / terraform / workflow / deploy / monitor`
   - 激活 `基础设施与运维专项专家`

5. 涉及 `react / vue / template / css / accessibility / browser`
   - 激活 `前端体验与可访问性专项专家`

6. 涉及特定语言或框架关键目录
   - 激活对应 `语言与框架专项专家`

### 8.9 专家输入上下文建议

不同专家不应看到完全相同的上下文。建议在共享基础上下文之上做差异化补充。

#### 全体专家共享基础上下文

- `ReviewSubject` 基本信息
- 变更 diff 与 diff slice
- 相关文件的最小必要上下文
- 仓库级编码规范与审查基线
- 历史同文件 review 摘要

#### 专家差异化上下文

1. 功能正确性与业务逻辑专家
   - 需求描述、用户故事、接口语义、状态机、错误处理约定

2. 架构与设计专家
   - 模块依赖图、ADR、目录边界、分层约束、公共抽象清单

3. 安全与合规专家
   - 鉴权链路、安全基线、敏感字段表、合规规则、依赖漏洞信息

4. 性能与资源效率专家
   - 热点路径、性能基线、缓存策略、SQL profile、资源使用阈值

5. 可靠性与故障处理专家
   - 超时配置、重试策略、熔断策略、并发模型、失败案例

6. 可维护性与代码健康专家
   - 复杂度阈值、代码规范、历史重构点、模块演化说明

7. 测试与验证专家
   - 测试目录、覆盖率报告、测试约定、回归缺陷历史

8. 兼容性与变更影响专家
   - API 契约、版本策略、迁移脚本、发布清单、回滚步骤

### 8.10 专家输出 Schema 建议

建议所有专家统一输出结构，便于后续去重、辩论和仲裁:

```json
{
  "finding_id": "uuid",
  "reviewer_key": "security_compliance",
  "reviewer_name_zh": "安全与合规专家",
  "reviewer_name_en": "Security & Compliance Reviewer",
  "title": "Potential authorization bypass on internal API",
  "category": "security",
  "severity": "major",
  "confidence": 0.81,
  "file_path": "src/api/internal/order.py",
  "line_spans": [[42, 68]],
  "claim": "The new endpoint skips the existing permission guard and trusts caller input.",
  "why_it_matters": "This may allow unauthorized reads of order details.",
  "evidence": [
    {
      "type": "diff",
      "ref": "slice_03",
      "summary": "Permission decorator removed from handler."
    }
  ],
  "suggestion": "Reuse the existing guard middleware or enforce the same permission check in the handler.",
  "counterpoints": [
    "If this endpoint is only reachable from a trusted internal network, severity may be reduced."
  ],
  "needs_debate": true,
  "needs_human": false
}
```

约束建议:

- `claim` 必须可验证，不允许空泛表述
- `evidence` 至少包含一条代码或规则证据
- `suggestion` 必须是可执行建议
- `counterpoints` 用于提前暴露不确定性，便于后续辩论

### 8.11 专家提示词骨架建议

每个专家的系统提示词可采用统一骨架，再按角色注入差异化规则:

```text
你是{reviewer_name_zh}。
你的英文角色名是 {reviewer_name_en}。

你的职责边界:
{responsibility_scope}

你不应该处理的问题:
{out_of_scope}

你收到的输入包括:
- review subject 元数据
- diff slice
- 相关代码上下文
- 与你角色相关的知识库片段

你的任务:
1. 仅从你的职责边界出发识别真实问题
2. 每条意见都必须给出证据
3. 如果证据不足，降低置信度或明确写出不确定性
4. 不要重复其他专家可能已经覆盖的纯风格问题
5. 输出必须符合统一 JSON schema

严重度定义:
- blocker
- major
- minor
- info

置信度要求:
- 0.0 到 1.0
- 只有在代码证据明确、推理链稳定时才能高于 0.8

如果你无法确认问题成立:
- 输出低置信度 finding
- 或明确标记 needs_debate / needs_human
```

### 8.12 专家辩论发言约束

为避免“多 agent 空转聊天”，建议统一辩论协议:

1. 每次发言只能围绕一个 `DebateIssue`
2. 必须引用原始 finding 或证据，不允许脱离代码空谈原则
3. 不允许简单重复自己的上一轮观点
4. 可以提出以下四类动作:
   - 支持
   - 反驳
   - 补证
   - 降级结论
5. 若无法继续补充新证据，应明确表示“保留意见”而不是无限争论
6. Moderator 在每轮结束后总结:
   - 已确认点
   - 争议点
   - 缺失证据
   - 是否进入 verifier 或 human gate

建议单条辩论消息结构如下:

```json
{
  "issue_id": "debate_12",
  "reviewer_name_zh": "架构与设计专家",
  "stance": "rebuttal",
  "target_finding_id": "f_102",
  "summary": "This is a local helper extraction rather than a new architectural boundary.",
  "evidence": [
    "No new cross-module dependency is introduced in the diff.",
    "The helper remains private to the same package."
  ],
  "proposal": "Downgrade from major to info"
}
```

### 8.13 非专家型 Agent 中文命名建议

除专家外，还建议为控制角色提供中英文名，便于前端展示和审计:

- 审核协调器 (`Reviewer Coordinator`)
- 辩论主持人 (`Debate Moderator`)
- 证据核验员 (`Evidence Verifier`)
- 审核裁判官 (`Judge Agent`)
- 报告生成器 (`Report Writer`)

### 8.14 非专家型 Agent

除专家外，还需要以下控制角色:

1. 审核协调器 (`Reviewer Coordinator`)
   - 决定要激活哪些专家
   - 控制轮数、预算与终止条件

2. 辩论主持人 (`Debate Moderator`)
   - 组织冲突议题辩论
   - 限制无效争论

3. 证据核验员 (`Evidence Verifier`)
   - 检查意见是否有真实代码证据、规则依据、知识依据

4. 审核裁判官 (`Judge Agent`)
   - 负责最终采信、降权、驳回与合并

5. 报告生成器 (`Report Writer`)
   - 把结构化 findings 组织为平台友好的最终输出

### 8.15 对标业界后的平台化设计结论

结合开源仓和官方资料，可以归纳出几条对平台化最有价值的经验:

1. 专家要注册化，而不是硬编码
   - GitHub Custom Agents 已经采用“agent profile + tools + mcp-servers”的声明式方式来配置 agent 能力。
   - 对我们的系统而言，专家也应通过注册表声明名称、能力、工具、知识源和路由规则。

2. 工具接入要白名单化，而不是默认全开放
   - GitHub Custom Agents 明确支持对工具做 allowlist，并可按 `server/tool` 粒度启用 MCP 工具。
   - 对代码审核场景，专家必须按角色拿到最小权限工具集，避免安全和成本失控。

3. 多专家协作更适合“分层协调 + 共享黑板”
   - LangGraph Supervisor 提供分层 supervisor。
   - Agent Blackboard 采用 Blackboard Pattern，把共享知识与协调流程解耦。
   - 这类模式比完全自由对话更可控，更适合生产审查系统。

4. 私有知识和长期记忆应该成为一等公民
   - LangMem 原生支持与 LangGraph store 集成。
   - Markdown RAG 项目已经验证“Markdown 文档 -> 向量检索知识库”是一条低门槛高实用路径。
   - 这意味着“每个专家拥有自己的知识库”是完全可行且合理的。

5. Agent 本身也可以成为工具
   - DeepMCPAgent 展示了 cross-agent communication，可以把别的 agent 作为可调用工具。
   - 这对“专家向专项专家求证”非常有帮助。

### 8.16 专家能力模型设计

建议把每个专家设计成一个标准化能力单元，而不是单纯的一段 prompt。

#### 专家能力单元结构

```text
ExpertProfile
- expert_id
- name_zh
- name_en
- version
- description
- review_scope
- out_of_scope
- trigger_rules[]
- input_policy
- output_schema_version
- prompt_template
- knowledge_sources[]
- skill_bindings[]
- tool_bindings[]
- mcp_bindings[]
- model_policy
- budget_policy
- safety_policy
- evaluation_policy
- enabled
```

#### 能力分层

建议把每个专家的能力分成四层:

1. 推理层
   - 角色提示词
   - 输出 schema
   - 严重度与置信度规则

2. 知识层
   - 专家私有知识库
   - 仓库共享知识库
   - 用户上传参考文档

3. 工具层
   - 内建 tool
   - MCP tool
   - skill 封装工具

4. 治理层
   - 权限控制
   - 调用预算
   - 观测指标
   - 审计日志

### 8.17 专家注册表设计

建议系统引入 `Expert Registry`，统一管理专家声明。

#### 注册表职责

- 注册内置专家与自定义专家
- 为 `route_experts` 提供候选专家列表
- 返回专家的知识源、工具权限、MCP 权限和 skill 权限
- 支持版本控制、灰度发布、启停和审计

#### 推荐配置形式

建议使用 `YAML + Markdown + JSON Schema` 三件套:

- `expert.yaml`
  - 声明元数据、工具权限、知识源、路由条件
- `prompt.md`
  - 声明角色提示词与辩论规则
- `schema.json`
  - 声明输出结构

示例:

```yaml
id: security_compliance
name_zh: 安全与合规专家
name_en: Security & Compliance Reviewer
enabled: true
kind: builtin
trigger_rules:
  - path_matches: ["**/auth/**", "**/api/**"]
  - diff_keywords: ["token", "permission", "jwt", "secret"]
knowledge_sources:
  - type: repo_kb
    ref: security_baseline
  - type: user_docs
    ref: docs/security/*.md
tools:
  - read_file
  - search_code
  - github_diff
mcp_tools:
  - semgrep/run_scan
skills:
  - security-pattern-check
model_policy:
  model: gpt-5
  max_tokens: 12000
budget_policy:
  max_tool_calls: 6
  max_debate_rounds: 2
```

### 8.18 是否支持每个专家有自己的知识库

答案是支持，而且建议作为标准能力。

#### 推荐知识架构

每个专家至少可以挂 4 类知识:

1. 平台共享知识
   - 组织级规则、公司编码规范、统一安全基线

2. 仓库共享知识
   - README、ADR、接口文档、设计说明、目录约束

3. 专家私有知识
   - 只对某个专家开放，例如数据库规范、安全红线、DDD 建模约束

4. 用户自定义知识
   - 用户为某个专家单独上传或绑定的参考资料

#### 存储策略

建议同时使用:

- 对象存储 / 文件存储保存原始 Markdown
- 向量库保存 chunk embedding
- 关系库存储知识元数据、权限和版本
- LangGraph Store / LangMem 保存专家长期记忆与反馈学习结果

#### 检索策略

建议按“知识域隔离 + 检索融合”设计:

```text
final_context =
  shared_repo_docs
  + expert_private_docs
  + user_bound_docs
  + historical_memories
```

但在执行上要限制优先级:

1. 先取仓库相关上下文
2. 再取该专家私有知识
3. 再取用户绑定资料
4. 最后补充长期记忆

这样能减少“拿到太多不相关文档”的问题。

### 8.19 是否支持用户自己定义专家参考知识 Markdown 文档

答案是支持，并且这是非常值得做的产品能力。

#### 推荐做法

允许用户通过以下方式为专家绑定参考知识:

1. 仓库内路径绑定
   - 例如 `docs/review/security.md`
   - 优点: 与代码同版本，方便变更审计

2. 平台上传文档
   - 用户在 UI 上传 `.md`、`.mdx`、`.txt`、`.pdf`
   - 系统转换成 Markdown 或纯文本后入库

3. Git URL / Wiki / Docs 目录绑定
   - 用于组织级共享规范

#### 产品形态建议

每个专家支持 3 个知识槽位:

- `baseline_docs`
- `repo_docs`
- `custom_docs`

其中 `custom_docs` 就是用户可自定义的知识文档集合。

#### 文档处理流程

1. 上传或扫描 Markdown
2. 做 chunking 和 metadata 标注
3. 建 embedding
4. 记录所属专家、所属仓库、版本和生效范围
5. 在运行时按专家路由检索

#### 元数据建议

```text
KnowledgeDoc
- doc_id
- source_type: repo | upload | url
- owner_scope: org | repo | expert
- bound_expert_ids[]
- title
- path_or_url
- content_hash
- version
- tags[]
- language
- enabled
```

### 8.20 是否支持 skill、MCP、tool 调用

答案是支持，但建议统一抽象成“能力适配层”，而不是让专家直接感知不同协议。

#### 推荐统一抽象

```text
CapabilityAdapter
- capability_id
- capability_type: tool | mcp | skill | agent
- name
- description
- input_schema
- output_schema
- auth_policy
- timeout_policy
- retry_policy
- cost_policy
- enabled
```

然后每个专家只声明自己依赖哪些 capability，而不直接耦合底层实现。

#### 四类能力的定位

1. Tool
   - 适合本地确定性能力
   - 例如读文件、搜代码、运行 AST 分析、读取 diff

2. MCP
   - 适合外部系统和标准化工具接入
   - 例如 Semgrep、Git 平台 API、Issue 系统、浏览器自动化

3. Skill
   - 适合高层工作流或专家方法论封装
   - 例如“安全红线检查”“数据库迁移风险评估”“DDD 聚合一致性审查”

4. Agent-as-Tool
   - 适合专家向其他专家或专项专家求助
   - 例如“架构与设计专家”调用“数据库专项专家”做二次核验

#### 推荐调用顺序

1. 先本地 tool
2. 再仓库知识检索
3. 再 MCP 外部能力
4. 最后必要时调 skill 或其他 agent

这样最省成本，也更稳定。

### 8.21 专家能力接入架构设计

建议增加一层 `Capability Gateway`，负责把 skill、MCP、tool 统一封装。

```text
Expert Agent
  -> Capability Gateway
      -> Built-in Tools
      -> MCP Clients
      -> Skill Runtime
      -> Peer Experts
```

#### Capability Gateway 职责

- 做统一鉴权和权限校验
- 统一入参出参 schema
- 记录调用日志、耗时、错误率、token 和成本
- 做重试、熔断、超时控制
- 暴露统一接口给 LangGraph 节点使用

#### 为什么要单独这一层

- 避免每个专家自己集成一堆协议
- 方便后续替换 MCP server 或 tool 实现
- 便于做租户隔离和权限治理
- 便于统计“哪个专家为什么误报，调用了哪些工具”

### 8.22 平台型产品架构建议

为了支撑“内置专家 + 用户自定义专家 + 私有知识库 + 技能工具扩展”，建议在原有架构上补齐以下服务:

1. `expert-registry-service`
   - 管理专家注册、版本、启停、灰度发布

2. `knowledge-ingestion-service`
   - 导入 Markdown / PDF / URL
   - 做 chunking、embedding、索引与权限绑定

3. `capability-gateway`
   - 统一封装 tools、MCP、skills、peer agents

4. `policy-service`
   - 管理权限、预算、模型策略、工具白名单

5. `review-orchestrator`
   - 执行 LangGraph 主图

6. `feedback-learning-service`
   - 汇总误报、漏报、人工确认结果，写入长期记忆

### 8.23 用户自定义专家设计

建议产品支持“用户定义专家”，但采用受控配置，而不是完全自由编程。

#### 推荐自定义方式

用户创建自定义专家时，只配置以下内容:

- 中文名
- 英文名
- 专家职责说明
- 触发条件
- 参考知识文档
- 可用工具白名单
- 可用 MCP 白名单
- 可用 skill 白名单
- 输出风格偏好

而不直接让用户写任意执行代码。

#### 自定义专家包结构建议

```text
experts/
  custom/
    biz_rule_checker/
      expert.yaml
      prompt.md
      schema.json
      knowledge/
        glossary.md
        review_rules.md
```

#### 安全边界建议

- 自定义专家默认不能执行 shell
- 默认只能访问用户显式授权的知识文档
- MCP 和 skill 采用白名单
- 高风险工具必须二次授权

### 8.24 审核质量视角下的专家能力建议

即使本轮以平台设计为主，也需要提前考虑质量设计，否则平台会变成“可配置但不好用”。

建议每个专家都具备以下最小质量能力:

1. 证据约束
   - 没有代码或规则证据时不能输出高置信 blocker

2. 不确定性表达
   - 必须显式写出 `counterpoints` 和 `needs_debate`

3. 可执行建议
   - 输出不能只有批评，没有建议

4. 工具优先
   - 能被工具验证的问题，优先调用工具

5. 记忆隔离
   - 专家长期记忆默认按仓库和专家隔离，防止污染

### 8.25 推荐结论

基于业界最佳实践，建议把“专家”设计成可注册、可挂知识、可挂工具、可挂技能、可被审计的标准能力单元。

结论如下:

1. 每个专家拥有自己的知识库是可行且推荐的
2. 用户自定义 Markdown 参考知识文档是可行且应作为标准产品能力
3. 专家支持 `skill / MCP / tool` 调用是可行的，但必须通过统一能力网关治理
4. 专家注册表 + 知识摄取服务 + 能力网关 + LangGraph Supervisor/StateGraph，是最稳妥的生产级组合

### 8.26 审核质量设计的业界基线

对标业界公开实践，专家能力设计不应只围绕“能不能发现问题”，还要围绕“如何稳定、低误报地发现问题”。

可直接借鉴的质量基线包括:

1. Google Code Review
   - 强调 `design / functionality / complexity / tests / naming / comments / style / context`
   - 启示: 专家必须检查上下文与整体设计，不能只看局部 diff

2. GitLab Review Focus Areas
   - 强调 `code style / code quality / testing / documentation / security / backward compatibility`
   - 启示: 兼容性、测试、文档更新应纳入稳定维度

3. Sonar Clean Code
   - 强调 `consistency / intentionality / adaptability / responsibility`
   - 启示: 可维护性专家不能只看风格，还要看意图表达和演化能力

4. AWS Reliability Pillar
   - 强调 `idempotency / retry limits / backoff / failure handling`
   - 启示: 可靠性专家应把幂等、重试、超时、重入作为高优先级检查项

5. GitHub CodeQL
   - 强调用确定性 query 检查安全与错误模式
   - 启示: 对安全、数据流、错误模式问题，优先用工具验证而非纯 LLM 断言

### 8.27 专家质量能力模型

建议为每个专家定义统一的质量能力模型，而不是只定义“职责范围”。

```text
ExpertQualityProfile
- coverage_dimensions[]
- evidence_requirements[]
- tool_first_categories[]
- retrieval_policy
- debate_policy
- escalation_policy
- acceptance_policy
- false_positive_guardrails[]
- scoring_policy
- evaluation_metrics[]
```

每个专家都应至少具备以下 8 类质量能力:

1. 上下文感知能力
   - 不只看 diff，还要看文件上下文、调用路径、测试和设计边界

2. 证据约束能力
   - 没有代码证据、规则证据或工具证据时，不能输出高严重度高置信度结论

3. 不确定性表达能力
   - 对条件性问题必须写出 `counterpoints`、假设前提和待验证点

4. 工具优先验证能力
   - 能被静态分析、测试、schema 校验验证的问题，先调用工具

5. 反驳接受能力
   - 在辩论中能降级、撤回或修正意见，而不是无限坚持

6. 去重协作能力
   - 不重复评论其他专家已覆盖的问题

7. 可执行建议能力
   - 输出不仅指出问题，还能给出可执行的修复方向

8. 反馈学习能力
   - 能根据人工确认和误报历史调整检索与打分策略

### 8.28 各专家的质量重点设计

#### 功能正确性与业务逻辑专家

- 高价值问题:
  - 状态流转错误
  - 边界条件遗漏
  - 需求语义偏差
  - 异常分支未覆盖
- 质量约束:
  - 若没有需求说明、测试或状态机上下文，不应输出高置信 blocker
  - 对“也许业务想这样做”的判断必须降置信度
- 优先工具:
  - 测试 diff 检查
  - API 契约对比
  - 关键路径调用链分析

#### 架构与设计专家

- 高价值问题:
  - 依赖方向反转
  - 模块边界泄漏
  - 抽象层级错位
  - 不必要的过度设计
- 质量约束:
  - 必须结合模块边界和仓库既有架构约束
  - 不能把单纯代码抽取误判为架构问题
- 优先工具:
  - 依赖图
  - import graph
  - package boundary rules

#### 安全与合规专家

- 高价值问题:
  - 输入校验缺失
  - 鉴权绕过
  - 数据泄露
  - 危险依赖
- 质量约束:
  - 安全问题默认优先工具验证
  - 没有明确攻击面证据时，避免过度升级严重度
- 优先工具:
  - CodeQL
  - Semgrep
  - secret scan
  - dependency scan

#### 性能与资源效率专家

- 高价值问题:
  - N+1
  - 无界扫描
  - 重复 I/O
  - 热路径不必要分配
- 质量约束:
  - 没有热点路径或数据规模假设时，避免夸大性能问题
  - 对“理论上慢”与“真实风险高”要区分
- 优先工具:
  - SQL explain
  - profiler
  - benchmark baseline
  - AST pattern scan

#### 可靠性与故障处理专家

- 高价值问题:
  - 幂等缺失
  - 重试风暴风险
  - 超时传播缺失
  - 回滚和补偿不完整
- 质量约束:
  - 可靠性问题要结合部署模型和调用链
  - 对分布式事务、异步消息等场景要特别谨慎
- 优先工具:
  - workflow checker
  - timeout/retry config scan
  - integration tests

#### 可维护性与代码健康专家

- 高价值问题:
  - 复杂度过高
  - 意图表达不清
  - 重复代码
  - 无法安全修改的脆弱结构
- 质量约束:
  - 避免输出纯主观审美意见
  - 格式和 lint 能覆盖的问题不再重复评论
- 优先工具:
  - complexity metrics
  - duplication scan
  - lint/static analysis

#### 测试与验证专家

- 高价值问题:
  - 缺少关键回归测试
  - 测试断言无效
  - mock 过度
  - 没有覆盖失败路径
- 质量约束:
  - 不只看“有没有测试”，还要看测试是否真的会失败
  - 测试本身也按维护成本审查
- 优先工具:
  - coverage diff
  - flaky test history
  - mutation testing

#### 兼容性与变更影响专家

- 高价值问题:
  - API breaking change
  - schema 不兼容
  - 配置默认值变化
  - 上线回滚路径缺失
- 质量约束:
  - 必须结合版本策略和发布方式判断严重度
  - 内部接口与外部接口要区分处理
- 优先工具:
  - OpenAPI diff
  - schema diff
  - config change scan

### 8.29 知识检索质量设计

为了让“每个专家都有自己的知识库”真正有价值，需要避免检索污染和上下文噪音。

#### 检索优先级

建议固定检索顺序:

1. 变更局部上下文
2. 同文件或同模块上下文
3. 专家私有知识
4. 仓库共享知识
5. 用户绑定 Markdown 文档
6. 历史相似 finding 和记忆

原因:

- 先让专家理解代码本身
- 再补专家视角的知识
- 最后再看外部规则和历史经验

#### 检索守卫

建议增加以下 guardrails:

1. 单次最多引入固定数量 chunk
2. chunk 必须带来源和版本
3. 不允许不同专家默认共享全部私有知识
4. 低相关 chunk 不进入最终 prompt
5. 用户自定义文档优先作为“参考依据”，不是绝对真理

#### 用户 Markdown 文档的最佳实践

建议对用户上传的 Markdown 做结构约束:

- 标题清晰
- 规则一条一段
- 示例代码单独块
- 标明适用范围和例外

这样更利于稳定检索，也更利于引用到 review 证据中。

### 8.30 工具优先级与调用策略

对标 CodeQL、Sonar 等实践，建议建立“tool-first but not tool-only”原则。

#### 优先用工具的场景

1. 安全数据流与危险模式
2. 依赖漏洞
3. 代码复杂度与重复
4. API / Schema breaking changes
5. 覆盖率与测试变化
6. 迁移脚本与 SQL 问题

#### 优先用 LLM 推理的场景

1. 业务语义偏差
2. 抽象层级和架构意图
3. 测试断言是否真的有意义
4. 文档与实现是否一致

#### 混合策略

建议每条 finding 记录 `evidence_mode`:

- `tool_verified`
- `code_evidence_only`
- `knowledge_supported`
- `debate_verified`
- `human_confirmed`

这能帮助后续做误报分析。

### 8.31 低误报辩论与仲裁机制

多专家系统最常见的问题不是“发现不出问题”，而是“发现太多不值得提的问题”。

建议采用以下低误报机制:

1. 首轮独立审查
   - 防止相互污染判断

2. 争议项定向辩论
   - 只辩论高严重度、低证据或意见冲突项

3. Verifier 二次核验
   - 对 blocker / major 优先走工具或规则核验

4. Judge 只做采信，不重新审查
   - 避免仲裁器变成另一个泛化 reviewer

5. 高风险需人工确认
   - 特别是安全阻断、数据库迁移、架构重构、发布兼容性破坏

#### 仲裁规则建议

Judge 对每条 finding 至少检查以下维度:

1. 是否有明确代码定位
2. 是否有可复述的 claim
3. 是否有至少一种有效证据
4. 是否被其他专家反驳
5. 是否经过工具验证
6. 是否属于重复问题
7. 是否值得打扰开发者

第 7 点很关键。生产代码审核不是“尽可能多报问题”，而是“尽可能只报值得处理的问题”。

### 8.32 专家评估与反馈学习设计

建议为每个专家建立长期质量指标，而不只统计“发现数量”。

#### 核心指标

1. Precision
   - 被人工接受的 finding 占比

2. Recall Proxy
   - 后续 bug / 回滚 / 事故中，系统是否曾覆盖到相关风险

3. Actionability
   - 建议是否可执行，开发者是否能据此修复

4. Dedup Rate
   - 与其他专家重复的比例

5. Debate Survival Rate
   - 首轮 finding 在辩论和核验后仍保留的比例

6. Tool Confirmation Rate
   - 可被工具验证的问题中，有多少最终被工具支持

7. Human Escalation Accuracy
   - 被升级为人工确认的问题里，真正高风险的占比

#### 学习闭环

建议人工 reviewer 可以对最终意见打标签:

- 接受
- 拒绝
- 重复
- 证据不足
- 价值低
- 分类错误

然后把这些标签回写给:

- Expert Registry
- LangMem / 长期记忆
- 检索排序器
- 置信度权重模型

### 8.33 审核质量架构建议

在平台型架构之外，建议再补一层“质量控制架构”:

```text
Change Intake
  -> Context Builder
  -> Expert Reviews
  -> Debate Filter
  -> Evidence Verifier
  -> Judge
  -> Human Gate
  -> Reporter
  -> Feedback Learner
```

其中新增的关键组件是:

1. `Debate Filter`
   - 决定哪些问题值得进入辩论

2. `Evidence Verifier`
   - 负责 tool-first 核验

3. `Feedback Learner`
   - 负责把人工反馈转成专家质量改进信号

这三层是多专家系统从“好看”走向“好用”的关键。

### 8.34 推荐的质量落地顺序

建议不要一开始就把所有质量能力做到极致，而是分三步:

1. 第一步
   - 统一输出 schema
   - 工具优先策略
   - 基础辩论协议

2. 第二步
   - Expert Registry
   - 私有知识库
   - 用户 Markdown 知识绑定
   - 低误报仲裁逻辑

3. 第三步
   - 反馈学习
   - 专家级指标
   - 动态调权
   - 自动灰度和回滚某个专家版本

## 9. 审核流程设计

### 9.1 阶段一: 独立审查

每个专家仅基于:

- 变更 diff
- 必要源码上下文
- 自己的知识库
- 统一输出 schema

先独立产出 findings，避免一开始就互相污染判断。

输出建议结构:

```json
{
  "finding_id": "uuid",
  "title": "Potential transaction scope leak",
  "severity": "high",
  "confidence": 0.78,
  "file_path": "app/order/service.py",
  "line_spans": [[88, 121]],
  "category": "database",
  "claim": "Transaction is opened before network I/O and may hold row locks too long.",
  "evidence": [
    {
      "type": "diff",
      "content": "..."
    }
  ],
  "suggestion": "Move remote call outside transaction boundary.",
  "needs_debate": true
}
```

### 9.2 阶段二: 争议检测

系统自动识别以下情况:

- 两个专家对同一问题给出相反结论
- 同一问题证据不足
- 结论过强但置信度过低
- 发现高度重复
- 某个高风险改动没人覆盖

只有这些情况进入辩论环节，而不是让所有问题都辩论。

### 9.3 阶段三: 定向辩论

建议采用“议题驱动”的辩论，而不是全量自由聊天。

辩论单元 `DebateIssue`:

```text
- issue_id
- proposition
- supporting_findings[]
- opposing_findings[]
- required_experts[]
- max_rounds
- status
```

每个议题最多 2 到 3 轮:

1. 正方陈述
2. 反方质询
3. 必要时补证
4. Moderator 总结争议点

约束:

- 必须引用具体代码证据
- 不允许重复原话
- 不允许脱离当前议题扩散

### 9.4 阶段四: 证据核验

对高严重度或高争议 finding，增加独立核验:

- 规则库比对
- 知识库检索
- 静态分析工具结果核对
- 单元测试 / linters / schema checker / SQL explain 工具调用
- 与仓库既有模式对比

这里建议“LLM 判断”与“确定性工具判断”并存，减少幻觉。

### 9.5 阶段五: 仲裁与收敛

Judge Agent 不直接重新审代码，而是基于:

- 原始 finding
- 辩论记录
- 核验证据
- 历史误报模式

做以下动作:

- 采纳
- 驳回
- 合并
- 降级
- 升级
- 标记需人工确认

最终每条意见都带:

- 严重度
- 置信度
- 支持专家列表
- 反对专家列表
- 证据摘要
- 是否建议阻断合并

## 10. 知识库设计

### 10.1 知识库分层

建议将知识拆为四层:

1. 通用工程知识
   - 安全规范、性能规则、数据库最佳实践、DDD 规则

2. 组织级规范
   - 编码规范、架构原则、服务边界、发布规则

3. 仓库级知识
   - README、ADR、模块设计、目录约定、关键业务流程、常见坑

4. 反馈记忆
   - 历史误报、已接受例外、 reviewer 偏好、已知风险豁免

### 10.2 检索策略

建议每个专家拥有独立检索视角:

- 数据库专家优先检索 DB schema、migration、ORM 约定
- 安全专家优先检索 auth、middleware、security policy
- 业务专家优先检索需求说明、状态流转文档、接口契约

不要让所有专家共享完全相同的上下文，否则会降低角色差异。

## 11. 置信度模型设计

最终高置信输出不能只靠模型主观打分，建议综合以下信号:

1. 专家自评置信度
2. 多专家一致性
3. 反方挑战后是否仍成立
4. 是否存在明确代码证据
5. 是否被确定性工具验证
6. 是否命中知识库或历史同类缺陷
7. 是否被人工 reviewer 认可过

建议置信度公式采用可解释的加权模型，而不是黑盒:

```text
final_confidence =
  0.20 * self_confidence +
  0.20 * cross_expert_agreement +
  0.20 * evidence_strength +
  0.15 * verifier_score +
  0.15 * tool_validation_score +
  0.10 * historical_precision_score
```

后续可通过线上反馈逐步学习权重。

## 12. LangGraph 状态模型建议

建议核心状态使用结构化对象:

```text
ReviewState
- review_subject
- repository_snapshot
- diff_slices[]
- active_experts[]
- expert_findings[]
- debate_issues[]
- debate_transcript[]
- verified_findings[]
- final_findings[]
- review_report
- metrics
- human_actions[]
- checkpoint_metadata
```

关键要求:

- 每个阶段都可 checkpoint
- 任一节点失败可局部重试
- 全流程可回放和审计

## 13. 工具链建议

每个 Agent 不应只依赖语言模型，应具备工具能力:

- 平台 API 工具
  - 获取 diff
  - 获取文件内容
  - 获取 MR 评论
  - 发布 comment / check run

- Git 工具
  - 比较 branch
  - 获取 blame
  - 获取历史提交

- 代码分析工具
  - AST / tree-sitter
  - lint
  - test
  - coverage
  - dependency graph

- 检索工具
  - 向量检索
  - 关键词检索
  - 知识条目检索

- 观测工具
  - LangSmith tracing
  - metrics / logs / token accounting

## 14. 输出设计

### 14.1 平台侧输出

MR 模式建议输出三层结果:

1. 总结评论
   - 本次变更摘要
   - 风险总览
   - 关键阻断项

2. 行级评论
   - 仅对高置信且定位明确的问题下钻到行级

3. 审核报告链接
   - 包含完整争议过程、证据、专家分歧、置信度来源

Branch 模式建议输出:

- Markdown 报告
- JSON 结构化结果
- 可选回写平台状态检查

### 14.2 结果分级

建议 findings 分为四类:

- blocker: 建议阻断合并
- major: 建议修改后再合并
- minor: 建议优化
- info: 提示与观察

## 15. 前端用户界面设计

### 15.1 设计目标

参考 GitLab 的 MR review 流程、Sonar 的质量看板、以及 code scanning 产品的 triage 经验，前端界面需要同时满足以下目标:

1. 让作者快速理解“这次变更最值得先处理什么”
2. 让 reviewer 快速区分“高置信问题”和“低价值噪音”
3. 让管理员可配置专家、知识库、工具权限和质量策略
4. 让整个平台能清晰展示“专家讨论过程”和“最终裁决依据”

### 15.2 用户角色

前端默认服务 3 类用户:

1. 代码作者
   - 关注最终建议、行级评论、修复优先级、是否阻断合并

2. 审核者 / Maintainer
   - 关注争议点、证据质量、人工仲裁、批准与驳回

3. 平台管理员 / 规则管理员
   - 关注专家注册、知识库绑定、MCP / tool / skill 权限、质量策略和指标

### 15.3 信息架构

建议采用三大产品模块:

1. 审核工作台 (`Review Workbench`)
   - 面向单个 MR / branch 的审查主页面

2. 发现中心 (`Findings Center`)
   - 面向跨审查任务的 finding 检索、筛选、趋势和回放

3. 专家配置中心 (`Expert Studio`)
   - 面向专家定义、知识库绑定、工具权限和自定义专家管理

### 15.4 审核工作台设计

审核工作台是核心页面，建议采用“三栏布局”。

#### 左栏: 导航与概览

- 变更摘要
- 风险分布
- 专家执行状态
- 审核阶段时间线
- 文件树与模块树

左栏目标是让用户快速回答:

- 这次改了什么
- 哪些区域风险最高
- 审核执行到了哪一步

#### 中栏: 代码与对话流主区

- Diff / 文件视图
- 行级评论
- 实时对话流
- 线程化讨论
- 专家 finding 锚点
- 人工 reviewer 回复

这里应参考 GitLab 的 thread 模型:

- 每个议题单独成线程
- 行级评论和议题级评论分开
- 支持“已解决 / 待确认 / 需复审”

同时增加“实时对话流”模式:

- 按时间顺序展示专家发言和系统事件
- 支持切换“全局流”和“议题流”
- 支持在对话流中直接跳转到代码、证据和裁决

#### 右栏: 专家与证据面板

- 专家意见卡片
- 证据摘要
- 辩论记录
- Verifier 结果
- Judge 裁决
- 知识来源引用

右栏是多专家系统区别于普通 code review 的关键，需要把“为什么得出这个结论”展示清楚。

### 15.4.1 对话流式工作台设计

为了满足“实时展示各个专家讨论过程”，建议将审核工作台升级成“对话流工作台”。

#### 两层对话流模型

1. 全局审查对话流
   - 面向整个 MR / branch
   - 展示所有专家和系统组件的实时事件
   - 用于回答“系统现在正在发生什么”

2. 议题级讨论线程
   - 面向单个 finding 或 debate issue
   - 展示围绕某个问题的完整讨论、补证、核验和裁决
   - 用于回答“这个问题为什么成立或不成立”

#### 全局对话流应展示的事件

- 专家启动
- 专家读取上下文
- 专家生成初步判断
- 专家提出 finding
- 专家质疑其他专家
- 调用 tool / MCP / skill
- verifier 返回结果
- judge 给出裁决
- human gate 介入

#### 议题级线程应展示的消息

- 议题创建消息
- 支持方发言
- 反对方发言
- 补证消息
- 工具结果消息
- Moderator 总结
- Judge 结论
- 人工 reviewer 结论

### 15.4.2 对话流 UI 布局建议

建议在工作台中部提供两个可切换 Tab:

1. `代码视图`
   - 传统 diff + 行级评论

2. `对话流视图`
   - 类消息流界面，实时滚动展示专家讨论

对话流视图建议布局为:

- 左侧: 议题列表
- 中间: 对话流消息区
- 右侧: 证据 / 知识引用 / 裁决摘要

#### 消息卡片建议字段

每条消息卡片至少展示:

- 发言角色中文名
- 角色类型
  - 专家
  - verifier
  - judge
  - human
  - system
- 发言时间
- 当前动作类型
  - 提出问题
  - 反驳
  - 补证
  - 核验
  - 裁决
- 摘要内容
- 关联代码位置
- 关联知识引用
- 关联工具结果

#### 可视化语义建议

- 专家消息使用固定身份色
- 工具结果使用中性色
- verifier 使用蓝色语义
- judge 使用金色或强调色
- human reviewer 使用高对比色
- blocker 相关线程始终置顶

### 15.4.3 对话流交互能力建议

建议支持以下交互:

1. 实时自动滚动
   - 新事件进入时自动滚动到底部
   - 用户手动回看时暂停自动滚动

2. 按专家过滤
   - 只看安全专家、架构专家等

3. 按议题过滤
   - 只看某个 debate issue

4. 按事件类型过滤
   - 只看 finding、tool 调用、裁决等

5. 一键跳转代码
   - 点击消息直接定位到 diff 对应位置

6. 一键查看依据
   - 展开知识文档引用、工具输出和历史案例

7. 回放模式
   - 按时间回放整个审查过程

### 15.4.4 为什么需要对话流而不是只展示结论

这样设计有四个直接收益:

1. 提升可解释性
   - 用户能看到结论如何形成

2. 提升信任感
   - 多专家系统不再是黑盒

3. 便于人工接管
   - reviewer 能在辩论过程中介入，而不是只能看最终结果

4. 便于误报分析
   - 可以直接回看专家在哪一步判断失真

### 15.5 审核工作台核心区块

#### 顶部 Header 区

建议包含:

- 仓库 / MR / branch 标识
- 当前状态
  - 审核中
  - 需人工确认
  - 可合并
  - 建议阻断
- 风险总分
- 发现数量统计
- 专家运行情况
- 重新运行 / 增量运行按钮

#### 风险总览卡

建议以卡片方式展示:

- `blocker / major / minor / info`
- 高置信 finding 数
- 争议 finding 数
- 已验证 finding 数
- 需要人工确认数

#### 专家执行泳道

建议使用可视化泳道显示:

- 专家已启动
- 正在审查
- 已输出 finding
- 进入辩论
- 已被裁决
- 被跳过

这能帮助用户理解系统不是“一个黑盒大模型”，而是一个明确的多专家流程。

同时，泳道中的每一步都应可点击展开对应的对话流片段。

#### 争议与裁决面板

建议单独突出:

- 当前争议议题数
- 支持专家 / 反对专家
- 缺失证据
- 是否进入 verifier
- 是否进入 human gate

并支持“一键打开该议题的完整对话线程”。

#### 证据来源面板

每条 finding 都应显示:

- 代码证据
- 工具证据
- 知识文档引用
- 专家辩论摘要
- 最终裁决依据

### 15.6 Findings Center 设计

这是面向质量治理和回顾分析的页面。

建议支持以下视图:

1. Findings 列表
   - 按仓库、专家、严重度、状态、误报标签筛选

2. 趋势视图
   - 每周 blocker 数
   - 高置信问题趋势
   - 误报率趋势
   - 各专家 precision 变化

3. 回放视图
   - 查看某次审查里一个 finding 如何从初始意见演化到最终裁决
   - 支持按时间线重放多专家对话流

4. 对账视图
   - 系统 finding 与人工 reviewer 结论的差异

### 15.7 Expert Studio 设计

这是平台化能力的核心后台。

建议包含以下页面:

1. 专家列表页
   - 中文名 / 英文名
   - 内置 / 自定义
   - 启用状态
   - 版本
   - 最近效果指标

2. 专家详情页
   - 职责说明
   - 触发规则
   - 绑定知识源
   - 可用 tool / MCP / skill
   - prompt 模板
   - 输出 schema

3. 知识库管理页
   - 上传 Markdown / PDF
   - 绑定到专家
   - 查看 chunk、标签、版本和生效范围

4. 权限策略页
   - 配置工具白名单
   - 配置 MCP 白名单
   - 配置 skill 白名单
   - 配置预算和超时

5. 效果评估页
   - precision
   - debate survival rate
   - tool confirmation rate
   - dedup rate

### 15.8 用户自定义专家 UI 设计

建议通过“创建向导”而不是自由表单来降低配置门槛。

创建向导建议分 5 步:

1. 基本信息
   - 中文名
   - 英文名
   - 职责描述

2. 审查范围
   - 选择适用仓库
   - 选择触发路径 / 文件类型 / diff 关键词

3. 参考知识
   - 上传 Markdown
   - 绑定仓库文档
   - 绑定组织级规范

4. 可用能力
   - 选择 tool
   - 选择 MCP
   - 选择 skill

5. 运行策略
   - 模型
   - 最大工具调用数
   - 辩论轮数
   - 是否允许人工 gate

### 15.9 交互原则

建议遵循以下交互原则:

1. 线程化讨论
   - 参考 GitLab MR review，每个话题一个线程，便于关闭和复审

2. 风险优先
   - 默认先展示 blocker 和 high-confidence finding

3. 证据优先
   - 所有结论都能一键展开证据

4. 不确定性可见
   - 明确展示“争议中”“证据不足”“需人工确认”

5. 人工可接管
   - reviewer 能直接覆盖裁决、降级严重度、标记误报

6. 增量友好
   - 对二次提交只突出新问题、已解决问题和状态变化

### 15.10 页面清单建议

MVP 前端建议先做以下页面:

1. 审核工作台页
2. Findings 列表页
3. 专家列表页
4. 专家详情页
5. 知识库管理页
6. 审核运行详情抽屉 / 弹层
7. 议题对话流抽屉 / 全屏回放页

### 15.11 前端技术建议

若后续落地为 Web 平台，建议:

- React + TypeScript
- TanStack Router 或 Next.js App Router
- TanStack Query
- ECharts / Recharts 用于质量趋势图
- Monaco Diff Editor 或平台内嵌 diff 组件
- WebSocket 或 SSE 用于实时事件流
- 设计系统采用清晰、偏工程控制台风格，而不是营销式页面

界面风格建议:

- 信息密度较高，但层次清楚
- 以中性色和状态色为主
- 对 blocker / major / verified / debated 使用稳定色语义
- 动画只用于状态变化和面板过渡，不做过强装饰

### 15.12 实时事件流设计

为了支撑“对话流式展示”，后端应提供统一实时事件流。

#### 事件推送方式

建议优先采用:

1. SSE
   - 实现简单
   - 适合单向推送审查事件

2. WebSocket
   - 若后续需要更强实时交互、人工即时插话、协同标注，再升级为 WebSocket

MVP 建议先用 SSE。

#### 统一事件模型

```text
ReviewEvent
- event_id
- review_id
- issue_id
- slice_id
- actor_type
- actor_id
- actor_name_zh
- actor_name_en
- event_type
- summary
- payload
- created_at
- sequence_no
```

#### 推荐事件类型

- `review_started`
- `expert_started`
- `expert_context_loaded`
- `expert_thought_summary`
- `finding_created`
- `finding_updated`
- `debate_started`
- `debate_message`
- `tool_called`
- `tool_result`
- `verifier_result`
- `judge_decision`
- `human_gate_requested`
- `human_decision_recorded`
- `review_completed`

### 15.13 对话消息数据结构建议

建议把“消息”和“finding”区分开。

`Finding` 是结构化结论，`ConversationMessage` 是讨论过程中的事件与发言。

```text
ConversationMessage
- message_id
- review_id
- issue_id
- parent_message_id
- actor_type
- actor_name_zh
- message_type
- stance
- content_summary
- code_refs[]
- knowledge_refs[]
- tool_refs[]
- finding_ref
- created_at
```

#### 推荐消息类型

- `observation`
- `claim`
- `rebuttal`
- `support`
- `evidence`
- `tool_result`
- `moderator_summary`
- `judge_summary`
- `human_comment`

### 15.14 前端实时订阅架构建议

前端建议采用:

```text
Review Workbench
  -> subscribe(review_id)
      -> SSE/WebSocket Client
          -> Review Event Store
              -> Global Timeline View
              -> Issue Thread View
              -> Expert Lane View
```

这样可以做到:

- 全局时间线和议题线程共享同一事件源
- 支持断线重连和增量追平
- 支持回放模式和实时模式切换

### 15.15 LangGraph 事件发射设计

为了让前端能实时看到专家讨论过程，LangGraph 节点不应只写最终结果，还应在关键时点发射事件。

建议在以下节点发事件:

1. `run_expert_reviews`
   - 发 `expert_started`
   - 发 `finding_created`

2. `run_debate`
   - 发 `debate_started`
   - 发 `debate_message`
   - 发 `moderator_summary`

3. `run_verifier`
   - 发 `tool_called`
   - 发 `tool_result`
   - 发 `verifier_result`

4. `judge_findings`
   - 发 `judge_decision`

5. `human_gate`
   - 发 `human_gate_requested`
   - 发 `human_decision_recorded`

### 15.16 专家审核流程的对话流展示模板

每个专家在 UI 中都应以相同的流程模板展示，便于用户理解:

1. 启动
2. 读取上下文
3. 检索知识
4. 初步观察
5. 形成 claim
6. 调用工具或引用知识补证
7. 接受质疑或反驳他人
8. 更新结论
9. 进入裁决

建议在 UI 上以“消息流 + 状态节点”的方式组合展示，而不是只显示最终 finding 列表。

## 16. 最终实现蓝图

### 16.1 蓝图目标

最终实现蓝图需要同时满足:

1. 支持类 GitHub 平台的 MR / branch 审查
2. 支持多专家协同、辩论、核验、仲裁
3. 支持专家私有知识库和用户自定义 Markdown 知识
4. 支持 `tool / MCP / skill / agent-as-tool`
5. 支持前端工作台、治理后台、质量回溯和反馈学习

#### 首版存储约束

首版实现暂不引入外部数据库，统一采用“本地文件存储 + 进程内索引 + 可替换存储接口”的方式实现。

这意味着:

- 审查任务、事件流、finding、专家注册信息都落本地文件
- 知识文档和报告快照落本地目录
- 检索索引可先使用本地轻量索引
- 存储访问一律通过 Repository 抽象，便于后续替换为 Postgres / Redis / 向量库

### 16.2 总体系统拓扑

建议整体系统拆成如下模块:

```text
Platform Adapter Layer
  -> Review API
  -> Review Orchestrator (LangGraph)
  -> Context Builder
  -> Expert Registry
  -> Capability Gateway
  -> Knowledge Ingestion Service
  -> Knowledge Retrieval Service
  -> Evidence Verifier
  -> Judge / Reporter
  -> Feedback Learner
  -> Web UI
```

### 16.3 服务拆分蓝图

#### 1. `platform-adapter-service`

职责:

- 对接类 GitHub 平台 API
- 读取 MR、branch compare、comment、commit、file blob
- 回写 summary comment、line comment、check run

#### 2. `review-api`

职责:

- 接收 webhook 和手动触发请求
- 创建 review task
- 提供工作台和后台所需查询接口

#### 3. `review-orchestrator`

职责:

- 执行 LangGraph 主图
- 管理专家运行、辩论、核验、仲裁
- 通过 checkpointer 持久化线程状态

#### 4. `context-builder`

职责:

- 切片 diff
- 补充文件上下文
- 生成专家输入包
- 路由候选专家

#### 5. `expert-registry-service`

职责:

- 管理内置专家和自定义专家
- 下发专家 profile、prompt、schema、知识源、可用能力

#### 6. `capability-gateway`

职责:

- 统一访问 tools、MCP、skills、peer experts
- 做权限、预算、超时、重试和审计

#### 7. `knowledge-ingestion-service`

职责:

- 导入 Markdown / PDF / URL / repo docs
- chunking、embedding、索引和版本管理

#### 8. `knowledge-retrieval-service`

职责:

- 按专家和仓库做检索
- 融合共享知识、私有知识和用户知识

#### 9. `evidence-verifier-service`

职责:

- 调用 CodeQL、Semgrep、schema diff、coverage diff 等工具
- 为高风险 finding 生成验证结果

#### 10. `feedback-learner-service`

职责:

- 汇总人工反馈和结果标签
- 更新专家指标和长期记忆

#### 11. `web-console`

职责:

- 承载 Review Workbench、Findings Center、Expert Studio

### 16.3.1 单进程优先落地建议

考虑首版不引入外部数据库，建议 MVP 先采用“单后端进程 + 本地文件存储”的部署方式:

```text
web-console
  -> review-api
      -> orchestrator
      -> local repositories
      -> local knowledge index
      -> capability gateway
```

这样可以:

- 降低基础设施复杂度
- 先验证产品闭环和交互体验
- 保持未来服务拆分的可能性

### 16.4 LangGraph 主图蓝图

建议主图节点如下:

1. `ingest_subject`
2. `normalize_subject`
3. `slice_diff`
4. `build_context`
5. `route_experts`
6. `run_expert_reviews`
7. `merge_duplicates`
8. `select_debate_issues`
9. `run_debate`
10. `run_verifier`
11. `judge_findings`
12. `human_gate`
13. `publish_results`
14. `persist_feedback_seed`

#### 节点职责简述

- `ingest_subject`
  - 拉取平台对象和原始 diff

- `normalize_subject`
  - 统一成 `ReviewSubject`

- `slice_diff`
  - 生成可并行处理的 `DiffSlice`

- `build_context`
  - 为每个 slice 和专家构建上下文包

- `route_experts`
  - 依据 Expert Registry 和规则选择专家

- `run_expert_reviews`
  - 并行执行专家审查

- `merge_duplicates`
  - 合并高度相似 finding

- `select_debate_issues`
  - 仅选出值得辩论的问题

- `run_debate`
  - 执行定向辩论子图

- `run_verifier`
  - 对高风险问题做 tool-first 核验

- `judge_findings`
  - 最终采信、降级、驳回、合并

- `human_gate`
  - 人工确认高风险项

- `publish_results`
  - 发布评论和报告

- `persist_feedback_seed`
  - 为反馈学习写入初始记录

### 16.5 核心子图蓝图

#### 专家审查子图

```text
prepare_expert_input
  -> retrieve_expert_knowledge
  -> run_expert
  -> validate_schema
  -> score_finding_quality
```

#### 定向辩论子图

```text
create_debate_issue
  -> invite_supporting_expert
  -> invite_opposing_expert
  -> moderator_summary
  -> verifier_or_judge
```

#### 人工仲裁子图

```text
queue_human_review
  -> collect_human_decision
  -> update_final_finding
```

### 16.6 核心数据对象蓝图

建议至少定义以下核心对象:

```text
ReviewSubject
ReviewTask
DiffSlice
ExpertProfile
ExpertRun
Finding
DebateIssue
DebateMessage
VerificationResult
JudgeDecision
KnowledgeDoc
CapabilityBinding
HumanReviewAction
FeedbackLabel
```

#### 关键对象建议

`ExpertProfile`
- id
- name_zh
- name_en
- kind: builtin | custom
- trigger_rules
- knowledge_sources
- capability_bindings
- prompt_ref
- schema_ref
- model_policy
- enabled

`Finding`
- finding_id
- review_task_id
- slice_id
- reviewer_id
- reviewer_name_zh
- category
- severity
- confidence
- evidence_mode
- claim
- evidence
- suggestion
- status

`VerificationResult`
- finding_id
- verifier_name
- tool_name
- result: supported | not_supported | inconclusive
- summary

### 16.6.1 本地文件对象映射建议

在不使用外部数据库的前提下，建议每类核心对象都映射到独立 JSON 文件或 JSONL 文件。

推荐映射如下:

```text
ReviewTask            -> storage/reviews/{review_id}/review.json
ReviewEvent           -> storage/reviews/{review_id}/events.jsonl
Finding               -> storage/reviews/{review_id}/findings/{finding_id}.json
DebateIssue           -> storage/reviews/{review_id}/debates/{issue_id}.json
JudgeDecision         -> storage/reviews/{review_id}/judgments/{finding_id}.json
ExpertProfile         -> storage/experts/{expert_id}/expert.yaml
KnowledgeDoc          -> storage/knowledge/docs/{doc_id}.md
Knowledge metadata    -> storage/knowledge/meta/{doc_id}.json
FeedbackLabel         -> storage/reviews/{review_id}/feedback/{label_id}.json
```

### 16.7 存储蓝图

首版建议采用“本地文件存储为主”的混合方案:

1. Local File Storage
   - review task
   - finding
   - debate
   - expert registry
   - feedback labels
   - 原始 diff
   - Markdown / PDF 文档
   - 报告快照

2. In-Memory Cache
   - 运行态索引
   - SSE 订阅状态
   - 最近访问的 review 快照

3. Local Search / Embedding Index
   - 知识 chunk 索引
   - 可先用本地轻量实现，后续再替换为独立向量库

4. LangGraph Checkpointer / Local Memory Store
   - 线程状态
   - 长期记忆

#### 本地目录结构建议

```text
storage/
  reviews/
    rev_001/
      review.json
      subject.json
      events.jsonl
      report.md
      findings/
      debates/
      judgments/
      feedback/
      artifacts/
  experts/
    correctness_business/
      expert.yaml
      prompt.md
      schema.json
  knowledge/
    docs/
    meta/
    chunks/
    indexes/
  runtime/
    sessions/
    locks/
    checkpoints/
```

#### 本地文件存储实现建议

- 元数据文件用 `JSON`
- 事件流用 `JSONL`
- 专家定义用 `YAML + Markdown + JSON Schema`
- 报告用 `Markdown`
- 上传知识原文尽量保留原始文件

#### 文件一致性建议

- 单 review 目录内采用原子写入
- 使用临时文件 + rename 防止半写入
- 每个 review 目录单独加锁
- 事件流只追加，不原地修改

### 16.7.1 Repository 抽象建议

虽然首版用本地文件，但代码层必须抽象存储接口。

建议定义:

```text
ReviewRepository
ExpertRepository
KnowledgeRepository
EventRepository
FeedbackRepository
```

首版实现:

- `FileReviewRepository`
- `FileExpertRepository`
- `FileKnowledgeRepository`
- `FileEventRepository`
- `FileFeedbackRepository`

这样后续迁移数据库时，业务层和 LangGraph 层不需要重写。

### 16.8 接口蓝图

#### 外部接口

- `POST /api/reviews`
  - 创建 MR / branch 审查任务

- `GET /api/reviews/{id}`
  - 获取审查详情

- `GET /api/reviews/{id}/findings`
  - 获取 finding 列表

- `POST /api/reviews/{id}/rerun`
  - 重跑或增量重跑

- `POST /api/reviews/{id}/human-decisions`
  - 提交人工裁决

- `GET /api/experts`
  - 获取专家列表

- `POST /api/experts`
  - 创建自定义专家

- `POST /api/knowledge/docs`
  - 上传知识文档

#### 实时接口

- `GET /api/reviews/{id}/events/stream`
  - SSE 实时订阅审查事件

- `GET /api/reviews/{id}/events`
  - 拉取历史事件列表

- `GET /api/reviews/{id}/issues/{issue_id}/messages`
  - 拉取某个议题的对话消息

#### 内部接口

- `registry.get_expert_profile(expert_id)`
- `knowledge.retrieve(expert_id, review_context)`
- `capability.invoke(binding_id, input)`
- `verifier.verify(finding_id, strategy)`

### 16.8.1 API Schema 建议

#### `POST /api/reviews`

```json
{
  "subject_type": "mr",
  "repo_id": "repo_123",
  "project_id": "proj_01",
  "mr_id": "mr_456",
  "source_ref": "feature/order-timeout",
  "target_ref": "main",
  "triggered_by": "user_neo"
}
```

#### `GET /api/reviews/{id}` 响应示例

```json
{
  "review_id": "rev_001",
  "status": "running",
  "subject": {
    "subject_type": "mr",
    "source_ref": "feature/order-timeout",
    "target_ref": "main"
  },
  "summary": {
    "blocker": 1,
    "major": 3,
    "minor": 5,
    "info": 4
  },
  "active_experts": [
    "功能正确性与业务逻辑专家",
    "安全与合规专家"
  ]
}
```

#### `ReviewEvent` 响应示例

```json
{
  "event_id": "evt_102",
  "review_id": "rev_001",
  "issue_id": "issue_09",
  "actor_type": "expert",
  "actor_id": "security_compliance",
  "actor_name_zh": "安全与合规专家",
  "actor_name_en": "Security & Compliance Reviewer",
  "event_type": "debate_message",
  "summary": "新接口缺少权限检查，建议维持 major。",
  "payload": {
    "finding_ref": "f_102",
    "code_refs": [
      {
        "path": "src/api/internal/order.py",
        "start_line": 42,
        "end_line": 68
      }
    ]
  },
  "created_at": "2026-03-12T13:02:11+08:00",
  "sequence_no": 37
}
```

#### `ConversationMessage` 响应示例

```json
{
  "message_id": "msg_19",
  "review_id": "rev_001",
  "issue_id": "issue_09",
  "actor_type": "expert",
  "actor_name_zh": "架构与设计专家",
  "message_type": "rebuttal",
  "stance": "oppose",
  "content_summary": "当前改动仍在原模块内，没有形成新的跨层依赖。",
  "code_refs": [
    {
      "path": "src/service/order_service.py",
      "start_line": 88,
      "end_line": 121
    }
  ],
  "knowledge_refs": [],
  "tool_refs": [],
  "finding_ref": "f_102",
  "created_at": "2026-03-12T13:02:55+08:00"
}
```

### 16.9 前后端协作蓝图

前端与后端的协作建议分为三条主链路:

1. 审查执行链路
   - webhook / 手动触发
   - Review API 创建任务
   - Orchestrator 执行
   - WebSocket / SSE 推送状态到工作台

2. 配置治理链路
   - Expert Studio 修改专家与知识库
   - Registry 和 Knowledge Service 更新配置
   - 新任务按新配置生效

3. 反馈学习链路
   - reviewer 在工作台标记接受/误报/重复
   - Feedback Learner 汇总标签
   - 更新 Expert Profile 和记忆

### 16.9.1 前端页面与组件树建议

建议首版组件结构如下:

```text
AppShell
  ReviewWorkbenchPage
    ReviewHeader
    RiskSummaryCards
    ExpertLaneBoard
    WorkbenchTabs
      DiffPanel
      ConversationFlowPanel
        GlobalTimeline
        IssueThreadList
        ConversationMessageList
        MessageCard
        EvidenceDrawer
    RightSidebar
      ExpertInsightCard
      DebateSummaryCard
      JudgeDecisionCard
      KnowledgeReferenceCard
  FindingsCenterPage
    FindingsFilterBar
    FindingsTable
    FindingReplayDrawer
  ExpertStudioPage
    ExpertList
    ExpertEditor
    KnowledgeBindingPanel
    CapabilityPolicyPanel
```

#### 对话流关键组件建议

- `ConversationFlowPanel`
  - 负责全局流 / 议题流切换

- `GlobalTimeline`
  - 展示 `ReviewEvent`

- `IssueThreadList`
  - 展示当前所有 debate issue

- `ConversationMessageList`
  - 展示某个 issue 的消息流

- `MessageCard`
  - 统一渲染专家、tool、judge、human 等消息

- `EvidenceDrawer`
  - 展开代码片段、知识引用、工具结果

### 16.10 MVP 实现范围蓝图

建议 MVP 只做最小可闭环能力:

1. 支持 MR 和 branch 两种入口
2. 支持 6 个默认专家
3. 支持专家私有知识库和用户 Markdown 文档
4. 支持基础 tools 和少量 MCP
5. 支持定向辩论和 verifier
6. 支持工作台首页和专家配置基础页
7. 支持实时对话流和议题线程展示

#### MVP 推荐默认专家

- 功能正确性与业务逻辑专家
- 架构与设计专家
- 安全与合规专家
- 性能与可靠性专家
- 可维护性与代码健康专家
- 测试与验证专家

#### MVP 推荐工具集

- read_file
- search_code
- get_diff
- run_tests_metadata
- coverage_diff
- semgrep
- schema_diff

#### MVP 推荐 MCP / skill 范围

- 1 到 2 个 MCP server
  - Git 平台
  - 安全扫描或文档检索
- 2 到 3 个内置 skill
  - 安全规则检查
  - 数据库迁移风险检查
  - DDD 建模一致性检查

### 16.11 分阶段交付蓝图

#### Phase 1: 核心审查闭环

- Platform Adapter
- Review API
- LangGraph 主图
- 6 个默认专家
- Review Workbench 基础页

#### Phase 2: 平台化能力

- Expert Registry
- 自定义专家
- 知识摄取与用户 Markdown 文档绑定
- Capability Gateway
- Expert Studio

#### Phase 3: 质量治理

- Evidence Verifier 扩展
- Feedback Learner
- Findings Center
- 专家指标和灰度发布

#### Phase 4: 生态扩展

- 更多专项专家
- 更多 MCP / skill
- 多租户隔离
- 组织级治理和模板市场

### 16.12 实施建议总结

最终建议的落地策略是:

1. 先实现最小多专家审查闭环
2. 再把专家注册、知识库和能力接入平台化
3. 最后做反馈学习和质量治理

原因是:

- 先验证“多专家审查是否有价值”
- 再验证“平台化能力是否易用”
- 最后再优化“长期质量和运营效率”

### 16.13 LangGraph 事件发射伪代码

下面给出首版可直接落地的伪代码结构:

```python
def emit_event(event_repo, review_id, event_type, actor, summary, payload=None, issue_id=None):
    event = {
        "event_id": new_id("evt"),
        "review_id": review_id,
        "issue_id": issue_id,
        "actor_type": actor["type"],
        "actor_id": actor["id"],
        "actor_name_zh": actor["name_zh"],
        "actor_name_en": actor["name_en"],
        "event_type": event_type,
        "summary": summary,
        "payload": payload or {},
        "created_at": now_iso(),
        "sequence_no": event_repo.next_sequence(review_id),
    }
    event_repo.append(review_id, event)
    stream_hub.publish(review_id, event)


def run_expert_review(state, expert):
    emit_event(event_repo, state.review_id, "expert_started", expert.actor(), "开始审查")

    context = build_expert_context(state, expert)
    emit_event(event_repo, state.review_id, "expert_context_loaded", expert.actor(), "已加载上下文")

    findings = expert.review(context)
    for finding in findings:
        finding_repo.save(state.review_id, finding)
        emit_event(
            event_repo,
            state.review_id,
            "finding_created",
            expert.actor(),
            finding["title"],
            payload={"finding_ref": finding["finding_id"]},
            issue_id=finding.get("issue_id"),
        )


def run_debate_issue(state, issue):
    emit_event(event_repo, state.review_id, "debate_started", moderator.actor(), f"议题 {issue['issue_id']} 开始")
    for message in debate(issue):
        message_repo.save(state.review_id, issue["issue_id"], message)
        emit_event(
            event_repo,
            state.review_id,
            "debate_message",
            message["actor"],
            message["content_summary"],
            payload={"message_id": message["message_id"]},
            issue_id=issue["issue_id"],
        )


def run_verifier(state, finding):
    emit_event(event_repo, state.review_id, "tool_called", verifier.actor(), "开始调用验证工具")
    result = verifier.verify(finding)
    verification_repo.save(state.review_id, result)
    emit_event(
        event_repo,
        state.review_id,
        "verifier_result",
        verifier.actor(),
        result["summary"],
        payload={"finding_ref": finding["finding_id"], "result": result["result"]},
        issue_id=finding.get("issue_id"),
    )
```

## 17. 工程实现建议

### 17.1 技术栈建议

推荐 Python 技术栈:

- `langgraph`
- `langchain`
- `pydantic`
- `fastapi`
- `httpx`
- `orjson`
- `pyyaml`
- `watchfiles`
- `sqlite` 可选，仅作为本地索引或迁移过渡层，不作为首版必需
- 自研轻量任务执行器或进程内后台任务

原因:

- LangGraph Python 生态更成熟
- 更适合与代码分析、Git 工具、平台 webhook 服务集成
- 首版用本地文件存储可以显著降低部署复杂度

### 17.2 服务拆分建议

建议拆为 4 个服务:

1. `review-api`
   - 接收 webhook / 手动触发请求

2. `review-orchestrator`
   - 执行 LangGraph 流程

3. `knowledge-service`
   - 检索规则库和仓库知识

4. `reporter`
   - 回写平台评论、生成报告

MVP 阶段也可以先合并成单体服务，后续再拆分。

## 18. MVP 范围建议

建议首版不要一次做全，优先做以下闭环:

1. 支持 MR + Branch 两种入口
2. 支持 6 个默认专家:
   - 功能正确性与业务逻辑专家
   - 架构与设计专家
   - 安全与合规专家
   - 性能与可靠性专家
   - 可维护性与代码健康专家
   - 测试与验证专家
3. 支持独立审查 + 定向辩论 + 仲裁
4. 支持基础知识库检索
5. 支持 Markdown 报告 + MR 评论回写
6. 支持人工确认高风险项

暂缓项:

- 全自动修复建议
- 太多专家角色
- 自学习权重在线更新
- 跨 MR 长周期复杂记忆

## 19. 里程碑建议

### Phase 1: 可运行 MVP

- 平台 Token 接入
- MR / Branch compare
- diff 切片
- 6 个默认专家独立审查
- 一轮争议辩论
- Judge 合并输出

### Phase 2: 高质量增强

- 知识库检索
- 规则库
- 确定性工具核验
- 增量 review
- 人工审核中断点

### Phase 3: 生产治理

- 反馈闭环
- 误报学习
- 指标体系
- 审核回放
- 配置中心
- 多仓库隔离

## 20. 风险与应对

### 20.1 主要风险

1. Token 成本过高
2. 大 MR 上下文过载
3. 专家输出高度重复
4. 幻觉导致误报
5. 辩论回合失控
6. 业务专家缺乏足够知识支撑

### 20.2 应对策略

1. 先切片再审查，按风险和文件类型激活专家
2. 仅对争议项辩论，不对全部 findings 辩论
3. 强制结构化证据输出
4. 引入 verifier + 工具核验
5. 限制最大轮数和 token budget
6. 用仓库知识库与人工反馈持续校准

## 21. 推荐的实现蓝图摘要

最终推荐蓝图如下:

1. 用平台适配器统一 MR / Branch 变更输入
2. 用变更理解层做 diff 切片和上下文扩展
3. 用 LangGraph 顶层图控制审核生命周期
4. 用多个专家 Agent 独立产出 findings
5. 用 Debate Moderator 对冲突议题做定向辩论
6. 用 Evidence Verifier 做规则和工具核验
7. 用 Judge Agent 做最终采信与置信度合成
8. 用 Reporter 将结果回写到平台并生成完整报告
9. 用 Postgres + Checkpointer 持久化全流程状态
10. 用 LangSmith 观察质量、成本、耗时和误报率

## 22. 结论

对于“多个专家共同审核同一 MR / Branch，并通过充分讨论后输出高置信度意见”的目标，最合适的方案不是单 Agent reviewer，也不是完全自由的多 Agent 聊天，而是:

“基于 LangGraph 的分层式、多阶段、多专家协作审核图”

其关键成功因素是:

- 统一变更抽象
- 专家独立审查
- 争议驱动辩论
- 证据核验
- 仲裁收敛
- 持久化与人工介入

这套方案既吸收了现有 PR reviewer 工具的工程实践，也利用了 LangGraph 在多 Agent 编排、持久化、可控性和人机协作方面的优势，适合作为生产级多专家代码审核系统的总体架构。

## 23. 参考项目与资料

- qodo-ai/pr-agent: https://github.com/qodo-ai/pr-agent
- codedog-ai/codedog: https://github.com/codedog-ai/codedog
- tupizz/pr-code-analyzer: https://github.com/tupizz/pr-code-analyzer
- iason-solomos/Deb8flow: https://github.com/iason-solomos/Deb8flow
- instadeepai/DebateLLM: https://github.com/instadeepai/DebateLLM
- LangGraph Multi-agent network: https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/
- LangGraph Hierarchical Agent Teams: https://langchain-ai.github.io/langgraph/tutorials/multi_agent/hierarchical_agent_teams/
- LangGraph Supervisor: https://langchain-ai.github.io/langgraphjs/reference/modules/langgraph-supervisor.html
- LangGraph Persistence: https://langchain-ai.github.io/langgraph/how-tos/persistence-functional/
- LangGraph Multi-agent network functional API: https://langchain-ai.github.io/langgraph/how-tos/multi-agent-network-functional/
- GitHub About custom agents: https://docs.github.com/en/enterprise-cloud@latest/copilot/concepts/agents/coding-agent/about-custom-agents
- GitHub Custom agents configuration: https://docs.github.com/zh/copilot/reference/custom-agents-configuration
- GitHub Extending coding agent with MCP: https://docs.github.com/copilot/how-tos/use-copilot-agents/coding-agent/extend-coding-agent-with-mcp
- GitHub Configuring toolsets for the GitHub MCP Server: https://docs.github.com/copilot/how-tos/provide-context/use-mcp/configure-toolsets
- Google Looking for in a code review: https://google.github.io/eng-practices/review/reviewer/looking-for.html
- GitLab Code review guidelines: https://docs.gitlab.com/development/code_review/
- Sonar Clean Code definition: https://docs.sonarsource.com/sonarqube-server/latest/user-guide/clean-code/definition/
- Sonar Software qualities: https://docs.sonarsource.com/sonarqube-server/latest/user-guide/clean-code/software-qualities/
- GitHub About code scanning with CodeQL: https://docs.github.com/code-security/code-scanning/automatically-scanning-your-code-for-vulnerabilities-and-errors/about-code-scanning-with-codeql
- AWS Reliability Pillar: https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html
- langchain-ai/langmem: https://github.com/langchain-ai/langmem
- cryxnet/DeepMCPAgent: https://github.com/cryxnet/DeepMCPAgent
- claudioed/agent-blackboard: https://github.com/claudioed/agent-blackboard
- whiteducksoftware/flock: https://github.com/whiteducksoftware/flock
- Zackriya-Solutions/MCP-Markdown-RAG: https://github.com/Zackriya-Solutions/MCP-Markdown-RAG
