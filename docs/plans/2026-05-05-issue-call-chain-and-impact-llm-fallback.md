# Issue Call Chain And Impact LLM Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show issue-level call relationships as Mermaid call-chain diagrams and make GitNexus impact reporting degrade to a deterministic fact-based report when the LLM times out.

**Architecture:** Frontend extracts call-chain facts from existing finding code context and evidence chain, then renders a reusable Mermaid block in the issue detail panel. Backend keeps GitNexus graph analysis as the source of truth and treats the LLM as optional report synthesis: use 120s for the primary call, and fall back to deterministic template rendering if the LLM fails or times out.

**Tech Stack:** FastAPI/Python services, Pydantic report models, React/TypeScript, Ant Design, Mermaid.

---

### Task 1: Impact LLM Fallback

**Files:**
- Modify: `backend/app/services/change_impact_report_service.py`
- Test: `backend/tests/services/test_change_impact_report_service.py`

**Steps:**
1. Add a failing test where `LLMChatService.complete_text` raises a timeout-like `RuntimeError`.
2. Verify the test fails because `synthesize()` currently raises instead of returning a report.
3. Update `synthesize()` to call LLM with `timeout_seconds=120.0`.
4. Catch LLM synthesis failures and use `_fallback_payload()` plus `_render_template()` to return an `ImpactReport` with `llm_generated=False`, `llm_markdown` populated when possible, and a limitation explaining the LLM fallback.
5. Run the targeted test.

### Task 2: Issue Detail Mermaid Call Chain

**Files:**
- Create: `frontend/src/components/review/callChainGraph.ts`
- Create: `frontend/src/components/review/MermaidBlock.tsx`
- Modify: `frontend/src/components/review/CodeReviewConclusionPanel.tsx`
- Modify: `frontend/src/styles/global.css`

**Steps:**
1. Add pure functions that convert `finding.code_context` and `evidence_chain` into Mermaid `flowchart LR`.
2. Add a lightweight Mermaid renderer component using the existing `mermaid` dependency.
3. Render a “调用关系依据” section in problem details when a chart is available; otherwise show the old text evidence.
4. Run frontend typecheck.

### Task 3: Verification

**Files:**
- Existing files above.

**Steps:**
1. Run backend targeted tests for change impact and GitNexus.
2. Run `npm run typecheck`.
3. Run `git diff --check`.
