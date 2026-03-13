from __future__ import annotations


class DiffExcerptService:
    def list_hunks(self, unified_diff: str, file_path: str) -> list[dict[str, object]]:
        lines = unified_diff.splitlines()
        active_file = ""
        current_new_line: int | None = None
        current_header = ""
        file_entries: list[tuple[int | None, str]] = []
        hunks: list[dict[str, object]] = []

        def flush_hunk() -> None:
            nonlocal file_entries, current_header
            if active_file != file_path or not file_entries:
                file_entries = []
                current_header = ""
                return
            line_numbers = [line for line, _ in file_entries if line is not None]
            changed_lines = [
                line
                for line, entry in file_entries
                if line is not None and "| +" in entry
            ]
            if not line_numbers:
                file_entries = []
                current_header = ""
                return
            hunks.append(
                {
                    "file_path": file_path,
                    "hunk_header": current_header,
                    "start_line": min(line_numbers),
                    "end_line": max(line_numbers),
                    "changed_lines": changed_lines,
                    "excerpt": f"# {file_path}\n" + "\n".join(entry for _, entry in file_entries),
                }
            )
            file_entries = []
            current_header = ""

        for raw_line in lines:
            if raw_line.startswith("diff --git "):
                flush_hunk()
                active_file = self._parse_file_path(raw_line)
                current_new_line = None
                continue
            if active_file != file_path:
                continue
            if raw_line.startswith("@@"):
                flush_hunk()
                current_header = raw_line
                current_new_line = self._parse_hunk_start(raw_line)
                continue
            if (
                not raw_line
                or current_new_line is None
                or raw_line.startswith("--- ")
                or raw_line.startswith("+++ ")
                or raw_line == r"\ No newline at end of file"
            ):
                continue
            prefix = raw_line[0]
            if prefix not in {"+", "-", " "}:
                continue
            content = raw_line[1:] if prefix in {"+", "-", " "} else raw_line
            if prefix == "-":
                file_entries.append((None, f"   - | {content}"))
                continue
            file_entries.append((current_new_line, f"{current_new_line:>4} | {prefix}{content}"))
            current_new_line += 1

        flush_hunk()
        return hunks

    def find_best_hunk(
        self,
        unified_diff: str,
        file_path: str,
        target_line: int,
    ) -> dict[str, object] | None:
        hunks = self.list_hunks(unified_diff, file_path)
        if not hunks:
            return None
        return min(
            hunks,
            key=lambda item: min(
                abs(int(line) - target_line)
                for line in list(item.get("changed_lines") or []) or [int(item.get("start_line") or target_line)]
            ),
        )

    def find_nearest_line(
        self,
        unified_diff: str,
        file_path: str,
        target_line: int,
    ) -> int | None:
        line_numbers = self._collect_changed_line_numbers(unified_diff, file_path) or self._collect_line_numbers(
            unified_diff,
            file_path,
        )
        if not line_numbers:
            return None
        return min(line_numbers, key=lambda line: abs(line - target_line))

    def extract_excerpt(
        self,
        unified_diff: str,
        file_path: str,
        target_line: int,
        *,
        context_lines: int = 2,
    ) -> str:
        best_hunk = self.find_best_hunk(unified_diff, file_path, target_line)
        if best_hunk:
            hunk_lines = str(best_hunk.get("excerpt") or "").splitlines()
            if hunk_lines:
                return "\n".join(hunk_lines[: 1 + ((context_lines * 2) + 5)])
        lines = unified_diff.splitlines()
        active_file = ""
        current_new_line: int | None = None
        file_entries: list[tuple[int | None, str]] = []
        candidates: list[tuple[int, list[tuple[int | None, str]]]] = []

        for raw_line in lines:
            if raw_line.startswith("diff --git "):
                if active_file == file_path and file_entries:
                    candidates.extend(self._collect_candidates(file_entries, target_line, context_lines))
                active_file = self._parse_file_path(raw_line)
                file_entries = []
                current_new_line = None
                continue
            if active_file != file_path:
                continue
            if raw_line.startswith("@@"):
                if file_entries:
                    candidates.extend(self._collect_candidates(file_entries, target_line, context_lines))
                    file_entries = []
                current_new_line = self._parse_hunk_start(raw_line)
                continue
            if (
                not raw_line
                or current_new_line is None
                or raw_line.startswith("--- ")
                or raw_line.startswith("+++ ")
                or raw_line == r"\ No newline at end of file"
            ):
                continue
            prefix = raw_line[0]
            if prefix not in {"+", "-", " "}:
                continue
            content = raw_line[1:] if prefix in {"+", "-", " "} else raw_line
            if prefix == "-":
                file_entries.append((None, f"   - | {content}"))
                continue
            file_entries.append((current_new_line, f"{current_new_line:>4} | {prefix}{content}"))
            current_new_line += 1

        if active_file == file_path and file_entries:
            candidates.extend(self._collect_candidates(file_entries, target_line, context_lines))

        if not candidates:
            return ""

        _, best_window = min(candidates, key=lambda item: item[0])
        return f"# {file_path}\n" + "\n".join(entry for _, entry in best_window)

    def _collect_line_numbers(self, unified_diff: str, file_path: str) -> list[int]:
        lines = unified_diff.splitlines()
        active_file = ""
        current_new_line: int | None = None
        line_numbers: list[int] = []

        for raw_line in lines:
            if raw_line.startswith("diff --git "):
                active_file = self._parse_file_path(raw_line)
                current_new_line = None
                continue
            if active_file != file_path:
                continue
            if raw_line.startswith("@@"):
                current_new_line = self._parse_hunk_start(raw_line)
                continue
            if (
                not raw_line
                or current_new_line is None
                or raw_line.startswith("--- ")
                or raw_line.startswith("+++ ")
                or raw_line == r"\ No newline at end of file"
            ):
                continue

            prefix = raw_line[0]
            if prefix not in {"+", "-", " "}:
                continue
            if prefix == "-":
                continue

            line_numbers.append(current_new_line)
            current_new_line += 1

        return line_numbers

    def _collect_changed_line_numbers(self, unified_diff: str, file_path: str) -> list[int]:
        lines = unified_diff.splitlines()
        active_file = ""
        current_new_line: int | None = None
        changed_lines: list[int] = []

        for raw_line in lines:
            if raw_line.startswith("diff --git "):
                active_file = self._parse_file_path(raw_line)
                current_new_line = None
                continue
            if active_file != file_path:
                continue
            if raw_line.startswith("@@"):
                current_new_line = self._parse_hunk_start(raw_line)
                continue
            if (
                not raw_line
                or current_new_line is None
                or raw_line.startswith("--- ")
                or raw_line.startswith("+++ ")
                or raw_line == r"\ No newline at end of file"
            ):
                continue

            prefix = raw_line[0]
            if prefix not in {"+", "-", " "}:
                continue
            if prefix == "+":
                changed_lines.append(current_new_line)
                current_new_line += 1
                continue
            if prefix == "-":
                continue

            current_new_line += 1

        return changed_lines

    def _collect_candidates(
        self,
        file_entries: list[tuple[int | None, str]],
        target_line: int,
        context_lines: int,
    ) -> list[tuple[int, list[tuple[int | None, str]]]]:
        line_numbers = [line for line, _ in file_entries if line is not None]
        if not line_numbers:
            return []
        nearest_line = min(line_numbers, key=lambda line: abs(line - target_line))
        nearest_index = next(index for index, (line, _) in enumerate(file_entries) if line == nearest_line)
        start = max(0, nearest_index - context_lines)
        end = min(len(file_entries), nearest_index + context_lines + 1)
        return [(abs(nearest_line - target_line), file_entries[start:end])]

    def _parse_file_path(self, diff_header: str) -> str:
        parts = diff_header.split()
        if len(parts) < 4:
            return ""
        right = parts[3]
        return right[2:] if right.startswith("b/") else right

    def _parse_hunk_start(self, hunk_header: str) -> int:
        try:
            after_plus = hunk_header.split("+", 1)[1]
            number_part = after_plus.split(",", 1)[0].split(" ", 1)[0]
            return int(number_part)
        except (IndexError, ValueError):
            return 1
