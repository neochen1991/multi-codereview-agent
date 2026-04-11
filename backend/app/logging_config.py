from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(logs_root: Path) -> None:
    """配置后端日志文件和控制台输出（支持重复调用，幂等补齐）。"""

    logs_root.mkdir(parents=True, exist_ok=True)
    backend_log = logs_root / "backend.log"
    root_logger = logging.getLogger()

    # 始终设置 root level，避免被外部默认 WARNING 覆盖导致 info 日志静默。
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    has_backend_file_handler = any(
        isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(backend_log)
        for handler in root_logger.handlers
    )
    if not has_backend_file_handler:
        file_handler = logging.FileHandler(backend_log, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    # 统一 uvicorn 系列 logger 到 root，确保 API 请求日志也带 asctime。
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(logging.INFO)
