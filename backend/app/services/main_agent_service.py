from __future__ import annotations

import os
import re

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewSubject, ReviewTask
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.diff_excerpt_service import DiffExcerptService
from app.services.expert_capability_service import ExpertCapabilityService
from app.services.llm_chat_service import LLMChatService, LLMTextResult
from app.services.repository_context_service import RepositoryContextService


class MainAgentService:
    agent_id = "main_agent"
    agent_name = "MainAgent"

    def __init__(self) -> None:
        self._llm = LLMChatService()
        self._diff_excerpt_service = DiffExcerptService()
        self._capability_service = ExpertCapabilityService()

    def build_command(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        runtime_settings: RuntimeSettings,
    ) -> dict[str, object]:
        change_chain = self.build_change_chain(subject)
        repository_service = self._build_repository_service(runtime_settings)
        target_focus = self._pick_target_focus(subject, expert, repository_service)
        file_path = str(target_focus.get("file_path") or self._pick_file_path(subject, expert))
        line_start = int(target_focus.get("line_start") or self._pick_line_start(subject, expert.expert_id, file_path))
        target_hunk = dict(target_focus.get("target_hunk") or {})
        related_files = self._build_expert_related_files(subject, expert, file_path, change_chain["related_files"])
        routing_repo_excerpt = self._format_repo_matches(dict(target_focus.get("repo_hits") or {}))
        routing_reason = self._capability_service.build_routing_reason(
            expert,
            file_path,
            str(target_hunk.get("excerpt") or ""),
            routing_repo_excerpt,
        )
        expected_checks = self._build_expected_checks(expert, change_chain)
        disallowed_inference = self._build_disallowed_inference(expert)
        repo_context = self._build_repository_context(
            repository_service,
            file_path,
            line_start,
            related_files,
            dict(target_focus.get("repo_hits") or {}),
        )
        routeable, skip_reason = self._should_route_expert(expert, target_focus, file_path)
        summary = self._build_command_fallback(
            subject,
            expert,
            file_path,
            line_start,
            target_hunk=target_hunk,
            routing_reason=routing_reason,
            expected_checks=expected_checks,
            disallowed_inference=disallowed_inference,
            related_files=related_files,
        )
        return {
            "expert_id": expert.expert_id,
            "expert_name": expert.name_zh,
            "file_path": file_path,
            "line_start": line_start,
            "related_files": related_files,
            "target_hunk": target_hunk,
            "repository_context": repo_context,
            "expected_checks": expected_checks,
            "disallowed_inference": disallowed_inference,
            "routeable": routeable,
            "skip_reason": skip_reason,
            "summary": summary,
            "llm": {
                "provider": "main-agent-template",
                "model": "template",
                "base_url": "",
                "api_key_env": "",
                "mode": "template",
                "error": "",
            },
        }

    def build_change_chain(self, subject: ReviewSubject) -> dict[str, object]:
        changed_files = [item for item in subject.changed_files if item]
        related_files: list[str] = []
        token_links = {
            "migration": ["schema", "repository", "service", "transform", "output"],
            "schema": ["migration", "repository", "service", "transform", "output"],
            "service": ["transform", "output", "schema", "migration"],
            "transform": ["service", "output", "schema", "migration"],
            "output": ["service", "transform", "schema", "migration"],
            "repository": ["schema", "migration", "service"],
        }
        lowered = {path: path.lower() for path in changed_files}
        for path in changed_files:
            if path not in related_files:
                related_files.append(path)
            current = lowered[path]
            for token, candidates in token_links.items():
                if token not in current:
                    continue
                for candidate_path, candidate_lowered in lowered.items():
                    if candidate_path in related_files:
                        continue
                    if any(candidate in candidate_lowered for candidate in candidates):
                        related_files.append(candidate_path)
        return {
            "primary_files": changed_files[:2],
            "related_files": related_files[:8] or changed_files[:],
        }

    def build_final_summary(
        self,
        review: ReviewTask,
        issues: list[DebateIssue],
        runtime_settings: RuntimeSettings,
    ) -> tuple[str, dict[str, object]]:
        blocker_count = len([issue for issue in issues if issue.severity in {"blocker", "critical"}])
        pending_count = len([issue for issue in issues if issue.needs_human and issue.status != "resolved"])
        resolution = self._llm.resolve_main_agent(runtime_settings)
        fallback_text = (
            f"主Agent收敛完成：本次共形成 {len(issues)} 个议题，其中 blocker/critical {blocker_count} 个，"
            f"待人工裁决 {pending_count} 个，审核状态 {review.status}，下一步请优先处理高风险结论。"
        )
        user_prompt = (
            f"审核状态: {review.status}\n"
            f"审核阶段: {review.phase}\n"
            f"议题总数: {len(issues)}\n"
            f"高风险议题数: {blocker_count}\n"
            f"待人工裁决数: {pending_count}\n"
            f"请输出一段中文总结，风格像主Agent对控制台的收敛播报。"
        )
        result = self._llm.complete_text(
            system_prompt="你是主代码审查协调Agent，负责在多专家完成分析后输出最终的收敛播报。",
            user_prompt=user_prompt,
            resolution=resolution,
            fallback_text=fallback_text,
            allow_fallback=self._allow_fallback(runtime_settings),
        )
        return result.text.strip(), self._llm_metadata(result)

    def _allow_fallback(self, runtime_settings: RuntimeSettings) -> bool:
        return bool(runtime_settings.allow_llm_fallback or os.getenv("PYTEST_CURRENT_TEST"))

    def _build_command_fallback(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        *,
        target_hunk: dict[str, object] | None = None,
        routing_reason: str = "",
        expected_checks: list[str] | None = None,
        disallowed_inference: list[str] | None = None,
        related_files: list[str] | None = None,
    ) -> str:
        focus = expert.focus_areas[0] if expert.focus_areas else expert.role
        checks_text = " / ".join(list(expected_checks or [])[:4]) or focus
        disallowed_text = " / ".join(list(disallowed_inference or [])[:3]) or "不要越界评论"
        related_text = " / ".join(list(related_files or [])[:4]) or "无"
        hunk_header = str((target_hunk or {}).get("hunk_header") or "未定位到明确 hunk")
        return (
            f"**派工指令**\n\n"
            f"**目标专家：** {expert.expert_id} / {expert.name_zh}\n\n"
            f"**审查对象：** {subject.title or subject.mr_url or subject.source_ref}\n\n"
            f"**定向任务：** 请聚焦文件 `{file_path}` 第 **{line_start} 行** 附近的变更，"
            f"重点从“{focus}”视角审查。\n\n"
            f"**目标 hunk：** {hunk_header}\n"
            f"**派工理由：** {routing_reason or f'该变更与 {focus} 风险直接相关'}\n"
            f"**关联文件：** {related_text}\n"
            f"**必查项：** {checks_text}\n"
            f"**禁止推断：** {disallowed_text}\n\n"
            f"请明确给出：1. 代码证据 2. 问题倾向 3. 修复建议。"
        )

    def _build_expected_checks(self, expert: ExpertProfile, change_chain: dict[str, object]) -> list[str]:
        checks = list(expert.required_checks)
        if change_chain.get("related_files") and "跨文件一致性" not in checks:
            checks.append("跨文件一致性")
        return checks[:6]

    def _build_disallowed_inference(self, expert: ExpertProfile) -> list[str]:
        rules = [
            "不要仅凭 import 变化断言未完成需求",
            "不要仅凭命名猜测权限缺陷",
            "证据不足时只能输出待验证风险",
        ]
        rules.extend(expert.out_of_scope)
        deduped: list[str] = []
        for item in rules:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:6]

    def _pick_file_path(self, subject: ReviewSubject, expert: ExpertProfile) -> str:
        changed_files = list(subject.changed_files)
        if not changed_files:
            return "src/review/runtime.py"
        ranked = sorted(
            changed_files,
            key=lambda item: self._capability_service.score_file_relevance(expert, item),
            reverse=True,
        )
        if ranked and self._capability_service.score_file_relevance(expert, ranked[0]) > 0:
            return ranked[0]
        expert_id = expert.expert_id
        if expert_id == "security_compliance":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["auth", "security", "permission", "token"]):
                    return file_path
        if expert_id in {"performance_reliability", "compatibility_change_impact"}:
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository"]):
                    return file_path
        if expert_id == "database_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["migration", "sql", "schema", "db", "repository", "dao"]):
                    return file_path
        if expert_id == "redis_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["redis", "cache"]):
                    return file_path
        if expert_id == "mq_analysis":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["mq", "kafka", "rocketmq", "rabbit", "queue", "consumer", "producer"]):
                    return file_path
        if expert_id == "ddd_specification":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["domain", "aggregate", "entity", "repository", "service", "application"]):
                    return file_path
        if expert_id == "test_verification":
            for file_path in changed_files:
                if any(token in file_path.lower() for token in ["test", "spec", "playwright", "jest", "vitest"]):
                    return file_path
        return changed_files[0]

    def _pick_target_focus(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        repository_service: RepositoryContextService,
    ) -> dict[str, object]:
        changed_files = [item for item in subject.changed_files if item]
        if expert.expert_id == "correctness_business":
            preferred = self._pick_correctness_chain_focus(subject, repository_service)
            if preferred:
                return preferred
        best_candidate: dict[str, object] | None = None
        best_score = -1
        for file_path in self._ordered_changed_files(subject, expert, changed_files):
            hunks = self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path)
            if not hunks:
                fallback_line = self._pick_line_start(subject, expert.expert_id, file_path)
                hunks = [
                    {
                        "file_path": file_path,
                        "hunk_header": "",
                        "start_line": fallback_line,
                        "end_line": fallback_line,
                        "changed_lines": [fallback_line],
                        "excerpt": self._diff_excerpt_service.extract_excerpt(subject.unified_diff, file_path, fallback_line),
                    }
                ]
            for hunk in hunks:
                repo_hits = self._search_related_repo_context(repository_service, file_path, hunk)
                score = self._capability_service.score_hunk_relevance(
                    expert,
                    file_path,
                    str(hunk.get("excerpt") or ""),
                    self._format_repo_matches(repo_hits),
                )
                if score <= best_score:
                    continue
                changed_lines = [int(item) for item in list(hunk.get("changed_lines") or []) if isinstance(item, int)]
                line_start = changed_lines[0] if changed_lines else int(hunk.get("start_line") or 1)
                best_candidate = {
                    "file_path": file_path,
                    "line_start": line_start,
                    "target_hunk": hunk,
                    "repo_hits": repo_hits,
                    "score": score,
                }
                best_score = score
        return best_candidate or {}

    def _pick_correctness_chain_focus(
        self,
        subject: ReviewSubject,
        repository_service: RepositoryContextService,
    ) -> dict[str, object]:
        changed_files = [item for item in subject.changed_files if item]
        for file_path in changed_files:
            lowered = file_path.lower()
            if "transform" not in lowered:
                continue
            hunks = self._diff_excerpt_service.list_hunks(subject.unified_diff, file_path)
            for hunk in hunks:
                excerpt = str(hunk.get("excerpt") or "")
                if not any(token in excerpt for token in ["createdAt", "updatedAt", "override"]):
                    continue
                changed_lines = [int(item) for item in list(hunk.get("changed_lines") or []) if isinstance(item, int)]
                line_start = changed_lines[0] if changed_lines else int(hunk.get("start_line") or 1)
                repo_hits = self._search_related_repo_context(repository_service, file_path, hunk)
                return {
                    "file_path": file_path,
                    "line_start": line_start,
                    "target_hunk": hunk,
                    "repo_hits": repo_hits,
                    "score": 100,
                }
        return {}

    def _ordered_changed_files(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        changed_files: list[str],
    ) -> list[str]:
        if expert.expert_id != "correctness_business":
            return changed_files

        def priority(path: str) -> tuple[int, int]:
            lowered = path.lower()
            excerpt = self._diff_excerpt_service.extract_excerpt(subject.unified_diff, path, 1)
            score = 99
            if "transform" in lowered:
                score = 0
            elif "output" in lowered:
                score = 1
            elif "service" in lowered:
                score = 2
            elif "schema" in lowered or "migration" in lowered:
                score = 3
            if any(token in excerpt for token in ["createdAt", "updatedAt", "override"]):
                score -= 1
            return score, len(path)

        return sorted(changed_files, key=priority)

    def _pick_line_start(self, subject: ReviewSubject, expert_id: str, file_path: str) -> int:
        preferred_line = 12
        if expert_id == "security_compliance":
            preferred_line = 18
        elif expert_id == "architecture_design":
            preferred_line = 42
        elif expert_id == "performance_reliability":
            preferred_line = 57
        elif expert_id == "database_analysis":
            preferred_line = 36
        elif expert_id == "redis_analysis":
            preferred_line = 28
        elif expert_id == "mq_analysis":
            preferred_line = 30
        elif expert_id == "ddd_specification":
            preferred_line = 40
        elif expert_id == "test_verification":
            preferred_line = 73

        return self._diff_excerpt_service.find_nearest_line(
            subject.unified_diff,
            file_path,
            preferred_line,
        ) or preferred_line

    def _build_expert_related_files(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        related_files: list[str],
    ) -> list[str]:
        if expert.expert_id != "correctness_business":
            return related_files
        chain_tokens = ("transform", "output", "service", "schema", "migration")
        prioritized: list[str] = []
        for path in subject.changed_files:
            lowered = path.lower()
            if any(token in lowered for token in chain_tokens) and path not in prioritized:
                prioritized.append(path)
        for path in related_files:
            if path not in prioritized:
                prioritized.append(path)
        if file_path in prioritized:
            prioritized.remove(file_path)
        return [file_path, *prioritized][:8]

    def _should_route_expert(
        self,
        expert: ExpertProfile,
        target_focus: dict[str, object],
        file_path: str,
    ) -> tuple[bool, str]:
        score = int(target_focus.get("score") or 0)
        target_hunk = dict(target_focus.get("target_hunk") or {})
        import_only = self._is_import_only_hunk(str(target_hunk.get("excerpt") or ""))
        expert_id = expert.expert_id
        lowered = file_path.lower()
        excerpt_lowered = str(target_hunk.get("excerpt") or "").lower()

        if expert_id == "mq_analysis" and not any(token in f"{lowered}\n{excerpt_lowered}" for token in ["mq", "queue", "kafka", "rabbit", "consumer", "producer"]):
            return False, "当前变更未命中该中间件专家的关键线索"
        if expert_id == "redis_analysis" and not any(token in f"{lowered}\n{excerpt_lowered}" for token in ["redis", "cache", "ttl", "expire", "setnx", "pipeline"]):
            return False, "当前变更未命中该缓存专家的关键线索"
        if expert_id == "security_compliance" and not any(token in f"{lowered}\n{excerpt_lowered}" for token in ["auth", "security", "permission", "token", "secret"]):
            return False, "当前变更未命中安全相关线索"
        if expert_id == "frontend_accessibility" and "frontend" not in lowered:
            return False, "当前变更不属于前端可访问性审查范围"
        if expert_id in {"ddd_specification", "architecture_design", "maintainability_code_health"} and import_only:
            return False, "当前 hunk 仅为 import 级调整，缺少足够的结构性审查信号"
        return True, ""

    def _is_import_only_hunk(self, excerpt: str) -> bool:
        if not excerpt.strip():
            return False
        changed_lines: list[str] = []
        for line in excerpt.splitlines():
            cleaned = line
            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[1]
            cleaned = cleaned.strip()
            if cleaned.startswith("# "):
                continue
            if not cleaned.startswith(("+", "-")):
                continue
            changed_lines.append(cleaned)
        return bool(changed_lines) and all(line.startswith(("+import", "-import")) for line in changed_lines)

    def _llm_metadata(self, result: LLMTextResult) -> dict[str, object]:
        return {
            "provider": result.provider,
            "model": result.model,
            "base_url": result.base_url,
            "api_key_env": result.api_key_env,
            "mode": result.mode,
            "error": result.error,
        }

    def _build_repository_context(
        self,
        service: RepositoryContextService,
        file_path: str,
        line_start: int,
        related_files: list[str],
        repo_hits: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not service.is_ready():
            return {
                "summary": "代码仓上下文未配置或本地仓不可用",
                "primary_context": {},
                "related_contexts": [],
                "search_matches": [],
                "context_files": [],
            }
        primary_context = service.load_file_context(file_path, line_start, radius=10)
        repo_hit_matches = list((repo_hits or {}).get("matches", []) or [])
        repo_hit_paths = [
            str(item.get("path") or "").strip()
            for item in repo_hit_matches
            if str(item.get("path") or "").strip() and str(item.get("path") or "").strip() != file_path
        ]
        related_contexts = [
            service.load_file_context(item, 1, radius=8)
            for item in [*related_files[:3], *repo_hit_paths[:3]]
            if item != file_path
        ]
        context_files: list[str] = []
        for item in [file_path, *related_files[:3], *repo_hit_paths[:3]]:
            normalized = str(item).strip()
            if normalized and normalized not in context_files:
                context_files.append(normalized)
        return {
            "summary": (
                f"已补充 {len(context_files)} 个目标分支文件上下文，"
                f"并命中 {len(repo_hit_matches)} 条关联代码检索结果"
            ),
            "primary_context": primary_context,
            "related_contexts": related_contexts,
            "search_matches": repo_hit_matches,
            "symbol_contexts": list((repo_hits or {}).get("symbol_contexts", []) or []),
            "context_files": context_files,
        }

    def _build_repository_service(self, runtime_settings: RuntimeSettings) -> RepositoryContextService:
        return RepositoryContextService(
            clone_url=runtime_settings.code_repo_clone_url,
            local_path=runtime_settings.code_repo_local_path,
            default_branch=runtime_settings.code_repo_default_branch or runtime_settings.default_target_branch,
            access_token=runtime_settings.code_repo_access_token,
            auto_sync=runtime_settings.code_repo_auto_sync,
        )

    def _search_related_repo_context(
        self,
        service: RepositoryContextService,
        file_path: str,
        hunk: dict[str, object],
    ) -> dict[str, object]:
        if not service.is_ready():
            return {"queries": [], "matches": [], "symbol_contexts": []}
        queries = self._derive_repo_queries(file_path, hunk)
        if not queries:
            return {"queries": [], "matches": [], "symbol_contexts": []}
        search_result = service.search_many(queries, globs=None, limit_per_query=4, total_limit=8)
        symbol_contexts = [
            service.search_symbol_context(query, globs=None, definition_limit=2, reference_limit=3)
            for query in queries[:3]
        ]
        return {
            **search_result,
            "symbol_contexts": symbol_contexts,
        }

    def _derive_repo_queries(self, file_path: str, hunk: dict[str, object]) -> list[str]:
        tokens: list[str] = []
        stem = file_path.rsplit("/", 1)[-1].split(".", 1)[0]
        if stem:
            tokens.append(stem)
        excerpt = str(hunk.get("excerpt") or "")
        for match in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", excerpt):
            if match.lower() in {"diff", "const", "return", "true", "false", "null", "none"}:
                continue
            if match not in tokens:
                tokens.append(match)
            if len(tokens) >= 6:
                break
        return tokens[:6]

    def _format_repo_matches(self, repo_hits: dict[str, object]) -> str:
        matches = list(repo_hits.get("matches", []) or [])
        fragments: list[str] = []
        for item in matches[:4]:
            path = str(item.get("path") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if path:
                fragments.append(f"{path} {snippet}".strip())
        for symbol_context in list(repo_hits.get("symbol_contexts", []) or [])[:2]:
            if not isinstance(symbol_context, dict):
                continue
            symbol = str(symbol_context.get("symbol") or "").strip()
            definitions = list(symbol_context.get("definitions", []) or [])
            references = list(symbol_context.get("references", []) or [])
            if symbol:
                fragments.append(f"symbol:{symbol} defs={len(definitions)} refs={len(references)}")
        return "\n".join(fragments)
