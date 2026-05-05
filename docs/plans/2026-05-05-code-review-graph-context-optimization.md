# 借鉴 code-review-graph 的关联上下文优化方案

## 背景

当前系统已经具备多专家检视、目标仓上下文检索、GitNexus 关联影响报告和 Java DDD 上下文补充能力。但关联上下文主要依赖 diff、文本搜索、命名约定和部分 GitNexus 输出，仍存在几个问题：

- 关键词搜索容易把无关文件带入上下文，增加误报。
- Java 调用方、被调方、接口实现和测试影响主要靠正则和路径启发式，关系不够稳定。
- GitNexus 影响报告与主检视链路仍相对独立，未充分反向驱动专家上下文。
- 大仓场景下缺少“最小关联上下文”机制，LLM 仍可能读到过多噪声。

`code-review-graph` 已经验证过一条更好的路径：用 Tree-sitter 解析代码结构，持久化本地图谱，基于变更计算 blast radius，再返回 token 友好的 minimal/review context。我们应按这个主链路改造，而不是只借鉴概念。

## code-review-graph 的具体做法

参考：

- <https://github.com/tirth8205/code-review-graph>
- <https://raw.githubusercontent.com/tirth8205/code-review-graph/main/README.md>
- <https://raw.githubusercontent.com/tirth8205/code-review-graph/main/pyproject.toml>

核心事项：

1. **Tree-sitter 解析**
   使用 `tree-sitter` 和 `tree-sitter-language-pack` 解析代码，提取函数、类、import、调用点、继承、测试等结构。

2. **本地 SQLite 图谱**
   图谱落在仓库本地 `.code-review-graph/` 目录，存储节点、边、文件哈希和统计信息，不依赖外部数据库或云服务。

3. **只索引必要文件**
   Git 仓优先使用 `git ls-files`，只索引 tracked files。额外通过 `.code-review-graphignore` 排除生成代码、vendor、构建产物等。

4. **增量更新**
   通过文件哈希和变更列表，只重新解析 changed files，并根据依赖关系补充更新 dependents，避免全仓重建。

5. **blast radius**
   变更发生后，从图谱追踪调用方、依赖方、测试和执行流，得到可能受影响的最小文件集合。策略偏 recall，宁可多带一点相关上下文，也不能漏掉真正受影响文件。

6. **minimal context / review context**
   LLM 不直接扫仓库，而是先读取极简结构摘要，再按需读取 token 优化后的 review context。

7. **可配置深度和预算**
   通过最大影响节点数、最大影响深度、BFS 深度和工具过滤控制上下文规模。

8. **边置信度**
   边带有 extracted / inferred / ambiguous 这类置信分层，便于区分强证据和候选关系。

9. **MCP 工具化**
   将 build/update graph、minimal context、impact radius、review context、query graph 等能力做成工具，供审核流程自动调用。

## 本系统目标架构

新增一层本地 AST 图谱能力，与 GitNexus 和现有 repo search 合流：

```text
MR diff
  -> 变更文件/变更符号提取
  -> Tree-sitter 本地图谱增量更新
  -> GitNexus 影响分析
  -> Context Graph Planner
  -> minimal context
  -> review context
  -> 专家检视 / 证据验证 / 影响报告
```

上下文来源统一为：

```text
gitnexus
tree_sitter
repo_search
llm_evidence
```

优先级：

```text
GitNexus ready + Tree-sitter ready:
  使用 GitNexus 做影响路径，Tree-sitter 做本地结构校验和片段回填。

GitNexus unavailable + Tree-sitter ready:
  Tree-sitter 成为主关联上下文来源。

Tree-sitter unavailable:
  回退当前 RepositoryContextService + JavaDddContextAssembler 启发式逻辑。
```

关联上下文搜索策略必须明确为：

```text
Tree-sitter 图谱搜索优先
  -> 如果命中结构化节点/边，使用 AST 关系回填上下文
  -> 如果没有命中、图谱未就绪或解析失败，再退化到关键词搜索
  -> 退化路径必须写入审核过程对话流
```

## 数据模型

### CodeGraphNode

```text
node_id
kind
name
qualified_name
language
file_path
line_start
line_end
parent_qualified_name
signature
return_type
modifiers
is_test
file_hash
metadata_json
updated_at
```

首批 `kind`：

```text
file
class
interface
enum
method
constructor
field
test_method
config
```

### CodeGraphEdge

```text
edge_id
kind
source_node_id
target_node_id
source_qualified_name
target_qualified_name
file_path
line_number
confidence
confidence_tier
metadata_json
updated_at
```

首批 `kind`：

```text
contains
calls
imports_from
extends
implements
references_type
injects
tested_by
depends_on
```

### ContextBundle

```text
changed_nodes
direct_context
upstream_callers
downstream_callees
contracts
implementations
dependent_files
test_context
config_context
paths
limitations
source_summary
```

这个结构会进入专家 prompt、问题证据验证和结果页展示。

## Java 首批解析范围

第一阶段只支持 Java，优先覆盖我们当前质量问题最多的场景。

Tree-sitter 节点：

```text
class_declaration
interface_declaration
enum_declaration
method_declaration
constructor_declaration
field_declaration
method_invocation
object_creation_expression
import_declaration
superclass
super_interfaces
annotation
```

重点关系：

- Controller -> ApplicationService
- ApplicationService -> Domain / Repository
- Interface -> Implementation
- Constructor injection / field injection
- Method caller / callee
- Test -> production method
- Repository / Mapper -> SQL / XML 配置

## 建图与更新策略

### 文件选择

优先使用：

```bash
git ls-files
```

叠加忽略规则：

```text
.code-review-graphignore
.ai-review.yml excluded_paths
系统默认排除: .git, node_modules, build, target, dist, generated, vendor, coverage
```

Windows 处理：

- 运行 `git ls-files` 时使用 list args，不使用 shell 拼接。
- 入库路径统一转成 `/`。
- Windows 盘符和反斜杠只保留在本地 repo root，不进入图谱相对路径。

### 全量建图

入口：

```text
CodeGraphIndexService.full_build(repository_id)
```

步骤：

1. 解析仓库配置，定位本地路径。
2. 读取 tracked files 和 ignore 规则。
3. 对支持语言文件计算 hash。
4. Tree-sitter parse。
5. 写入 nodes / edges / file_state。
6. 写入 `.code-review-graph/status.json`。

### 增量更新

入口：

```text
CodeGraphIndexService.update_changed_files(repository_id, changed_files)
```

步骤：

1. 根据 changed files 过滤可解析文件。
2. 删除这些文件旧节点和旧边。
3. 重新 parse changed files。
4. 找到直接 dependents，按预算补充更新。
5. 更新统计和状态。

## blast radius 查询

接口：

```text
CodeGraphQueryService.get_impact_radius(
  repository_id,
  changed_files,
  changed_symbols,
  max_depth=2,
  max_nodes=500
)
```

查询路径：

```text
changed file -> changed nodes
changed node -> reverse calls
changed node -> forward calls
changed node -> imports / dependents
changed node -> extends / implements
changed node -> tested_by
changed node -> config / SQL / XML references
```

输出：

```text
changed_nodes
caller_nodes
callee_nodes
dependent_files
test_nodes
context_paths
risk_hints
truncated
confidence_summary
```

## minimal context 与 review context

### minimal context

用于专家选择、初步分流和 prompt 预算规划。

内容：

- 变更类/方法签名。
- 直接 caller/callee 计数。
- 受影响文件 top N。
- 受影响测试 top N。
- 图谱来源和图谱新鲜度。
- 主要风险提示。

### review context

用于专家正式检视。

内容：

- 变更方法完整代码块。
- 关键 caller/callee 代码片段。
- 接口/实现片段。
- 注入与装配片段。
- 相关测试片段。
- SQL/XML/配置片段。
- GitNexus 影响路径与 Tree-sitter 本地关系的交叉验证结果。

## 对现有模块的改造

### 新增模块

```text
backend/app/services/code_graph/
  models.py
  storage.py
  java_tree_sitter_parser.py
  file_selector.py
  index_service.py
  query_service.py
  context_planner.py
```

### 改造 RepositoryContextService

保留现有文本搜索能力，但在 Java 项目中优先使用图谱：

```text
search_symbol_context()
  -> code_graph exact symbol lookup
  -> code_graph relationship lookup
  -> rg fallback
```

执行规则：

- 只要 `code_graph_enabled=true` 且目标仓图谱 ready，关联上下文先走 Tree-sitter 图谱。
- Tree-sitter 命中时，结果必须带 `context_source=tree_sitter` 和关系类型，例如 `calls`、`implements`、`references_type`。
- Tree-sitter 没有命中、图谱过期、图谱未建或解析失败时，才进入现有关键词搜索。
- 关键词搜索结果必须带 `context_source=keyword_search` 和 fallback 原因。
- 两类结果都进入统一 `ContextBundle`，但在 prompt 和结果页中区分来源。

### 改造 JavaDddContextAssembler

将这些方法迁移为 AST 图查询：

```text
_find_parent_contracts -> implements / extends
_find_callers -> reverse calls
_find_callees -> forward calls
_find_domain_models -> references_type + package community
_find_persistence_contexts -> repository/mapper/config edges
```

### 改造 ReviewRunner

在专家执行前增加：

```text
CodeGraphContextPlanner.build_context_bundle()
```

然后把 bundle 注入：

- 主 Agent 专家选择上下文。
- 专家 prompt。
- issue evidence validation。
- change impact report。

同时在过程记录对话流中追加结构化说明，避免用户不知道本次关联上下文来自哪里：

```text
event_type=code_graph_context_started
message=正在使用 Tree-sitter 代码图谱检索关联上下文

event_type=code_graph_context_ready
message=Tree-sitter 已命中关联上下文：变更节点 X 个，调用方 Y 个，被调方 Z 个，测试 N 个

event_type=code_graph_context_fallback
message=Tree-sitter 未命中有效关联上下文，已退化为关键词搜索

event_type=keyword_context_ready
message=关键词搜索已补充关联上下文：命中文件 X 个，命中片段 Y 个
```

对话流展示要求：

- 成功走 Tree-sitter 时，显示“已使用 Tree-sitter 结构化图谱”。
- 退化到关键词搜索时，显示明确原因，例如“图谱未建”“图谱过期”“未找到符号关系”“解析失败”。
- 如果 Tree-sitter 和关键词搜索都有结果，标注主来源和补充来源。
- 这些说明只展示在过程记录里，不进入正式问题清单。

### 改造 GitNexusImpactService

GitNexus 输出不再只进入影响报告，还要转换为统一 context path：

```text
GitNexus impacted_files -> ContextBundle.dependent_files
GitNexus impact_paths -> ContextBundle.paths
GitNexus recommended_tests -> ContextBundle.test_context
```

## 前端展示

结果页新增“关联上下文”信息，但正式问题仍放在核心位置。

每个正式问题展示：

```text
直接证据
关联证据
受影响调用链
受影响测试
上下文来源
```

设置页新增“本地代码图谱”状态：

```text
是否启用
最近建图时间
节点数
边数
tracked 文件数
ignore 规则数
增量更新状态
最近错误
```

## 配置建议

新增运行时配置：

```json
{
  "code_graph_enabled": false,
  "code_graph_languages": ["java"],
  "code_graph_max_impact_depth": 2,
  "code_graph_max_impact_nodes": 500,
  "code_graph_update_before_review": true,
  "code_graph_watch_enabled": false
}
```

默认关闭，避免影响现有用户。打开后：

- Java review 优先使用 Tree-sitter 图谱。
- GitNexus ready 时做交叉验证。
- 任何解析失败都 fail open，回退现有逻辑。

## 依赖与 Windows 可行性

新增 Python 可选依赖组 `code-graph`：

```text
tree-sitter>=0.23,<1
tree-sitter-language-pack>=0.3,<1
networkx>=3.2,<4
watchdog>=4,<6
```

安装：

```bash
pip install -e ".[code-graph]"
```

说明：

- `tree-sitter-language-pack` 提供预编译 grammar，减少 Windows 下手动编译成本。
- `networkx` 用于 blast radius、hub、bridge 和路径查询。
- `watchdog` 只用于可选 watch 模式，默认不开。
- SQLite 使用 Python 标准库 `sqlite3`，不需要额外安装数据库。

Windows 注意事项：

- Python 使用 3.11 或更高版本。
- 需要安装 Git for Windows，并确认 `git` 在 `PATH` 中。
- 不要求 bash，所有核心命令使用 Python subprocess list args。
- 路径入库统一为 Git 风格 `/`。

## 实施计划

### Phase 1: Java Tree-sitter 图谱基础

- 新增依赖和可选配置。
- 新增 SQLite storage。
- 新增 tracked files + ignore 文件选择器。
- 新增 Java parser。
- 新增 full build / status。
- 单元测试覆盖节点、边、ignore、Windows 路径。

### Phase 2: 增量更新与 blast radius

- 新增 changed files 更新。
- 新增 dependent files 查询。
- 新增 `get_impact_radius`。
- 限制 max depth / max nodes。
- 单元测试覆盖 caller、callee、implements、tested_by。

### Phase 3: minimal/review context 接入审核链路

- 新增 `CodeGraphContextPlanner`。
- 接入 `ReviewRunner`。
- 注入专家 prompt 和 issue validation。
- 保留现有 rg fallback。
- 在过程记录对话流展示 Tree-sitter 优先检索、命中统计和关键词搜索退化原因。

### Phase 4: Java DDD 上下文迁移

- 用 AST 图查询替换 caller/callee/contract。
- 加强 transaction、factory、aggregate、repository 风险验证。
- 用复杂 Java 用例评估误报和漏报。

### Phase 5: GitNexus 合流与前端展示

- GitNexus impact paths 转为 context paths。
- 结果页展示上下文来源。
- 设置页展示本地图谱状态。
- 增加全流程 UI 测试。

## 验收指标

- Java 复杂用例正式问题召回率提升。
- 人工驳回率下降。
- 关联影响文件 recall 不低于当前 GitNexus-only 方案。
- 单次 review 上下文 token 数下降。
- GitNexus 不可用时仍能输出结构化关联上下文。
- Windows 下可以完成建图、增量更新和审核回退。

## 风险与控制

- Tree-sitter 解析不完整：使用 fallback，且在 limitations 中展示。
- Tree-sitter 无命中但关键词有命中：在过程记录对话流展示退化原因，避免误认为系统没有使用结构化上下文。
- 图谱过期：review 前轻量 update，并展示状态。
- 上下文过宽：通过 max depth / max nodes / token budget 限制。
- Windows 依赖安装失败：依赖使用预编译 grammar，README 单独写清 Git 和 Python 要求。
- 与 GitNexus 重复：统一 ContextBundle，不维护两套展示口径。

## 2026-05-05 落地进展

已完成：

- `CodeGraphStorage` 增加 `analyze_change_impact`，支持 changed nodes、BFS 影响半径、候选受影响文件、测试覆盖缺口和风险评分。
- `search_related_context` 返回 `minimal_context` 和 `impact_analysis`，让审核链路先拿图谱摘要，再展开关联片段。
- `CodeGraphContextPlanner` 在过程事件中携带 minimal context、impact preview、命中片段和 fallback 原因。
- `ReviewRunner` 将 Tree-sitter 图谱摘要、影响分析和关联片段合入专家 prompt 上下文。
- Java parser 增加 `record_declaration`、`references_type` 和 `tested_by` 边，覆盖 Java 17+ DomainEvent/DTO 和 JUnit 测试线索。
- 前端过程记录展示 Tree-sitter 检索方式、风险概览、变更节点、候选受影响文件、测试覆盖缺口和命中片段。
- 新增离线 benchmark：Tree-sitter 平均可检视性评分 90.72，关键词搜索 71.67，噪声片段从 12 降到 3。
- 吸收 `code-review-graph` 的 diff range 映射方式：解析 unified diff 的新增/删除行号范围，优先用行号与 AST 节点范围重叠来确定 changed nodes，避免整文件粗筛放大误报。
- 吸收 `code-review-graph` 的调用边解析思路：Java parser 会从 import、字段、构造器注入、方法参数和局部变量推断类型，把 `service.create()` / `repository.save()` 解析成可跨文件匹配的完整目标符号，并让 `tested_by` 边指向真实生产方法。
- 继续对齐 `code-review-graph` 的运行链路：本次评审 `workspace_repo_path/.code-review-graph/graph.db` 优先于配置仓库路径，避免多仓/临时工作区场景下误退化为关键词搜索。
- 当符号搜索未命中但 diff 行号已映射到 Tree-sitter 节点时，仍用 changed node、relationship edge 和 impacted node 生成关联上下文，不再把有效 AST 证据误判为无命中。
- 风险评分改为更接近 `code-review-graph` 的节点优先级模型：按变更节点计算调用方、跨文件调用、测试覆盖缺口、安全敏感命名和关系参与度，再取最高风险作为整体优先级。

仍待增强：

- GitNexus impact path 与 Tree-sitter BFS 结果的统一排序和冲突标记。
- Java 调用目标仍需继续增强 Spring Bean、Mapper/XML、泛型接口和多实现选择。
- 设置页展示 `.code-review-graph/graph.db` 状态和最近构建时间。

## 2026-05-05 五阶段优化实施记录

本轮按 `code-review-graph` 的核心链路继续落地五个阶段：

1. **检视上下文包 v2**
   - `ReviewRunner` 在专家执行前调用 `CodeGraphContextPlanner.build_context_bundle()`。
   - 上下文包写入 `code_graph_source_summary`、`code_graph_minimal_context`、`code_graph_impact_analysis`、`code_graph_related_contexts` 和 `code_graph_evidence_chain`。
   - 命中 Tree-sitter 时优先注入 AST 图谱上下文；无图谱、无命中或异常时退化到关键词搜索，并保留 fallback 原因。

2. **专家审查风险优先**
   - 专家任务新增 `code_graph_priority_score`。
   - 派工队列按图谱风险优先级排序，高风险变更节点、调用链入口和测试缺口优先进入专家检视。
   - 批量任务合并时保留最高图谱风险分，避免高风险 hunk 被低风险批次淹没。

3. **正式问题证据链**
   - `ReviewFinding` 新增 `context_source` 和 `evidence_chain`。
   - finding 证据链包含 claim、diff anchor、Tree-sitter minimal context、changed node、graph relationship、review priority、affected flows。
   - issue 聚合时透传 finding 的图谱证据链，并标记 `tree_sitter_context=true`、`tool_name=tree_sitter_code_graph`。

4. **Java 业务流程/入口影响摘要**
   - `CodeGraphStorage.analyze_change_impact()` 新增 `affected_flows` 和 `affected_flow_count`。
   - minimal context 会输出候选影响流程，用于专家 prompt、问题详情和过程记录展示。
   - 影响流程优先识别 Controller/Resource/Endpoint/Listener/Consumer/Scheduler/Application 等入口。

5. **前端展示与评估**
   - 过程记录对话流展示“Tree-sitter 结构化检索 / 关键词搜索”、风险优先级、变更节点、候选受影响文件、测试覆盖缺口、候选影响流程和命中片段。
   - 问题详情侧栏展示关联检索方式、图谱风险、图谱摘要、优先检查点、候选影响流程和证据链。
   - 离线对比脚本验证：Tree-sitter 平均可检视性评分 90.72，关键词搜索 71.67，评分提升 19.06；噪声片段从 12 降到 3。

验证命令：

```bash
.venv/bin/python -m pytest backend/tests/services/test_code_graph_storage.py backend/tests/services/test_code_graph_context_planner.py backend/tests/services/test_code_graph_index_service.py backend/tests/services/test_java_tree_sitter_parser.py backend/tests/services/test_diff_excerpt_service.py backend/tests/services/test_review_runner.py::test_review_runner_records_code_graph_context_events_in_process_flow backend/tests/services/test_review_runner.py::test_review_runner_prefers_workspace_code_graph_db_from_review_metadata backend/tests/services/test_review_runner.py::test_review_runner_build_finding_code_context_contains_diff_and_related_context backend/tests/services/test_review_runner.py::test_review_runner_sorts_expert_jobs_by_code_graph_priority backend/tests/services/test_review_runner.py::test_review_runner_enriches_issues_with_graph_evidence_chain -q
npm --prefix frontend run typecheck
.venv/bin/python scripts/compare_code_graph_context_quality.py
```
