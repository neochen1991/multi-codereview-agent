from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.code_graph.models import CodeGraphEdge, CodeGraphNode

logger = logging.getLogger(__name__)


class CodeGraphStorage:
    """SQLite-backed local code graph storage."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_state (
                    file_path TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    language TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    parent_qualified_name TEXT NOT NULL DEFAULT '',
                    signature TEXT NOT NULL DEFAULT '',
                    return_type TEXT NOT NULL DEFAULT '',
                    modifiers_json TEXT NOT NULL DEFAULT '[]',
                    is_test INTEGER NOT NULL DEFAULT 0,
                    snippet TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
                CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
                CREATE TABLE IF NOT EXISTS edges (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    source_qualified_name TEXT NOT NULL,
                    target_qualified_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    confidence_tier TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_edges_file ON edges(file_path);
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qualified_name);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qualified_name);
                """
            )

    def replace_file_graph(
        self,
        *,
        file_path: str,
        file_hash: str,
        nodes: list[CodeGraphNode],
        edges: list[CodeGraphEdge],
    ) -> None:
        self.initialize()
        normalized_file = self._normalize_path(file_path)
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM nodes WHERE file_path = ?", (normalized_file,))
            conn.execute("DELETE FROM edges WHERE file_path = ?", (normalized_file,))
            conn.execute(
                """
                INSERT INTO file_state(file_path, file_hash, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash=excluded.file_hash,
                    updated_at=excluded.updated_at
                """,
                (normalized_file, str(file_hash or ""), now),
            )
            conn.executemany(
                """
                INSERT INTO nodes(
                    kind, name, qualified_name, language, file_path, line_start, line_end,
                    parent_qualified_name, signature, return_type, modifiers_json, is_test,
                    snippet, metadata_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._node_row(node, now) for node in nodes],
            )
            conn.executemany(
                """
                INSERT INTO edges(
                    kind, source_qualified_name, target_qualified_name, file_path,
                    line_number, confidence, confidence_tier, metadata_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._edge_row(edge, now) for edge in edges],
            )

    def file_state(self, file_path: str) -> dict[str, Any]:
        self.initialize()
        normalized = self._normalize_path(file_path)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM file_state WHERE file_path = ?", (normalized,)).fetchone()
        return dict(row) if row is not None else {}

    def list_nodes(self, *, file_path: str = "") -> list[dict[str, Any]]:
        self.initialize()
        query = "SELECT * FROM nodes"
        params: tuple[Any, ...] = ()
        if file_path:
            query += " WHERE file_path = ?"
            params = (self._normalize_path(file_path),)
        query += " ORDER BY file_path, line_start, qualified_name"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_node_row(dict(row)) for row in rows]

    def list_edges(self, *, file_path: str = "") -> list[dict[str, Any]]:
        self.initialize()
        query = "SELECT * FROM edges"
        params: tuple[Any, ...] = ()
        if file_path:
            query += " WHERE file_path = ?"
            params = (self._normalize_path(file_path),)
        query += " ORDER BY file_path, line_number, kind"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_edge_row(dict(row)) for row in rows]

    def search_related_contexts(self, changed_symbols: list[str], *, limit: int = 12) -> list[dict[str, Any]]:
        self.initialize()
        symbols = [str(symbol or "").strip() for symbol in changed_symbols if str(symbol or "").strip()]
        if not symbols:
            return []
        clauses = []
        params: list[Any] = []
        for symbol in symbols:
            like = f"%{symbol}%"
            clauses.append("(e.source_qualified_name LIKE ? OR e.target_qualified_name LIKE ? OR n.qualified_name LIKE ? OR n.snippet LIKE ?)")
            params.extend([like, like, like, like])
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    e.kind AS relationship,
                    e.file_path AS path,
                    e.line_number AS line_number,
                    e.source_qualified_name AS source_qualified_name,
                    e.target_qualified_name AS target_qualified_name,
                    e.confidence AS confidence,
                    e.confidence_tier AS confidence_tier,
                    COALESCE(n.snippet, '') AS snippet
                FROM edges e
                LEFT JOIN nodes n
                  ON n.file_path = e.file_path
                 AND n.line_start <= e.line_number
                 AND n.line_end >= e.line_number
                WHERE {" OR ".join(clauses)}
                ORDER BY e.confidence DESC, e.file_path, e.line_number
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        contexts: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context_source"] = "tree_sitter"
            contexts.append(item)
        return contexts

    def search_related_context(
        self,
        *,
        repository_id: str = "",
        changed_files: list[str] | None = None,
        changed_symbols: list[str] | None = None,
        limit: int = 12,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[str, object]:
        normalized_files = list(changed_files or [])
        symbols = list(changed_symbols or [])
        normalized_ranges = self._normalize_changed_ranges(changed_ranges or {})
        impact_analysis = self.analyze_change_impact(
            changed_files=normalized_files,
            changed_symbols=symbols,
            changed_ranges=normalized_ranges,
            max_depth=2,
            max_nodes=80,
        )
        contexts = self.search_related_contexts(symbols, limit=limit)
        if not contexts:
            contexts = self._contexts_from_impact(impact_analysis, limit=limit)
        result = {
            "ready": True,
            "contexts": contexts,
            "fallback_reason": "" if contexts else "未找到符号关系",
            "impact_analysis": impact_analysis,
            "minimal_context": self._minimal_context_from_impact(impact_analysis),
            "stats": {
                "repository_id": repository_id,
                "changed_file_count": len(normalized_files),
                "changed_node_count": int(impact_analysis.get("changed_node_count") or len(symbols)),
                "impacted_file_count": int(impact_analysis.get("impacted_file_count") or 0),
                "test_gap_count": int(impact_analysis.get("test_gap_count") or 0),
                "risk_score": float(impact_analysis.get("risk_score") or 0),
                "context_count": len(contexts),
            },
        }
        logger.info(
            "tree_sitter storage search_related_context io request=%s response=%s",
            self._compact_json(
                {
                    "repository_id": repository_id,
                    "changed_files": normalized_files,
                    "changed_symbols": symbols,
                    "changed_ranges": normalized_ranges,
                    "limit": limit,
                }
            ),
            self._compact_json(
                {
                    "ready": result["ready"],
                    "context_count": len(contexts),
                    "fallback_reason": result["fallback_reason"],
                    "minimal_context": result["minimal_context"],
                    "impact_analysis": result["impact_analysis"],
                    "stats": result["stats"],
                }
            ),
        )
        return result

    def analyze_change_impact(
        self,
        *,
        changed_files: list[str] | None = None,
        changed_symbols: list[str] | None = None,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
        max_depth: int = 2,
        max_nodes: int = 80,
    ) -> dict[str, Any]:
        """Compute a compact graph-based impact summary for review prompts."""
        self.initialize()
        normalized_files = [self._normalize_path(item) for item in list(changed_files or []) if self._normalize_path(item)]
        symbols = [str(item or "").strip() for item in list(changed_symbols or []) if str(item or "").strip()]
        normalized_ranges = self._normalize_changed_ranges(changed_ranges or {})
        changed_nodes = self._find_changed_nodes_by_ranges(normalized_ranges)
        if not changed_nodes:
            changed_nodes = self._find_changed_nodes(normalized_files, symbols)
        seed_names = {str(node.get("qualified_name") or "") for node in changed_nodes if node.get("qualified_name")}
        impacted_node_names, traversed_edges, truncated = self._bfs_related_nodes(
            seed_names,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        impacted_nodes = self._nodes_by_qualified_names(impacted_node_names.difference(seed_names))
        impacted_files = sorted(
            {
                self._normalize_path(node.get("file_path"))
                for node in impacted_nodes
                if self._normalize_path(node.get("file_path"))
            }
        )
        test_gaps = self._test_gaps(changed_nodes)
        risk_score = self._risk_score(
            changed_nodes=changed_nodes,
            impacted_nodes=impacted_nodes,
            traversed_edges=traversed_edges,
            test_gaps=test_gaps,
        )
        review_priorities = self._review_priorities(
            changed_nodes=changed_nodes,
            traversed_edges=traversed_edges,
            test_gaps=test_gaps,
        )
        affected_flows = self._affected_flow_preview(
            changed_nodes=changed_nodes,
            impacted_nodes=impacted_nodes,
            traversed_edges=traversed_edges,
        )
        return {
            "status": "ok",
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "changed_nodes": self._node_preview(changed_nodes, limit=10),
            "impacted_nodes": self._node_preview(impacted_nodes, limit=20),
            "changed_node_count": len(changed_nodes),
            "impacted_node_count": len(impacted_nodes),
            "impacted_files": impacted_files[:30],
            "impacted_file_count": len(impacted_files),
            "test_gaps": test_gaps[:20],
            "test_gap_count": len(test_gaps),
            "relationships": self._edge_preview(traversed_edges, limit=30),
            "relationship_count": len(traversed_edges),
            "truncated": truncated,
            "summary": self._impact_summary_text(
                changed_node_count=len(changed_nodes),
                impacted_file_count=len(impacted_files),
                test_gap_count=len(test_gaps),
                risk_score=risk_score,
            ),
            "review_priorities": review_priorities[:10],
            "affected_flows": affected_flows[:10],
            "affected_flow_count": len(affected_flows),
        }

    def _find_changed_nodes(self, changed_files: list[str], changed_symbols: list[str]) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if changed_files:
            placeholders = ",".join("?" for _ in changed_files)
            clauses.append(f"file_path IN ({placeholders})")
            params.extend(changed_files)
        for symbol in changed_symbols:
            like = f"%{symbol}%"
            clauses.append("(qualified_name LIKE ? OR name LIKE ? OR signature LIKE ?)")
            params.extend([like, like, like])
        if not clauses:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM nodes
                WHERE {" OR ".join(clauses)}
                ORDER BY
                    CASE kind
                        WHEN 'method' THEN 1
                        WHEN 'constructor' THEN 2
                        WHEN 'class' THEN 3
                        ELSE 4
                    END,
                    file_path,
                    line_start
                LIMIT 100
                """,
                tuple(params),
            ).fetchall()
        return [self._decode_node_row(dict(row)) for row in rows]

    def _find_changed_nodes_by_ranges(self, changed_ranges: dict[str, list[tuple[int, int]]]) -> list[dict[str, Any]]:
        if not changed_ranges:
            return []
        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        with self._connect() as conn:
            for file_path, ranges in changed_ranges.items():
                for start, end in ranges:
                    rows = conn.execute(
                        """
                        SELECT *,
                               (line_end - line_start) AS span
                        FROM nodes
                        WHERE file_path = ?
                          AND line_start <= ?
                          AND line_end >= ?
                        ORDER BY
                            CASE kind
                                WHEN 'method' THEN 1
                                WHEN 'constructor' THEN 2
                                WHEN 'record' THEN 3
                                WHEN 'class' THEN 4
                                ELSE 5
                            END,
                            span ASC,
                            line_start
                        LIMIT 20
                        """,
                        (self._normalize_path(file_path), int(end), int(start)),
                    ).fetchall()
                    for row in rows:
                        node = self._decode_node_row(dict(row))
                        key = str(node.get("qualified_name") or "")
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        nodes.append(node)
        return nodes

    def _bfs_related_nodes(
        self,
        seed_names: set[str],
        *,
        max_depth: int,
        max_nodes: int,
    ) -> tuple[set[str], list[dict[str, Any]], bool]:
        if not seed_names:
            return set(), [], False
        visited = set(seed_names)
        frontier = set(seed_names)
        traversed_edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str, int]] = set()
        truncated = False
        for _depth in range(max(0, int(max_depth or 0))):
            if not frontier:
                break
            edges = self._edges_touching(frontier)
            next_frontier: set[str] = set()
            for edge in edges:
                key = (
                    str(edge.get("kind") or ""),
                    str(edge.get("source_qualified_name") or ""),
                    str(edge.get("target_qualified_name") or ""),
                    int(edge.get("line_number") or 0),
                )
                if key not in seen_edges:
                    seen_edges.add(key)
                    traversed_edges.append(edge)
                source = str(edge.get("source_qualified_name") or "")
                target = str(edge.get("target_qualified_name") or "")
                for candidate in (source, target):
                    if candidate and candidate not in visited:
                        next_frontier.add(candidate)
            if len(visited) + len(next_frontier) > max_nodes:
                available = max(0, max_nodes - len(visited))
                next_frontier = set(sorted(next_frontier)[:available])
                truncated = True
            visited.update(next_frontier)
            frontier = next_frontier
            if truncated:
                break
        return visited, traversed_edges, truncated

    def _edges_touching(self, qualified_names: set[str]) -> list[dict[str, Any]]:
        if not qualified_names:
            return []
        names = sorted(qualified_names)
        placeholders = ",".join("?" for _ in names)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM edges
                WHERE source_qualified_name IN ({placeholders})
                   OR target_qualified_name IN ({placeholders})
                ORDER BY confidence DESC, file_path, line_number
                LIMIT 300
                """,
                tuple(names + names),
            ).fetchall()
        return [self._decode_edge_row(dict(row)) for row in rows]

    def _nodes_by_qualified_names(self, qualified_names: set[str]) -> list[dict[str, Any]]:
        if not qualified_names:
            return []
        names = sorted(qualified_names)
        placeholders = ",".join("?" for _ in names)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM nodes
                WHERE qualified_name IN ({placeholders})
                ORDER BY file_path, line_start
                """,
                tuple(names),
            ).fetchall()
        return [self._decode_node_row(dict(row)) for row in rows]

    def _test_gaps(self, changed_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        production_nodes = [
            node
            for node in changed_nodes
            if str(node.get("kind") or "") in {"method", "constructor", "class"}
            and not bool(node.get("is_test"))
        ]
        if not production_nodes:
            return []
        names = [str(node.get("qualified_name") or "") for node in production_nodes if node.get("qualified_name")]
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT source_qualified_name
                FROM edges
                WHERE kind = 'tested_by'
                  AND source_qualified_name IN ({placeholders})
                """,
                tuple(names),
            ).fetchall()
        tested = {str(row["source_qualified_name"]) for row in rows}
        gaps: list[dict[str, Any]] = []
        for node in production_nodes:
            qualified_name = str(node.get("qualified_name") or "")
            if qualified_name in tested:
                continue
            gaps.append(
                {
                    "name": str(node.get("name") or ""),
                    "qualified_name": qualified_name,
                    "file_path": self._normalize_path(node.get("file_path")),
                    "line_start": int(node.get("line_start") or 1),
                    "line_end": int(node.get("line_end") or node.get("line_start") or 1),
                }
            )
        return gaps

    def _risk_score(
        self,
        *,
        changed_nodes: list[dict[str, Any]],
        impacted_nodes: list[dict[str, Any]],
        traversed_edges: list[dict[str, Any]],
        test_gaps: list[dict[str, Any]],
    ) -> float:
        if not changed_nodes:
            score = 0.0
            score += min(len(impacted_nodes) / 30.0, 0.30)
            score += min(len(test_gaps) * 0.12, 0.30)
            return round(min(max(score, 0.0), 1.0), 4)
        priorities = self._review_priorities(
            changed_nodes=changed_nodes,
            traversed_edges=traversed_edges,
            test_gaps=test_gaps,
        )
        return round(max((float(item.get("risk_score") or 0.0) for item in priorities), default=0.0), 4)

    def _review_priorities(
        self,
        *,
        changed_nodes: list[dict[str, Any]],
        traversed_edges: list[dict[str, Any]],
        test_gaps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        test_gap_names = {str(item.get("qualified_name") or "") for item in test_gaps}
        priorities: list[dict[str, Any]] = []
        for node in changed_nodes:
            qualified_name = str(node.get("qualified_name") or "")
            if not qualified_name:
                continue
            caller_edges = [
                edge
                for edge in traversed_edges
                if str(edge.get("kind") or "").lower() == "calls"
                and str(edge.get("target_qualified_name") or "") == qualified_name
            ]
            touched_edges = [
                edge
                for edge in traversed_edges
                if qualified_name
                in {
                    str(edge.get("source_qualified_name") or ""),
                    str(edge.get("target_qualified_name") or ""),
                }
            ]
            score = 0.0
            # code-review-graph uses flow membership here; in this schema the closest
            # signal is how many extracted relationships touch the changed node.
            score += min(len(touched_edges) * 0.05, 0.25)
            node_file = self._normalize_path(node.get("file_path"))
            cross_file_callers = [
                edge
                for edge in caller_edges
                if self._normalize_path(edge.get("file_path")) and self._normalize_path(edge.get("file_path")) != node_file
            ]
            score += min(len(cross_file_callers) * 0.05, 0.15)
            score += 0.30 if qualified_name in test_gap_names else 0.05
            haystack = f"{node.get('name', '')} {qualified_name}".lower()
            sensitive_words = (
                "auth",
                "token",
                "password",
                "permission",
                "security",
                "payment",
                "sql",
                "delete",
            )
            if any(word in haystack for word in sensitive_words):
                score += 0.20
            score += min(len(caller_edges) / 20.0, 0.10)
            priorities.append(
                {
                    "name": str(node.get("name") or ""),
                    "qualified_name": qualified_name,
                    "file_path": node_file,
                    "line_start": int(node.get("line_start") or 1),
                    "risk_score": round(min(max(score, 0.0), 1.0), 4),
                    "caller_count": len(caller_edges),
                    "cross_file_caller_count": len(cross_file_callers),
                    "test_gap": qualified_name in test_gap_names,
                }
            )
        priorities.sort(key=lambda item: float(item.get("risk_score") or 0.0), reverse=True)
        return priorities

    def _affected_flow_preview(
        self,
        *,
        changed_nodes: list[dict[str, Any]],
        impacted_nodes: list[dict[str, Any]],
        traversed_edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        nodes_by_name = {
            str(node.get("qualified_name") or ""): node
            for node in [*changed_nodes, *impacted_nodes]
            if str(node.get("qualified_name") or "")
        }
        changed_names = {str(node.get("qualified_name") or "") for node in changed_nodes if node.get("qualified_name")}
        if not changed_names:
            return []
        entrypoint_tokens = (
            "controller",
            "resource",
            "endpoint",
            "listener",
            "subscriber",
            "consumer",
            "scheduler",
            "commandhandler",
            "application",
            "creator",
        )
        flows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for edge in traversed_edges:
            source = str(edge.get("source_qualified_name") or "")
            target = str(edge.get("target_qualified_name") or "")
            if not source or not target:
                continue
            source_node = nodes_by_name.get(source, {})
            target_node = nodes_by_name.get(target, {})
            source_text = f"{source} {source_node.get('file_path', '')}".lower()
            target_text = f"{target} {target_node.get('file_path', '')}".lower()
            if source in changed_names:
                entrypoint = target if any(token in target_text for token in entrypoint_tokens) else source
                changed = source
            elif target in changed_names:
                entrypoint = source if any(token in source_text for token in entrypoint_tokens) else target
                changed = target
            else:
                continue
            key = (entrypoint, changed)
            if key in seen:
                continue
            seen.add(key)
            flows.append(
                {
                    "entrypoint": entrypoint,
                    "changed_node": changed,
                    "relationship": str(edge.get("kind") or ""),
                    "path": [entrypoint, changed] if entrypoint != changed else [changed],
                    "file_path": self._normalize_path(edge.get("file_path")),
                    "line_number": int(edge.get("line_number") or 1),
                    "criticality": self._flow_criticality(entrypoint, changed, source_text + " " + target_text),
                }
            )
        flows.sort(key=lambda item: float(item.get("criticality") or 0.0), reverse=True)
        return flows

    def _flow_criticality(self, entrypoint: str, changed_node: str, haystack: str) -> float:
        score = 0.35
        if any(token in haystack for token in ("controller", "resource", "endpoint", "/api/")):
            score += 0.25
        if any(token in haystack for token in ("event", "listener", "subscriber", "consumer")):
            score += 0.20
        if any(token in haystack for token in ("repository", "mapper", "sql", "hibernate")):
            score += 0.15
        if entrypoint != changed_node:
            score += 0.10
        return round(min(score, 1.0), 4)

    def _risk_level(self, score: float) -> str:
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    def _node_preview(self, nodes: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for node in nodes[:limit]:
            result.append(
                {
                    "kind": str(node.get("kind") or ""),
                    "name": str(node.get("name") or ""),
                    "qualified_name": str(node.get("qualified_name") or ""),
                    "file_path": self._normalize_path(node.get("file_path")),
                    "line_start": int(node.get("line_start") or 1),
                    "line_end": int(node.get("line_end") or node.get("line_start") or 1),
                    "is_test": bool(node.get("is_test")),
                    "signature": str(node.get("signature") or ""),
                    "snippet": str(node.get("snippet") or "")[:1200],
                }
            )
        return result

    def _edge_preview(self, edges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for edge in edges[:limit]:
            result.append(
                {
                    "kind": str(edge.get("kind") or ""),
                    "source": str(edge.get("source_qualified_name") or ""),
                    "target": str(edge.get("target_qualified_name") or ""),
                    "file_path": self._normalize_path(edge.get("file_path")),
                    "line_number": int(edge.get("line_number") or 1),
                    "confidence": float(edge.get("confidence") or 0),
                    "confidence_tier": str(edge.get("confidence_tier") or ""),
                }
            )
        return result

    def _impact_summary_text(
        self,
        *,
        changed_node_count: int,
        impacted_file_count: int,
        test_gap_count: int,
        risk_score: float,
    ) -> str:
        return (
            f"Tree-sitter 图谱识别 {changed_node_count} 个变更节点，"
            f"{impacted_file_count} 个候选受影响文件，"
            f"{test_gap_count} 个测试覆盖缺口，"
            f"风险评分 {risk_score:.2f}。"
        )

    def _minimal_context_from_impact(self, impact_analysis: dict[str, Any]) -> dict[str, Any]:
        changed_nodes = list(impact_analysis.get("changed_nodes") or [])
        review_priorities = [
            dict(item)
            for item in list(impact_analysis.get("review_priorities") or [])[:5]
            if isinstance(item, dict)
        ]
        impacted_file_count = int(impact_analysis.get("impacted_file_count") or 0)
        test_gap_count = int(impact_analysis.get("test_gap_count") or 0)
        relationship_count = int(impact_analysis.get("relationship_count") or 0)
        review_guidance: list[str] = []
        if test_gap_count:
            review_guidance.append(f"{test_gap_count} 个变更节点缺少测试覆盖，优先核对回归用例。")
        if impacted_file_count >= 3:
            review_guidance.append("影响文件较多，优先检查入口、调用方和持久化边界。")
        if relationship_count:
            review_guidance.append("已识别调用、继承或包含关系，优先围绕这些关系核验证据链。")
        affected_flow_count = int(impact_analysis.get("affected_flow_count") or 0)
        if affected_flow_count:
            review_guidance.append(f"识别到 {affected_flow_count} 条候选受影响流程，优先核对入口到变更节点的链路。")
        return {
            "source": "tree_sitter",
            "summary": str(impact_analysis.get("summary") or ""),
            "risk_level": str(impact_analysis.get("risk_level") or "low"),
            "risk_score": float(impact_analysis.get("risk_score") or 0),
            "changed_node_count": int(impact_analysis.get("changed_node_count") or 0),
            "impacted_file_count": impacted_file_count,
            "test_gap_count": test_gap_count,
            "affected_flow_count": affected_flow_count,
            "key_entities": [
                str(node.get("qualified_name") or node.get("name") or "")
                for node in changed_nodes[:5]
                if isinstance(node, dict)
            ],
            "review_priorities": review_priorities,
            "affected_flows": [
                dict(item)
                for item in list(impact_analysis.get("affected_flows") or [])[:5]
                if isinstance(item, dict)
            ],
            "next_steps": [
                "优先审查高风险变更节点",
                "核对调用方和测试覆盖缺口",
                "只在证据不足时展开更多关联片段",
            ],
            "next_tool_suggestions": [
                "get_review_context(detail_level=minimal)",
                "get_impact_radius(max_depth=2)",
                "get_affected_flows",
            ],
            "review_guidance": review_guidance,
        }

    def _contexts_from_impact(self, impact_analysis: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()

        def append_context(item: dict[str, Any]) -> None:
            if len(contexts) >= limit:
                return
            path = self._normalize_path(item.get("path") or item.get("file_path"))
            if not path:
                return
            line_number = int(item.get("line_number") or item.get("line_start") or 1)
            relationship = str(item.get("relationship") or "")
            key = (path, line_number, relationship)
            if key in seen:
                return
            seen.add(key)
            normalized = dict(item)
            normalized["path"] = path
            normalized["line_number"] = line_number
            normalized["context_source"] = "tree_sitter"
            normalized.setdefault("confidence", 1.0)
            normalized.setdefault("confidence_tier", "changed_node")
            normalized["snippet"] = str(normalized.get("snippet") or normalized.get("signature") or "").strip()
            contexts.append(normalized)

        for node in list(impact_analysis.get("changed_nodes") or []):
            if not isinstance(node, dict):
                continue
            append_context(
                {
                    "relationship": "changed_node",
                    "file_path": node.get("file_path"),
                    "line_start": node.get("line_start"),
                    "source_qualified_name": node.get("qualified_name"),
                    "target_qualified_name": "",
                    "snippet": node.get("snippet") or node.get("signature") or node.get("qualified_name"),
                    "confidence": 1.0,
                    "confidence_tier": "changed_node",
                }
            )

        for edge in list(impact_analysis.get("relationships") or []):
            if not isinstance(edge, dict):
                continue
            append_context(
                {
                    "relationship": edge.get("kind"),
                    "file_path": edge.get("file_path"),
                    "line_number": edge.get("line_number"),
                    "source_qualified_name": edge.get("source"),
                    "target_qualified_name": edge.get("target"),
                    "snippet": f"{edge.get('source', '')} -> {edge.get('target', '')}".strip(" ->"),
                    "confidence": edge.get("confidence"),
                    "confidence_tier": edge.get("confidence_tier") or "related_edge",
                }
            )

        for node in list(impact_analysis.get("impacted_nodes") or []):
            if not isinstance(node, dict):
                continue
            append_context(
                {
                    "relationship": "impacted_node",
                    "file_path": node.get("file_path"),
                    "line_start": node.get("line_start"),
                    "source_qualified_name": node.get("qualified_name"),
                    "target_qualified_name": "",
                    "snippet": node.get("snippet") or node.get("signature") or node.get("qualified_name"),
                    "confidence": 0.72,
                    "confidence_tier": "impacted_node",
                }
            )
        return contexts

    def _normalize_changed_ranges(self, changed_ranges: dict[str, list[tuple[int, int]]]) -> dict[str, list[tuple[int, int]]]:
        normalized: dict[str, list[tuple[int, int]]] = {}
        for file_path, ranges in dict(changed_ranges or {}).items():
            path = self._normalize_path(file_path)
            if not path:
                continue
            cleaned: list[tuple[int, int]] = []
            for item in list(ranges or []):
                try:
                    start = int(item[0])
                    end = int(item[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if start <= 0:
                    continue
                cleaned.append((start, max(start, end)))
            if cleaned:
                normalized[path] = cleaned
        return normalized

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _node_row(self, node: CodeGraphNode, updated_at: str) -> tuple[Any, ...]:
        return (
            str(node.kind),
            str(node.name),
            str(node.qualified_name),
            str(node.language),
            self._normalize_path(node.file_path),
            int(node.line_start or 1),
            int(node.line_end or node.line_start or 1),
            str(node.parent_qualified_name or ""),
            str(node.signature or ""),
            str(node.return_type or ""),
            json.dumps(list(node.modifiers or []), ensure_ascii=False),
            1 if node.is_test else 0,
            str(node.snippet or ""),
            json.dumps(dict(node.metadata or {}), ensure_ascii=False),
            updated_at,
        )

    def _edge_row(self, edge: CodeGraphEdge, updated_at: str) -> tuple[Any, ...]:
        return (
            str(edge.kind),
            str(edge.source_qualified_name),
            str(edge.target_qualified_name),
            self._normalize_path(edge.file_path),
            int(edge.line_number or 1),
            float(edge.confidence),
            str(edge.confidence_tier or "extracted"),
            json.dumps(dict(edge.metadata or {}), ensure_ascii=False),
            updated_at,
        )

    def _decode_node_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["modifiers"] = self._loads(row.pop("modifiers_json", "[]"), [])
        row["metadata"] = self._loads(row.pop("metadata_json", "{}"), {})
        row["is_test"] = bool(row.get("is_test"))
        return row

    def _decode_edge_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["metadata"] = self._loads(row.pop("metadata_json", "{}"), {})
        return row

    def _loads(self, value: object, fallback: Any) -> Any:
        try:
            return json.loads(str(value or ""))
        except json.JSONDecodeError:
            return fallback

    def _normalize_path(self, value: object) -> str:
        return str(value or "").strip().replace("\\", "/")

    def _compact_json(self, value: object, *, max_chars: int = 6000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}... [truncated {len(text) - max_chars} chars]"
