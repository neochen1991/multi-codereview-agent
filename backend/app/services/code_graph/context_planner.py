from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from app.domain.models.event import ReviewEvent

logger = logging.getLogger(__name__)


class CodeGraphSearchService(Protocol):
    """Protocol implemented by code-graph search services."""

    def search_related_context(
        self,
        *,
        repository_id: str,
        changed_files: list[str],
        changed_symbols: list[str],
        limit: int,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[str, object]:
        """Return related contexts extracted from the local AST graph."""


class CodeGraphContextPlanner:
    """Build review context with code graph first and keyword fallback."""

    def __init__(self, code_graph_service: CodeGraphSearchService | None = None) -> None:
        self.code_graph_service = code_graph_service

    def build_context_bundle(
        self,
        *,
        review_id: str,
        repository_id: str,
        changed_files: list[str],
        changed_symbols: list[str],
        repository_context_service: Any,
        limit: int = 12,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[str, Any]:
        normalized_files = self._dedupe(changed_files)
        normalized_symbols = self._normalize_symbols(changed_symbols, normalized_files)
        normalized_ranges = self._normalize_changed_ranges(changed_ranges or {})
        events: list[ReviewEvent] = [
            ReviewEvent(
                review_id=review_id,
                event_type="code_graph_context_started",
                phase="context",
                message="正在使用代码结构图谱检索关联上下文",
                payload={
                    "repository_id": repository_id,
                    "changed_files": normalized_files,
                    "changed_symbols": normalized_symbols,
                    "changed_ranges": normalized_ranges,
                    "context_source": "tree_sitter",
                },
            )
        ]

        graph_result = self._search_code_graph(
            repository_id=repository_id,
            changed_files=normalized_files,
            changed_symbols=normalized_symbols,
            changed_ranges=normalized_ranges,
            repository_context_service=repository_context_service,
            limit=limit,
        )
        logger.info(
            "tree_sitter context planner io review_id=%s repository_id=%s request=%s response=%s",
            review_id,
            repository_id,
            self._compact_json(
                {
                    "changed_files": normalized_files,
                    "changed_symbols": normalized_symbols,
                    "changed_ranges": normalized_ranges,
                    "limit": limit,
                }
            ),
            self._compact_json(
                {
                    "ready": graph_result.get("ready"),
                    "fallback_reason": graph_result.get("fallback_reason"),
                    "context_count": len(list(graph_result.get("contexts") or [])),
                    "minimal_context": graph_result.get("minimal_context"),
                    "impact_analysis": graph_result.get("impact_analysis"),
                    "stats": graph_result.get("stats"),
                }
            ),
        )
        graph_contexts = self._normalize_graph_contexts(graph_result.get("contexts"))
        minimal_context = self._normalize_mapping(graph_result.get("minimal_context"))
        impact_analysis = self._normalize_mapping(graph_result.get("impact_analysis"))
        if graph_contexts:
            events.append(
                ReviewEvent(
                    review_id=review_id,
                    event_type="code_graph_context_ready",
                    phase="context",
                    message=(
                        "代码结构图谱已命中关联上下文："
                        f"变更节点 {self._stat(graph_result, 'changed_node_count')} 个，"
                        f"关联片段 {len(graph_contexts)} 个"
                    ),
                    payload={
                        "repository_id": repository_id,
                        "context_source": "tree_sitter",
                        "context_count": len(graph_contexts),
                        "changed_files": normalized_files,
                        "changed_symbols": normalized_symbols,
                        "changed_ranges": normalized_ranges,
                        "related_contexts": self._context_preview(graph_contexts),
                        "minimal_context": minimal_context,
                        "impact_analysis": self._impact_preview(impact_analysis),
                        "stats": dict(graph_result.get("stats") or {}),
                    },
                )
            )
            return {
                "changed_files": normalized_files,
                "changed_symbols": normalized_symbols,
                "related_contexts": graph_contexts,
                "source_summary": {
                    "primary_source": "tree_sitter",
                    "fallback_used": False,
                    "fallback_reason": "",
                },
                "minimal_context": minimal_context,
                "impact_analysis": impact_analysis,
                "events": events,
            }

        fallback_reason = self._fallback_reason(graph_result)
        events.append(
            ReviewEvent(
                review_id=review_id,
                event_type="code_graph_context_fallback",
                phase="context",
                message=f"代码结构图谱未命中有效关联上下文，改用关键词搜索：{fallback_reason}",
                payload={
                    "repository_id": repository_id,
                    "context_source": "tree_sitter",
                    "fallback_source": "keyword_search",
                    "fallback_reason": fallback_reason,
                    "changed_files": normalized_files,
                    "changed_symbols": normalized_symbols,
                    "changed_ranges": normalized_ranges,
                    "minimal_context": minimal_context,
                    "impact_analysis": self._impact_preview(impact_analysis),
                    "stats": dict(graph_result.get("stats") or {}),
                },
            )
        )
        keyword_contexts = self._search_keyword_contexts(
            repository_context_service=repository_context_service,
            queries=normalized_symbols,
            limit=limit,
            fallback_reason=fallback_reason,
        )
        events.append(
            ReviewEvent(
                review_id=review_id,
                event_type="keyword_context_ready",
                phase="context",
                message=f"关键词搜索已补充关联上下文：命中片段 {len(keyword_contexts)} 个",
                payload={
                    "repository_id": repository_id,
                    "context_source": "keyword_search",
                    "context_count": len(keyword_contexts),
                    "fallback_reason": fallback_reason,
                    "changed_files": normalized_files,
                    "changed_symbols": normalized_symbols,
                    "changed_ranges": normalized_ranges,
                    "related_contexts": self._context_preview(keyword_contexts),
                },
            )
        )
        return {
            "changed_files": normalized_files,
            "changed_symbols": normalized_symbols,
            "related_contexts": keyword_contexts,
            "source_summary": {
                "primary_source": "keyword_search",
                "fallback_used": True,
                "fallback_reason": fallback_reason,
            },
            "minimal_context": minimal_context,
            "impact_analysis": impact_analysis,
            "events": events,
        }

    def _search_code_graph(
        self,
        *,
        repository_id: str,
        changed_files: list[str],
        changed_symbols: list[str],
        changed_ranges: dict[str, list[tuple[int, int]]],
        repository_context_service: Any,
        limit: int,
    ) -> dict[str, object]:
        if self.code_graph_service is None:
            return self._search_tree_sitter_direct(
                changed_files=changed_files,
                changed_symbols=changed_symbols,
                changed_ranges=changed_ranges,
                repository_context_service=repository_context_service,
                limit=limit,
            )
        try:
            result = self.code_graph_service.search_related_context(
                repository_id=repository_id,
                changed_files=changed_files,
                changed_symbols=changed_symbols,
                limit=limit,
                changed_ranges=changed_ranges,
            )
        except Exception as error:
            return {
                "ready": False,
                "contexts": [],
                "fallback_reason": f"图谱检索失败：{error.__class__.__name__}",
                "stats": {},
            }
        return dict(result or {})

    def _search_tree_sitter_direct(
        self,
        *,
        changed_files: list[str],
        changed_symbols: list[str],
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
        repository_context_service: Any,
        limit: int,
    ) -> dict[str, object]:
        try:
            from app.services.code_graph.java_tree_sitter_parser import create_java_tree_sitter_parser
        except Exception:
            return {"ready": False, "contexts": [], "fallback_reason": "代码结构图谱依赖未安装", "stats": {}}
        local_path = getattr(repository_context_service, "local_path", None)
        if local_path is None:
            return {"ready": False, "contexts": [], "fallback_reason": "目标代码仓上下文不可用", "stats": {}}
        try:
            parser = create_java_tree_sitter_parser()
        except Exception as error:
            return {"ready": False, "contexts": [], "fallback_reason": str(error), "stats": {}}
        contexts: list[dict[str, object]] = []
        scanned_file_count = 0
        for relative_path in self._java_candidate_files(repository_context_service, changed_files):
            if len(contexts) >= limit:
                break
            target = local_path / relative_path
            if not target.exists() or not target.is_file():
                continue
            try:
                source = target.read_bytes()
            except OSError:
                continue
            try:
                tree = parser.parse(source)
            except Exception:
                continue
            scanned_file_count += 1
            contexts.extend(
                self._extract_tree_sitter_symbol_contexts(
                    root_node=tree.root_node,
                    source=source,
                    relative_path=str(relative_path),
                    changed_symbols=changed_symbols,
                    remaining=max(0, limit - len(contexts)),
                )
            )
        if contexts:
            return {
                "ready": True,
                "contexts": contexts,
                "fallback_reason": "",
                "stats": {
                    "changed_node_count": len(changed_symbols),
                    "context_count": len(contexts),
                    "scanned_file_count": scanned_file_count,
                },
            }
        return {
            "ready": True,
            "contexts": [],
            "fallback_reason": "未找到符号关系",
            "stats": {"changed_node_count": len(changed_symbols), "scanned_file_count": scanned_file_count},
        }

    def _normalize_graph_contexts(self, raw_contexts: object) -> list[dict[str, object]]:
        contexts: list[dict[str, object]] = []
        for item in list(raw_contexts or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("file_path") or "").strip()
            snippet = str(item.get("snippet") or item.get("code") or "").strip()
            if not path and not snippet:
                continue
            normalized = dict(item)
            normalized["path"] = path
            normalized["snippet"] = snippet
            normalized["context_source"] = "tree_sitter"
            normalized.setdefault("relationship", str(item.get("relationship") or item.get("edge_kind") or "related").strip() or "related")
            contexts.append(normalized)
        return contexts

    def _context_preview(self, contexts: list[dict[str, object]], limit: int = 8) -> list[dict[str, object]]:
        preview: list[dict[str, object]] = []
        for item in contexts[:limit]:
            path = str(item.get("path") or item.get("file_path") or "").strip()
            snippet = str(item.get("snippet") or item.get("code") or "").strip()
            try:
                line_number = int(item.get("line_number") or item.get("line_start") or 0)
            except (TypeError, ValueError):
                line_number = 0
            preview.append(
                {
                    "path": path,
                    "line_start": line_number,
                    "symbol": str(item.get("symbol") or "").strip(),
                    "relationship": str(item.get("relationship") or "").strip(),
                    "context_source": str(item.get("context_source") or "").strip(),
                    "snippet": re.sub(r"\s+", " ", snippet)[:360],
                }
            )
        return preview

    def _normalize_mapping(self, value: object) -> dict[str, object]:
        return dict(value) if isinstance(value, dict) else {}

    def _impact_preview(self, impact_analysis: dict[str, object]) -> dict[str, object]:
        if not impact_analysis:
            return {}
        return {
            "summary": str(impact_analysis.get("summary") or ""),
            "risk_level": str(impact_analysis.get("risk_level") or ""),
            "risk_score": impact_analysis.get("risk_score") or 0,
            "changed_node_count": impact_analysis.get("changed_node_count") or 0,
            "impacted_file_count": impact_analysis.get("impacted_file_count") or 0,
            "test_gap_count": impact_analysis.get("test_gap_count") or 0,
            "changed_nodes": list(impact_analysis.get("changed_nodes") or [])[:5],
            "impacted_files": list(impact_analysis.get("impacted_files") or [])[:8],
            "test_gaps": list(impact_analysis.get("test_gaps") or [])[:5],
        }

    def _java_candidate_files(self, repository_context_service: Any, changed_files: list[str]) -> list[str]:
        candidates: list[str] = []
        for file_path in changed_files:
            normalized = str(file_path or "").strip().replace("\\", "/")
            if normalized.lower().endswith(".java"):
                candidates.append(normalized)
        local_path = getattr(repository_context_service, "local_path", None)
        if local_path is None:
            return self._dedupe(candidates)
        try:
            for path in local_path.rglob("*.java"):
                relative = path.relative_to(local_path).as_posix()
                is_searchable = getattr(repository_context_service, "is_searchable_path", None)
                if callable(is_searchable) and not is_searchable(relative):
                    continue
                candidates.append(relative)
                if len(candidates) >= 80:
                    break
        except OSError:
            return self._dedupe(candidates)
        return self._dedupe(candidates)

    def _extract_tree_sitter_symbol_contexts(
        self,
        *,
        root_node: Any,
        source: bytes,
        relative_path: str,
        changed_symbols: list[str],
        remaining: int,
    ) -> list[dict[str, object]]:
        contexts: list[dict[str, object]] = []
        symbols = [symbol for symbol in changed_symbols if len(symbol) >= 3]
        if not symbols or remaining <= 0:
            return contexts
        stack = [root_node]
        while stack and len(contexts) < remaining:
            node = stack.pop()
            node_type = str(getattr(node, "type", "") or "")
            if node_type in {
                "method_invocation",
                "object_creation_expression",
                "class_declaration",
                "interface_declaration",
                "record_declaration",
                "method_declaration",
            }:
                text = self._node_text(node, source)
                match_text = self._node_match_text(node_type, text)
                matched_symbol = next((symbol for symbol in symbols if self._symbol_matches(symbol, match_text)), "")
                if matched_symbol:
                    start_point = getattr(node, "start_point", (0, 0))
                    line_number = int(start_point[0]) + 1 if isinstance(start_point, tuple) and start_point else 1
                    contexts.append(
                        {
                            "path": relative_path,
                            "line_number": line_number,
                            "symbol": matched_symbol,
                            "relationship": self._relationship_for_node_type(node_type),
                            "snippet": text.strip().replace("\n", " ")[:500],
                            "confidence_tier": "extracted",
                        }
                    )
            stack.extend(reversed(list(getattr(node, "children", []) or [])))
        return contexts

    def _node_text(self, node: Any, source: bytes) -> str:
        start_byte = int(getattr(node, "start_byte", 0) or 0)
        end_byte = int(getattr(node, "end_byte", start_byte) or start_byte)
        return source[start_byte:end_byte].decode("utf-8", errors="ignore")

    def _node_match_text(self, node_type: str, text: str) -> str:
        if node_type in {"class_declaration", "interface_declaration", "record_declaration"}:
            return text.split("{", 1)[0]
        return text

    def _symbol_matches(self, symbol: str, text: str) -> bool:
        if not symbol or not text:
            return False
        if "." in symbol:
            return symbol in text
        return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])", text) is not None

    def _relationship_for_node_type(self, node_type: str) -> str:
        if node_type == "method_invocation":
            return "calls"
        if node_type == "object_creation_expression":
            return "references_type"
        if node_type in {"class_declaration", "interface_declaration", "record_declaration"}:
            return "defines_type"
        if node_type == "method_declaration":
            return "defines_method"
        return "related"

    def _search_keyword_contexts(
        self,
        *,
        repository_context_service: Any,
        queries: list[str],
        limit: int,
        fallback_reason: str,
    ) -> list[dict[str, object]]:
        if repository_context_service is None or not queries:
            return []
        search_many = getattr(repository_context_service, "search_many", None)
        if not callable(search_many):
            return []
        try:
            result = search_many(queries, globs=["*.java", "*.xml", "*.sql"], limit_per_query=4, total_limit=limit)
        except TypeError:
            result = search_many(queries, limit_per_query=4, total_limit=limit)
        except Exception:
            return []
        contexts: list[dict[str, object]] = []
        for item in list((result or {}).get("matches") or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if not path and not snippet:
                continue
            normalized = dict(item)
            normalized["context_source"] = "keyword_search"
            normalized["fallback_reason"] = fallback_reason
            normalized.setdefault("relationship", "keyword_match")
            contexts.append(normalized)
        return contexts

    def _fallback_reason(self, graph_result: dict[str, object]) -> str:
        reason = str(graph_result.get("fallback_reason") or "").strip()
        if reason:
            return reason
        if graph_result.get("ready") is False:
            return "图谱未建"
        return "未找到符号关系"

    def _normalize_symbols(self, changed_symbols: list[str], changed_files: list[str]) -> list[str]:
        values = self._dedupe(changed_symbols)
        for symbol in list(values):
            for part in re.split(r"[^A-Za-z0-9_$]+", symbol):
                if len(part) >= 3:
                    values.append(part)
        for file_path in changed_files:
            stem = str(file_path).rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
            if stem:
                values.append(stem)
        return self._dedupe(values)

    def _normalize_changed_ranges(self, changed_ranges: dict[str, list[tuple[int, int]]]) -> dict[str, list[tuple[int, int]]]:
        normalized: dict[str, list[tuple[int, int]]] = {}
        for path, ranges in dict(changed_ranges or {}).items():
            normalized_path = str(path or "").strip().replace("\\", "/")
            if not normalized_path:
                continue
            clean_ranges: list[tuple[int, int]] = []
            for item in list(ranges or []):
                try:
                    start = int(item[0])
                    end = int(item[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if start <= 0:
                    continue
                clean_ranges.append((start, max(start, end)))
            if clean_ranges:
                normalized[normalized_path] = clean_ranges
        return normalized

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = str(value or "").strip().replace("\\", "/")
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _stat(self, graph_result: dict[str, object], key: str) -> int:
        stats = graph_result.get("stats")
        if not isinstance(stats, dict):
            return 0
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def _compact_json(self, value: object, *, max_chars: int = 6000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}... [truncated {len(text) - max_chars} chars]"
