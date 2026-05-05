from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


class CodeGraphFileSelector:
    """Select repository files for local code graph indexing."""

    DEFAULT_EXCLUDED_PARTS = {
        ".git",
        ".idea",
        "node_modules",
        "dist",
        "build",
        "target",
        "coverage",
        "vendor",
        "generated",
        "__pycache__",
    }
    LANGUAGE_SUFFIXES = {
        "java": {".java"},
        "python": {".py"},
        "typescript": {".ts", ".tsx"},
        "javascript": {".js", ".jsx"},
    }

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser()

    def select_files(self, *, languages: list[str] | None = None) -> list[str]:
        suffixes = self._suffixes_for_languages(languages or ["java"])
        candidates = self._git_tracked_files()
        if not candidates:
            candidates = self._filesystem_files()
        ignore_patterns = self._load_ignore_patterns()
        result = [
            path
            for path in candidates
            if self._is_indexable(path, suffixes=suffixes, ignore_patterns=ignore_patterns)
        ]
        return sorted(dict.fromkeys(result))

    def _git_tracked_files(self) -> list[str]:
        try:
            completed = subprocess.run(
                ["git", "ls-files"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return []
        if completed.returncode != 0:
            return []
        return [
            self._normalize_path(line)
            for line in str(completed.stdout or "").splitlines()
            if self._normalize_path(line)
        ]

    def _filesystem_files(self) -> list[str]:
        if not self.repo_root.exists() or not self.repo_root.is_dir():
            return []
        result: list[str] = []
        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(self.repo_root).as_posix()
            except ValueError:
                continue
            result.append(relative)
        return result

    def _load_ignore_patterns(self) -> list[str]:
        path = self.repo_root / ".code-review-graphignore"
        if not path.exists() or not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []
        patterns: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            patterns.append(self._normalize_path(stripped))
        return patterns

    def _is_indexable(self, relative_path: str, *, suffixes: set[str], ignore_patterns: list[str]) -> bool:
        normalized = self._normalize_path(relative_path)
        if not normalized:
            return False
        path = Path(normalized)
        if any(part in self.DEFAULT_EXCLUDED_PARTS for part in path.parts):
            return False
        if path.suffix.lower() not in suffixes:
            return False
        return not any(self._matches(normalized, pattern) for pattern in ignore_patterns)

    def _matches(self, relative_path: str, pattern: str) -> bool:
        normalized_pattern = self._normalize_path(pattern)
        if fnmatch.fnmatch(relative_path, normalized_pattern):
            return True
        if normalized_pattern.endswith("/**"):
            prefix = normalized_pattern[:-3].rstrip("/")
            return relative_path == prefix or relative_path.startswith(f"{prefix}/")
        return False

    def _suffixes_for_languages(self, languages: list[str]) -> set[str]:
        suffixes: set[str] = set()
        for language in languages:
            suffixes.update(self.LANGUAGE_SUFFIXES.get(str(language or "").lower(), set()))
        return suffixes or {".java"}

    def _normalize_path(self, value: object) -> str:
        return str(value or "").strip().replace("\\", "/")
