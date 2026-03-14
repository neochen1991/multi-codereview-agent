from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def ensure_parent(path: Path) -> None:
    """确保目标文件的父目录已经存在。"""

    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    """以原子替换方式写入 JSON，避免并发读到半截文件。"""

    ensure_parent(path)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def read_json(path: Path) -> Any:
    """读取并解析 JSON 文件。"""

    return json.loads(path.read_text(encoding="utf-8"))
