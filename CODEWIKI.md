# 多专家协同代码检视系统 - 开发指南

> 本文档面向开发人员，介绍项目技术架构、开发规范和扩展方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术架构](#2-技术架构)
3. [目录结构](#3-目录结构)
4. [开发环境搭建](#4-开发环境搭建)
5. [核心模块详解](#5-核心模块详解)
6. [功能扩展指南](#6-功能扩展指南)
7. [调试与测试](#7-调试与测试)
8. [常见问题](#8-常见问题)

---

## 1. 项目概述

### 1.1 项目定位

多专家协同代码检视系统是一个基于 **FastAPI + LangGraph + React** 的代码质量保障平台。通过模拟多领域专家（业务、安全、性能等）协同审查的方式，在代码合并前自动发现潜在问题。

### 1.2 核心概念

| 概念 | 说明 | 类比 |
|------|------|------|
| **Review** | 一次代码审查任务 | 一次会诊 |
| **Expert** | 特定领域的审查专家 | 专科医生 |
| **Finding** | 专家的初步发现 | 初步诊断 |
| **Issue** | 聚合后的正式问题 | 确诊病历 |
| **Skill** | 专家的特殊能力包 | 专科检查项目 |
| **Tool** | 底层执行工具 | 检查仪器 |

---

## 2. 技术架构

### 2.1 架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                         前端层                                   │
│  React 18 + TypeScript + Ant Design 5 + Vite                   │
├─────────────────────────────────────────────────────────────────┤
│                         API网关层                                │
│  FastAPI + Uvicorn + SSE(服务端推送)                            │
├─────────────────────────────────────────────────────────────────┤
│                      核心引擎层 (LangGraph)                       │
│  状态机: ingest → route → review → detect → debate → judge    │
├─────────────────────────────────────────────────────────────────┤
│                       专家执行层                                │
│  ThreadPoolExecutor 并行执行6类专家审查                         │
├─────────────────────────────────────────────────────────────────┤
│                      扩展能力层                                   │
│  Skill机制(业务能力包) + Tool机制(执行插件)                      │
├─────────────────────────────────────────────────────────────────┤
│                      数据持久化层                                │
│  SQLite (任务/事件/发现/Issue) + 文件系统 (review详情)           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈清单

| 层级 | 技术/框架 | 版本 | 用途 |
|------|-----------|------|------|
| 后端框架 | FastAPI | >=0.115 | Web API框架 |
| 数据验证 | Pydantic | >=2.8 | 数据模型校验 |
| 工作流引擎 | LangGraph | latest | 审核状态机 |
| HTTP客户端 | httpx | >=0.27 | LLM API调用 |
| 数据库 | SQLite | 3.x | 数据持久化 |
| 前端框架 | React | 18.x | UI框架 |
| 构建工具 | Vite | 5.x | 前端构建 |
| UI组件库 | Ant Design | 5.27.x | 组件库 |

---

## 3. 目录结构

```
多专家协同代码检视系统/
├── backend/                          # 后端代码
│   ├── app/                          # 主应用目录
│   │   ├── api/                      # API层
│   │   │   └── routes/               # 路由定义
│   │   ├── domain/                   # 领域层
│   │   │   └── models/               # 领域模型
│   │   ├── repositories/             # 数据访问层
│   │   ├── services/                 # 服务层
│   │   │   ├── orchestrator/         # LangGraph编排器
│   │   │   │   ├── graph.py          # 状态机定义
│   │   │   │   ├── nodes/            # 状态节点
│   │   │   │   └── state.py          # 状态定义
│   │   │   ├── expert_registry.py    # 专家注册
│   │   │   ├── review_runner.py      # 审核执行引擎
│   │   │   └── ...                   # 其他服务
│   │   ├── storage/                  # 内置专家配置
│   │   │   └── experts/              # 专家Prompt和配置
│   │   ├── config.py                 # 全局配置
│   │   └── main.py                   # 应用入口
│   ├── tests/                        # 测试代码
│   └── requirements.txt              # 依赖清单
│
├── frontend/                         # 前端代码
│   ├── src/
│   │   ├── components/               # 组件
│   │   │   ├── common/               # 通用组件
│   │   │   └── review/               # 审核相关组件
│   │   ├── pages/                    # 页面
│   │   ├── services/                 # API服务
│   │   ├── store/                    # 状态管理
│   │   ├── App.tsx                   # 根组件
│   │   └── main.tsx                  # 入口
│   ├── package.json                  # 依赖
│   └── vite.config.ts                # Vite配置
│
├── extensions/                       # 扩展目录
│   ├── skills/                       # Skill扩展
│   │   └── design-consistency-check/ # 设计一致性检查
│   │       ├── SKILL.md
│   │       └── metadata.json
│   └── tools/                        # Tool扩展
│       └── custom_tool/              # 自定义工具
│           ├── tool.json
│           └── run.py
│
├── docs/                             # 文档
│   └── plans/                        # 设计方案
│
├── scripts/                          # 脚本
│   ├── start-all.sh                  # 启动脚本
│   └── stop-all.sh                   # 停止脚本
│
├── config.json                       # 全局配置文件
├── CLAUDE.md                         # 项目说明
└── CODEWIKI.md                       # 本文档
```

---

## 4. 开发环境搭建

### 4.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.11 | 后端运行环境 |
| Node.js | >= 18.x | 前端构建环境 |
| SQLite | 3.x | 数据持久化（内置） |
| Git | 任意 | 代码版本控制 |

### 4.2 快速启动

```bash
# 1. 克隆项目
git clone <repository-url>
cd multi-codereview-agent

# 2. 创建Python虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. 安装后端依赖
pip install -e .

# 4. 安装前端依赖
cd frontend
npm install

# 5. 一键启动（前后端）
cd ..
bash scripts/start-all.sh

# 6. 访问
# 前端: http://127.0.0.1:5174
# 后端API: http://127.0.0.1:8011
```

### 4.3 开发模式启动

```bash
# 后端开发模式（热重载）
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8011

# 前端开发模式
cd frontend
npm run dev

# 运行测试
.venv/bin/pytest backend/tests -q
```

### 4.4 配置文件说明

**config.json** 主要配置项：

```json
{
  "server": {
    "backend_port": 8011,
    "frontend_port": 5174
  },
  "llm": {
    "default_provider": "dashscope-openai-compatible",
    "default_model": "kimi-k2.5",
    "default_api_key": "your-api-key"
  },
  "code_repo": {
    "clone_url": "https://github.com/example/repo.git",
    "local_path": "/path/to/repo",
    "default_branch": "main"
  },
  "runtime": {
    "default_analysis_mode": "standard",
    "default_max_debate_rounds": 3,
    "allow_human_gate": true
  }
}
```

---

## 5. 核心模块详解

### 5.1 LangGraph状态机 (orchestrator)

**核心文件：**
- `backend/app/services/orchestrator/graph.py` - 状态机定义
- `backend/app/services/orchestrator/nodes/` - 状态节点

**状态流转：**

```python
# graph.py 中的状态定义
def build_review_graph():
    graph = StateGraph(ReviewState)
    
    # 添加节点
    graph.add_node("ingest_subject", ingest_subject)          # 1. 接收MR
    graph.add_node("slice_change", slice_change)              # 2. 解析Diff
    graph.add_node("expand_context", expand_context)          # 3. 扩展上下文
    graph.add_node("route_experts", route_experts)            # 4. 专家路由
    graph.add_node("run_independent_reviews", run_expert_reviews)  # 5. 并行审查
    graph.add_node("detect_conflicts", detect_conflicts)        # 6. 冲突检测
    graph.add_node("run_targeted_debate", run_targeted_debate)  # 7. 定向辩论
    graph.add_node("evidence_verification", evidence_verification)  # 8. 证据验证
    graph.add_node("judge_and_merge", judge_and_merge)          # 9. 裁决合并
    graph.add_node("human_gate", human_gate)                    # 10. 人工裁决
    graph.add_node("publish_report", publish_report)            # 11. 报告发布
    graph.add_node("persist_feedback", persist_feedback)        # 12. 反馈持久化
    
    # 边连接形成DAG
    graph.add_edge("ingest_subject", "slice_change")
    graph.add_edge("slice_change", "expand_context")
    # ... 更多边
    
    return graph.compile()
```

### 5.2 专家执行引擎 (review_runner)

**核心文件：**
- `backend/app/services/review_runner.py` - 审核执行引擎

**关键方法：**

```python
class ReviewRunner:
    """审核执行引擎。
    
    负责把一次代码审核真正跑起来：
    - 选择专家
    - 主Agent派工
    - 专家调用运行时工具并产出finding
    - graph/judge收敛issue
    - human gate / 最终报告落盘
    """
    
    def __init__(self, storage_root: Path | None = None):
        # 初始化各类服务
        self.review_repo = SqliteReviewRepository(db_path)
        self.registry = ExpertRegistry(self.storage_root / "experts")
        self.java_quality_signal_extractor = JavaQualitySignalExtractor()
        self.review_tool_gateway = ReviewToolGateway(self.storage_root)
        # ... 更多服务
        self.graph = build_review_graph()  # LangGraph状态机
    
    def run_once(self, review_id: str) -> ReviewTask:
        """完整执行一次审核主链。"""
        # 1. 加载审核任务
        review = self.review_repo.get(review_id)
        
        # 2. 选择专家
        selection_plan = self.main_agent_service.select_review_experts(...)
        
        # 3. 并行执行专家审查
        self._execute_expert_jobs(expert_jobs, ...)
        
        # 4. 运行LangGraph状态机收敛结果
        final_state = self.graph.invoke(initial_state)
        
        # 5. 发布报告
        return review
```

### 5.3 专家注册表 (expert_registry)

**核心文件：**
- `backend/app/services/expert_registry.py` - 专家注册与管理
- `backend/app/storage/experts/` - 内置专家配置

**专家配置结构：**

```
backend/app/storage/experts/
├── correctness_business/           # 业务正确性专家
│   ├── metadata.json               # 专家元数据
│   └── prompt.md                   # 专家Prompt
├── correctness_technical/          # 技术正确性专家
│   └── ...
├── security_compliance/            # 安全合规专家
│   └── ...
├── performance_reliability/        # 性能可靠专家
│   └── ...
├── maintainability_code_health/    # 可维护性专家
│   └── ...
└── test_verification/              # 测试验证专家
    └── ...
```

**metadata.json 示例：**

```json
{
  "expert_id": "correctness_business",
  "name_zh": "业务正确性专家",
  "description": "专注检查业务逻辑、领域模型、业务流程的正确性",
  "enabled": true,
  "applicable_file_patterns": [
    "**/service/**",
    "**/domain/**",
    "**/application/**",
    "**/usecase/**"
  ],
  "excluded_file_patterns": [
    "**/test/**",
    "**/mock/**"
  ],
  "finding_types": ["direct_defect", "risk_hypothesis", "design_concern"],
  "priority": "high",
  "max_tool_calls": 5,
  "llm_overrides": {
    "model": "kimi-k2.5",
    "temperature": 0.2
  }
}
```

### 5.4 扩展机制 (Skill + Tool)

**Skill机制：**

```
extensions/skills/
└── design-consistency-check/       # 设计一致性检查Skill
    ├── metadata.json               # Skill元数据
    └── SKILL.md                    # 能力定义
```

**metadata.json：**

```json
{
  "skill_id": "design-consistency-check",
  "name": "设计一致性检查",
  "version": "1.0.0",
  "bound_experts": ["correctness_business"],
  "activation_rules": {
    "required_doc_types": ["design_spec"],
    "file_path_patterns": [
      "**/service/**",
      "**/usecase/**"
    ]
  }
}
```

**SKILL.md：**

```markdown
# Design Consistency Check

## Purpose
检查详细设计文档与代码实现是否一致。

## When To Use
- 专家为 `correctness_business`
- 绑定了 `design_spec` 类型文档
- 改动命中 service/usecase 文件

## Required Tools
- `diff_inspector`
- `repo_context_search`
- `design_spec_alignment`

## Output Contract
- `design_alignment_status`
- `matched_implementation_points`
- `missing_implementation_points`
```

**Tool机制：**

```
extensions/tools/
└── custom_complexity_analyzer/     # 圈复杂度分析工具
    ├── tool.json                   # 工具配置
    ├── run.py                      # 执行脚本
    └── README.md                   # 使用说明
```

**tool.json：**

```json
{
  "id": "custom_complexity_analyzer",
  "name": "圈复杂度分析器",
  "version": "1.0.0",
  "description": "分析Java方法的圈复杂度",
  "entry": "run.py",
  "execution_mode": "subprocess",
  "timeout_seconds": 60,
  "input_schema": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"},
      "source_code": {"type": "string"}
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "cyclomatic_complexity": {"type": "integer"},
      "complexity_rating": {"type": "string"}
    }
  }
}
```

---

## 6. 功能扩展指南

### 6.1 新增专家

**步骤：**

1. 创建专家目录

```bash
mkdir -p backend/app/storage/experts/my_custom_expert
```

2. 编写 metadata.json

```json
{
  "expert_id": "my_custom_expert",
  "name_zh": "我的自定义专家",
  "description": "专注检查XXX问题",
  "enabled": true,
  "applicable_file_patterns": ["**/*.java"],
  "finding_types": ["direct_defect", "risk_hypothesis"],
  "priority": "high"
}
```

3. 编写 prompt.md

```markdown
# 我的自定义专家审查指南

## 审查重点
1. XXX检查
2. YYY验证

## 输出格式
```json
{
  "finding_type": "direct_defect|risk_hypothesis",
  "severity": "high|medium|low",
  "title": "问题标题",
  "summary": "详细描述"
}
```
```

### 6.2 新增Skill

```bash
mkdir -p extensions/skills/my_skill
cat > extensions/skills/my_skill/metadata.json << 'EOF'
{
  "skill_id": "my_skill",
  "name": "我的Skill",
  "bound_experts": ["expert_id"],
  "activation_rules": {
    "file_path_patterns": ["**/*.java"]
  }
}
EOF

cat > extensions/skills/my_skill/SKILL.md << 'EOF'
# My Skill

## Purpose
XXX检查

## Required Tools
- tool_name

## Output Contract
- output_field
EOF
```

### 6.3 新增Tool

```bash
mkdir -p extensions/tools/my_tool
cat > extensions/tools/my_tool/tool.json << 'EOF'
{
  "id": "my_tool",
  "name": "我的工具",
  "entry": "run.py",
  "execution_mode": "subprocess",
  "timeout_seconds": 60
}
EOF

cat > extensions/tools/my_tool/run.py << 'EOF'
#!/usr/bin/env python3
import json
import sys

def main():
    input_data = json.load(sys.stdin)
    
    # 处理逻辑
    result = {
        "success": True,
        "data": "处理结果"
    }
    
    print(json.dumps(result))

if __name__ == "__main__":
    main()
EOF

chmod +x extensions/tools/my_tool/run.py
```

---

## 7. 调试与测试

### 7.1 调试技巧

**1. 查看日志**

```bash
# 实时查看日志
tail -f backend/logs/app.log

# 查看特定review的日志
grep "review_id=rev_xxx" backend/logs/app.log

# 查看特定专家的日志
grep "expert_id=xxx" backend/logs/app.log
```

**2. LangGraph状态可视化**

```python
# 在代码中打印状态
from app.services.orchestrator.graph import build_review_graph

graph = build_review_graph()
print(graph.get_graph().draw_mermaid())  # 输出Mermaid图
```

**3. API调试**

```bash
# 创建审核任务
curl -X POST http://localhost:8011/api/reviews \
  -H "Content-Type: application/json" \
  -d '{
    "subject_type": "mr",
    "repo_id": "test",
    "source_ref": "feature/test",
    "target_ref": "main"
  }'

# 查看审核状态
curl http://localhost:8011/api/reviews/{review_id}
```

### 7.2 测试规范

**目录结构：**
```
backend/tests/
├── conftest.py                 # 测试配置
├── services/                   # 服务层测试
│   ├── test_expert_registry.py
│   ├── test_review_runner.py
│   └── test_*.py
└── fixtures/                   # 测试数据
    └── sample_reviews.json
```

**编写测试：**

```python
# 示例: test_expert_registry.py
import pytest
from app.services.expert_registry import ExpertRegistry

@pytest.fixture
def registry(tmp_path):
    return ExpertRegistry(tmp_path)

def test_list_enabled(registry):
    experts = registry.list_enabled()
    assert len(experts) > 0
    assert all(e.enabled for e in experts)

def test_create_custom_expert(registry):
    payload = {
        "expert_id": "test_expert",
        "name_zh": "测试专家",
        "description": "用于测试的专家"
    }
    expert = registry.create(payload)
    assert expert.expert_id == "test_expert"
```

**运行测试：**

```bash
# 运行所有测试
.venv/bin/pytest backend/tests -q

# 运行特定测试文件
.venv/bin/pytest backend/tests/services/test_expert_registry.py -v

# 生成覆盖率报告
.venv/bin/pytest --cov=app --cov-report=html
```

---

## 8. 常见问题

### Q1: 启动时报 "ModuleNotFoundError"

**解决：**
```bash
# 确保在项目根目录执行
pip install -e .
# 或
.venv/bin/pip install -e .
```

### Q2: 前端无法连接后端

**解决：**
```bash
# 检查后端是否运行在8011端口
curl http://localhost:8011/api/health

# 检查前端代理配置
# frontend/vite.config.ts
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8011',  # 确保正确
      changeOrigin: true,
    },
  },
}
```

### Q3: LLM API调用失败

**解决：**
```bash
# 检查config.json中的API配置
cat config.json | grep -A 5 llm

# 测试API连通性
curl https://api.example.com/v1/models \
  -H "Authorization: Bearer your-api-key"
```

### Q4: 如何新增一个专家发现的问题类型

**解决：**
1. 在 `backend/app/domain/models/finding.py` 中定义新的finding类型
2. 在专家的 `prompt.md` 中添加对应输出格式
3. 在 `detect_conflicts.py` 中添加新的权重计算

### Q5: 调试时如何查看完整的LangGraph状态流转

**解决：**
```python
# 在review_runner.py中添加调试日志
import logging
logger = logging.getLogger(__name__)

# 在状态转换时打印
for event in self.graph.stream(initial_state):
    logger.debug(f"State transition: {event}")
```

---

## 附录：快速参考

### 常用命令速查表

| 命令 | 说明 |
|------|------|
| `bash scripts/start-all.sh` | 一键启动前后端 |
| `bash scripts/stop-all.sh` | 停止所有服务 |
| `.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8011` | 后端热重载模式 |
| `cd frontend && npm run dev` | 前端开发模式 |
| `.venv/bin/pytest backend/tests -q` | 运行测试 |
| `tail -f backend/logs/app.log` | 查看日志 |

### 关键文件位置速查

| 文件 | 路径 | 说明 |
|------|------|------|
| 主配置 | `config.json` | 全局配置 |
| 后端入口 | `backend/app/main.py` | FastAPI入口 |
| 状态机 | `backend/app/services/orchestrator/graph.py` | LangGraph定义 |
| 执行引擎 | `backend/app/services/review_runner.py` | 审核执行 |
| 专家注册 | `backend/app/services/expert_registry.py` | 专家管理 |
| 前端入口 | `frontend/src/main.tsx` | React入口 |
| API服务 | `frontend/src/services/api.ts` | 前端API |

---

> **文档维护：** 本文档随代码更新持续维护，如有疑问请在项目中提交Issue。
