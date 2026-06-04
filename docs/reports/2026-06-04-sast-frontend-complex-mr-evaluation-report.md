# SAST 前端复杂 MR 检视评估报告

## 目标

验证静态工具是否已经从旁路信号变成检视过程中的证据源，并重点观察：

- SAST/linter/report 工具是否进入 `tool_observations`
- 专家是否被要求消费工具观察项
- 工具观察项是否能转化为 finding / issue
- 最终检视结果是否足以评估整体效果

## 复测样本

样本仓库：`sast-demo-local-mr`

重点变更：

- `.semgrep.yml`
- `eslint.config.js`
- `src/web/adminPanel.ts`
- `src/main/java/demo/UserController.java`
- `src/main/java/demo/UserDao.java`
- `src/main/java/demo/OrderWorkflow.java`
- `target/spotbugsXml.xml`
- `target/surefire-reports/TEST-demo.ArchitectureTest.xml`

涉及工具：

- Semgrep
- ESLint
- SpotBugs report
- ArchUnit/Surefire report

## 运行记录

### rev_7eeb9516

标题：`复杂 MR：验证静态工具采纳链路（NO_PROXY 重跑）`

状态：`closed / closed`

结果：

- findings：6
- issues：0
- `expert_tool_observation_scan`：2
- 关闭原因：用户手动关闭

观察到的工具相关 finding：

- `静态工具候选需复核：java.logging.authorization-token`
  - adopted observation：`sast:semgrep:java.logging.authorization-token:src/main/java/demo/UserController.java:9`
  - `sast_cross_validated=true`
- `静态工具候选需复核：java.sql.concat-user-input`
  - adopted observation：`sast:semgrep:java.sql.concat-user-input:src/main/java/demo/UserController.java:9`
  - `sast_cross_validated=true`
- `静态工具候选需复核：SQL_INJECTION_JDBC`
  - adopted observation：`sast:spotbugs:SQL_INJECTION_JDBC:src/main/java/demo/UserDao.java:14`
  - `sast_cross_validated=true`
- `静态工具候选需复核：ArchUnit_OrderWorkflow_must_not_swallow_domain_errors`
  - adopted observation：`sast:archunit:ArchUnit_OrderWorkflow_must_not_swallow_domain_errors:src/main/java/demo/OrderWorkflow.java:1`

结论：工具观察项已经进入专家链路，并能形成 finding；但任务被手动关闭，未进入最终 issue 收敛，不能作为完整检视效果结论。

### rev_e1bed0a4

标题：`复杂 MR：静态工具端到端效果评估（standard）`

状态：`closed / closed`

结果：

- findings：0
- issues：0
- 关闭原因：用户手动关闭

结论：该任务完成了 intake、专家选择、派工和影响分析，但没有专家 finding / issue 输出，不能作为工具效果评估样本。

### rev_ccd52f4c

标题：`前端复杂 MR：SAST 工具采纳闭环复测`

状态：`failed / failed`

结果：

- findings：1
- issues：1
- 失败原因：外部 LLM 网关 DNS / 连接失败，最终系统判定“未产生任何真实 LLM 调用”

观察到的结果：

- finding：`静态工具候选需复核：rule`
- issue：`静态工具候选需复核：rule`
- 文件：`/private/tmp/mcra-sast-mr-demo/repo/src/web/adminPanel.ts`

结论：静态工具候选可以在前端变更中进入 finding/issue 收敛候选，但当前模型服务不可达导致任务未能以正式 completed/human_gate 状态结束。

### rev_2ee096a7

标题：`前端复杂 MR：SAST 工具采纳闭环复测（mocked-live-3）`

状态：`completed / completed`

结果：

- mocked live LLM calls：12
- findings：0
- issues：0

结论：流程可完成，但 mocked-live fallback 没有产生有效专家确认，不适合作为工具采纳效果正样本。

## 整体判断

当前实现侧已经完成：

- SAST 默认开启
- 工具状态诊断
- 工具结果规范化为 observation
- 专家 prompt 包含 `TOOL_OBSERVATION_REVIEW_ONLY`
- 工具 observation 覆盖缺失会记录 schema error
- 语义匹配后才做 SAST 交叉验证
- Governance 暴露工具观察数、采纳率、确认率、交叉验证数和误报率

当前评估侧尚未完全完成：

- 尚无一条真实 LLM 完整跑完、且包含工具采纳 issue 的 `completed` 或 `human_gate` 复测记录
- 当前外部模型网关不可达会阻断真实端到端评估
- mocked-live 只能证明流程终态，不能证明模型实际检视质量

## 工具效果评估

有效信号：

- Semgrep 能提供敏感日志、SQL 拼接、XSS 类候选
- SpotBugs report 能提供 Java SQL 注入候选
- ArchUnit/Surefire report 能提供架构/领域错误吞噬类候选
- 工具候选被写入 `adopted_tool_observations` 后，可以参与 SAST cross validation

不足：

- 工具 observation scan 对 observation id 格式较敏感，`semgrep:rule` 与 canonical `sast:semgrep:rule:file:line` 不一致时会触发 schema error
- 若专家输出未覆盖全部 observation，工具候选会被记录为失败，而不是稳定转成高质量 finding
- 当前最终效果依赖可用 LLM；模型不可达时只能看到 deterministic fallback 的部分能力

## 下一步验收条件

要把该方案标为“全部完成”，还需要补一条真实端到端记录：

1. 使用可达 LLM 网关重新运行前端/SAST 复杂 MR
2. 任务状态达到 `completed` 或 `human_gate`
3. `/issues` 至少包含 1 条来自工具 observation 且经专家语义确认的问题
4. 报告中保留原始代码位置、问题描述和修复建议
5. Governance 指标出现非 0 的工具观察数和采纳/确认数据

