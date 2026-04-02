# Context Compression Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不牺牲检视质量的前提下，降低主 Agent、专家 Agent、工具链和页面接口在 Python 进程中的上下文构造、复制、序列化和持久化开销，重点缓解 Windows 下的内存峰值和超时问题。

**Architecture:** 采用“结构化压缩而非二次 LLM 压缩”的方案。上下文压缩只做四类事情：按角色分层、去重复、做结构化摘要、按需加载；绝不直接删除专家真正需要的规范、规则、目标代码和关键关联上下文。压缩策略优先落在主 Agent、工具结果映射、持久化 payload 和高频页面接口，专家正式审查阶段继续保留完整证据包。

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, React, TypeScript, pytest

---

## 1. Constraints

### 1.1 Hard constraints

- 不影响已有业务逻辑
- 不降低检视质量
- 不影响结果页、过程页和控制台现有展示
- 不删除专家真正用于判断的输入

### 1.2 Expert input must remain complete

每个专家最终拿到的输入必须继续包含：

- 专家绑定规范
- 命中规则与绑定文档
- 语言通用规范提示
- 目标问题代码原文
- 关联源码上下文
- 工具产出的关键结构化证据

### 1.3 Compression must not rely on another LLM

默认不增加“先调一个 LLM 帮忙压缩”的链路。压缩以工程化、可预测、可测试的结构化裁剪为主，只在极少数离线知识预处理场景考虑预摘要。

---

## 2. Current bottlenecks

### 2.1 Main Agent selection is too heavy

虽然已经做过一轮减重，但主 Agent 阶段仍会持有：

- 关键 diff
- 相关变更摘要
- Java 质量信号
- 专家画像
- 语言规范提示

这一阶段不应承担专家级上下文。

### 2.2 The same context exists in multiple places

当前同一份上下文容易同时存在于：

- prompt
- message metadata
- finding.code_context
- tool_result
- replay/report serialization

这会导致内存占用不是“信息量”本身，而是“同一信息的多份副本”。

### 2.3 Tool outputs are richer than what the model/page actually needs

尤其是：

- repo_context_search
- Java inspectors
- rule screening

原始输出很重，但真正被模型和页面消费的只是一小部分。

### 2.4 Persistence format is still too text-heavy

当前数据库的主要膨胀点已经证明是：

- messages.metadata_json
- findings.payload_json

这意味着除了运行时对象，持久化副本本身也在加剧内存峰值。

---

## 3. Design principles

### 3.1 Compress repetition, not evidence

优先压这些内容：

- 重复 diff
- 重复源码片段
- 重复规则说明
- 重复工具原始 payload
- 重复页面回放对象

不压这些内容：

- 目标问题代码
- 关键关联代码
- 触发结论的规则
- 核心工具证据

### 3.2 Use role-based context budgets

不是所有 Agent 都需要同一份上下文。

- 主 Agent：轻上下文，只做选专家和路由
- 专家 Agent：完整证据包，做深审
- 页面接口：只返回当前页面真正需要展示的最小视图

### 3.3 Prefer structured summaries over free-form excerpts

所有长文本都优先变成结构化摘要卡，而不是简单截断。

例如：

- 规则卡：`rule_id/title/priority/trigger_reason/checklist`
- 工具卡：`signal/top_contexts/risk_summary`
- 上下文卡：`path/symbol/line_start/end/snippet/why_selected`

### 3.4 Compression must be verifiable

压缩后必须可验证：

- 专家输入完整性是否仍然满足
- 结果页是否仍能展示同样的信息
- benchmark 分数是否没有显著下降

---

## 4. Compression architecture

### 4.1 Layer A: Main Agent lightweight context

主 Agent 只保留：

- 变更文件列表
- 关键 target hunk
- 相关变更摘要
- 语言通用规范提示
- 高价值结构化信号
- 专家画像摘要

主 Agent 不再保留：

- 完整源码片段
- 关联源码全文
- runtime tool 原始结果
- 完整规则/文档正文

### 4.2 Layer B: Expert evidence pack

专家继续拿完整证据包，但证据包内部要去重：

- 目标代码只保留一份 canonical block
- 关联上下文只保留最相关 `2-4` 段
- 规则与规范优先给摘要卡，高风险规则再补局部原文
- 工具结果统一转成结构化 summary，再拼 prompt

### 4.3 Layer C: Runtime tool result normalization

每个工具输出统一拆成三层：

- `full`: 仅当前专家 prompt 使用
- `compact`: 仅 metadata / replay 摘要使用
- `display`: 仅页面展示使用

禁止直接把 `full` 层对象整包落到 message metadata 或 finding payload。

### 4.4 Layer D: Shared context sections

为 review 级别建立共享上下文段：

- `target_diff_section`
- `source_context_section`
- `related_context_sections`
- `rule_summary_sections`

`finding.code_context`、`message.metadata`、`report/replay` 不再重复持有全文，而是持有：

- section id
- path
- kind
- line range
- display snippet

页面展示时按 section id 还原。

---

## 5. Concrete compression strategies

### 5.1 Prompt compression

#### Main Agent

- 保留关键 diff
- 保留结构化信号
- 去掉源码级全文上下文
- 去掉重复的规则/文档正文

#### Expert Agent

- 必须保留完整证据
- 但按主题分块并限制重复：
  - 规则卡
  - 目标代码块
  - 关联上下文块
  - 工具信号块

#### Important

不按“总字符数硬截断”压 prompt，避免截掉关键证据。

### 5.2 Tool result compression

对 `repo_context_search`、Java inspectors、rule screening：

- 保留 `signals`
- 保留 `top_contexts`
- 保留 `matched_rules`
- 保留 `risk_summary`

不保留：

- 全量 raw definitions
- 全量 raw references
- 全量内部调试对象

### 5.3 Message metadata compression

保留：

- 页面和过程页真正使用的字段
- tool summary
- input completeness
- matched rule summary

不保留：

- 原始 repository_context 大对象
- 原始 runtime_tool_results 大对象
- 原始知识库全文对象

### 5.4 Finding payload compression

保留：

- 当前代码展示所需块
- 关联上下文展示所需块
- 结论相关 rule/signal summary

共享：

- 大段 diff
- 重复源码片段
- review 级上下文块

### 5.5 Page/API compression

继续坚持：

- `snapshot` 高频
- `review/replay/artifacts` 按需
- `messages` 用过程页白名单视图
- `report` 不全量读取 messages

---

## 6. Safety guardrails

### 6.1 Input completeness gate

压缩后仍必须通过：

- `review_spec_present`
- `language_guidance_present`
- `target_file_diff_present`
- `source_context_present`
- `related_context_count > 0`

若缺失：

- 不允许高置信度结论
- 自动降级为 `risk_hypothesis`

### 6.2 Review quality gate

每次压缩改动后必须跑：

- 通用 Java case
- DDD Java case
- benchmark 评分
- 过程页/结果页实际打开检查

### 6.3 Display compatibility gate

页面字段契约保持不变：

- 结果页摘要
- 当前代码
- 关联上下文
- 过程页消息
- 回放页时间线

压缩只改后端装载与存储方式，不改前端语义。

---

## 7. Rollout plan

### Phase 1: Main Agent compression

- 进一步把主 Agent 固定在“轻 diff + signal + expert profile”
- 不触碰专家正式审查输入

### Phase 2: Tool summary normalization

- 统一工具的 `full/compact/display` 三层输出
- 去掉原始大对象落库

### Phase 3: Message metadata slimming

- 逐步减少 `messages.metadata_json`
- 以过程页真实消费字段为准

### Phase 4: Finding shared sections

- 引入 review 级共享上下文块
- `finding.code_context` 改为 section 引用

### Phase 5: Replay/report dedup

- 继续减少 replay / report 的重复对象
- 保持页面展示一致

---

## 8. Metrics

### 8.1 Runtime metrics

- `rss_mb`
- `delta_mb`
- request duration
- replay/report/messages endpoint latency

### 8.2 Storage metrics

- `messages.metadata_json` total bytes
- `findings.payload_json` total bytes
- `reviews.subject_json` total bytes
- `app.db` size

### 8.3 Quality metrics

- benchmark score
- required expert coverage
- required rule hit
- finding keyword coverage
- input quality coverage

### 8.4 UX metrics

- 过程页是否报错
- 结果页是否报错
- 回放页是否报错
- 首页是否再出现 `timeout of 120000ms exceeded`

---

## 9. Risks

### Risk 1: Over-compressing related contexts

风险：
- 模型看不到关键调用链

缓解：
- 关联上下文至少保留 `2-4` 段
- 优先保留带 `why_selected` 的高相关段

### Risk 2: Shared sections break display compatibility

风险：
- 页面读取不到旧字段

缓解：
- 先兼容双写
- 前端不变，后端先提供相同展示结构

### Risk 3: Tool summary loses critical evidence

风险：
- 工具输出被压得太干

缓解：
- `full` 层仍然存在，只是不再到处复制

---

## 10. Recommended implementation order

1. 主 Agent 继续减重
2. 工具结果三层化
3. `messages.metadata_json` 瘦身
4. `finding.code_context` 共享段
5. replay/report 去重复
6. Windows 内存与首页超时复测

---

## 11. Success criteria

达到以下条件才算压缩成功：

- Windows 下首页不再频繁出现 `timeout of 120000ms exceeded`
- `rss_mb` 峰值明显下降
- `app.db` 增长速度明显下降
- Java benchmark 分数无显著回退
- 过程页 / 结果页 / 回放页无新展示异常
- 专家输入完整性门禁仍然全部通过
