from __future__ import annotations

import logging
import os
import faulthandler
from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import experts, governance, issues, knowledge, reviews, settings as settings_routes, streams, triggers
from app.config import settings
import app.services.review_service as review_service_module
from app.services.auto_review_scheduler import AutoReviewScheduler
from app.services.memory_probe import MemoryProbe


def configure_logging() -> None:
    """配置后端日志文件和控制台输出。"""

    logs_root = settings.LOGS_ROOT
    logs_root.mkdir(parents=True, exist_ok=True)
    backend_log = logs_root / "backend.log"
    root_logger = logging.getLogger()

    # 不提前 return，避免重复启动/热重载后 root level 被外部改写成 WARNING，
    # 导致 llm info 日志静默。
    root_logger.setLevel(logging.INFO)

    has_backend_file_handler = any(
        isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(backend_log)
        for handler in root_logger.handlers
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    if not has_backend_file_handler:
        file_handler = logging.FileHandler(backend_log, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)


def create_application() -> FastAPI:
    """创建并装配 FastAPI 应用及所有业务路由。"""

    configure_logging()
    MemoryProbe.log("app.create.begin")
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
    scheduler = AutoReviewScheduler(review_service_module.review_service)
    app.state.auto_review_scheduler = scheduler

    http_probe_enabled = str(os.getenv("REVIEW_HTTP_MEMORY_PROBE", "")).strip().lower() in {"1", "true", "on", "yes"}
    if http_probe_enabled:
        @app.middleware("http")
        async def memory_probe_http_middleware(request: Request, call_next):
            path = str(request.url.path or "")
            MemoryProbe.log("http.request.start", method=request.method, path=path)
            response = await call_next(request)
            MemoryProbe.log("http.request.finish", method=request.method, path=path, status_code=response.status_code)
            return response

    watchdog_dump_seconds = max(0, int(os.getenv("REVIEW_WATCHDOG_DUMP_SECONDS", "0") or 0))

    @app.on_event("startup")
    def startup_scheduler() -> None:
        MemoryProbe.log("app.startup.begin")
        if watchdog_dump_seconds > 0:
            faulthandler.enable()
            faulthandler.dump_traceback_later(watchdog_dump_seconds, repeat=True)
        scheduler.start()
        MemoryProbe.log("app.startup.after_scheduler")

    @app.on_event("shutdown")
    def shutdown_scheduler() -> None:
        MemoryProbe.log("app.shutdown.begin")
        scheduler.stop()
        if watchdog_dump_seconds > 0:
            faulthandler.cancel_dump_traceback_later()
        MemoryProbe.log("app.shutdown.after_scheduler")

    @app.get("/health")
    def health() -> dict[str, str]:
        """提供轻量健康检查接口。"""

        return {"status": "ok"}

    MemoryProbe.log("app.create.finish")
    return app


app = create_application()
