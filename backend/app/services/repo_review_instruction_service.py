from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any


class RepoReviewInstructionService:
    """读取仓库内自带的代码检视规则。

    支持两类轻量约定：
    - `REVIEW.md`: 仓库级通用 review 说明，也会读取目标文件父目录链上的 REVIEW.md。
    - `.codereview.yaml` / `.codereview.yml`: 简单 paths 规则，按 glob 匹配目标文件。
    """

    ROOT_FILENAMES = (".codereview.yaml", ".codereview.yml")
    REVIEW_FILENAME = "REVIEW.md"

    def load_for_file(self, repo_root: str | Path, file_path: str) -> dict[str, object]:
        root = Path(str(repo_root or "")).expanduser()
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        if not normalized_file or not root.exists() or not root.is_dir():
            return {"instructions": [], "summary": ""}

        instructions: list[dict[str, object]] = []
        instructions.extend(self._load_review_md_chain(root, normalized_file))
        instructions.extend(self._load_codereview_config(root, normalized_file))
        return {
            "instructions": instructions[:8],
            "summary": self.build_summary(instructions),
        }

    def build_summary(self, instructions: list[dict[str, object]]) -> str:
        if not instructions:
            return ""
        lines = ["仓库内检视规则："]
        for item in instructions[:8]:
            title = str(item.get("title") or item.get("source") or "规则").strip()
            content = str(item.get("content") or "").strip()
            path = str(item.get("path") or "").strip()
            prefix = f"- {title}"
            if path:
                prefix += f"（{path}）"
            lines.append(prefix)
            if content:
                excerpt = " ".join(line.strip() for line in content.splitlines() if line.strip())
                lines.append(f"  {excerpt[:420]}")
        return "\n".join(lines)

    def _load_review_md_chain(self, root: Path, file_path: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        candidate_paths = [root / self.REVIEW_FILENAME]
        parent = Path(file_path).parent
        parts: list[str] = []
        for part in parent.parts:
            parts.append(part)
            candidate_paths.append(root.joinpath(*parts) / self.REVIEW_FILENAME)
        seen: set[Path] = set()
        for path in candidate_paths:
            if path in seen or not path.exists() or not path.is_file():
                continue
            seen.add(path)
            content = self._safe_read_text(path)
            if not content:
                continue
            result.append(
                {
                    "source": "REVIEW.md",
                    "title": "目录检视说明" if path.parent != root else "仓库检视说明",
                    "path": str(path.relative_to(root)),
                    "content": content[:2400],
                    "matched_globs": ["**/*"],
                }
            )
        return result

    def _load_codereview_config(self, root: Path, file_path: str) -> list[dict[str, object]]:
        config_path = next((root / name for name in self.ROOT_FILENAMES if (root / name).exists()), None)
        if config_path is None:
            return []
        content = self._safe_read_text(config_path)
        if not content:
            return []
        entries = self._parse_simple_path_rules(content)
        result: list[dict[str, object]] = []
        for entry in entries:
            globs = [str(item).strip() for item in list(entry.get("paths") or []) if str(item).strip()]
            if globs and not any(fnmatch.fnmatch(file_path, pattern) for pattern in globs):
                continue
            body = str(entry.get("instruction") or entry.get("content") or "").strip()
            if not body:
                continue
            result.append(
                {
                    "source": config_path.name,
                    "title": str(entry.get("title") or "路径检视规则").strip(),
                    "path": str(config_path.relative_to(root)),
                    "content": body[:1800],
                    "matched_globs": globs,
                    "expert_ids": [str(item).strip() for item in list(entry.get("experts") or []) if str(item).strip()],
                }
            )
        return result

    def _parse_simple_path_rules(self, content: str) -> list[dict[str, object]]:
        # 这是一个有意克制的解析器，覆盖团队常见的 .codereview.yaml 写法；
        # 如果 YAML 更复杂，至少不会失败，只是忽略无法识别的规则。
        lines = str(content or "").splitlines()
        entries: list[dict[str, object]] = []
        current: dict[str, Any] | None = None
        active_list_key = ""
        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                payload = stripped[2:].strip()
                if ":" in payload:
                    if current:
                        entries.append(current)
                    current = {}
                    key, value = payload.split(":", 1)
                    self._assign_yaml_value(current, key.strip(), value.strip())
                    active_list_key = key.strip()
                    continue
                if current is not None and active_list_key:
                    current.setdefault(active_list_key, []).append(self._strip_quotes(payload))
                    continue
            if current is None:
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped):
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()
                self._assign_yaml_value(current, key, value)
                active_list_key = key if not value else ""
                continue
            if active_list_key and stripped.startswith("-"):
                current.setdefault(active_list_key, []).append(self._strip_quotes(stripped[1:].strip()))
                continue
            if active_list_key in {"instruction", "content"}:
                previous = str(current.get(active_list_key) or "")
                current[active_list_key] = (previous + "\n" + stripped).strip()
        if current:
            entries.append(current)
        return entries

    def _assign_yaml_value(self, target: dict[str, Any], key: str, value: str) -> None:
        normalized = str(value or "").strip()
        if normalized in {"", "|", ">"}:
            target[key] = [] if key in {"paths", "experts"} else ""
            return
        if normalized.startswith("[") and normalized.endswith("]"):
            target[key] = [
                self._strip_quotes(item.strip())
                for item in normalized[1:-1].split(",")
                if item.strip()
            ]
            return
        target[key] = self._strip_quotes(normalized)

    def _strip_quotes(self, value: str) -> str:
        normalized = str(value or "").strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
            return normalized[1:-1]
        return normalized

    def _safe_read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")[:6000]
        except OSError:
            return ""
