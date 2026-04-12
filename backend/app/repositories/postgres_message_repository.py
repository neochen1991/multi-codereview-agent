from __future__ import annotations

import json
import os
from pathlib import Path

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.message import ConversationMessage


class PostgresMessageRepository:
    """Persist conversation messages in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig, storage_root: Path) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.messages"
        self._storage_root = Path(storage_root).resolve()

    def append(self, message: ConversationMessage) -> ConversationMessage:
        normalized = self._normalize_message_payload(message)
        self.append_many([normalized])
        return normalized

    def append_many(self, messages: list[ConversationMessage]) -> list[ConversationMessage]:
        normalized = [self._normalize_message_payload(item) for item in list(messages or []) if isinstance(item, ConversationMessage)]
        if not normalized:
            return []
        rows = []
        for message in normalized:
            payload = message.model_dump(mode="json")
            rows.append(
                (
                    message.message_id,
                    message.review_id,
                    message.issue_id,
                    message.expert_id,
                    message.message_type,
                    message.content,
                    json.dumps(payload["metadata"], ensure_ascii=False),
                    payload["created_at"],
                )
            )
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {self._table} (
                        message_id,
                        review_id,
                        issue_id,
                        expert_id,
                        message_type,
                        content,
                        metadata_json,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO UPDATE SET
                        review_id = EXCLUDED.review_id,
                        issue_id = EXCLUDED.issue_id,
                        expert_id = EXCLUDED.expert_id,
                        message_type = EXCLUDED.message_type,
                        content = EXCLUDED.content,
                        metadata_json = EXCLUDED.metadata_json,
                        created_at = EXCLUDED.created_at
                    """,
                    rows,
                )
            connection.commit()
        return normalized

    def list(self, review_id: str) -> list[ConversationMessage]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    WHERE review_id = %s
                    ORDER BY created_at ASC
                    """,
                    (review_id,),
                )
                rows = cursor.fetchall()
        return self._deserialize_rows(rows)

    def list_since(self, review_id: str, *, since: str, limit: int = 500) -> list[ConversationMessage]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    WHERE review_id = %s AND created_at >= %s
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (review_id, since, max(1, min(5000, int(limit or 500)))),
                )
                rows = cursor.fetchall()
        return self._deserialize_rows(rows)

    def list_by_issue(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return [item for item in self.list(review_id) if item.issue_id == issue_id]

    def summarize_llm_usage(self, review_id: str) -> dict[str, int]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT metadata_json
                    FROM {self._table}
                    WHERE review_id = %s
                    ORDER BY created_at ASC
                    """,
                    (review_id,),
                )
                rows = cursor.fetchall()
        seen_call_ids: set[str] = set()
        total_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except Exception:
                metadata = {}
            call_id = str(metadata.get("llm_call_id") or "").strip()
            mode = str(metadata.get("mode") or "").strip().lower()
            if not call_id or call_id in seen_call_ids or mode in {"", "pending", "template", "rule_only_light"}:
                continue
            seen_call_ids.add(call_id)
            total_calls += 1
            prompt_tokens += self._safe_int(metadata.get("prompt_tokens"))
            completion_tokens += self._safe_int(metadata.get("completion_tokens"))
            total_tokens += self._safe_int(metadata.get("total_tokens"))
        return {
            "total_calls": total_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
            connection.commit()

    def _deserialize_rows(self, rows: list[dict[str, object]]) -> list[ConversationMessage]:
        return [
            ConversationMessage.model_validate(
                {
                    "message_id": row["message_id"],
                    "review_id": row["review_id"],
                    "issue_id": row["issue_id"],
                    "expert_id": row["expert_id"],
                    "message_type": row["message_type"],
                    "content": row["content"],
                    "metadata": json.loads(str(row["metadata_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _normalize_message_payload(self, message: ConversationMessage) -> ConversationMessage:
        content = str(message.content or "")
        max_inline_chars = self._max_inline_message_chars()
        if len(content) <= max_inline_chars:
            return message
        offload_path = self._offload_message_content(
            review_id=message.review_id,
            message_id=message.message_id,
            content=content,
        )
        preview = content[:max_inline_chars].rstrip()
        compact_content = f"{preview}\n\n[内容过长已截断，完整内容已落盘: {offload_path.relative_to(self._storage_root)}]"
        metadata = dict(message.metadata or {})
        metadata["content_truncated"] = True
        metadata["original_content_chars"] = len(content)
        metadata["offloaded_content_path"] = str(offload_path.relative_to(self._storage_root))
        metadata["inline_content_chars"] = len(preview)
        return message.model_copy(update={"content": compact_content, "metadata": metadata})

    def _offload_message_content(self, *, review_id: str, message_id: str, content: str) -> Path:
        payload_dir = self._storage_root / "reviews" / review_id / "payloads" / "messages"
        payload_dir.mkdir(parents=True, exist_ok=True)
        payload_path = payload_dir / f"{message_id}.txt"
        payload_path.write_text(content, encoding="utf-8")
        return payload_path

    def _max_inline_message_chars(self) -> int:
        raw = os.getenv("MESSAGE_INLINE_MAX_CHARS", "").strip()
        if raw:
            try:
                return max(1000, min(200_000, int(raw)))
            except ValueError:
                return 12_000
        return 12_000
