from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask
from app.repositories.fs import read_json, write_json


class ReviewLearningService:
    """把人工反馈沉淀为可检索的误报边界案例。"""

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = Path(storage_root)
        self._cases_path = self.storage_root / "review_learning" / "cases.json"

    def list_cases(self, *, repo_id: str = "", issue_type: str = "") -> list[dict[str, object]]:
        cases = self._load_cases()
        normalized_repo = self._normalize(repo_id)
        normalized_issue_type = self._normalize(issue_type)
        result = []
        for case in cases:
            if normalized_repo and self._normalize(case.get("repo_id")) != normalized_repo:
                continue
            if normalized_issue_type and self._normalize(case.get("issue_type")) != normalized_issue_type:
                continue
            result.append(case)
        return sorted(result, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def record_issue_decision_case(
        self,
        *,
        review: ReviewTask,
        issue: DebateIssue,
        decision: str,
        comment: str,
    ) -> dict[str, object] | None:
        normalized_decision = self._normalize(decision)
        if normalized_decision != "rejected":
            return None
        cases = self._load_cases()
        case_payload = self._build_case_payload(review=review, issue=issue, comment=comment)
        dedupe_key = str(case_payload.get("dedupe_key") or "")
        cases = [case for case in cases if str(case.get("dedupe_key") or "") != dedupe_key]
        cases.append(case_payload)
        write_json(self._cases_path, cases)
        return case_payload

    def build_prompt_hints(
        self,
        *,
        repo_id: str,
        issue_types: list[str] | None = None,
        file_paths: list[str] | None = None,
        max_items: int = 3,
        max_chars: int = 600,
    ) -> str:
        scored = self._score_cases_for_context(
            repo_id=repo_id,
            issue_types=issue_types or [],
            file_paths=file_paths or [],
            issue={},
        )
        summaries: list[str] = []
        seen: set[str] = set()
        for score, case in scored:
            if not self._case_has_prompt_relevance(case, issue_types or [], file_paths or []):
                continue
            if score < 0.35:
                continue
            summary = str(case.get("learning_summary") or "").strip()
            if not summary or summary in seen:
                continue
            seen.add(summary)
            summaries.append(f"- {summary}")
            if len(summaries) >= max(1, max_items):
                break
        if not summaries:
            return ""
        text = "历史误报边界:\n" + "\n".join(summaries)
        return self._compact(text, max_chars)

    def _case_has_prompt_relevance(
        self,
        case: dict[str, object],
        issue_types: list[str],
        file_paths: list[str],
    ) -> bool:
        normalized_issue_types = {self._normalize(item) for item in issue_types if self._normalize(item)}
        case_issue_type = self._normalize(case.get("issue_type"))
        if case_issue_type and case_issue_type in normalized_issue_types:
            return True
        normalized_file_paths = {self._normalize_path(item) for item in file_paths if str(item or "").strip()}
        if not normalized_file_paths:
            return False
        if self._normalize_path(case.get("file_path")) in normalized_file_paths:
            return True
        return any(
            self._normalize_path(context_file) in normalized_file_paths
            for context_file in list(case.get("context_files") or [])
        )

    def evaluate_issue_candidate(
        self,
        *,
        repo_id: str,
        issue: dict[str, object],
    ) -> dict[str, object]:
        issue_type = self._issue_type(issue)
        scored = self._score_cases_for_context(
            repo_id=repo_id,
            issue_types=[issue_type] if issue_type else [],
            file_paths=[str(issue.get("file_path") or "")],
            issue=issue,
        )
        if not scored:
            return {"action": "keep", "similarity": 0.0, "matched_case_id": "", "reason": ""}
        score, case = scored[0]
        if score >= 0.85:
            return {
                "action": "reject",
                "similarity": round(score, 2),
                "matched_case_id": str(case.get("case_id") or ""),
                "reason": (
                    "命中高相似历史人工驳回案例："
                    f"{case.get('learning_summary') or '当前结论与已驳回误报边界一致。'}"
                ),
            }
        if score >= 0.7:
            return {
                "action": "needs_verification",
                "similarity": round(score, 2),
                "matched_case_id": str(case.get("case_id") or ""),
                "reason": "命中相似历史误报案例，进入人工或工具复核后再进入正式议题。",
            }
        return {
            "action": "keep",
            "similarity": round(score, 2),
            "matched_case_id": str(case.get("case_id") or ""),
            "reason": "",
        }

    def _build_case_payload(
        self,
        *,
        review: ReviewTask,
        issue: DebateIssue,
        comment: str,
    ) -> dict[str, object]:
        issue_type = self._normalize(issue.normalized_issue_type or issue.category_label or issue.finding_type)
        text_blob = self._issue_text(issue.model_dump(mode="json"), extra=[comment])
        reason_category = self._classify_reason_category(issue_type, text_blob)
        learning_summary = self._build_learning_summary(issue_type, reason_category, issue, comment)
        repo_id = str(review.subject.repo_id or "").strip()
        claim_pattern = self._claim_pattern(issue_type, text_blob)
        counterexample_pattern = self._counterexample_pattern(text_blob)
        return {
            "case_id": f"rlc_{uuid4().hex[:12]}",
            "review_id": review.review_id,
            "issue_id": issue.issue_id,
            "repo_id": repo_id,
            "project_id": review.subject.project_id,
            "issue_type": issue_type,
            "decision": "rejected",
            "reason_category": reason_category,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
            "claim": issue.summary or issue.title,
            "evidence": list(issue.evidence or []),
            "cross_file_evidence": list(issue.cross_file_evidence or []),
            "context_files": list(issue.context_files or []),
            "counter_evidence": comment,
            "learning_summary": learning_summary,
            "claim_pattern": claim_pattern,
            "counterexample_pattern": counterexample_pattern,
            "status": "active",
            "created_at": datetime.now(UTC).isoformat(),
            "dedupe_key": f"{repo_id}:{issue_type}:{issue.file_path}:{issue.line_start}:{claim_pattern}:{counterexample_pattern}",
        }

    def _score_cases_for_context(
        self,
        *,
        repo_id: str,
        issue_types: list[str],
        file_paths: list[str],
        issue: dict[str, object],
    ) -> list[tuple[float, dict[str, object]]]:
        normalized_repo = self._normalize(repo_id)
        normalized_issue_types = {self._normalize(item) for item in issue_types if self._normalize(item)}
        normalized_file_paths = {self._normalize_path(item) for item in file_paths if str(item or "").strip()}
        issue_text = self._issue_text(issue)
        issue_claim_pattern = self._claim_pattern(self._issue_type(issue), issue_text)
        issue_counterexample_pattern = self._counterexample_pattern(issue_text)
        scored: list[tuple[float, dict[str, object]]] = []
        for case in self._load_cases():
            if str(case.get("status") or "active") != "active":
                continue
            score = 0.0
            if normalized_repo and self._normalize(case.get("repo_id")) == normalized_repo:
                score += 0.25
            case_issue_type = self._normalize(case.get("issue_type"))
            if case_issue_type and case_issue_type in normalized_issue_types:
                score += 0.25
            if issue_claim_pattern and issue_claim_pattern == str(case.get("claim_pattern") or ""):
                score += 0.20
            if issue_counterexample_pattern and issue_counterexample_pattern == str(case.get("counterexample_pattern") or ""):
                score += 0.20
            if self._path_or_symbol_matches(case, normalized_file_paths, issue_text):
                score += 0.10
            if score > 0:
                scored.append((round(score, 4), case))
        return sorted(scored, key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)

    def _path_or_symbol_matches(
        self,
        case: dict[str, object],
        file_paths: set[str],
        issue_text: str,
    ) -> bool:
        case_path = self._normalize_path(case.get("file_path"))
        if case_path and case_path in file_paths:
            return True
        for context_file in list(case.get("context_files") or []):
            if self._normalize_path(context_file) in file_paths:
                return True
        symbols = set(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", str(case.get("claim") or "")))
        symbols.update(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", " ".join(str(item) for item in case.get("evidence") or [])))
        return any(symbol and symbol in issue_text for symbol in symbols)

    def _classify_reason_category(self, issue_type: str, text_blob: str) -> str:
        if issue_type == "comment_contract_unimplemented" and self._counterexample_pattern(text_blob):
            return "context_counterexample"
        return "false_positive"

    def _build_learning_summary(
        self,
        issue_type: str,
        reason_category: str,
        issue: DebateIssue,
        comment: str,
    ) -> str:
        if issue_type == "comment_contract_unimplemented" and reason_category == "context_counterexample":
            return (
                "comment_contract_unimplemented：接口契约类问题必须先检查实现类；"
                "实现类已 implements 对应接口并存在同名非占位 @Override 方法体时，不要报承诺未落地。"
            )
        subject = self._compact(comment or issue.summary or issue.title, 120)
        return f"{issue_type or 'general'}：历史人工驳回为误报，后续同类结论必须补足反例排除证据；{subject}"

    def _claim_pattern(self, issue_type: str, text_blob: str) -> str:
        if issue_type == "comment_contract_unimplemented":
            if any(token in text_blob for token in ["承诺未落地", "未实现", "unimplemented", "接口声明", "接口定义"]):
                return "interface_contract_unimplemented"
        return self._normalize(issue_type) or "general"

    def _counterexample_pattern(self, text_blob: str) -> str:
        has_interface_impl = any(token in text_blob for token in ["implements", "实现类", "已实现", "implements "])
        has_override = "@override" in text_blob or "override" in text_blob
        has_body = any(token in text_blob for token in ["return ", "{ return", "jdbc.query", "service.", "mapper."])
        if has_interface_impl and has_override and has_body:
            return "implemented_interface_override_body"
        if has_interface_impl and ("同名" in text_blob or "方法体" in text_blob):
            return "implemented_interface_override_body"
        return ""

    def _issue_type(self, issue: dict[str, object]) -> str:
        return self._normalize(
            issue.get("normalized_issue_type")
            or issue.get("category_label")
            or issue.get("finding_type")
            or ""
        )

    def _issue_text(self, issue: dict[str, object] | DebateIssue, *, extra: list[str] | None = None) -> str:
        if isinstance(issue, DebateIssue):
            payload = issue.model_dump(mode="json")
        else:
            payload = dict(issue or {})
        chunks: list[str] = []
        for key in (
            "title",
            "summary",
            "claim",
            "file_path",
            "normalized_issue_type",
            "category_label",
            "finding_type",
            "counter_evidence",
        ):
            value = payload.get(key)
            if value:
                chunks.append(str(value))
        for key in ("evidence", "cross_file_evidence", "context_files", "assumptions"):
            chunks.extend(str(item) for item in list(payload.get(key) or []) if str(item).strip())
        chunks.extend(str(item) for item in extra or [] if str(item).strip())
        return "\n".join(chunks).lower()

    def _load_cases(self) -> list[dict[str, object]]:
        if not self._cases_path.exists():
            return []
        try:
            payload = read_json(self._cases_path)
        except Exception:
            return []
        return [dict(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _normalize(self, value: object) -> str:
        return str(value or "").strip().lower()

    def _normalize_path(self, value: object) -> str:
        return self._normalize(str(value or "").replace("\\", "/"))

    def _compact(self, text: str, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 12)].rstrip() + "...<已截断>"
