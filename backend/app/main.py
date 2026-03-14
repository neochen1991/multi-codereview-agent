from __future__ import annotations

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import experts, governance, issues, knowledge, reviews, settings as settings_routes, streams, triggers
from app.config import settings


def configure_logging() -> None:
    """配置后端日志文件和控制台输出。"""

    logs_root = settings.LOGS_ROOT
    logs_root.mkdir(parents=True, exist_ok=True)
    backend_log = logs_root / "backend.log"
    root_logger = logging.getLogger()
    if any(
        isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(backend_log)
        for handler in root_logger.handlers
    ):
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = logging.FileHandler(backend_log, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    ):
        root_logger.addHandler(stream_handler)


def create_application() -> FastAPI:
    """创建并装配 FastAPI 应用及所有业务路由。"""

    configure_logging()
    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(reviews.router, prefix=settings.API_PREFIX)
    app.include_router(triggers.router, prefix=settings.API_PREFIX)
    app.include_router(streams.router, prefix=settings.API_PREFIX)
    app.include_router(issues.router, prefix=settings.API_PREFIX)
    app.include_router(experts.router, prefix=settings.API_PREFIX)
    app.include_router(knowledge.router, prefix=settings.API_PREFIX)
    app.include_router(settings_routes.router, prefix=settings.API_PREFIX)
    app.include_router(governance.router, prefix=settings.API_PREFIX)

    @app.get("/health")
    def health() -> dict[str, str]:
        """提供轻量健康检查接口。"""

        return {"status": "ok"}

    return app


app = create_application()
