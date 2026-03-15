from __future__ import annotations

import json
from pathlib import Path

from app.domain.models.review_tool_plugin import ReviewToolPlugin


class ToolPluginLoader:
    """扫描扩展 tool 目录并返回可执行插件元数据。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def list_all(self) -> list[ReviewToolPlugin]:
        if not self.root.exists():
            return []
        plugins: list[ReviewToolPlugin] = []
        for tool_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            plugin = self._load_tool(tool_dir)
            if plugin is not None:
                plugins.append(plugin)
        return plugins

    def get(self, tool_id: str) -> ReviewToolPlugin | None:
        for plugin in self.list_all():
            if plugin.tool_id == tool_id:
                return plugin
        return None

    def _load_tool(self, tool_dir: Path) -> ReviewToolPlugin | None:
        tool_json = tool_dir / "tool.json"
        if not tool_json.exists():
            return None
        metadata = json.loads(tool_json.read_text(encoding="utf-8"))
        return ReviewToolPlugin.model_validate({**metadata, "tool_path": str(tool_dir)})
