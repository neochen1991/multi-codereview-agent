from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.code_graph.models import CodeGraphEdge, CodeGraphNode


class JavaTreeSitterParser:
    """Extract Java nodes and relationship edges with Tree-sitter."""

    def __init__(self) -> None:
        from tree_sitter_language_pack import get_parser

        self.parser = get_parser("java")

    def parse_file(self, repo_root: Path, relative_path: str) -> dict[str, object]:
        target = Path(repo_root) / relative_path
        source = target.read_bytes()
        tree = self.parser.parse(source)
        package_name = self._package_name(source)
        import_map = self._import_map(source)
        class_nodes = self._find_nodes(
            tree.root_node,
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
        )
        method_nodes = self._find_nodes(tree.root_node, {"method_declaration", "constructor_declaration"})
        nodes: list[CodeGraphNode] = []
        edges: list[CodeGraphEdge] = []
        class_ranges: list[tuple[Any, str]] = []
        class_field_types: dict[str, dict[str, str]] = {}
        test_method_names: set[str] = set()

        for class_node in class_nodes:
            class_name = self._named_child_text(class_node, source, "identifier") or Path(relative_path).stem
            qualified_class = self._qualified(package_name, class_name)
            line_start, line_end = self._node_lines(class_node)
            class_kind = self._class_kind(class_node.type)
            class_field_types[qualified_class] = self._class_field_types(class_node, source, package_name, import_map)
            nodes.append(
                CodeGraphNode(
                    kind=class_kind,
                    name=class_name,
                    qualified_name=qualified_class,
                    language="java",
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    snippet=self._node_text(class_node, source)[:1000],
                )
            )
            class_ranges.append((class_node, qualified_class))
            for contract in self._extract_super_interfaces(class_node, source):
                edges.append(
                    CodeGraphEdge(
                        kind="implements",
                        source_qualified_name=qualified_class,
                        target_qualified_name=self._qualified_type(package_name, contract, import_map),
                        file_path=relative_path,
                        line_number=line_start,
                        confidence=1.0,
                        confidence_tier="extracted",
                    )
                )
            superclass = self._extract_superclass(class_node, source)
            if superclass:
                edges.append(
                    CodeGraphEdge(
                        kind="extends",
                        source_qualified_name=qualified_class,
                        target_qualified_name=self._qualified_type(package_name, superclass, import_map),
                        file_path=relative_path,
                        line_number=line_start,
                        confidence=1.0,
                        confidence_tier="extracted",
                    )
                )

        for method_node in method_nodes:
            method_name = self._named_child_text(method_node, source, "identifier")
            parent_class = self._enclosing_class(method_node, class_ranges)
            if not method_name and parent_class:
                method_name = parent_class.rsplit(".", 1)[-1]
            if not method_name:
                continue
            qualified_method = f"{parent_class}.{method_name}" if parent_class else self._qualified(package_name, method_name)
            line_start, line_end = self._node_lines(method_node)
            nodes.append(
                CodeGraphNode(
                    kind="constructor" if method_node.type == "constructor_declaration" else "method",
                    name=method_name,
                    qualified_name=qualified_method,
                    language="java",
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    parent_qualified_name=parent_class,
                    signature=self._method_signature(method_node, source),
                    is_test=self._looks_like_test_method(method_node, source),
                    snippet=self._node_text(method_node, source)[:1200],
                )
            )
            if self._looks_like_test_method(method_node, source):
                test_method_names.add(qualified_method)
            if parent_class:
                edges.append(
                    CodeGraphEdge(
                        kind="contains",
                        source_qualified_name=parent_class,
                        target_qualified_name=qualified_method,
                        file_path=relative_path,
                        line_number=line_start,
                        confidence=1.0,
                        confidence_tier="extracted",
                    )
                )
            scope_types = {
                **class_field_types.get(parent_class, {}),
                **self._method_parameter_types(method_node, source, package_name, import_map),
                **self._local_variable_types(method_node, source, package_name, import_map),
            }
            for invocation in self._find_nodes(method_node, {"method_invocation"}):
                target_name = self._method_invocation_name(
                    invocation,
                    source,
                    package_name=package_name,
                    parent_class=parent_class,
                    scope_types=scope_types,
                    import_map=import_map,
                )
                if not target_name:
                    continue
                edges.append(
                    CodeGraphEdge(
                        kind="calls",
                        source_qualified_name=qualified_method,
                        target_qualified_name=target_name,
                        file_path=relative_path,
                        line_number=self._node_lines(invocation)[0],
                        confidence=1.0,
                        confidence_tier="extracted",
                        metadata={"raw_text": self._node_text(invocation, source)[:300]},
                    )
                )
            for creation in self._find_nodes(method_node, {"object_creation_expression"}):
                target_type = self._object_creation_type(creation, source)
                if not target_type:
                    continue
                edges.append(
                    CodeGraphEdge(
                        kind="references_type",
                        source_qualified_name=qualified_method,
                        target_qualified_name=self._qualified_type(package_name, target_type, import_map),
                        file_path=relative_path,
                        line_number=self._node_lines(creation)[0],
                        confidence=1.0,
                        confidence_tier="extracted",
                        metadata={"raw_text": self._node_text(creation, source)[:300]},
                    )
                )
        for edge in list(edges):
            if edge.kind == "calls" and edge.source_qualified_name in test_method_names:
                edges.append(
                    CodeGraphEdge(
                        kind="tested_by",
                        source_qualified_name=edge.target_qualified_name,
                        target_qualified_name=edge.source_qualified_name,
                        file_path=edge.file_path,
                        line_number=edge.line_number,
                        confidence=edge.confidence,
                        confidence_tier=edge.confidence_tier,
                        metadata={"source_call": edge.metadata.get("raw_text", "")},
                    )
                )
        return {"nodes": nodes, "edges": edges}

    def _find_nodes(self, root: Any, node_types: set[str]) -> list[Any]:
        result: list[Any] = []
        stack = [root]
        while stack:
            node = stack.pop()
            if str(getattr(node, "type", "") or "") in node_types:
                result.append(node)
            stack.extend(reversed(list(getattr(node, "children", []) or [])))
        return result

    def _package_name(self, source: bytes) -> str:
        text = source.decode("utf-8", errors="ignore")
        match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", text, flags=re.MULTILINE)
        return match.group(1) if match else ""

    def _named_child_text(self, node: Any, source: bytes, child_type: str) -> str:
        for child in list(getattr(node, "children", []) or []):
            if str(getattr(child, "type", "") or "") == child_type:
                return self._node_text(child, source).strip()
        return ""

    def _extract_super_interfaces(self, class_node: Any, source: bytes) -> list[str]:
        text = self._node_text(class_node, source)
        match = re.search(r"\bimplements\s+([A-Za-z0-9_.,\s<>]+)", text)
        if not match:
            return []
        return [item.strip().split("<", 1)[0] for item in match.group(1).split(",") if item.strip()]

    def _extract_superclass(self, class_node: Any, source: bytes) -> str:
        text = self._node_text(class_node, source)
        match = re.search(r"\bextends\s+([A-Za-z0-9_.$]+)", text)
        return match.group(1).strip() if match else ""

    def _enclosing_class(self, method_node: Any, class_ranges: list[tuple[Any, str]]) -> str:
        start = int(getattr(method_node, "start_byte", 0) or 0)
        end = int(getattr(method_node, "end_byte", 0) or 0)
        candidates: list[tuple[int, str]] = []
        for class_node, qualified_name in class_ranges:
            class_start = int(getattr(class_node, "start_byte", 0) or 0)
            class_end = int(getattr(class_node, "end_byte", 0) or 0)
            if class_start <= start and end <= class_end:
                candidates.append((class_end - class_start, qualified_name))
        if not candidates:
            return ""
        return sorted(candidates, key=lambda item: item[0])[0][1]

    def _method_invocation_name(
        self,
        node: Any,
        source: bytes,
        *,
        package_name: str,
        parent_class: str,
        scope_types: dict[str, str],
        import_map: dict[str, str],
    ) -> str:
        text = self._node_text(node, source).strip()
        match = re.search(r"([A-Za-z_$][A-Za-z0-9_$.]*)\s*\(", text)
        raw_target = match.group(1) if match else ""
        if not raw_target:
            return ""
        if "." not in raw_target:
            return f"{parent_class}.{raw_target}" if parent_class else raw_target
        qualifier, method_name = raw_target.rsplit(".", 1)
        if qualifier == "this" and parent_class:
            return f"{parent_class}.{method_name}"
        if qualifier in scope_types:
            return f"{scope_types[qualifier]}.{method_name}"
        if qualifier and qualifier[0].isupper():
            return f"{self._qualified_type(package_name, qualifier, import_map)}.{method_name}"
        return raw_target

    def _object_creation_type(self, node: Any, source: bytes) -> str:
        text = self._node_text(node, source).strip()
        match = re.search(r"\bnew\s+([A-Za-z_$][A-Za-z0-9_$.]*)", text)
        return match.group(1) if match else ""

    def _method_signature(self, method_node: Any, source: bytes) -> str:
        text = self._node_text(method_node, source).strip()
        brace_index = text.find("{")
        return text if brace_index < 0 else text[:brace_index].strip()

    def _import_map(self, source: bytes) -> dict[str, str]:
        text = source.decode("utf-8", errors="ignore")
        imports: dict[str, str] = {}
        for match in re.finditer(r"^\s*import\s+(?!static)([A-Za-z0-9_.]+)\s*;", text, flags=re.MULTILINE):
            qualified = match.group(1).strip()
            simple = qualified.rsplit(".", 1)[-1]
            if simple and qualified:
                imports[simple] = qualified
        return imports

    def _class_field_types(
        self,
        class_node: Any,
        source: bytes,
        package_name: str,
        import_map: dict[str, str],
    ) -> dict[str, str]:
        text = self._node_text(class_node, source)
        fields: dict[str, str] = {}
        for match in re.finditer(
            r"(?:private|protected|public)?\s*(?:final\s+)?([A-Z][A-Za-z0-9_$.<>]*)\s+([a-zA-Z_$][A-Za-z0-9_$]*)\s*(?:=|;)",
            text,
        ):
            field_type = self._qualified_type(package_name, match.group(1), import_map)
            field_name = match.group(2)
            fields[field_name] = field_type
        constructor_params = self._constructor_parameter_types(text, package_name, import_map)
        for assignment in re.finditer(r"this\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*;", text):
            field_name = assignment.group(1)
            parameter_name = assignment.group(2)
            if parameter_name in constructor_params:
                fields[field_name] = constructor_params[parameter_name]
        return fields

    def _constructor_parameter_types(
        self,
        class_text: str,
        package_name: str,
        import_map: dict[str, str],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for params in re.findall(r"\b[A-Z][A-Za-z0-9_$]*\s*\(([^)]*)\)\s*\{", class_text):
            result.update(self._parameter_types_from_text(params, package_name, import_map))
        return result

    def _method_parameter_types(
        self,
        method_node: Any,
        source: bytes,
        package_name: str,
        import_map: dict[str, str],
    ) -> dict[str, str]:
        signature = self._method_signature(method_node, source)
        match = re.search(r"\(([^)]*)\)", signature)
        if not match:
            return {}
        return self._parameter_types_from_text(match.group(1), package_name, import_map)

    def _local_variable_types(
        self,
        method_node: Any,
        source: bytes,
        package_name: str,
        import_map: dict[str, str],
    ) -> dict[str, str]:
        text = self._node_text(method_node, source)
        result: dict[str, str] = {}
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9_$.<>]*)\s+([a-zA-Z_$][A-Za-z0-9_$]*)\s*=", text):
            result[match.group(2)] = self._qualified_type(package_name, match.group(1), import_map)
        return result

    def _parameter_types_from_text(
        self,
        params_text: str,
        package_name: str,
        import_map: dict[str, str],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_param in params_text.split(","):
            normalized = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", raw_param).strip()
            parts = normalized.split()
            if len(parts) < 2:
                continue
            param_name = parts[-1].replace("...", "").replace("[]", "").strip()
            param_type = parts[-2].replace("...", "").replace("[]", "").strip()
            if param_name and param_type and param_type != "var":
                result[param_name] = self._qualified_type(package_name, param_type, import_map)
        return result

    def _looks_like_test_method(self, method_node: Any, source: bytes) -> bool:
        text = self._node_text(method_node, source)
        return "@Test" in text or "org.junit" in text

    def _node_lines(self, node: Any) -> tuple[int, int]:
        start = getattr(node, "start_point", (0, 0))
        end = getattr(node, "end_point", start)
        start_line = int(start[0]) + 1 if isinstance(start, tuple) and start else 1
        end_line = int(end[0]) + 1 if isinstance(end, tuple) and end else start_line
        return start_line, end_line

    def _node_text(self, node: Any, source: bytes) -> str:
        start_byte = int(getattr(node, "start_byte", 0) or 0)
        end_byte = int(getattr(node, "end_byte", start_byte) or start_byte)
        return source[start_byte:end_byte].decode("utf-8", errors="ignore")

    def _qualified(self, package_name: str, name: str) -> str:
        if not package_name or "." in name:
            return name
        return f"{package_name}.{name}"

    def _qualified_type(self, package_name: str, name: str, import_map: dict[str, str]) -> str:
        normalized = str(name or "").strip()
        normalized = normalized.split("<", 1)[0].replace("[]", "").strip()
        if not normalized:
            return ""
        if "." in normalized:
            return normalized
        if normalized in import_map:
            return import_map[normalized]
        return self._qualified(package_name, normalized)

    def _class_kind(self, node_type: str) -> str:
        if node_type == "interface_declaration":
            return "interface"
        if node_type == "enum_declaration":
            return "enum"
        if node_type == "record_declaration":
            return "record"
        return "class"
