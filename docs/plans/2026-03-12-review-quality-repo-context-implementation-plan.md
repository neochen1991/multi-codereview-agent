# Review Quality And Repo Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 提升多专家代码审核系统在真实复杂 PR 上的误报控制、漏报治理、报告可信度，并为所有专家增加目标分支源码上下文检索能力。

**Architecture:** 在现有 FastAPI + file-backed repository + LangGraph-style runtime 骨架上，先补系统级代码仓配置和 `RepositoryContextService`，再升级主 Agent 派工上下文、专家结构化协议与 Judge 裁决，最后把结果映射到报告和过程页。优先保证结果可信，再做并行与缓存优化。

**Tech Stack:** Python, FastAPI, Pydantic, file-backed repositories, ripgrep, git CLI, React, TypeScript, Ant Design

---

### Task 1: 系统级代码仓配置与源码检索基础能力

**Files:**
- Modify: `backend/app/domain/models/runtime_settings.py`
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/app/services/runtime_settings_service.py`
- Create: `backend/app/services/repository_context_service.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/pages/Settings/index.tsx`
- Test: `backend/tests/services/test_repository_context_service.py`
- Test: `backend/tests/api/test_settings_api.py`

**Step 1: Write the failing test**

```python
def test_repository_context_service_searches_local_repo(tmp_path):
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search(query="repo.search", globs=["src/**/*.ts"])
    assert result["matches"]
    assert result["matches"][0]["path"].endswith("src/service.ts")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_repository_context_service.py -q`
Expected: FAIL because the repository context service does not exist.

**Step 3: Write minimal implementation**

```python
class RepositoryContextService:
    def __init__(self, clone_url: str, local_path: Path, default_branch: str) -> None:
        self.clone_url = clone_url
        self.local_path = Path(local_path)
        self.default_branch = default_branch

    def search(self, query: str, globs: list[str] | None = None) -> dict[str, object]:
        matches = []
        for path in self.local_path.rglob("*"):
            if path.is_file() and query in path.read_text(encoding="utf-8", errors="ignore"):
                matches.append({"path": str(path), "snippet": query})
        return {"matches": matches[:20]}
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_repository_context_service.py backend/tests/api/test_settings_api.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/domain/models/runtime_settings.py backend/app/api/routes/settings.py backend/app/services/runtime_settings_service.py backend/app/services/repository_context_service.py frontend/src/services/api.ts frontend/src/pages/Settings/index.tsx backend/tests/services/test_repository_context_service.py backend/tests/api/test_settings_api.py
git commit -m "feat: add repo context runtime settings and search service"
```

### Task 2: 主 Agent 变更链路派工

**Files:**
- Modify: `backend/app/services/main_agent_service.py`
- Modify: `backend/app/services/platform_adapter.py`
- Modify: `backend/app/services/review_runner.py`
- Test: `backend/tests/services/test_main_agent_service.py`

**Step 1: Write the failing test**

```python
def test_main_agent_builds_related_file_chain_for_schedule_changes():
    agent = MainAgentService()
    chain = agent.build_change_chain(
        changed_files=[
            "packages/prisma/migrations/001.sql",
            "packages/prisma/schema.prisma",
            "apps/api/schedules/output.service.ts",
            "packages/lib/schedules/getScheduleListItemData.ts",
        ]
    )
    assert "packages/lib/schedules/getScheduleListItemData.ts" in chain.related_files
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py -q`
Expected: FAIL because related change chain context does not exist.

**Step 3: Write minimal implementation**

```python
dispatch = {
    "primary_hunk": primary_hunk,
    "supporting_hunks": supporting_hunks,
    "related_files": related_files,
    "expected_checks": expected_checks,
    "disallowed_inference": disallowed_inference,
}
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_main_agent_service.py backend/tests/services/test_review_runner.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/main_agent_service.py backend/app/services/platform_adapter.py backend/app/services/review_runner.py backend/tests/services/test_main_agent_service.py
git commit -m "feat: dispatch experts with change-chain context"
```

### Task 3: 专家结构化输出与证据分层

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/domain/models/finding.py`
- Modify: `backend/app/domain/models/message.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_expert_analysis_persists_finding_type_and_context_files(storage_root):
    runner = ReviewRunner(storage_root=storage_root)
    payload = runner.parse_expert_analysis(
        {
            "finding_type": "risk_hypothesis",
            "context_files": ["packages/lib/schedules/getScheduleListItemData.ts"],
        }
    )
    assert payload["finding_type"] == "risk_hypothesis"
    assert payload["context_files"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -q`
Expected: FAIL because structured finding type and context files are not persisted.

**Step 3: Write minimal implementation**

```python
finding.finding_type = parsed.get("finding_type", "risk_hypothesis")
finding.context_files = parsed.get("context_files", [])
finding.assumptions = parsed.get("assumptions", [])
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_review_runner.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/app/domain/models/finding.py backend/app/domain/models/message.py backend/tests/services/test_review_runner.py
git commit -m "feat: persist structured expert output metadata"
```

### Task 4: Judge 证据驱动裁决

**Files:**
- Modify: `backend/app/services/orchestrator/nodes/judge_and_merge.py`
- Modify: `backend/app/domain/models/issue.py`
- Test: `backend/tests/services/test_debate_transcript.py`

**Step 1: Write the failing test**

```python
def test_judge_keeps_risk_hypothesis_in_needs_verification():
    result = judge_issue(
        finding_type="risk_hypothesis",
        direct_evidence=False,
        tool_verified=False,
    )
    assert result["resolution"] == "needs_verification"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_debate_transcript.py -q`
Expected: FAIL because judge currently accepts most issues by default.

**Step 3: Write minimal implementation**

```python
if finding_type == "risk_hypothesis" and not direct_evidence:
    issue.resolution = "needs_verification"
    issue.status = "needs_verification"
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_debate_transcript.py backend/tests/services/test_review_runner.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/orchestrator/nodes/judge_and_merge.py backend/app/domain/models/issue.py backend/tests/services/test_debate_transcript.py
git commit -m "feat: make judge evidence-driven"
```

### Task 5: 报告分层展示

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/components/review/FindingsPanel.tsx`
- Modify: `frontend/src/components/review/ReportSummaryPanel.tsx`
- Modify: `frontend/src/pages/ReviewWorkbench/index.tsx`

**Step 1: Write the failing test**

```tsx
it("groups findings by accepted risks, verification-needed risks, and test gaps", () => {
  render(<FindingsPanel findings={fixtures} />);
  expect(screen.getByText("阻塞问题")).toBeInTheDocument();
  expect(screen.getByText("待验证风险")).toBeInTheDocument();
  expect(screen.getByText("测试与回归缺口")).toBeInTheDocument();
});
```

**Step 2: Run test to verify it fails**

Run: `npm test -- FindingsPanel`
Expected: FAIL because current findings panel does not group by finding type and resolution.

**Step 3: Write minimal implementation**

```tsx
const accepted = findings.filter((item) => item.resolution === "accepted");
const needsVerification = findings.filter((item) => item.resolution === "needs_verification");
const testGaps = findings.filter((item) => item.finding_type === "test_gap");
```

**Step 4: Run test to verify it passes**

Run: `npm run build`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/services/api.ts frontend/src/components/review/FindingsPanel.tsx frontend/src/components/review/ReportSummaryPanel.tsx frontend/src/pages/ReviewWorkbench/index.tsx
git commit -m "feat: group report findings by review confidence layer"
```

### Task 6: 并行与缓存优化

**Files:**
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/knowledge_retrieval_service.py`
- Modify: `backend/app/services/repository_context_service.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write the failing test**

```python
def test_repository_context_service_caches_same_query(tmp_path):
    service = RepositoryContextService("https://github.com/example/repo.git", tmp_path, "main")
    first = service.search("foo", ["src/**/*.ts"])
    second = service.search("foo", ["src/**/*.ts"])
    assert second["cache_hit"] is True
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest backend/tests/services/test_repository_context_service.py backend/tests/services/test_review_runner.py -q`
Expected: FAIL because no cache metadata exists.

**Step 3: Write minimal implementation**

```python
cache_key = (query, tuple(globs or []))
if cache_key in self._cache:
    return {"matches": self._cache[cache_key], "cache_hit": True}
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest backend/tests/services/test_repository_context_service.py backend/tests/services/test_review_runner.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/services/review_runner.py backend/app/services/knowledge_retrieval_service.py backend/app/services/repository_context_service.py backend/tests/services/test_repository_context_service.py backend/tests/services/test_review_runner.py
git commit -m "feat: cache repo context retrieval and reduce serial review latency"
```
