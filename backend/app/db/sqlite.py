from __future__ import annotations

import sqlite3
import os
from pathlib import Path


class SqliteDatabase:
    """Provide low-level SQLite bootstrap and connection helpers."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.schema_path = Path(__file__).with_name("schema.sql")

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row access by column name."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = self._connect_timeout_seconds()
        connection = sqlite3.connect(self.db_path, timeout=timeout_seconds)
        connection.row_factory = sqlite3.Row
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
