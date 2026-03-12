# Remaining Gap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 补齐当前多专家代码审核系统与 `2026-03-12-multi-agent-code-review-design.md` 之间的关键差距，优先完成真实平台接入、工具核验、议题辩论、知识检索与治理闭环。

**Architecture:** 在现有 `FastAPI + file repository + LangGraph-style orchestrator + React workbench` 的基础上继续纵向补能力，不推翻现有骨架。后端优先补真实服务和状态数据，再把这些能力逐步映射到 V1 风格前端工作台与治理页。

**Tech Stack:** Python, FastAPI, Pydantic, file-backed repositories, React, TypeScript, Ant Design, SSE

---

### Task 1: 真实平台接入与触发入口

**Files:**
- Modify: `backend/app/services/platform_adapter.py`
- Modify: `backend/app/domain/models/review.py`
- Modify: `backend/app/api/routes/reviews.py`
- Create: `backend/app/api/routes/triggers.py`
- Create: `backend/tests/services/test_platform_adapter_live_subjects.py`
- Create: `backend/tests/api/test_trigger_routes.py`

**Step 1: Write the failing test**

```python
def test_platform_adapter_builds_branch_compare_subject():
    adapter = PlatformAdapter()
    subject = adapter.normalize(
        ReviewSubject(
            subject_type="branch",
            repo_id="payments",
            project_id="platform",
            source_ref="feature/risk-guard",
            target_ref="main",
            repo_url="https://git.example.com/platform/payments",
        )
    )
    assert subject.metadata["compare_mode"] == "branch_compare"
    assert subject.commits
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_platform_adapter_live_subjects.py -q`
Expected: FAIL because compare metadata and richer trigger behavior do not exist.

**Step 3: Write minimal implementation**

```python
metadata["compare_mode"] = "branch_compare"
metadata["platform_kind"] = "gitlab_like"
metadata["trigger_source"] = "manual"
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_platform_adapter_live_subjects.py backend/tests/api/test_trigger_routes.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/platform_adapter.py backend/app/domain/models/review.py backend/app/api/routes/triggers.py backend/tests/services/test_platform_adapter_live_subjects.py backend/tests/api/test_trigger_routes.py
git commit -m "feat: add richer platform subject normalization and trigger routes"
```

### Task 2: 工具白名单与 verifier 服务

**Files:**
- Modify: `backend/app/services/capability_gateway.py`
- Create: `backend/app/services/evidence_verifier_service.py`
- Create: `backend/app/services/tools/__init__.py`
- Create: `backend/app/services/tools/local_diff_tool.py`
- Create: `backend/app/services/tools/coverage_diff_tool.py`
- Create: `backend/app/services/tools/schema_diff_tool.py`
- Modify: `backend/app/services/orchestrator/nodes/evidence_verification.py`
- Create: `backend/tests/services/test_evidence_verifier_service.py`

**Step 1: Write the failing test**

```python
def test_verifier_marks_issue_tool_verified_when_tool_succeeds():
    verifier = EvidenceVerifierService()
    result = verifier.verify(
        issue_id="iss_1",
        strategy="schema_diff",
        payload={"changed_files": ["backend/db/migrations/001.sql"]},
    )
    assert result["tool_verified"] is True
    assert result["tool_name"] == "schema_diff"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_evidence_verifier_service.py -q`
Expected: FAIL because verifier service and tool-first result schema do not exist.

**Step 3: Write minimal implementation**

```python
class EvidenceVerifierService:
    def verify(self, issue_id: str, strategy: str, payload: dict[str, object]) -> dict[str, object]:
        return {"issue_id": issue_id, "tool_name": strategy, "tool_verified": True, "score": 0.9}
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_evidence_verifier_service.py backend/tests/services/test_capability_gateway.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/capability_gateway.py backend/app/services/evidence_verifier_service.py backend/app/services/tools backend/app/services/orchestrator/nodes/evidence_verification.py backend/tests/services/test_evidence_verifier_service.py
git commit -m "feat: add tool allowlist and verifier service"
```

### Task 3: 真实议题辩论转录与 judge 决策

**Files:**
- Modify: `backend/app/domain/models/message.py`
- Modify: `backend/app/domain/models/issue.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/orchestrator/nodes/run_targeted_debate.py`
- Modify: `backend/app/services/orchestrator/nodes/judge_and_merge.py`
- Create: `backend/tests/services/test_debate_transcript.py`

**Step 1: Write the failing test**

```python
def test_review_runner_persists_debate_messages_for_conflicted_issue(storage_root):
    runner = ReviewRunner(storage_root=storage_root)
    review_id = runner.bootstrap_demo_review()
    runner.run_once(review_id)
    messages = runner.message_repo.list(review_id)
    assert any(item.message_type == "debate_message" for item in messages)
    assert any(item.expert_id == "judge" for item in messages)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_debate_transcript.py -q`
Expected: FAIL because only finding statement and judge summary are persisted.

**Step 3: Write minimal implementation**

```python
self.message_repo.append(
    ConversationMessage(
        review_id=review_id,
        issue_id=issue.issue_id,
        expert_id=participant_id,
        message_type="debate_message",
        content="我反对当前结论，证据还不够充分。",
    )
)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_debate_transcript.py backend/tests/services/test_review_runner.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/domain/models/message.py backend/app/domain/models/issue.py backend/app/services/review_runner.py backend/app/services/orchestrator/nodes/run_targeted_debate.py backend/app/services/orchestrator/nodes/judge_and_merge.py backend/tests/services/test_debate_transcript.py
git commit -m "feat: persist debate transcript and judge decisions"
```

### Task 4: 知识摄取、检索与 expert 绑定

**Files:**
- Modify: `backend/app/services/knowledge_service.py`
- Create: `backend/app/services/knowledge_ingestion_service.py`
- Create: `backend/app/services/knowledge_retrieval_service.py`
- Modify: `backend/app/api/routes/knowledge.py`
- Create: `backend/tests/services/test_knowledge_retrieval_service.py`
- Create: `backend/tests/api/test_knowledge_write_api.py`

**Step 1: Write the failing test**

```python
def test_knowledge_retrieval_returns_docs_for_expert_and_context(storage_root):
    service = KnowledgeRetrievalService(storage_root)
    docs = service.retrieve("security_compliance", {"changed_files": ["backend/app/security/authz.py"]})
    assert docs
    assert docs[0].expert_id == "security_compliance"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_knowledge_retrieval_service.py -q`
Expected: FAIL because retrieval by expert and review context does not exist.

**Step 3: Write minimal implementation**

```python
def retrieve(self, expert_id: str, review_context: dict[str, object]) -> list[KnowledgeDocument]:
    return [doc for doc in self._repo.list() if doc.expert_id == expert_id]
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_knowledge_retrieval_service.py backend/tests/api/test_knowledge_write_api.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/knowledge_service.py backend/app/services/knowledge_ingestion_service.py backend/app/services/knowledge_retrieval_service.py backend/app/api/routes/knowledge.py backend/tests/services/test_knowledge_retrieval_service.py backend/tests/api/test_knowledge_write_api.py
git commit -m "feat: add knowledge ingestion and retrieval flows"
```

### Task 5: 反馈学习、误报标签与报告落盘

**Files:**
- Modify: `backend/app/domain/models/report.py`
- Create: `backend/app/domain/models/feedback.py`
- Create: `backend/app/repositories/file_feedback_repository.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/app/services/orchestrator/nodes/persist_feedback.py`
- Create: `backend/tests/services/test_feedback_learning_flow.py`

**Step 1: Write the failing test**

```python
def test_human_decision_persists_feedback_label(storage_root):
    service = ReviewService(storage_root=storage_root)
    review = service.create_review({...})
    service.start_review(review.review_id)
    issue = service.list_issues(review.review_id)[0]
    service.record_human_decision(review.review_id, issue.issue_id, "rejected", "误报")
    labels = service.list_feedback_labels(review.review_id)
    assert labels
    assert labels[0].label == "false_positive"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_feedback_learning_flow.py -q`
Expected: FAIL because feedback labels and storage do not exist.

**Step 3: Write minimal implementation**

```python
feedback_repo.save(
    FeedbackLabel(review_id=review_id, issue_id=issue_id, label="false_positive", source="human")
)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_feedback_learning_flow.py backend/tests/api/test_human_review_api.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/domain/models/feedback.py backend/app/repositories/file_feedback_repository.py backend/app/services/review_service.py backend/app/services/orchestrator/nodes/persist_feedback.py backend/tests/services/test_feedback_learning_flow.py
git commit -m "feat: persist human feedback labels and report artifacts"
```

### Task 6: 工作台深度升级与治理页落地

**Files:**
- Modify: `frontend/src/pages/ReviewWorkbench/index.tsx`
- Modify: `frontend/src/components/review/ConversationMessageList.tsx`
- Modify: `frontend/src/components/review/IssueDetailPanel.tsx`
- Create: `frontend/src/components/review/ToolAuditPanel.tsx`
- Create: `frontend/src/components/review/KnowledgeRefPanel.tsx`
- Modify: `frontend/src/pages/Experts/index.tsx`
- Modify: `frontend/src/pages/Settings/index.tsx`
- Modify: `frontend/src/styles/global.css`

**Step 1: Write the failing test**

```tsx
it("renders tool and knowledge references for the selected issue", async () => {
  render(<ReviewWorkbenchPage />);
  expect(await screen.findByText("工具核验")).toBeInTheDocument();
  expect(await screen.findByText("知识引用")).toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- ReviewWorkbench`
Expected: FAIL because tool and knowledge panels are not rendered.

**Step 3: Write minimal implementation**

```tsx
<ToolAuditPanel issue={selectedIssue} />
<KnowledgeRefPanel issue={selectedIssue} />
```

**Step 4: Run test to verify it passes**

Run: `npm test -- ReviewWorkbench`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/pages/ReviewWorkbench/index.tsx frontend/src/components/review frontend/src/pages/Experts/index.tsx frontend/src/pages/Settings/index.tsx frontend/src/styles/global.css
git commit -m "feat: add tool audit and knowledge references to workbench"
```

### Task 7: 运营触发、历史回放与治理指标

**Files:**
- Modify: `backend/app/api/routes/streams.py`
- Modify: `backend/app/services/stream_hub.py`
- Create: `backend/app/api/routes/governance.py`
- Create: `backend/tests/api/test_governance_api.py`
- Modify: `frontend/src/pages/History/index.tsx`
- Create: `frontend/src/pages/Governance/index.tsx`
- Modify: `frontend/src/components/common/Sider/index.tsx`

**Step 1: Write the failing test**

```python
def test_governance_endpoint_returns_quality_metrics(client):
    response = client.get("/api/governance/quality-metrics")
    assert response.status_code == 200
    assert "tool_confirmation_rate" in response.json()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/api/test_governance_api.py -q`
Expected: FAIL because governance endpoints do not exist.

**Step 3: Write minimal implementation**

```python
@router.get("/governance/quality-metrics")
def quality_metrics() -> dict[str, float]:
    return {"tool_confirmation_rate": 0.0, "debate_survival_rate": 0.0}
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/api/test_governance_api.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/api/routes/governance.py backend/tests/api/test_governance_api.py frontend/src/pages/Governance/index.tsx frontend/src/pages/History/index.tsx frontend/src/components/common/Sider/index.tsx
git commit -m "feat: add governance metrics and replay surfaces"
```
