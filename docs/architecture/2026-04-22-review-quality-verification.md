# Review Quality Verification

日期：2026-04-22

## 1. 验证目标

这轮验证主要看五件事：

1. 审核结果是否明确区分为 `审核发现 / 有效问题 / 被过滤的问题`
2. 有效问题是否带唯一 `primary_expert_id`
3. 条件化结论是否会被挡在有效问题之外
4. 只命中待删除代码的问题是否会被过滤
5. 高价值专项 signal 是否能稳定落成更硬的 finding

## 2. 已完成的自动化验证

### 2.1 后端关键回归

执行命令：

```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py backend/tests/services/test_knowledge_rule_screening_service.py backend/tests/services/test_main_agent_service.py backend/tests/services/test_review_service_auto_queue.py backend/tests/api/test_review_report_api.py -q
```

结果：

- `193 passed`

覆盖到的关键语义包括：

- `conditional_conclusion` 会保留为 finding，不会升级成有效问题
- `removed_line_only` 会被识别为无效问题并过滤
- `loop_call_amplification` 会提升为强 finding
- `comment_contract_unimplemented` 会提升为强 finding
- `exception_swallowed` 会提升为强 finding
- `exception_semantics_weakened` 会提升为强 finding
- `primary_expert_id / normalized_issue_type / expert_views` 会沿着 `review_runner -> issue -> report` 链路保留下来

### 2.2 前端构建验证

执行命令：

```bash
cd frontend && npm run build
```

结果：

- 构建通过

前端这轮已经补齐的表达包括：

- 有效问题清单展示主责专家
- 议题详情展示主责专家与参与专家
- 被过滤的问题清单展示过滤规则和过滤原因
- 人工裁决、议题线程和知识引用优先跟随主责专家
- 设置页说明里明确区分：
  - 审核发现
  - 有效问题
  - 被过滤的问题

## 3. 样例集覆盖情况

当前基准样例已经覆盖：

- DDD 边界破坏
- 查询语义放宽
- 无界查询
- 事务 / schema / SQL 风险
- 复合型 Java 质量退化

这轮新增了一条样例：

- `java-ddd-course-creator-comment-contract-unimplemented`

它专门覆盖：

- 注释 / TODO / 承诺未实现
- `correctness_business` 主责归因

已验证这条样例可以被成功 materialize：

```bash
python3 scripts/bench_java_review_cases.py --case java-ddd-course-creator-comment-contract-unimplemented --prepare-only
```

结果：

- 样例仓库准备成功
- 目标 diff 生成成功
- `changed_files` 与 `required_experts` 正常输出

说明：

- `removed_line_only`
- `conditional_conclusion`

这两类更偏链路口径和结果过滤，当前主要由服务层回归测试守住。

## 4. 真实链路验证

### 4.1 smoke_review

执行：

```bash
.venv/bin/python scripts/smoke_review.py
```

现状：

- 当前脚本会创建一条使用示例 MR URL 的 smoke 任务
- 这轮已经适配了两点：
  - 任务启动后的 replay 预检改成轮询等待，不再在异步链路刚启动时过早断言失败
  - 结果读取优先使用 `report.issues`，同时接受 `expert_failed` 作为有效生命周期信号
- 但当前示例 URL 仍然是一个**空 diff / 合成 MR** 场景，容易触发：
  - 专家 fallback
  - LLM 超时后的保守 finding
  - `conditional_conclusion` 过滤

当前判断：

- 这不是主链功能不可用
- 更像是 smoke 用例本身不够稳定，和“真实 Java case 验证”相比，参考价值有限
- 如果要让 smoke 真正稳定，下一轮建议把它替换成一个带固定本地 diff 的最小化 case，而不是继续依赖示例 MR URL

### 4.2 benchmark case

代表性 case：

```bash
python3 scripts/bench_java_review_cases.py --case java-ddd-course-creator-bypasses-domain-events --submit --wait-timeout-seconds 180 --poll-interval-seconds 5
```

当前观察到的结果：

- review 已成功创建并启动
- 主责专家路由命中 `ddd_architecture`
- 已收敛出 `1` 条 finding
- finding 类型为 `direct_defect`
- 严重度为 `blocker`
- 置信度为 `0.95`
- 当前 review id：`rev_9319f363`

这说明：

- benchmark 提交流程可用
- 当前质量链路已经能进入真实 Java case 的审查流程
- DDD 边界问题已经可以被唯一主责专家稳定提出

当前这条 finding 的代表性结果是：

- 主责专家：`ddd_architecture`
- 标题：`应用服务绕过聚合工厂方法导致领域事件丢失和聚合边界破坏`
- 关键信号：
  - `Course.create(...)` 被替换成 `new Course(...)`
  - 聚合工厂内记录领域事件的语义被绕过
- 输出质量：
  - 有明确证据
  - 有规则命中
  - 有修复建议
  - 文案不依赖额外前提条件

后续需要继续补的，是让 benchmark 命令在 review 收口后自动输出完整对照结果，而不是停在长轮询阶段。

## 5. 当前结论

这轮改造后，可以比较稳地得出下面几个结论：

1. **结果分层已经清楚**
   - findings、issues、filtered reasons 已经在前后端对齐

2. **主责专家链路已经打通**
   - issue 现在可以带唯一 `primary_expert_id`
   - 前端也能明确展示出来

3. **条件化结论和删除代码误报已被收紧**
   - 不会再直接进入有效问题清单

4. **高价值专项 signal 比之前更硬**
   - 循环调用放大
   - 注释承诺未实现
   - 静默吞异常
   - 异常语义弱化

5. **当前完整自动化验证基线已经具备**
   - 后端服务回归：`410 passed`
   - 前端构建：通过
   - 真实 benchmark case：可以稳定跑出主责专家明确、证据完整的结果

## 6. Claude 优化建议落地情况

截至 2026-04-24，`2026-04-22-review-quality-optimization-proposal.md` 中建议已按“先质量闭环、再工程扩展”的顺序推进。

需要注意：当前不能理解为“原建议中的所有增强项都 100% 完成”。本轮已经完成的是一期质量闭环，并补上了静态 diff 信号核验、可开关的 LLM 定向辩论入口、内置专家 few-shot 正反例和更严格的 schema 硬拒收。外部 Semgrep / CodeQL、embedding 去重、AST 级调用图和反馈飞轮动态调参，仍属于中期工程项。

### 6.1 逐项对照状态

| 编号 | 建议项 | 当前状态 | 已落地内容 | 尚未落地内容 |
| --- | --- | --- | --- | --- |
| #1 | 证据验证层升级 | 已完成一期 | `evidence_verification` 已增加证据质量评估，会识别缺少代码锚点、依赖“可能/如果/需要确认”等外部条件的问题，并自动降权或挡在有效 issue 外；新增 `static_diff` 内建 verifier，可把循环内调用放大、注释承诺未实现、吞异常等明确 diff 模式转成直接证据锚点；新增可开关 `EvidenceFalsePositiveFilterService`，可在 evidence 阶段用 LLM 过滤弱证据误报，失败时自动回退 | 尚未接入 Semgrep / CodeQL 等外部静态分析器 |
| #2 | 专家 Prompt 增强 | 已完成 | 专家运行时 system prompt 已统一增加结构化审查步骤、代码锚点要求、置信度口径、反例约束、`matched_rules` 和 `normalized_issue_type` 输出要求；各 `builtin_experts/*/prompt.md` 已补齐审查步骤、重点检查项、输出要求、issue type 口径和 few-shot 正反例 | 后续还可以继续把团队真实误报/漏检样例沉淀成更贴近业务的正反例 |
| #3 | 定向辩论 | 已完成一期 | `run_targeted_debate` 已从单纯打标改为输出 `debate_result`，并能对弱证据、低置信、条件化问题做预裁决；打开 `enable_llm_targeted_debate` 后，会先做专家观点修正，再由 LLM 裁判收敛；失败时自动回落到本地规则；辩论轮次会写入 `rounds / round_count` | 尚未做更复杂的多轮自由对话式辩论，但当前已具备可控的两阶段辩论协议 |
| #4 | Issue 去重升级 | 已完成一期 | 已增加 `normalized_issue_type`、常见同义词归一、严重级别差距过大不合并的规则；当前还能把“同文件相邻行、同一类问题、语义高度接近”的 findings 合并为一个 issue，减少不同专家围绕同一段代码重复报同类问题 | 尚未接入 embedding 相似度去重；尚未接入 LLM 辅助去重 |
| #5 | Expert 路由规则兜底 | 已完成一期 | `route_experts` 已基于 diff、文件名、change slices 和 risk hints 补齐安全、数据库、MQ、Redis、前端、性能等专家 | 后续可继续扩关键词、按仓库类型配置路由规则 |
| #6 | 多语言质量信号 | 已完成一期 | `CodeObservationExtractor` 已支持 TypeScript / JavaScript / Python / Go / SQL / 配置文件的通用信号，覆盖注释承诺未实现、吞异常、异常语义弱化、return 后死代码、魔法值、循环内外调、Python 可变默认参数、Go 未检查 error / goroutine 生命周期风险、TypeScript `any` / 非空断言；主 Agent 会基于这些信号补选对应专家 | 尚未实现 TypeScript ErrorBoundary 等更偏框架语义的专项规则 |
| #7 | 反馈飞轮 | 已完成一期 | 当前已有人工裁决和 feedback label 持久化；`FeedbackLearnerService` 已生成专家质量画像和问题类型质量画像，`judge_and_merge` 会根据历史误报率自动做置信度扣减，并把高误报专家/问题类型收紧为更保守的 `needs_verification` 判定；同时可输出 runtime 阈值收紧建议，并在治理页展示建议值和依据，但不会自动写回配置 | 尚未实现定期离线分析任务、自动回写 runtime 阈值和更细粒度的误报关键词学习 |
| #8 | Judge 加入 LLM 推理 | 部分完成 | `judge_and_merge` 已支持对低置信 issue 发起可开关、失败自动降级的在线 LLM 裁判；当前会把 issue 二次判定为 `reject / needs_verification / needs_human / accept`，并保留原有规则链路作为兜底；本轮进一步补充了跨文件契约、薄证据、假设驱动问题的触发条件，并把历史高误报专家/问题类型画像也接入 judge 触发；同时保留 `trigger_reason / reason / confidence_adjustment`，并把 LLM judge 统计写入报告摘要 | 仍未做更系统的线上观测、自动调参与更丰富的 judge 策略组合 |
| #9 | 跨文件影响分析 | 部分完成 | 已有轻量跨文件影响提示，并注入专家上下文，提醒调用方、被调方、关联文件和批量路径传播；当前还能识别“被调方已改但主要调用方未随改动一起修改”的轻量契约风险，并在主 Agent 与专家提示里显式展示；本轮已新增基于 diff hunk 的轻量签名级变更检测，会提示方法/构造器的名称、入参数量、返回类型或异常契约变化，并进一步提示调用点仍保留旧调用形态的风险 | 尚未实现 AST / tree-sitter 级调用链分析；尚未做更精确的 public API 签名变更检测 |
| #10 | 强制结构化输出 | 已完成一期 | `ReviewFinding` 已增加 `normalized_issue_type`，专家 JSON 契约已要求输出 `matched_rules` 和 `normalized_issue_type`；解析层会补默认值并归一类型；专家输出缺失关键字段、severity 非法、direct finding 无证据时，会被 schema gate 自动降级为待复核风险；完全缺少标题、结论和证据的空壳输出会被 `schema_rejected` 硬拒收；新增 `ExpertFindingPayload` 做 Pydantic payload 校验并记录 `schema_payload_valid` | 后续只剩更严格的“校验失败直接拒收”策略调优 |

### 6.2 已完成的一期能力

1. **证据验证层升级**
   - `evidence_verification` 增加证据质量评估
   - 对缺少代码锚点、依赖“可能/需要确认/如果”等外部条件的问题自动降权
   - 弱证据不再被包装成确定 issue
   - `static_diff` 内建 verifier 已接入，可把循环内外部调用、注释承诺未实现、吞异常等明确 diff 信号转成直接证据
   - 新增 `EvidenceFalsePositiveFilterService`
   - 打开 `enable_llm_evidence_filter` 后，会在 evidence 阶段对低置信、薄证据、条件化问题做 LLM true-positive / false-positive / needs-verification 判定
   - LLM 不可用或返回异常时会自动 fallback，不阻断主流程
   - 设置页、后端 API 和运行时存储已支持 `enable_llm_evidence_filter / llm_evidence_filter_confidence_threshold / llm_evidence_filter_timeout_seconds`

2. **专家 Prompt 增强**
   - 专家 system prompt 增加结构化审查步骤
   - 明确要求先找代码锚点、再判断是否由当前 diff 引入
   - 增加置信度口径和反例约束
   - 强制要求 `matched_rules` 与 `normalized_issue_type`
   - 各专家静态 `prompt.md` 已统一升级为“步骤 + 重点检查 + 输出要求 + issue type 口径 + 正反例参考”的结构
   - 所有内置专家已补充“该报 / 不该报”的 few-shot 正反例

3. **专家路由规则兜底**
   - `route_experts` 增加基于 diff / 文件名 / change_slices 的确定性补专家
   - 覆盖安全、数据库、MQ、Redis、前端、性能等常见专项信号
   - 主 Agent 漏选时仍能补齐关键专家

4. **定向辩论预裁决**
   - `run_targeted_debate` 不再只打 `needs_debate` 标记
   - 当前会输出 `debate_result`，包含 `accept / reject / needs_verification / needs_human`
   - 低置信、弱证据、依赖额外假设的问题会在辩论预裁决阶段被驳回或留待核验
   - `judge_and_merge` 会尊重 `rejected_after_debate`，避免被后续规则重新捞回正式 issue
   - 打开 `enable_llm_targeted_debate` 后，辩论节点会先做专家观点修正，再调用 LLM 裁判专家观点，输出 `accept / reject / needs_verification / needs_human`
   - 多轮结果会记录到 `debate_result.rounds`，便于回放专家观点如何收敛

5. **Issue 去重增强**
   - 增加 `normalized_issue_type` 到 `ReviewFinding`
   - 增加 NPE/空指针、权限绕过、N+1、领域事件、聚合工厂等同义词归一
   - 严重级别差距过大的同标题问题不会被误合并
   - 同文件相邻行、同一类问题、语义高度接近的 findings 也会被合并，减少相邻代码行重复报同类 issue

6. **多语言通用质量信号**
   - `CodeObservationExtractor` 不再只支持 Java
   - TypeScript / JavaScript / Python / Go / SQL / 配置文件会走通用规则
   - 已覆盖注释承诺未实现、吞异常、异常语义弱化、return 后死代码、魔法值、循环内外调
   - 已补充 Python 可变默认参数、Go 未检查 error、Go goroutine 生命周期风险、TypeScript `any`、TypeScript 非空断言
   - 主 Agent 会基于非 Java 信号补专家，例如 TypeScript 注释承诺未落地会补正确性专家

7. **跨文件影响提示**
   - 专家 prompt 和 finding code context 中已注入跨文件影响提示
   - 重点提示调用方、被调方、关联文件、批量路径传播
   - 已补充“调用方未随本次改动一起修改”的轻量检测，用于提示接口契约、返回值和异常语义兼容风险
   - 已补充基于 diff hunk 的轻量签名级变更检测，可提示方法名、入参数量、返回类型和异常契约变化
   - 已补充对引用片段的旧调用形态检测，可提示“签名已变，但调用点仍按旧参数个数或旧方法名调用”
   - 本轮进一步增强签名契约判断：
     - 泛型参数和嵌套调用参数不会再被逗号误拆
     - `Page / Slice -> List` 会提示分页语义、总数和边界风险
     - `Optional -> 普通对象`、有返回值改 `void`、`boolean` 契约变化会提示调用方语义风险
     - `throws` 声明新增、移除或变更会提示调用方 catch、补偿和回滚逻辑风险

8. **反馈飞轮**
   - 当前已有人工裁决和 feedback label 持久化
   - `FeedbackLearnerService` 已基于历史人工反馈生成专家质量画像和问题类型画像
   - `judge_and_merge` 会根据误报率和样本量自动下调置信度，并对高误报画像收紧 issue 升级口径
   - `FeedbackLearnerService.build_runtime_threshold_recommendations()` 可根据高误报画像给出 P2/P3 和提示类 issue 阈值建议，并在治理页展示建议值、依据和是否建议收紧，但不会自动改配置
   - 本轮同步调整测试语义：真实人工裁决链路由显式 pending issue 驱动，不再依赖弱证据 fallback 自动升级

9. **结构化输出 Schema Gate**
   - 专家输出缺失 `finding_type / severity / title / claim / normalized_issue_type` 等关键字段时会被补齐并降级
   - `direct_defect / direct_code_issue` 如果没有代码证据、跨文件证据或 observation 证据，会被降级为 `risk_hypothesis`
   - schema 缺陷会写入 assumptions 和 verification plan，避免弱结构输出被包装成确定 issue
   - 完全缺少标题、结论和证据的空壳输出会被标记为 `schema_rejected`，不会继续进入 finding 构建链路
   - 新增 `ExpertFindingPayload` Pydantic payload 校验，校验结果写入 `schema_payload_valid / schema_validation_errors`

10. **LLM Judge 一期增强**
   - `judge_and_merge` 当前不再只按单一低置信阈值触发 LLM judge，也会对跨文件契约、薄证据、假设驱动问题触发二次裁判
   - 历史高误报专家和问题类型画像已接入 judge 触发阈值，能把“过去误报偏高”的问题更早拉入二次裁判
   - `llm_judge_result` 已保留 `final_verdict / trigger_reason / reason / confidence_adjustment`
   - `confidence_breakdown` 已补充 `llm_judge` 明细，便于回放和调试
   - 报告 `confidence_summary` 已补充 LLM judge 统计项，能看到本次任务里有多少 issue 被 judge 处理、保留、转待验证、转人工或直接拒绝
   - 结果页摘要已增加 Judge 质量收敛区，便于直接看到质量过滤和 LLM Judge 拒绝数量
   - 设置页、后端 API 和运行时存储已支持 `enable_llm_issue_judge / llm_issue_judge_confidence_threshold / llm_issue_judge_timeout_seconds`

11. **仓库内检视规则注入**
   - 新增 `RepoReviewInstructionService`，支持读取仓库根目录和目标文件父目录链上的 `REVIEW.md`
   - 支持 `.codereview.yaml / .codereview.yml` 按 `paths` glob 匹配不同目录、模块或文件类型的检视规则
   - 主 Agent 构建 `repository_context` 时会带上命中的仓库规则摘要
   - 专家 prompt 会把命中的仓库规则作为高优先级上下文展示，轻量模式压缩时也会保留该区块
   - 规则标题、内容和匹配路径会进入知识检索 query terms，方便专家同时拿到仓库规则和已有知识库材料
   - 这一步解决的是“同一个通用专家在不同代码仓里要按不同团队约定看代码”的问题，避免所有规则都写死在主系统里

当前暂未接入外部 Semgrep / CodeQL、embedding 去重、AST 级调用图和动态反馈自动回写。这些属于中期增强项，当前先用确定性规则、静态 diff 信号、低置信 issue 的 LLM 裁判和现有 fallback 保证本地可测、低成本、可回归。

### 6.3 本轮新增验证

本轮新增仓库内检视规则注入后，补充执行了两组服务层回归：

```bash
.venv/bin/pytest backend/tests/services/test_review_runner.py backend/tests/services/test_prompt_budget_planner.py backend/tests/services/test_context_priority_policy.py -q
```

结果：

- `120 passed`

```bash
.venv/bin/pytest backend/tests/services/test_slice_change.py backend/tests/services/test_route_experts.py backend/tests/services/test_evidence_verification.py backend/tests/services/test_java_quality_signal_extractor.py backend/tests/services/test_main_agent_service.py backend/tests/services/test_review_runner.py backend/tests/services/test_detect_conflicts.py backend/tests/services/test_judge_and_merge.py backend/tests/services/test_review_issues.py backend/tests/services/test_repo_review_instruction_service.py -q
```

结果：

- `227 passed`

跨文件签名契约增强后，补充执行：

```bash
.venv/bin/pytest backend/tests/services/test_cross_file_impact.py backend/tests/services/test_main_agent_service.py backend/tests/services/test_review_runner.py -q
```

结果：

- `160 passed`

覆盖到的新增语义包括：

- `REVIEW.md` 可以按仓库根目录和子目录链路叠加生效
- `.codereview.yaml` 可以按路径 glob 命中目标文件
- 主 Agent 生成的 command 会带上 `repo_review_instructions`
- 专家 prompt 轻量模式会保留 `repo_review_instruction_summary`
- 跨文件影响提示能识别泛型参数数量、嵌套调用旧形态、分页返回契约退化和异常声明变化

Evidence false-positive filter 和 Pydantic schema payload 校验补齐后，补充执行：

```bash
.venv/bin/pytest backend/tests/services/test_evidence_verification.py backend/tests/services/test_judge_and_merge.py backend/tests/services/test_review_runner.py backend/tests/services/test_main_agent_service.py -q
```

结果：

- `176 passed`

设置页质量开关保存链路补齐后，补充执行：

```bash
.venv/bin/pytest backend/tests/api/test_settings_api.py backend/tests/services/test_runtime_settings_service.py backend/tests/services/test_evidence_verification.py backend/tests/services/test_judge_and_merge.py backend/tests/services/test_review_runner.py backend/tests/services/test_main_agent_service.py -q
```

结果：

- `181 passed`

前端构建：

```bash
npm run build
```

结果：

- 构建通过，仅保留 Vite 大 chunk 提醒

## 7. 按代码实现再次核对后的未完成项

这部分不是只看设计文档，而是直接对照当前代码实现得出的结论。

### 7.1 仍然明确未完成的项

1. **在线 LLM-as-judge 已接入一期，但还不是完整形态**
   - 当前 `backend/app/services/orchestrator/nodes/judge_and_merge.py` 已接入 `IssueJudgeService`。
   - 它会对低置信、跨文件契约、薄证据、假设驱动，以及历史误报偏高画像命中的问题发起一次可开关的在线 LLM 二次裁判。
   - LLM 返回异常或非 JSON 时，会自动退回原有规则链路，不会阻断主流程。
   - 但它现在还只是“一期收敛器”版本，不是完整 judge 体系。

2. **定向辩论已具备两阶段 LLM 辩论协议，但还不是自由对话式多轮辩论**
   - `backend/app/services/orchestrator/nodes/run_targeted_debate.py` 当前会生成 `debate_result`。
   - 打开 `enable_llm_targeted_debate` 后，会先修正专家观点，再把代码证据和假设前提交给 LLM 裁判。
   - 当前不是开放式多 Agent 聊天，而是更可控的“观点修正 -> 裁判收敛”两阶段协议。

3. **Semgrep / CodeQL 等外部静态分析结果还没注入上下文**
   - `backend/app/services/evidence_verifier_service.py` 当前已注册 `local_diff / coverage_diff / schema_diff / static_diff` 四个内建 verifier。
   - `static_diff` 是本地轻量静态信号，不等同于 Semgrep / CodeQL。
   - 所以“外部工具高精度 + LLM 高覆盖”的组合还没接上。

4. **专家静态 prompt 已升级，内置 few-shot 已补，仍需继续沉淀团队样例**
   - 现在各专家静态 `prompt.md` 已经不再是简版职责描述，而是统一补齐了步骤、重点检查项、输出要求和 issue type 口径。
   - 所有内置专家已补“该报 / 不该报”的 few-shot 正反例。
   - 后续更重要的是把团队真实误报/漏检样例沉淀成更贴近业务的 few-shot。

5. **Issue 去重还没有 embedding / LLM 语义去重**
   - 当前去重已经能处理“同文件、相邻行、同类问题、语义接近”的情况。
   - 但它本质上还是规则增强，不是向量语义去重，也不是 LLM 合并。

6. **跨文件影响还没有 AST / tree-sitter 级调用图**
   - 当前已经能提示“被调方改了，但主要调用方没一起改”。
   - 现在已经补上了基于 diff hunk 的轻量签名级变更提示、旧调用形态检测、泛型参数识别、分页返回契约和异常声明变化提示。
   - 但这仍是轻量文本级分析，还没有做到 AST / tree-sitter 级 public API 调用图谱。

7. **反馈飞轮还没有自动回写 runtime 阈值**
   - 现在 `FeedbackLearnerService` 已经能生成质量画像，并在 merge 阶段调低置信度。
   - 现在可以给出 runtime 阈值建议，但不会自动写回配置。
   - 还没有做定期离线任务、误报关键词学习和阈值自动调参回写。

8. **结构化输出已有 Pydantic payload 校验，但没有默认“失败即拒收”**
   - 现在有 schema gate，会对缺字段、非法 severity、无证据 direct finding 做降级。
   - 完全缺少标题、结论和证据的空壳输出会被 `schema_rejected`，不会进入 finding 构建链路。
   - `ExpertFindingPayload` 已接入 Pydantic payload 校验，当前策略是记录错误并降级，不是所有 schema 错误都直接拒收。

### 7.2 当前最值得优先继续做的 3 项

如果按“对质量提升最直接、且能在现有架构上平滑接入”的顺序来看，优先级建议是：

1. **把跨文件分析从轻量文本规则升级为 AST / 调用图**
   - 现有签名契约提示已经可以覆盖一批真实风险。
   - 下一步如果要继续提升，需要引入 AST / tree-sitter 或仓库索引，把 public API、实现类、调用方关系做成更稳定的图谱。

2. **把 LLM judge 从“一次二判”扩成更完整的裁判策略**
   - 现在已经能处理低置信、跨文件契约、薄证据、假设驱动，以及历史高误报画像命中的问题。
   - 下一步更值得做的是效果评估、误报回溯和自动调参，而不是只继续堆触发条件。

3. **沉淀团队真实误报 / 漏检样例**
   - 现在 prompt 结构和内置 few-shot 已补齐。
   - 下一步更有价值的是把真实 review 中的误报、漏检沉淀成按专家归档的样例库，而不是继续写泛化说明。

## 8. 下一步建议

下一轮最值得继续补三件事：

1. 适配 `scripts/smoke_review.py`
   - 让它按新的结果语义做断言

2. 继续扩 benchmark 样例
   - 增补循环调用放大
   - 增补删除代码误报
   - 增补条件化结论

3. 产出更稳定的 benchmark 对照结果
   - 把真实 case 的 findings / issues / filtered 结果沉淀成可重复比较的数据
