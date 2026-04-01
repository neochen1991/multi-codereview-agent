# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-expert collaborative code review system built with:
- **Backend**: FastAPI + LangGraph-style orchestrator
- **Frontend**: React 18 + TypeScript + Vite + Ant Design 5

The system creates `ReviewTask` objects for MR/Branch reviews, runs multiple expert analyzers through a LangGraph state machine, aggregates findings into issues, and provides human gating before final reports.

## Common Commands

### Start/Stop (Full Stack)
```bash
# Start both backend and frontend
bash scripts/start-all.sh

# Stop all services
bash scripts/stop-all.sh
```

### Backend Only
```bash
# Development with reload on port 8011
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8011

# Run tests
.venv/bin/pytest backend/tests -q
```

### Frontend Only
```bash
cd frontend
npm install
npm run dev      # Development server on port 5174
npm run build    # Production build
npm run typecheck # TypeScript check only
```

## High-Level Architecture

### LangGraph State Machine
The core review flow is defined in `backend/app/services/orchestrator/graph.py`:

```
ingest_subject → slice_change → expand_context → route_experts → 
run_independent_reviews → detect_conflicts → run_targeted_debate → 
evidence_verification → judge_and_merge → human_gate → 
publish_report → persist_feedback
```

Each node is implemented in `backend/app/services/orchestrator/nodes/`.

### Domain Models (backend/app/domain/models/)
- **ReviewSubject**: Describes the code being reviewed (PR/MR/Branch)
- **ReviewTask**: Lifecycle state of a review
- **Finding**: Individual expert conclusion
- **Issue**: Aggregated findings from multiple experts
- **ExpertProfile**: Expert definition including applicable file patterns

### Extension System (extensions/)
Skills and tools are loaded dynamically:
- **Skills**: `extensions/skills/<skill>/SKILL.md` + `metadata.json`
  - Declare `bound_experts` to attach to specific experts
  - Example: `design-consistency-check` binds to `correctness_business`
- **Tools**: `extensions/tools/<tool>/tool.json` + `run.py`
  - Executed as subprocess via stdin/stdout JSON protocol

### Configuration
Main config file: `config.json` (root directory)
- `llm`: Provider, base URL, model, API key
- `code_repo`: Clone URL, local path, default branch
- `runtime`: Analysis mode (light/standard), debate rounds, human gate
- `network`: SSL verification options

## Key Implementation Details

### Diff Context Strategy
The system uses three diff perspectives:
1. **Frontend Diff Preview**: Full `ReviewSubject.unified_diff` for human browsing
2. **Main Agent**: Full diff for primary business files + summaries for others
3. **Expert Agent**: Full diff for target file + summaries for other changed files

This prevents token overflow while ensuring experts see complete file context.

### Issue Confidence Calculation
Located in `backend/app/services/orchestrator/nodes/detect_conflicts.py`:
- Weighted base confidence by finding type (direct_defect=1.0, test_gap=0.8, risk_hypothesis=0.65, design_concern=0.55)
- Consensus bonus for multi-expert agreement
- Evidence bonus for cross-file evidence
- Hypothesis penalty for unverified risk hypotheses

### Expert Registry
`backend/app/services/expert_registry.py` defines built-in experts:
- `correctness_business`: Domain logic correctness
- `correctness_technical`: Technical implementation
- `security`: Security analysis
- `performance`: Performance analysis
- `maintainability`: Code maintainability
- `test_coverage`: Test gap detection

## File Naming Conventions

- Backend: `snake_case.py`
- Frontend: `PascalCase.tsx` for components, `camelCase.ts` for utilities
- Tests: `test_*.py` alongside source or in `tests/` directories
