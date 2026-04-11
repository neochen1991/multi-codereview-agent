from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemorySnapshot:
    rss_bytes: int
    rss_mb: float


class MemoryProbe:
    """轻量内存探针。

    仅在显式打开 REVIEW_MEMORY_PROBE 时记录，
    用于定位 review 执行链路中的瞬时内存高峰。
    """

    _lock = threading.Lock()
    _last_snapshot: MemorySnapshot | None = None
    _last_snapshot_ts: float = 0.0
    _last_error: str = ""
    _kernel32 = None
    _psapi = None
    _pmc_type = None

    @classmethod
    def enabled(cls) -> bool:
        flag = str(os.getenv("REVIEW_MEMORY_PROBE", "")).strip().lower()
        return flag in {"1", "true", "on", "yes"}

    @classmethod
    def snapshot(cls) -> MemorySnapshot | None:
        min_interval_ms = cls._snapshot_min_interval_ms()
        now = time.monotonic()
        with cls._lock:
            if (
                cls._last_snapshot is not None
                and min_interval_ms > 0
                and (now - cls._last_snapshot_ts) * 1000.0 < float(min_interval_ms)
            ):
                return cls._last_snapshot

            rss_bytes = cls._current_rss_bytes()
            cls._last_snapshot_ts = now
            if rss_bytes is None:
                cls._last_snapshot = None
                return None
            cls._last_snapshot = MemorySnapshot(
                rss_bytes=rss_bytes,
                rss_mb=round(rss_bytes / (1024 * 1024), 2),
            )
            return cls._last_snapshot

    @classmethod
    def log(cls, tag: str, **fields: object) -> None:
        if not cls.enabled():
            return
        snapshot = cls.snapshot()
        if snapshot is None:
            error = getattr(cls, "_last_error", "")
            if error:
                logger.info("memory probe tag=%s rss=unavailable error=%s fields=%s", tag, error, fields)
            else:
                logger.info("memory probe tag=%s rss=unavailable fields=%s", tag, fields)
            return
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
        if extras:
            logger.info("memory probe tag=%s rss_mb=%s rss_bytes=%s %s", tag, snapshot.rss_mb, snapshot.rss_bytes, extras)
        else:
            logger.info("memory probe tag=%s rss_mb=%s rss_bytes=%s", tag, snapshot.rss_mb, snapshot.rss_bytes)

    @classmethod
    def _current_rss_bytes(cls) -> int | None:
        cls._last_error = ""
        if sys.platform == "win32":
            return cls._current_rss_bytes_windows()
        if sys.platform.startswith("linux"):
            return cls._current_rss_bytes_linux()
        return None

    @classmethod
    def _current_rss_bytes_linux(cls) -> int | None:
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as handle:
                content = handle.read().strip().split()
            if len(content) < 2:
                cls._last_error = "linux_statm_missing_rss"
                return None
            rss_pages = int(content[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return rss_pages * page_size
        except Exception as exc:
            cls._last_error = f"linux_probe_failed:{exc.__class__.__name__}:{exc}"
            return None

    @classmethod
    def _current_rss_bytes_windows(cls) -> int | None:
        try:
            cls._ensure_windows_api()
            if cls._kernel32 is None or cls._psapi is None or cls._pmc_type is None:
                cls._last_error = "windows_api_init_failed"
                return None
            counters = cls._pmc_type()
            counters.cb = ctypes.sizeof(cls._pmc_type)
            process = cls._kernel32.GetCurrentProcess()
            if not process:
                cls._last_error = f"GetCurrentProcess_failed:last_error={ctypes.get_last_error()}"
                return None
            success = cls._psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            if not success:
                cls._last_error = f"GetProcessMemoryInfo_failed:last_error={ctypes.get_last_error()}"
                return None
            return int(counters.WorkingSetSize)
        except Exception as exc:
            cls._last_error = f"windows_probe_failed:{exc.__class__.__name__}:{exc}"
            return None

    @classmethod
    def _snapshot_min_interval_ms(cls) -> int:
        raw = str(os.getenv("REVIEW_MEMORY_PROBE_MIN_INTERVAL_MS", "")).strip()
        if raw:
            try:
                return max(0, min(10_000, int(raw)))
            except ValueError:
                return 500
        return 500 if sys.platform == "win32" else 200

    @classmethod
    def _ensure_windows_api(cls) -> None:
        if cls._kernel32 is not None and cls._psapi is not None and cls._pmc_type is not None:
            return

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        psapi = ctypes.WinDLL("Psapi.dll", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        cls._kernel32 = kernel32
        cls._psapi = psapi
        cls._pmc_type = PROCESS_MEMORY_COUNTERS
