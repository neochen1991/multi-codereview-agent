from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _RetryingSqliteConnection(sqlite3.Connection):
    """SQLite connection with retry-on-lock for write-heavy concurrent scenes."""

    _retry_attempts: int = 3
    _retry_base_sleep_seconds: float = 0.05
    _retry_max_sleep_seconds: float = 0.5

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        return self._run_with_lock_retry(lambda: super().execute(sql, parameters), op="execute")

    def executemany(self, sql: str, seq_of_parameters: Any, /) -> sqlite3.Cursor:
        return self._run_with_lock_retry(
            lambda: super().executemany(sql, seq_of_parameters),
            op="executemany",
        )

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        return self._run_with_lock_retry(lambda: super().executescript(sql_script), op="executescript")

    def commit(self) -> None:
        self._run_with_lock_retry(lambda: super().commit(), op="commit")

    def _run_with_lock_retry(self, fn, *, op: str):
        attempts = max(1, int(getattr(self, "_retry_attempts", 3) or 3))
        base_sleep = max(0.0, float(getattr(self, "_retry_base_sleep_seconds", 0.05) or 0.05))
        max_sleep = max(base_sleep, float(getattr(self, "_retry_max_sleep_seconds", 0.5) or 0.5))
        for index in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                lowered = str(exc).lower()
                retryable = "database is locked" in lowered or "database schema is locked" in lowered
                if not retryable or index >= attempts - 1:
                    raise
                sleep_seconds = min(max_sleep, base_sleep * (2 ** index))
                logger.warning(
                    "sqlite locked; retrying op=%s attempt=%s/%s sleep_seconds=%s error=%s",
                    op,
                    index + 1,
                    attempts,
                    round(sleep_seconds, 3),
                    exc,
                )
                time.sleep(sleep_seconds)


class SqliteDatabase:
    """Provide low-level SQLite bootstrap and connection helpers."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.schema_path = Path(__file__).with_name("schema.sql")

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row access by column name."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = self._connect_timeout_seconds()
        connection = sqlite3.connect(
            self.db_path,
            timeout=timeout_seconds,
            factory=_RetryingSqliteConnection,
        )
        connection.row_factory = sqlite3.Row
        # Attach runtime retry policy to connection instance.
        connection._retry_attempts = self._locked_retry_attempts()  # type: ignore[attr-defined]
        connection._retry_base_sleep_seconds = self._locked_retry_base_sleep_seconds()  # type: ignore[attr-defined]
        connection._retry_max_sleep_seconds = self._locked_retry_max_sleep_seconds()  # type: ignore[attr-defined]
        # `journal_mode` is a database-level setting; changing it on every connect
        # can introduce lock contention under concurrent writes (especially on Windows).
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms()}")
        return connection

    def initialize(self) -> None:
        """Create the database file and required tables if they do not exist."""

        schema_sql = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema_sql)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.commit()

    def list_tables(self) -> list[str]:
        """Return user tables currently available in the SQLite database."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def compact(self) -> None:
        """Try to reclaim SQLite disk space after manual cleanup operations."""

        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")

    def _connect_timeout_seconds(self) -> float:
        raw = os.getenv("SQLITE_CONNECT_TIMEOUT_SECONDS", "").strip()
        if raw:
            try:
                return max(1.0, min(60.0, float(raw)))
            except ValueError:
                return 15.0
        return 15.0

    def _busy_timeout_ms(self) -> int:
        raw = os.getenv("SQLITE_BUSY_TIMEOUT_MS", "").strip()
        if raw:
            try:
                return max(500, min(60_000, int(raw)))
            except ValueError:
                return 8_000
        # 默认缩短锁等待，避免高并发轮询时线程池被长时间阻塞。
        return 8_000

    def _locked_retry_attempts(self) -> int:
        raw = os.getenv("SQLITE_LOCK_RETRY_ATTEMPTS", "").strip()
        if raw:
            try:
                return max(1, min(20, int(raw)))
            except ValueError:
                return 6
        return 6

    def _locked_retry_base_sleep_seconds(self) -> float:
        raw = os.getenv("SQLITE_LOCK_RETRY_BASE_SLEEP_SECONDS", "").strip()
        if raw:
            try:
                return max(0.01, min(1.0, float(raw)))
            except ValueError:
                return 0.08
        return 0.08

    def _locked_retry_max_sleep_seconds(self) -> float:
        raw = os.getenv("SQLITE_LOCK_RETRY_MAX_SLEEP_SECONDS", "").strip()
        if raw:
            try:
                return max(0.05, min(2.0, float(raw)))
            except ValueError:
                return 0.8
        return 0.8
