from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any


class RepositoryContextService:
    """目标代码仓上下文检索服务。

    专家不应该只看 PR/MR diff，这个服务负责让它们回到目标分支源码里继续找：
    - 文本命中
    - 定义/引用
    - 文件局部上下文
    """

    EXCLUDED_PATH_PARTS = {
        ".git",
        ".idea",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "__pycache__",
        "coverage",
    }
    EXCLUDED_FILENAMES = {
        "yarn.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "composer.lock",
    }
    EXCLUDED_SUFFIXES = {
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".zip",
        ".gz",
        ".ico",
        ".woff",
        ".woff2",
        ".class",
        ".jar",
        ".war",
        ".ear",
        ".dll",
        ".so",
        ".dylib",
        ".exe",
        ".o",
        ".a",
        ".pyc",
        ".pyo",
    }
    MAX_CACHE_ENTRIES = 128

    def __init__(
        self,
        clone_url: str,
        local_path: str | Path,
        default_branch: str,
        access_token: str | None = None,
        auto_sync: bool = False,
    ) -> None:
        self.clone_url = str(clone_url or "").strip()
        self.local_path = Path(local_path).expanduser() if str(local_path or "").strip() else Path()
        self.default_branch = str(default_branch or "").strip() or "main"
        self.access_token = (access_token or "").strip() or None
        self.auto_sync = auto_sync
        self._cache: OrderedDict[
            tuple[str, tuple[str, ...], int],
            list[dict[str, Any]],
        ] = OrderedDict()
        self._search_root_prefixes: tuple[str, ...] | None = None

    @classmethod
    def from_review_context(
        cls,
        *,
        clone_url: str,
        local_path: str | Path,
        default_branch: str,
        access_token: str | None = None,
        auto_sync: bool = False,
        subject: Any | None = None,
    ) -> "RepositoryContextService":
        resolved_local_path = cls._resolve_effective_local_path(local_path, subject=subject)
        return cls(
            clone_url=clone_url,
            local_path=resolved_local_path,
            default_branch=default_branch,
            access_token=access_token,
            auto_sync=auto_sync,
        )

    def is_configured(self) -> bool:
        return bool(str(self.local_path))

    def is_ready(self) -> bool:
        return self.is_configured() and self.local_path.exists() and self.local_path.is_dir()

    @classmethod
    def _resolve_effective_local_path(
        cls,
        local_path: str | Path,
        *,
        subject: Any | None = None,
    ) -> Path:
        metadata = cls._extract_subject_metadata(subject)
        workspace_repo_path = str(
            metadata.get("workspace_repo_path")
            or metadata.get("repo_context_workspace_path")
            or metadata.get("workspace_repo")
            or ""
        ).strip()
        if workspace_repo_path:
            workspace_repo = Path(workspace_repo_path).expanduser()
            if workspace_repo.exists() and workspace_repo.is_dir():
                return workspace_repo
        raw_local_path = str(local_path or "").strip()
        normalized_local_path = Path(raw_local_path).expanduser() if raw_local_path else Path()
        if raw_local_path:
            return normalized_local_path
        if not cls._should_use_workspace_fallback(subject):
            return normalized_local_path
        workspace = Path.cwd()
        if not workspace.exists() or not workspace.is_dir():
            return normalized_local_path
        if not (workspace / ".git").exists():
            return normalized_local_path
        changed_files = cls._extract_changed_files(subject)
        if not changed_files:
            return normalized_local_path
        if not any((workspace / item).exists() for item in changed_files):
            return normalized_local_path
        return workspace

    @staticmethod
    def _extract_subject_metadata(subject: Any | None) -> dict[str, Any]:
        if subject is None:
            return {}
        metadata = getattr(subject, "metadata", None)
        if isinstance(metadata, dict):
            return metadata
        if isinstance(subject, dict):
            raw_metadata = subject.get("metadata")
            if isinstance(raw_metadata, dict):
                return raw_metadata
        return {}

    @staticmethod
    def _extract_changed_files(subject: Any | None) -> list[str]:
        if subject is None:
            return []
        if hasattr(subject, "changed_files"):
            values = getattr(subject, "changed_files", []) or []
        elif isinstance(subject, dict):
            values = subject.get("changed_files", []) or []
        else:
            values = []
        return [str(item).strip() for item in values if str(item).strip()]

    @classmethod
    def _should_use_workspace_fallback(cls, subject: Any | None) -> bool:
        metadata = cls._extract_subject_metadata(subject)
        if bool(metadata.get("repo_context_local_fallback") or metadata.get("local_workspace_fallback")):
            return True
        trigger_source = str(metadata.get("trigger_source") or "").strip().lower()
        return trigger_source in {"manual", "manual_real_case_test", "local"}

    def search(self, query: str, globs: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        """搜索源码仓中的文本命中，并带简单缓存。"""
        normalized_query = str(query or "").strip()
        normalized_globs = tuple(sorted(str(item).strip() for item in (globs or []) if str(item).strip()))
        cache_key = (normalized_query, normalized_globs, limit)
        if cache_key in self._cache:
            cached = self._cache.pop(cache_key)
            self._cache[cache_key] = cached
            return {
                "matches": cached,
                "cache_hit": True,
                "local_path": str(self.local_path),
                "default_branch": self.default_branch,
            }

        if not self.is_ready() or not normalized_query:
            return {
                "matches": [],
                "cache_hit": False,
                "local_path": str(self.local_path),
                "default_branch": self.default_branch,
            }

        matches = self._search_with_ripgrep(normalized_query, list(normalized_globs), limit)
        if not matches:
            matches = self._search_with_fallback(normalized_query, list(normalized_globs), limit)
        self._store_cache(cache_key, matches)
        return {
            "matches": matches,
            "cache_hit": False,
            "local_path": str(self.local_path),
            "default_branch": self.default_branch,
        }

    def clear_cache(self) -> None:
        """清空搜索缓存，供 review 结束后主动释放历史上下文。"""

        self._cache.clear()

    def _store_cache(
        self,
        cache_key: tuple[str, tuple[str, ...], int],
        matches: list[dict[str, Any]],
    ) -> None:
        self._cache[cache_key] = [dict(item) for item in matches]
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def is_searchable_path(self, relative_path: str) -> bool:
        return self._is_searchable_relative_path(relative_path)

    def search_symbol_context(
        self,
        symbol: str,
        globs: list[str] | None = None,
        *,
        definition_limit: int = 4,
        reference_limit: int = 8,
    ) -> dict[str, Any]:
        """把 symbol 命中拆成 definitions / references 两类结果。"""
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol:
            return {
                "symbol": normalized_symbol,
                "definitions": [],
                "references": [],
                "cache_hit": False,
                "local_path": str(self.local_path),
                "default_branch": self.default_branch,
            }
        result = self.search(normalized_symbol, globs=globs, limit=definition_limit + reference_limit + 8)
        definitions: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        for item in result.get("matches", []):
            snippet = str(item.get("snippet") or "")
            if self._looks_like_definition(normalized_symbol, snippet):
                if len(definitions) < definition_limit:
                    definitions.append(item)
                continue
            if len(references) < reference_limit:
                references.append(item)
        return {
            "symbol": normalized_symbol,
            "definitions": definitions,
            "references": references,
            "cache_hit": bool(result.get("cache_hit")),
            "local_path": str(self.local_path),
            "default_branch": self.default_branch,
        }

    def search_many(
        self,
        queries: list[str],
        globs: list[str] | None = None,
        *,
        limit_per_query: int = 6,
        total_limit: int = 12,
    ) -> dict[str, Any]:
        """批量执行多个查询词，并把结果去重后合并。"""
        deduped_queries: list[str] = []
        for query in queries:
            normalized = str(query or "").strip()
            if not normalized or normalized in deduped_queries:
                continue
            deduped_queries.append(normalized)
        matches: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, int]] = set()
        cache_hit = True
        for query in deduped_queries:
            result = self.search(query, globs=globs, limit=limit_per_query)
            cache_hit = cache_hit and bool(result.get("cache_hit"))
            for item in result.get("matches", []):
                path = str(item.get("path") or "").strip()
                line_number = int(item.get("line_number") or 0)
                key = (path, line_number)
                if not path or key in seen_keys:
                    continue
                seen_keys.add(key)
                matches.append({"query": query, **item})
                if len(matches) >= total_limit:
                    break
            if len(matches) >= total_limit:
                break
        return {
            "queries": deduped_queries,
            "matches": matches,
            "cache_hit": cache_hit,
            "local_path": str(self.local_path),
            "default_branch": self.default_branch,
        }

    def load_file_context(self, relative_path: str, line_start: int, radius: int = 12) -> dict[str, Any]:
        """读取目标文件指定行附近的上下文片段。"""
        if not self.is_ready():
            return {"path": relative_path, "snippet": "", "line_start": line_start}
        target = (self.local_path / relative_path).resolve()
        if not target.exists() or not target.is_file():
            return {"path": relative_path, "snippet": "", "line_start": line_start}
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(0, line_start - radius - 1)
        end = min(len(lines), line_start + radius)
        snippet = "\n".join(f"{index + 1:>4} | {lines[index]}" for index in range(start, end))
        return {"path": relative_path, "snippet": snippet, "line_start": line_start}

    def load_file_range(
        self,
        relative_path: str,
        start_line: int,
        end_line: int,
        *,
        padding: int = 4,
        expand_to_block: bool = False,
    ) -> dict[str, Any]:
        """读取目标文件给定范围附近的完整片段。"""
        normalized_start = max(1, int(start_line or 1))
        normalized_end = max(normalized_start, int(end_line or normalized_start))
        if not self.is_ready():
            return {
                "path": relative_path,
                "snippet": "",
                "line_start": normalized_start,
                "line_end": normalized_end,
            }
        target = (self.local_path / relative_path).resolve()
        if not target.exists() or not target.is_file():
            return {
                "path": relative_path,
                "snippet": "",
                "line_start": normalized_start,
                "line_end": normalized_end,
            }
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(0, normalized_start - padding - 1)
        end = min(len(lines), normalized_end + padding)
        if expand_to_block:
            start, end = self._expand_range_to_code_block(lines, start, end, normalized_start, normalized_end)
        snippet = "\n".join(f"{index + 1:>4} | {lines[index]}" for index in range(start, end))
        return {
            "path": relative_path,
            "snippet": snippet,
            "line_start": normalized_start,
            "line_end": normalized_end,
        }

    def _expand_range_to_code_block(
        self,
        lines: list[str],
        start_index: int,
        end_index: int,
        normalized_start: int,
        normalized_end: int,
    ) -> tuple[int, int]:
        """尽量把片段扩展到完整方法/代码块，而不是只停在问题点附近几行。"""

        if not lines:
            return start_index, end_index

        total = len(lines)
        anchor_start = max(0, min(total - 1, normalized_start - 1))
        anchor_end = max(anchor_start, min(total - 1, normalized_end - 1))

        block_start = self._find_code_block_start(lines, anchor_start, lower_bound=start_index)
        block_end = self._find_code_block_end(lines, block_start, upper_bound=end_index)

        expanded_start = block_start
        expanded_end = block_end

        max_lines = 80
        if expanded_end - expanded_start > max_lines:
            centered_start = max(0, anchor_start - 24)
            centered_end = min(total, centered_start + max_lines)
            if centered_end - centered_start < max_lines:
                centered_start = max(0, centered_end - max_lines)
            expanded_start = min(expanded_start, centered_start)
            expanded_end = max(expanded_start + 1, min(expanded_end, centered_end))

        return expanded_start, expanded_end

    def _find_code_block_start(self, lines: list[str], anchor_index: int, *, lower_bound: int) -> int:
        signature_patterns = (
            "public ",
            "private ",
            "protected ",
            "internal ",
            "function ",
            "def ",
            "async ",
            "@",
        )
        control_flow_prefixes = ("if ", "for ", "while ", "switch ", "catch ", "else ", "try")
        index = anchor_index
        min_index = max(0, lower_bound - 24)
        while index >= min_index:
            stripped = lines[index].strip()
            if stripped:
                if any(stripped.startswith(prefix) for prefix in signature_patterns):
                    return index
                if (
                    stripped.endswith("{")
                    and not any(stripped.startswith(prefix) for prefix in control_flow_prefixes)
                    and ("(" in stripped or " class " in stripped or stripped.startswith("class "))
                ):
                    return index
            if not stripped and index < anchor_index - 1:
                break
            index -= 1
        return max(0, lower_bound)

    def _find_code_block_end(self, lines: list[str], block_start_index: int, *, upper_bound: int) -> int:
        total = len(lines)
        start = max(0, min(total - 1, block_start_index))
        max_index = min(total - 1, upper_bound + 36)
        balance = 0
        seen_open = False
        index = start
        while index <= max_index:
            line = lines[index]
            balance += line.count("{")
            if line.count("{") > 0:
                seen_open = True
            balance -= line.count("}")
            if seen_open and balance <= 0 and index >= block_start_index:
                return index + 1
            index += 1
        return min(total, upper_bound)

    def _search_with_ripgrep(self, query: str, globs: list[str], limit: int) -> list[dict[str, Any]]:
        rg_bin = shutil.which("rg")
        if not rg_bin:
            return []
        cmd = [
            rg_bin,
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-count",
            str(limit),
            "--glob",
            "!.git/**",
            "--glob",
            "!node_modules/**",
            "--glob",
            "!dist/**",
            "--glob",
            "!build/**",
            "--glob",
            "!.next/**",
            "--glob",
            "!.turbo/**",
            "--glob",
            "!coverage/**",
            "--glob",
            "!**/*.lock",
            query,
        ]
        for search_root_glob in self._build_search_root_globs():
            cmd.extend(["--glob", search_root_glob])
        for pattern in globs:
            cmd.extend(["--glob", pattern])
        try:
            completed = subprocess.run(
                cmd,
                cwd=self.local_path,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return []
        if completed.returncode not in {0, 1}:
            return []
        matches: list[dict[str, Any]] = []
        stdout_text = completed.stdout or ""
        for raw_line in stdout_text.splitlines():
            parts = raw_line.split(":", 2)
            if len(parts) != 3:
                continue
            rel_path, line_number, snippet = parts
            if not self._is_searchable_relative_path(rel_path):
                continue
            matches.append(
                {
                    "path": rel_path,
                    "line_number": int(line_number),
                    "snippet": snippet.strip(),
                }
            )
            if len(matches) >= limit:
                break
        return matches

    def _search_with_fallback(self, query: str, globs: list[str], limit: int) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for path in self.local_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.local_path).as_posix()
            if not self._is_searchable_relative_path(relative):
                continue
            if globs and not any(fnmatch.fnmatch(relative, pattern) for pattern in globs):
                continue
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for index, line in enumerate(lines, start=1):
                if query in line:
                    matches.append(
                        {
                            "path": relative,
                            "line_number": index,
                            "snippet": line.strip(),
                        }
                    )
                    break
            if len(matches) >= limit:
                break
        return matches

    def _looks_like_definition(self, symbol: str, snippet: str) -> bool:
        normalized_symbol = symbol.strip()
        normalized_snippet = snippet.strip()
        definition_markers = [
            f"def {normalized_symbol}",
            f"class {normalized_symbol}",
            f"interface {normalized_symbol}",
            f"enum {normalized_symbol}",
            f"record {normalized_symbol}",
            f"@interface {normalized_symbol}",
            f"function {normalized_symbol}",
            f"const {normalized_symbol}",
            f"let {normalized_symbol}",
            f"var {normalized_symbol}",
            f"export const {normalized_symbol}",
            f"export function {normalized_symbol}",
            f"export class {normalized_symbol}",
            f"public interface {normalized_symbol}",
            f"public class {normalized_symbol}",
            f"public enum {normalized_symbol}",
            f"public record {normalized_symbol}",
            f"{normalized_symbol} =",
            f"{normalized_symbol}:",
        ]
        lowered = normalized_snippet.lower()
        return any(marker.lower() in lowered for marker in definition_markers)

    def _is_searchable_relative_path(self, relative_path: str) -> bool:
        normalized = str(relative_path or "").strip().replace("\\", "/")
        if not normalized:
            return False
        path = Path(normalized)
        allowed_roots = self._get_search_root_prefixes()
        if allowed_roots and not any(
            normalized == root or normalized.startswith(f"{root}/")
            for root in allowed_roots
        ):
            return False
        if any(part in self.EXCLUDED_PATH_PARTS for part in path.parts):
            return False
        if self._looks_like_test_file(path):
            return False
        if path.name in self.EXCLUDED_FILENAMES:
            return False
        if path.suffix.lower() in self.EXCLUDED_SUFFIXES:
            return False
        return True

    def _get_search_root_prefixes(self) -> tuple[str, ...]:
        if self._search_root_prefixes is not None:
            return self._search_root_prefixes
        if not self.is_ready():
            self._search_root_prefixes = ()
            return self._search_root_prefixes

        prefixes: list[str] = []
        for path in self.local_path.rglob("src"):
            if not path.is_dir():
                continue
            try:
                relative = path.relative_to(self.local_path).as_posix()
            except ValueError:
                continue
            if not relative or relative == ".":
                continue
            relative_path = Path(relative)
            if any(part in self.EXCLUDED_PATH_PARTS for part in relative_path.parts):
                continue
            if relative.startswith("."):
                continue
            prefixes.append(relative)

        self._search_root_prefixes = tuple(sorted(dict.fromkeys(prefixes)))
        return self._search_root_prefixes

    def _build_search_root_globs(self) -> list[str]:
        prefixes = self._get_search_root_prefixes()
        if not prefixes:
            return []
        return [f"{prefix}/**" for prefix in prefixes]

    def _looks_like_test_file(self, path: Path) -> bool:
        lower_parts = [part.lower() for part in path.parts]
        if any(part in {"test", "tests", "__tests__", "__mocks__", "spec", "specs", "fixtures"} for part in lower_parts):
            return True
        name = path.name
        stem = path.stem
        lower_name = name.lower()
        lower_stem = stem.lower()
        if any(token in lower_name for token in [".test.", ".tests.", ".spec.", ".specs.", ".it."]):
            return True
        if lower_stem in {"test", "tests", "spec", "specs"}:
            return True
        if any(lower_stem.endswith(suffix) for suffix in ("_test", "_tests", "_spec", "_specs", "-test", "-tests", "-spec", "-specs")):
            return True
        return bool(re.search(r"(Test|Tests|Spec|Specs|IT|ITCase)$", stem))
