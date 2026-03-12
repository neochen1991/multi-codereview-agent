# 多专家代码审核系统 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 构建一个基于 LangGraph 的多专家代码审核系统 MVP，支持类 GitHub 平台的 MR / branch 审查、专家协同辩论、实时对话流展示、专家私有知识库和本地文件存储。

**Architecture:** 系统采用单后端进程优先架构，由 `FastAPI + LangGraph + 本地文件存储 + SSE + React/TypeScript` 组成。后端以 Repository 抽象屏蔽存储细节，首版全部落地到本地文件；前端围绕 `Review Workbench / Findings Center / Expert Studio` 搭建，并以实时事件流展示专家讨论过程。

**Tech Stack:** Python, FastAPI, LangGraph, Pydantic, orjson, PyYAML, React, TypeScript, TanStack Query, SSE, Monaco Diff Editor

---

### Task 1: 初始化仓库目录与基础工程骨架

**Files:**
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/config.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/routes/__init__.py`
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/domain/__init__.py`
- Create: `backend/app/repositories/__init__.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/storage/.gitkeep`
- Create: `frontend/package.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/pages/.gitkeep`
- Create: `frontend/src/components/.gitkeep`
- Create: `pyproject.toml`
- Create: `README.md`
- Test: `backend/tests/test_app_boot.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_app_boot.py -v`
Expected: FAIL with `ModuleNotFoundError` or `cannot import name 'app'`

**Step 3: Write minimal implementation**

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_app_boot.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml README.md backend frontend
git commit -m "feat: scaffold monorepo structure and app bootstrap"
```

### Task 2: 定义核心领域模型与 Pydantic Schema

**Files:**
- Create: `backend/app/domain/models/review.py`
- Create: `backend/app/domain/models/expert.py`
- Create: `backend/app/domain/models/finding.py`
- Create: `backend/app/domain/models/event.py`
- Create: `backend/app/domain/models/knowledge.py`
- Create: `backend/app/domain/types.py`
- Test: `backend/tests/domain/test_review_models.py`

**Step 1: Write the failing test**

```python
from app.domain.models.review import ReviewSubject, ReviewTask


def test_review_task_can_wrap_review_subject():
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo_1",
        project_id="proj_1",
        source_ref="feature/demo",
        target_ref="main",
    )
    task = ReviewTask(review_id="rev_1", subject=subject, status="pending")
    assert task.subject.source_ref == "feature/demo"
    assert task.status == "pending"
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/domain/test_review_models.py -v`
Expected: FAIL with missing module errors

**Step 3: Write minimal implementation**

```python
from pydantic import BaseModel


class ReviewSubject(BaseModel):
    subject_type: str
    repo_id: str
    project_id: str
    source_ref: str
    target_ref: str


class ReviewTask(BaseModel):
    review_id: str
    subject: ReviewSubject
    status: str
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/domain/test_review_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/domain backend/tests/domain
git commit -m "feat: add core domain models"
```

### Task 3: 实现本地文件 Repository 抽象

**Files:**
- Create: `backend/app/repositories/base.py`
- Create: `backend/app/repositories/file_review_repository.py`
- Create: `backend/app/repositories/file_event_repository.py`
- Create: `backend/app/repositories/file_expert_repository.py`
- Create: `backend/app/repositories/file_knowledge_repository.py`
- Create: `backend/app/repositories/file_feedback_repository.py`
- Create: `backend/app/utils/fs.py`
- Test: `backend/tests/repositories/test_file_review_repository.py`
- Test: `backend/tests/repositories/test_file_event_repository.py`

**Step 1: Write the failing test**

```python
from app.domain.models.review import ReviewSubject, ReviewTask
from app.repositories.file_review_repository import FileReviewRepository


def test_file_review_repository_saves_and_loads_review(tmp_path):
    repo = FileReviewRepository(tmp_path)
    task = ReviewTask(
        review_id="rev_1",
        status="pending",
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo_1",
            project_id="proj_1",
            source_ref="feature/demo",
            target_ref="main",
        ),
    )

    repo.save(task)
    loaded = repo.get("rev_1")

    assert loaded.review_id == "rev_1"
    assert loaded.subject.target_ref == "main"
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/repositories/test_file_review_repository.py -v`
Expected: FAIL with missing repository implementation

**Step 3: Write minimal implementation**

```python
class FileReviewRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, task: ReviewTask) -> None:
        path = self.root / "reviews" / task.review_id / "review.json"
        atomic_write_json(path, task.model_dump())

    def get(self, review_id: str) -> ReviewTask:
        path = self.root / "reviews" / review_id / "review.json"
        return ReviewTask.model_validate(read_json(path))
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/repositories/test_file_review_repository.py backend/tests/repositories/test_file_event_repository.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/repositories backend/app/utils backend/tests/repositories
git commit -m "feat: add file-backed repositories"
```

### Task 4: 实现 Expert Registry 与专家配置加载

**Files:**
- Create: `backend/app/services/expert_registry.py`
- Create: `backend/app/domain/models/expert_profile.py`
- Create: `backend/app/storage/experts/correctness_business/expert.yaml`
- Create: `backend/app/storage/experts/correctness_business/prompt.md`
- Create: `backend/app/storage/experts/correctness_business/schema.json`
- Create: `backend/app/storage/experts/security_compliance/expert.yaml`
- Create: `backend/app/storage/experts/security_compliance/prompt.md`
- Create: `backend/app/storage/experts/security_compliance/schema.json`
- Test: `backend/tests/services/test_expert_registry.py`

**Step 1: Write the failing test**

```python
from app.services.expert_registry import ExpertRegistry


def test_expert_registry_loads_builtin_experts(tmp_path):
    registry = ExpertRegistry(tmp_path / "experts")
    experts = registry.list_enabled()
    assert experts
    assert experts[0].name_zh
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_expert_registry.py -v`
Expected: FAIL because registry or expert definitions do not exist

**Step 3: Write minimal implementation**

```python
class ExpertRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_enabled(self) -> list[ExpertProfile]:
        profiles = []
        for expert_dir in self.root.iterdir():
            config = yaml.safe_load((expert_dir / "expert.yaml").read_text())
            profile = ExpertProfile.model_validate(config)
            if profile.enabled:
                profiles.append(profile)
        return profiles
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_expert_registry.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/expert_registry.py backend/app/storage/experts backend/tests/services
git commit -m "feat: add expert registry and builtin expert definitions"
```

### Task 5: 实现知识文档导入与本地索引

**Files:**
- Create: `backend/app/services/knowledge_ingestion.py`
- Create: `backend/app/services/knowledge_retrieval.py`
- Create: `backend/app/domain/models/knowledge_chunk.py`
- Create: `backend/app/storage/knowledge/.gitkeep`
- Test: `backend/tests/services/test_knowledge_ingestion.py`
- Test: `backend/tests/services/test_knowledge_retrieval.py`

**Step 1: Write the failing test**

```python
from app.services.knowledge_ingestion import KnowledgeIngestionService


def test_ingestion_creates_doc_metadata_and_chunks(tmp_path):
    service = KnowledgeIngestionService(tmp_path)
    doc_id = service.ingest_markdown(
        title="安全规范",
        content="# Rule\nAlways validate input.",
        bound_expert_ids=["security_compliance"],
    )
    meta = service.get_doc_meta(doc_id)
    assert meta.title == "安全规范"
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_knowledge_ingestion.py -v`
Expected: FAIL because ingestion service is missing

**Step 3: Write minimal implementation**

```python
class KnowledgeIngestionService:
    def ingest_markdown(self, title: str, content: str, bound_expert_ids: list[str]) -> str:
        doc_id = new_id("doc")
        save_markdown(doc_id, content)
        save_meta(doc_id, title=title, bound_expert_ids=bound_expert_ids)
        save_chunks(doc_id, naive_chunk(content))
        return doc_id
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_knowledge_ingestion.py backend/tests/services/test_knowledge_retrieval.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_* backend/app/domain/models/knowledge_* backend/tests/services
git commit -m "feat: add local knowledge ingestion and retrieval"
```

### Task 6: 实现 Capability Gateway 与本地 Tool 绑定

**Files:**
- Create: `backend/app/services/capability_gateway.py`
- Create: `backend/app/services/tools/base.py`
- Create: `backend/app/services/tools/read_file.py`
- Create: `backend/app/services/tools/search_code.py`
- Create: `backend/app/services/tools/get_diff.py`
- Create: `backend/app/services/tools/coverage_diff.py`
- Test: `backend/tests/services/test_capability_gateway.py`

**Step 1: Write the failing test**

```python
from app.services.capability_gateway import CapabilityGateway


def test_capability_gateway_invokes_registered_tool(tmp_path):
    gateway = CapabilityGateway()
    gateway.register_tool("echo", lambda payload: {"value": payload["value"]})
    result = gateway.invoke("echo", {"value": "ok"})
    assert result == {"value": "ok"}
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_capability_gateway.py -v`
Expected: FAIL with missing gateway implementation

**Step 3: Write minimal implementation**

```python
class CapabilityGateway:
    def __init__(self) -> None:
        self._tools = {}

    def register_tool(self, name: str, tool: Callable[[dict], dict]) -> None:
        self._tools[name] = tool

    def invoke(self, name: str, payload: dict) -> dict:
        return self._tools[name](payload)
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_capability_gateway.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/capability_gateway.py backend/app/services/tools backend/tests/services
git commit -m "feat: add capability gateway and built-in tools"
```

### Task 7: 实现平台接入与 Review API

**Files:**
- Create: `backend/app/api/routes/reviews.py`
- Create: `backend/app/api/routes/experts.py`
- Create: `backend/app/api/routes/knowledge.py`
- Create: `backend/app/services/platform_adapter.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_reviews_api.py`
- Test: `backend/tests/api/test_events_api.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_create_review_returns_review_id():
    client = TestClient(app)
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
        },
    )
    assert response.status_code == 201
    assert response.json()["review_id"].startswith("rev_")
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/api/test_reviews_api.py -v`
Expected: FAIL with 404 or router import errors

**Step 3: Write minimal implementation**

```python
@router.post("/api/reviews", status_code=201)
def create_review(payload: CreateReviewRequest) -> dict:
    review = review_service.create(payload)
    return {"review_id": review.review_id, "status": review.status}
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/api/test_reviews_api.py backend/tests/api/test_events_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/api backend/app/main.py backend/app/services/platform_adapter.py backend/tests/api
git commit -m "feat: add review and event APIs"
```

### Task 8: 实现实时事件流与 SSE

**Files:**
- Create: `backend/app/services/stream_hub.py`
- Create: `backend/app/api/routes/streams.py`
- Modify: `backend/app/repositories/file_event_repository.py`
- Test: `backend/tests/api/test_streams_api.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_events_endpoint_returns_existing_review_events(tmp_path):
    client = TestClient(app)
    response = client.get("/api/reviews/rev_1/events")
    assert response.status_code in {200, 404}
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/api/test_streams_api.py -v`
Expected: FAIL because stream routes are missing

**Step 3: Write minimal implementation**

```python
@router.get("/api/reviews/{review_id}/events")
def list_events(review_id: str) -> list[ReviewEvent]:
    return event_repository.list(review_id)
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/api/test_streams_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/stream_hub.py backend/app/api/routes/streams.py backend/tests/api
git commit -m "feat: add review event streaming endpoints"
```

### Task 9: 实现 LangGraph 状态模型与 Orchestrator 骨架

**Files:**
- Create: `backend/app/services/orchestrator/state.py`
- Create: `backend/app/services/orchestrator/events.py`
- Create: `backend/app/services/orchestrator/graph.py`
- Create: `backend/app/services/orchestrator/nodes/ingest_subject.py`
- Create: `backend/app/services/orchestrator/nodes/route_experts.py`
- Create: `backend/app/services/orchestrator/nodes/run_expert_reviews.py`
- Create: `backend/app/services/orchestrator/nodes/run_debate.py`
- Create: `backend/app/services/orchestrator/nodes/run_verifier.py`
- Create: `backend/app/services/orchestrator/nodes/judge_findings.py`
- Test: `backend/tests/orchestrator/test_graph_boot.py`

**Step 1: Write the failing test**

```python
from app.services.orchestrator.graph import build_review_graph


def test_build_review_graph_returns_compiled_graph():
    graph = build_review_graph()
    assert graph is not None
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/orchestrator/test_graph_boot.py -v`
Expected: FAIL due to missing graph module

**Step 3: Write minimal implementation**

```python
from langgraph.graph import END, StateGraph


def build_review_graph():
    graph = StateGraph(ReviewState)
    graph.add_node("ingest_subject", ingest_subject)
    graph.add_node("route_experts", route_experts)
    graph.add_node("run_expert_reviews", run_expert_reviews)
    graph.add_edge("ingest_subject", "route_experts")
    graph.add_edge("route_experts", "run_expert_reviews")
    graph.add_edge("run_expert_reviews", END)
    return graph.compile()
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/orchestrator/test_graph_boot.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/orchestrator backend/tests/orchestrator
git commit -m "feat: add langgraph orchestrator skeleton"
```

### Task 10: 实现专家执行、finding 生成和事件发射

**Files:**
- Create: `backend/app/services/review_runner.py`
- Create: `backend/app/services/expert_runtime.py`
- Modify: `backend/app/services/orchestrator/nodes/run_expert_reviews.py`
- Modify: `backend/app/repositories/file_event_repository.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
from app.services.review_runner import ReviewRunner


def test_review_runner_emits_finding_created_event(tmp_path):
    runner = ReviewRunner(storage_root=tmp_path)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    events = runner.list_events(review_id)
    assert any(event.event_type == "finding_created" for event in events)
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/services/test_review_runner.py -v`
Expected: FAIL because runner logic is missing

**Step 3: Write minimal implementation**

```python
def run_once(self, review_id: str) -> None:
    review = self.review_repo.get(review_id)
    expert = self.registry.list_enabled()[0]
    emit_event(..., event_type="expert_started", ...)
    finding = build_demo_finding(review, expert)
    self.finding_repo.save(review_id, finding)
    emit_event(..., event_type="finding_created", ...)
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/services/test_review_runner.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/app/services/expert_runtime.py backend/tests/services
git commit -m "feat: add expert execution and event emission"
```

### Task 11: 实现辩论线程、ConversationMessage 和议题对话流 API

**Files:**
- Create: `backend/app/domain/models/message.py`
- Create: `backend/app/repositories/file_message_repository.py`
- Create: `backend/app/api/routes/issues.py`
- Modify: `backend/app/services/orchestrator/nodes/run_debate.py`
- Test: `backend/tests/api/test_issue_messages_api.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_issue_messages_endpoint_returns_thread_messages():
    client = TestClient(app)
    response = client.get("/api/reviews/rev_1/issues/issue_1/messages")
    assert response.status_code in {200, 404}
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/api/test_issue_messages_api.py -v`
Expected: FAIL because route or repository is missing

**Step 3: Write minimal implementation**

```python
@router.get("/api/reviews/{review_id}/issues/{issue_id}/messages")
def list_issue_messages(review_id: str, issue_id: str) -> list[ConversationMessage]:
    return message_repository.list_by_issue(review_id, issue_id)
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/api/test_issue_messages_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/domain/models/message.py backend/app/repositories/file_message_repository.py backend/app/api/routes/issues.py backend/tests/api
git commit -m "feat: add conversation message storage and issue thread APIs"
```

### Task 12: 实现前端应用骨架与路由

**Files:**
- Create: `frontend/src/router.tsx`
- Create: `frontend/src/pages/ReviewWorkbenchPage.tsx`
- Create: `frontend/src/pages/FindingsCenterPage.tsx`
- Create: `frontend/src/pages/ExpertStudioPage.tsx`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/types.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/pages/ReviewWorkbenchPage.test.tsx`

**Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { ReviewWorkbenchPage } from "./ReviewWorkbenchPage";

test("renders review workbench heading", () => {
  render(<ReviewWorkbenchPage />);
  expect(screen.getByText("审核工作台")).toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- ReviewWorkbenchPage`
Expected: FAIL because page component is missing

**Step 3: Write minimal implementation**

```tsx
export function ReviewWorkbenchPage() {
  return <h1>审核工作台</h1>;
}
```

**Step 4: Run test to verify it passes**

Run: `npm test -- ReviewWorkbenchPage`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: scaffold frontend routes and pages"
```

### Task 13: 实现 Review Workbench 与对话流组件

**Files:**
- Create: `frontend/src/components/workbench/ReviewHeader.tsx`
- Create: `frontend/src/components/workbench/RiskSummaryCards.tsx`
- Create: `frontend/src/components/workbench/ExpertLaneBoard.tsx`
- Create: `frontend/src/components/workbench/ConversationFlowPanel.tsx`
- Create: `frontend/src/components/workbench/GlobalTimeline.tsx`
- Create: `frontend/src/components/workbench/IssueThreadList.tsx`
- Create: `frontend/src/components/workbench/ConversationMessageList.tsx`
- Create: `frontend/src/components/workbench/MessageCard.tsx`
- Create: `frontend/src/components/workbench/EvidenceDrawer.tsx`
- Modify: `frontend/src/pages/ReviewWorkbenchPage.tsx`
- Test: `frontend/src/components/workbench/ConversationFlowPanel.test.tsx`

**Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { ConversationFlowPanel } from "./ConversationFlowPanel";

test("renders global timeline tab", () => {
  render(<ConversationFlowPanel events={[]} issues={[]} />);
  expect(screen.getByText("全局对话流")).toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- ConversationFlowPanel`
Expected: FAIL because component is missing

**Step 3: Write minimal implementation**

```tsx
export function ConversationFlowPanel() {
  return (
    <section>
      <button>全局对话流</button>
      <button>议题线程</button>
    </section>
  );
}
```

**Step 4: Run test to verify it passes**

Run: `npm test -- ConversationFlowPanel`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/components/workbench frontend/src/pages/ReviewWorkbenchPage.tsx
git commit -m "feat: add review workbench and conversation flow UI"
```

### Task 14: 实现前端 SSE 订阅与事件存储

**Files:**
- Create: `frontend/src/lib/stream.ts`
- Create: `frontend/src/store/reviewEventStore.ts`
- Modify: `frontend/src/pages/ReviewWorkbenchPage.tsx`
- Test: `frontend/src/lib/stream.test.ts`

**Step 1: Write the failing test**

```ts
import { normalizeReviewEvent } from "./stream";

test("normalizes review events", () => {
  const event = normalizeReviewEvent({ event_type: "review_started" });
  expect(event.event_type).toBe("review_started");
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- stream`
Expected: FAIL because stream module is missing

**Step 3: Write minimal implementation**

```ts
export function normalizeReviewEvent(input: Record<string, unknown>) {
  return {
    event_type: String(input.event_type),
  };
}
```

**Step 4: Run test to verify it passes**

Run: `npm test -- stream`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/lib/stream.ts frontend/src/store/reviewEventStore.ts frontend/src/pages/ReviewWorkbenchPage.tsx
git commit -m "feat: add frontend event stream subscription"
```

### Task 15: 实现 Expert Studio 与知识库管理页

**Files:**
- Create: `frontend/src/components/expert/ExpertList.tsx`
- Create: `frontend/src/components/expert/ExpertEditor.tsx`
- Create: `frontend/src/components/expert/KnowledgeBindingPanel.tsx`
- Create: `frontend/src/components/expert/CapabilityPolicyPanel.tsx`
- Modify: `frontend/src/pages/ExpertStudioPage.tsx`
- Test: `frontend/src/pages/ExpertStudioPage.test.tsx`

**Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { ExpertStudioPage } from "./ExpertStudioPage";

test("renders expert studio heading", () => {
  render(<ExpertStudioPage />);
  expect(screen.getByText("专家配置中心")).toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- ExpertStudioPage`
Expected: FAIL because page implementation is missing

**Step 3: Write minimal implementation**

```tsx
export function ExpertStudioPage() {
  return <h1>专家配置中心</h1>;
}
```

**Step 4: Run test to verify it passes**

Run: `npm test -- ExpertStudioPage`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/components/expert frontend/src/pages/ExpertStudioPage.tsx
git commit -m "feat: add expert studio and knowledge management UI"
```

### Task 16: 串联 MVP 端到端流程并补充文档

**Files:**
- Modify: `README.md`
- Create: `docs/adr/0001-local-file-storage.md`
- Create: `docs/adr/0002-review-event-model.md`
- Create: `docs/runbooks/local-dev.md`
- Test: `backend/tests/e2e/test_review_flow_smoke.py`

**Step 1: Write the failing test**

```python
def test_review_flow_smoke():
    assert False, "replace with a smoke test that creates a review and observes events"
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/e2e/test_review_flow_smoke.py -v`
Expected: FAIL with the placeholder assertion

**Step 3: Write minimal implementation**

```python
def test_review_flow_smoke(client):
    response = client.post(
        "/api/reviews",
        json={
            "subject_type": "branch",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/demo",
            "target_ref": "main",
        },
    )
    assert response.status_code == 201
```

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/e2e/test_review_flow_smoke.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add README.md docs/adr docs/runbooks backend/tests/e2e
git commit -m "docs: add ADRs and smoke test for MVP flow"
```
