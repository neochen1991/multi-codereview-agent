# 代码检视质量优化方案 v2

> 目标：1) 提高检视质量——不漏检、不多检；2) 基于 GitNexus 给出每个 MR 的关联影响分析

---

## 一、业界关键经验总结

| 来源 | 核心经验 | 适用性 |
|------|---------|--------|
| **CodeRabbit** | 任务图(Task Graph)多链路探索，验证Agent二次复核，自动检测项目编码规范文件 | ⭐⭐⭐ 高度适用 |
| **Qodo/PR-Agent** | 跨仓影响追踪，自适应Token感知补丁压缩，单次LLM调用+深度验证 | ⭐⭐⭐ 高度适用 |
| **SonarQube AI** | 规则引擎(SAST) + LLM 混合架构，AI发现与确定性扫描交叉验证 | ⭐⭐⭐ 高度适用 |
| **Greptile** | RAG全仓库图谱，多轮扫描(Multi-pass)，变更后重扫 | ⭐⭐ 中度适用 |
| **Google Gemini** | 2M token上下文窗口，交互式追问(`/gemini review`) | ⭐⭐ 中度适用 |
| **微软** | AI作为"第一道筛选"，Human-in-the-loop为核心，94%团队要求代码检视 | ⭐⭐⭐ 高度适用 |
| **学术论文** | SAST交叉引用(Tier1)提升漏检率96.9%；双轮分析(Tier3)用于关键代码；**不要剥离注释** | ⭐⭐⭐ 高度适用 |

### 业界共识

1. **混合架构(规则+LLM)是唯一正确方向**：纯LLM误报多，纯SAST漏报多。IRIS论文证明混合方案比纯SAST提升104%。
2. **上下文工程比模型选择更重要**：CodeRabbit从数十个来源汇聚上下文，Qodo做跨仓追踪。
3. **验证Agent是降低误报的关键**：CodeRabbit的二次验证、Gitar的CI验证。
4. **"先保守，再扩展"**：50条无关评论后开发者就不再看AI评论了。
5. **影响分析必须超越diff**：跨文件、跨仓的依赖追踪是diff-only检视永远无法覆盖的。

---

## 二、当前系统架构优势与痛点

### 优势（应保留和强化）

| # | 优势 | 评价 |
|---|------|------|
| A1 | 12专家LangGraph流水线，从ingest到persist_feedback的完整闭环 | **业界领先**，CodeRabbit也是Task Graph思路 |
| A2 | 三层质量门控：detect_conflicts → debate → judge，每层都有规则+LLM双通道 | **设计精良**，比大多数工具只有单层judge强得多 |
| A3 | 反馈学习闭环：quality_profiles动态调整confidence阈值 | **独特优势**，CodeRabbit和Qodo都没有这个 |
| A4 | GitNexus MCP集成：符号提取→图谱查询→影响报告的完整链路 | **独特优势**，业界没有类似的开源方案 |
| A5 | 专家边界明确：focus_areas + out_of_scope + required_checks | **减少专家间重叠**，降低误报 |
| A6 | 信号驱动的专家注入：Java quality signals → heuristic expert retention | **减少漏检**的安全网 |

### 痛点（需要优化）

| # | 痛点 | 影响 | 严重度 |
|---|------|------|--------|
| P1 | **影响分析与检视流水线完全隔离**：ImpactReport不参与专家选择、不参与finding生成、不参与issue优先级 | 影响分析沦为"装饰品"，无法指导检视聚焦 | 🔴 严重 |
| P2 | **ReviewState无impact字段**：LangGraph状态机与GitNexus结果完全断开 | 架构层面的割裂，后续集成困难 | 🔴 严重 |
| P3 | **无SAST预扫描**：专家只能靠LLM发现"注入风险""空catch块"等模式问题 | 漏检率高，这些是SAST的强项 | 🔴 严重 |
| P4 | **符号提取仅靠正则**：无AST解析，不支持Go/Rust/C#等语言，遗漏删除的符号 | 影响分析质量受限，跨文件追踪不准 | 🟡 中等 |
| P5 | **无验证Agent(Verification Agent)**：只有单次LLM+规则判断，无二次独立验证 | 误报率高，CodeRabbit验证Agent是核心差异化 | 🟡 中等 |
| P6 | **专家间overlap无去重**：exception_swallowed同时触发security和maintainability专家 | 同一问题被两个专家重复报出 | 🟡 中等 |
| P7 | **无跨仓影响追踪**：GitNexus单仓范围 | 服务A的API变更影响服务B，无法捕获 | 🟡 中等 |
| P8 | **ReviewRunner 6212行单体**：逻辑交织，难以维护和扩展 | 开发效率低，容易引入bug | 🟡 中等 |
| P9 | **Prompt预算无组预算**：P0 blocks可能独占全部token | 关键P1上下文可能被挤出 | 🟢 低 |
| P10 | **无项目编码规范文件自动检测**：不会读取.cursorrules/CLAUDE.md等 | 无法适配团队特定规范 | 🟢 低 |

---

## 三、优化方案

### 方案1：影响分析驱动检视流水线（解决P1+P2） 🔴 最高优先级

**核心思想**：让GitNexus的影响分析结果反过来驱动专家选择和检视聚焦，而不是独立输出。

#### 1.1 扩展ReviewState，加入影响分析字段

```python
# orchestrator/state.py 新增字段
class ReviewState(TypedDict):
    # ... 现有字段 ...
    # 新增：影响分析结果
    impact_risk_level: str              # "critical"|"high"|"medium"|"low"
    impacted_modules: list[str]         # 受影响的模块列表 ["order-service", "payment-gateway"]
    impacted_entry_points: list[str]    # 受影响的入口点 ["OrderController.create()", "PaymentService.process()"]
    impact_confirmed_paths: list[dict]  # 已确认的影响链路 [{source, target, edge_type}]
    impact_recommended_tests: list[str] # GitNexus推荐的测试范围
    impact_graph_nodes: list[dict]      # 影响图节点（精简版）
    impact_graph_edges: list[dict]      # 影响图边（精简版）
```

#### 1.2 调整流水线节点顺序

当前：`ingest → slice → expand_context → route_experts → run_reviews → ...`
改为：`ingest → slice → **impact_analysis** → expand_context → route_experts → run_reviews → ...`

将impact_analysis节点插入到expand_context和route_experts之间：

```
ingest_subject
    ↓
slice_change (风险信号检测)
    ↓
impact_analysis (GitNexus查询，产出risk_level + impacted_modules + confirmed_paths)
    ↓
expand_context (结合impact结果，丰富risk_hints)
    ↓
route_experts (基于risk_hints + impact结果，选择专家和分配检视范围)
    ↓
run_independent_reviews (专家执行时注入impact上下文)
    ↓
detect_conflicts → debate → evidence → judge → human_gate → report
```

#### 1.3 impact结果驱动专家选择

在 `route_experts` 节点中，增加基于影响分析结果的专家注入规则：

```python
# 新增映射：影响分析结果 → 专家注入
IMPACT_DRIVEN_EXPERT_INJECTION = {
    # 影响到入口点(Controller/Endpoint) → 注入 correctness_business
    "entry_point_impacted": {
        "expert": "correctness_business",
        "confidence": 0.85,
        "reason": "入口点受影响，需检查业务正确性"
    },
    # 影响到数据库层 → 注入 database_analysis
    "data_access_impacted": {
        "expert": "database_analysis",
        "confidence": 0.82,
        "reason": "数据访问层受影响，需检查SQL和数据一致性"
    },
    # 影响到消息队列 → 注入 mq_analysis
    "mq_producer_impacted": {
        "expert": "mq_analysis",
        "confidence": 0.80,
        "reason": "MQ生产者受影响，需检查消息投递语义"
    },
    # 影响到多个模块 → 注入 architecture_design
    "cross_module_impact": {
        "expert": "architecture_design",
        "confidence": 0.78,
        "reason": "跨模块影响，需检查架构一致性"
    },
    # 影响到安全敏感路径 → 注入 security_compliance
    "security_surface_impacted": {
        "expert": "security_compliance",
        "confidence": 0.88,
        "reason": "安全敏感路径受影响，需检查鉴权和数据暴露"
    },
}
```

#### 1.4 专家prompt注入影响分析上下文

在 `_build_expert_prompt` 中，为每个专家注入与其相关的impact上下文：

```
## 影响分析上下文（GitNexus）

本次变更的影响范围: {risk_level}
受影响模块: {impacted_modules}
已确认的影响链路:
- {source} → {target} (类型: {edge_type})
推荐测试范围: {recommended_tests}

**请重点检查以下受影响的下游路径，确认变更不会破坏这些调用方的预期行为。**
```

#### 1.5 影响分析结果参与issue优先级排序

在 `judge_and_merge` 阶段，影响分析结果应影响issue的优先级：

```python
# 如果issue涉及的文件在impact_confirmed_paths中，提升优先级
def _impact_priority_boost(issue, impact_state):
    boost = 0.0
    for path in impact_state.get("impact_confirmed_paths", []):
        if issue.file_path in path.get("target", ""):
            boost += 0.05  # 确认影响链路中的文件，+0.05
    if issue.file_path in impact_state.get("impacted_entry_points", []):
        boost += 0.08  # 入口点受影响，+0.08
    return min(boost, 0.15)  # cap at 0.15
```

---

### 方案2：SAST预扫描层（解决P3） 🔴 高优先级

**核心思想**：在LLM检视之前，先跑规则引擎，将确定性发现注入为"验证目标"，减少漏检。

#### 2.1 新增SAST预扫描节点

在 `slice_change` 和 `impact_analysis` 之间插入 `sast_preflight` 节点：

```
slice_change
    ↓
sast_preflight (新增：轻量SAST扫描)
    ↓
impact_analysis
    ↓
expand_context
```

#### 2.2 集成的SAST工具

| 工具 | 语言 | 检测范围 | 集成方式 |
|------|------|---------|---------|
| **Semgrep** | 多语言 | 安全漏洞、代码模式、自定义规则 | CLI subprocess，JSON输出 |
| **ESLint** | JS/TS | 代码质量、安全、最佳实践 | CLI subprocess，JSON输出 |
| **Bandit** | Python | 安全漏洞 | CLI subprocess，JSON输出 |
| **内建正则规则** | 多语言 | 项目特定模式 | 已有CodeObservationExtractor扩展 |

#### 2.3 SAST发现注入为验证目标

SAST的发现不直接作为issue，而是作为"验证目标"注入专家prompt：

```
## 静态分析预扫描结果

以下问题由静态分析工具发现，请在检视中重点确认：

1. [Semgrep:sql-injection] OrderRepository.java:45 - 可能的SQL注入
   规则: sql-injection-non-constant-string
   置信度: HIGH
   请验证：此参数是否经过清洗？是否使用参数化查询？

2. [ESLint:no-eval] payment.ts:112 - 使用了eval()
   规则: security/detect-eval-with-expression
   置信度: MEDIUM
   请验证：eval的输入是否可控？
```

#### 2.4 交叉验证机制

SAST发现 + LLM确认 = 高置信度issue：
- SAST发现 + LLM确认 → confidence >= 0.90 (直接通过)
- SAST发现 + LLM否认 → 需要人工判断 (needs_human)
- LLM发现 + SAST确认 → confidence + 0.10
- LLM发现 + SAST未覆盖 → 正常流程

---

### 方案3：验证Agent二次复核（解决P5） 🟡 中优先级

**核心思想**：CodeRabbit的验证Agent是降低误报的关键。在judge_and_merge之后，增加一个独立的验证Agent。

#### 3.1 新增 verification_agent 节点

```
judge_and_merge
    ↓
verification_agent (新增：独立验证Agent)
    ↓
human_gate
```

#### 3.2 验证Agent的职责

1. **代码可执行性验证**：检查建议的修复代码是否能编译/通过基本测试
2. **上下文一致性验证**：检查issue是否考虑了完整的调用链上下文
3. **误报二次检查**：对confidence在 0.65-0.80 区间的issue进行独立复核

#### 3.3 验证Agent的prompt策略

```
你是一个代码检视验证Agent。你的任务是独立复核以下issue是否为真实问题。

对于每个issue，请：
1. 重新阅读diff和相关上下文，独立判断问题是否存在
2. 检查issue的修复建议是否可行（不会引入新问题）
3. 检查是否有遗漏的上下文（如调用方、配置、版本兼容性）

输出格式：
- verdict: "confirmed" | "rejected" | "needs_context"
- reason: 具体原因
- confidence_adjustment: -0.15 ~ +0.05
```

#### 3.4 验证Agent的触发条件

不是所有issue都需要验证Agent，只在以下条件触发：
- confidence 在 0.65-0.80 之间（"灰色地带"）
- severity 为 blocker 或 critical
- finding_type 为 risk_hypothesis（推测性问题更容易误报）
- 跨文件影响但没有直接证据

---

### 方案4：专家间Overlap去重（解决P6） 🟡 中优先级

#### 4.1 问题定义

当前 `exception_swallowed` 信号同时触发 `security_compliance` 和 `maintainability_code_health`，可能导致同一空catch块被两个专家重复报出。

#### 4.2 解决方案：在detect_conflicts中增加跨专家去重

当前 `detect_conflicts` 已经有 `_is_same_problem_type` 做问题合并，但只在同文件内合并。增加：

```python
def _dedup_cross_expert_findings(findings: list[Finding]) -> list[Finding]:
    """跨专家去重：相同位置+相同问题类型，只保留primary expert的finding"""
    dedup_map = {}  # key: (file_path, line_start, normalized_issue_type)

    # 定义primary expert优先级（哪个专家对哪类问题拥有"管辖权"）
    ISSUE_JURISDICTION = {
        "exception_swallowed": "maintainability_code_health",
        "injection_risk": "security_compliance",
        "missing_auth_check": "security_compliance",
        "performance_regression": "performance_reliability",
        "query_boundary_missing": "database_analysis",
    }

    for f in findings:
        key = (f.file_path, f.line_start, f.normalized_issue_type)
        if key not in dedup_map:
            dedup_map[key] = f
        else:
            existing = dedup_map[key]
            jurisdiction = ISSUE_JURISDICTION.get(f.normalized_issue_type)
            if jurisdiction and f.expert_id == jurisdiction:
                # 当前finding的专家拥有管辖权，替换
                dedup_map[key] = f
            elif f.confidence > existing.confidence:
                # 无管辖权定义时，保留高置信度的
                dedup_map[key] = f

    return list(dedup_map.values())
```

---

### 方案5：AST感知的符号提取（解决P4） 🟡 中优先级

#### 5.1 当前问题

- 符号提取纯靠正则，Java/Kotlin/TS/Python覆盖有限
- 不支持Go、Rust、C#
- 遗漏删除的符号（只看 `+` 行）
- 无法处理Lombok、注解、Builder模式

#### 5.2 分层符号提取策略

```
Level 1: GitNexus detect_changes (最权威，依赖图谱)
    ↓ 补充
Level 2: AST解析 (tree-sitter，多语言支持)
    ↓ 补充
Level 3: 正则提取 (当前实现，作为兜底)
```

#### 5.3 tree-sitter集成

```python
# 新增服务：ast_symbol_extractor.py
class AstSymbolExtractor:
    """基于tree-sitter的多语言符号提取器"""

    LANGUAGES = {
        ".java": "java",
        ".kt": "kotlin",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
        ".cs": "c_sharp",
    }

    def extract_symbols(self, file_path: str, content: str) -> list[Symbol]:
        lang = self._infer_language(file_path)
        parser = get_parser(lang)
        tree = parser.parse(content.encode())

        symbols = []
        for node in self._walk(tree.root_node):
            if node.type in ("method_declaration", "function_declaration",
                           "class_declaration", "interface_declaration"):
                symbols.append(Symbol(
                    name=self._extract_name(node),
                    kind=node.type,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=self._extract_signature(node, content),
                ))
        return symbols
```

#### 5.4 删除符号追踪

当前只提取 `+` 行的符号。需要同时追踪 `-` 行中被删除/修改的符号：

```python
def extract_deleted_symbols(diff: str) -> list[Symbol]:
    """提取被删除的符号，用于影响分析"""
    deleted_lines = [line[1:] for line in diff.split('\n') if line.startswith('-') and not line.startswith('---')]
    # 对删除的内容做符号提取，标记为 "deleted"
    # 这些符号的调用方可能仍然在调用旧签名
```

---

### 方案6：项目编码规范自动检测（解决P10） 🟢 低优先级

#### 6.1 自动检测规范文件

CodeRabbit自动检测 `.cursorrules`、`CLAUDE.md`、`.github/copilot-instructions.md` 等文件。我们应该同样支持：

```python
# 新增服务：team_standards_detector.py
class TeamStandardsDetector:
    """自动检测项目编码规范文件"""

    STANDARDS_FILES = [
        ".cursorrules",
        "CLAUDE.md",
        "AGENTS.md",
        ".github/copilot-instructions.md",
        ".editorconfig",
        ".eslintrc.*",
        ".prettierrc.*",
        "checkstyle.xml",
        "spotbugs.xml",
        "CONTRIBUTING.md",
        "REVIEW_GUIDELINES.md",
    ]

    def detect(self, repo_path: str) -> list[TeamStandard]:
        """扫描项目目录，发现规范文件"""
        found = []
        for filename in self.STANDARDS_FILES:
            filepath = os.path.join(repo_path, filename)
            if os.path.exists(filepath):
                content = open(filepath).read()
                found.append(TeamStandard(
                    filename=filename,
                    content=content[:2000],  # 截断
                    source="project_file",
                ))
        return found
```

#### 6.2 注入专家prompt

检测到的规范文件内容，作为P1优先级的上下文块注入专家prompt：

```
## 项目编码规范

本项目定义了以下编码规范，请在检视中参考：

### .cursorrules
{content}

### checkstyle.xml
{content}
```

---

### 方案7：跨仓影响追踪（解决P7） 🟡 中优先级（长期）

#### 7.1 问题

GitNexus当前是单仓范围。但微服务场景下，一个MR可能影响多个服务。

#### 7.2 分阶段实现

**Phase 1（短期）**：基于GitNexus registry的多仓分别查询

```python
async def analyze_cross_repo_impact(self, subject: ReviewSubject) -> CrossRepoImpactReport:
    """跨仓影响分析"""
    registry = self._load_registry()  # ~/.gitnexus/registry.json

    # 1. 对当前仓库做常规影响分析
    primary_report = await self.analyze(subject)

    # 2. 从影响链路中提取外部依赖（HTTP调用、MQ消费、共享库引用）
    external_deps = self._extract_external_dependencies(primary_report)

    # 3. 对每个外部依赖的仓库，查询是否有反方向依赖
    cross_repo_findings = []
    for dep in external_deps:
        if dep.repo_name in registry:
            reverse_deps = await self._query_reverse_impact(dep)
            cross_repo_findings.append(reverse_deps)

    return CrossRepoImpactReport(
        primary=primary_report,
        cross_repo_findings=cross_repo_findings,
    )
```

**Phase 2（长期）**：GitNexus支持跨仓图谱，一次查询跨仓影响链路。

---

## 四、实施优先级与路线图

### Phase 1：核心质量提升（2-3周）

| 优先级 | 方案 | 预期效果 | 工作量 |
|--------|------|---------|--------|
| P0 | **方案1：影响分析驱动检视流水线** | 影响分析不再是"装饰品"，直接指导检视聚焦 | 5天 |
| P0 | **方案2：SAST预扫描层** | 漏检率显著降低（SAST交叉引用提升96.9%检测率） | 3天 |
| P1 | **方案4：专家间Overlap去重** | 消除重复issue，降低多检 | 2天 |

### Phase 2：深度质量提升（2-3周）

| 优先级 | 方案 | 预期效果 | 工作量 |
|--------|------|---------|--------|
| P1 | **方案3：验证Agent二次复核** | 误报率降低（灰色地带issue二次验证） | 4天 |
| P2 | **方案5：AST感知的符号提取** | 影响分析准确性提升，多语言支持 | 5天 |
| P2 | **方案6：项目编码规范自动检测** | 适配团队特定规范，减少无关建议 | 2天 |

### Phase 3：跨仓与长期（3-4周）

| 优先级 | 方案 | 预期效果 | 工作量 |
|--------|------|---------|--------|
| P2 | **方案7：跨仓影响追踪** | 微服务场景下的完整影响分析 | 7天 |
| P3 | ReviewRunner拆分重构 | 可维护性提升 | 5天 |

---

## 五、关键指标与验证方法

### 5.1 漏检率（False Negative Rate）

| 指标 | 当前基线 | 目标 | 验证方法 |
|------|---------|------|---------|
| SAST可检测问题覆盖率 | ~0% (无SAST) | ≥85% | 注入已知缺陷，统计SAST+LLM联合检测率 |
| 跨文件影响遗漏率 | 未知 | ≤10% | 构造跨文件变更用例，检查是否被impact分析捕获 |
| 安全漏洞遗漏率 | 未知 | ≤5% | 使用OWASP基准测试集 |

### 5.2 误报率（False Positive Rate）

| 指标 | 当前基线 | 目标 | 验证方法 |
|------|---------|------|---------|
| issue被人工标记为"误报"的比例 | 未知 | ≤15% | 收集human_gate反馈 |
| 重复issue比例 | 未知 | ≤5% | 统计同一位置+同一类型被多个专家报出的次数 |
| 灰色地带issue(0.65-0.80)的误报率 | 未知 | ≤25% | 对灰色地带issue做全量人工复核 |

### 5.3 影响分析质量

| 指标 | 当前基线 | 目标 | 验证方法 |
|------|---------|------|---------|
| 影响分析驱动专家注入的准确率 | N/A | ≥80% | 检查注入的专家是否确实发现了相关问题 |
| 影响链路确认率 | 未知 | ≥70% | 人工验证GitNexus报告的链路是否真实 |
| 影响分析结果被专家引用的比例 | 0% | ≥40% | 统计专家finding中引用impact上下文的比例 |

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| SAST工具安装增加部署复杂度 | 中 | 中 | 提供Docker镜像，内含Semgrep；支持SAST可选开关 |
| 影响分析节点增加流水线耗时 | 高 | 中 | impact_analysis并行执行（独立线程），不阻塞主流程；设置超时(30s) |
| 验证Agent增加LLM调用成本 | 中 | 低 | 只对灰色地带issue触发，预计增加<20% LLM调用 |
| AST解析增加依赖 | 低 | 低 | tree-sitter是成熟库，Python绑定稳定 |
| 跨仓查询增加GitNexus负载 | 中 | 中 | 限制跨仓查询数量(最多5个)，结果缓存 |

---

## 七、总结

本方案的核心思路是**"让影响分析从'旁观者'变成'指挥官'"**：

1. **影响分析驱动专家选择**：GitNexus发现安全敏感路径受影响 → 自动注入security专家
2. **影响分析驱动检视聚焦**：专家检视时被告知"这3个下游模块会受影响" → 重点检查调用兼容性
3. **影响分析驱动优先级排序**：影响链路上的issue获得优先级提升 → 人工reviewer优先关注

加上SAST预扫描和验证Agent，形成三层防御：
- **第一层(SAST)**：确定性规则快速扫描，不漏掉模式问题
- **第二层(LLM专家)**：上下文感知的深度分析，发现SAST无法检测的逻辑问题
- **第三层(验证Agent)**：独立复核，过滤误报

这三层与现有的detect_conflicts → debate → judge流水线结合，将构建出业界领先的代码检视质量保障体系。
