from __future__ import annotations


class DiffExcerptService:
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
