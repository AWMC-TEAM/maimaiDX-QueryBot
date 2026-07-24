"""Persistent global announcements and per-user read receipts."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional

from .maimaidx_sqlite import configure_sqlite_connection


DB_DIR = Path(__file__).resolve().parent.parent / "data" / "announcement"
DB_PATH = DB_DIR / "announcement.db"


@dataclass(frozen=True)
class Announcement:
    id: int
    content: str
    required: bool
    revision: int
    is_current: bool
    created_at: float
    updated_at: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    required    INTEGER NOT NULL DEFAULT 0,
    revision    INTEGER NOT NULL DEFAULT 1,
    is_current  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_announcement_single_current
    ON announcements(is_current) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_announcement_recent
    ON announcements(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS announcement_receipts (
    announcement_id INTEGER NOT NULL,
    user_key         TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    seen_at          REAL NOT NULL,
    PRIMARY KEY (announcement_id, user_key, revision)
);
CREATE INDEX IF NOT EXISTS idx_announcement_receipt_user
    ON announcement_receipts(user_key, announcement_id, revision);
"""


class AnnouncementDatabase:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        configure_sqlite_connection(self._conn)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> Optional[Announcement]:
        if row is None:
            return None
        return Announcement(
            id=int(row["id"]),
            content=str(row["content"]),
            required=bool(row["required"]),
            revision=int(row["revision"]),
            is_current=bool(row["is_current"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def create(self, content: str, *, required: bool = False) -> Announcement:
        text = str(content).strip()
        if not text:
            raise ValueError("公告内容不能为空")
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE announcements SET is_current = 0 WHERE is_current = 1"
                )
                cursor = self._conn.execute(
                    """INSERT INTO announcements
                       (content, required, revision, is_current, created_at, updated_at)
                       VALUES (?, ?, 1, 1, ?, ?)""",
                    (text, int(required), now, now),
                )
                announcement_id = int(cursor.lastrowid)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        result = self.get(announcement_id)
        assert result is not None
        return result

    def get(self, announcement_id: int) -> Optional[Announcement]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM announcements WHERE id = ?",
                (int(announcement_id),),
            ).fetchone()
        return self._from_row(row)

    def current(self) -> Optional[Announcement]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM announcements WHERE is_current = 1 LIMIT 1"
            ).fetchone()
        return self._from_row(row)

    def recent(self, limit: int = 10) -> list[Announcement]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM announcements
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [item for row in rows if (item := self._from_row(row)) is not None]

    def update(
        self,
        announcement_id: int,
        *,
        content: Optional[str] = None,
        required: Optional[bool] = None,
    ) -> Optional[Announcement]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM announcements WHERE id = ?",
                (int(announcement_id),),
            ).fetchone()
            current = self._from_row(row)
            if current is None:
                return None
            text = current.content if content is None else str(content).strip()
            if not text:
                raise ValueError("公告内容不能为空")
            must_read = current.required if required is None else bool(required)
            if text == current.content and must_read == current.required:
                return current
            self._conn.execute(
                """UPDATE announcements
                   SET content = ?, required = ?, revision = revision + 1,
                       updated_at = ?
                   WHERE id = ?""",
                (text, int(must_read), time.time(), current.id),
            )
            self._conn.commit()
        return self.get(current.id)

    def delete(self, announcement_id: int) -> Optional[Announcement]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM announcements WHERE id = ?",
                (int(announcement_id),),
            ).fetchone()
            current = self._from_row(row)
            if current is None:
                return None
            self._conn.execute(
                "DELETE FROM announcement_receipts WHERE announcement_id = ?",
                (current.id,),
            )
            self._conn.execute(
                "DELETE FROM announcements WHERE id = ?", (current.id,)
            )
            self._conn.commit()
        return current

    def unseen_current(self, user_key: str) -> Optional[Announcement]:
        with self._lock:
            row = self._conn.execute(
                """SELECT a.* FROM announcements AS a
                   WHERE a.is_current = 1
                     AND NOT EXISTS (
                         SELECT 1 FROM announcement_receipts AS r
                         WHERE r.announcement_id = a.id
                           AND r.user_key = ?
                           AND r.revision = a.revision
                     )
                   LIMIT 1""",
                (str(user_key),),
            ).fetchone()
        return self._from_row(row)

    def mark_seen(
        self, user_key: str, announcement_id: int, revision: int
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO announcement_receipts
                       (announcement_id, user_key, revision, seen_at)
                   SELECT id, ?, revision, ? FROM announcements
                   WHERE id = ? AND revision = ?""",
                (
                    str(user_key),
                    time.time(),
                    int(announcement_id),
                    int(revision),
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def claim_optional_current(self, user_key: str) -> Optional[Announcement]:
        """Atomically claim an unseen optional current announcement."""
        with self._lock:
            current = self.unseen_current(user_key)
            if current is None or current.required:
                return None
            self.mark_seen(user_key, current.id, current.revision)
            return current

    def unmark_seen(
        self, user_key: str, announcement_id: int, revision: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                """DELETE FROM announcement_receipts
                   WHERE announcement_id = ? AND user_key = ? AND revision = ?""",
                (int(announcement_id), str(user_key), int(revision)),
            )
            self._conn.commit()


def format_announcement(
    announcement: Announcement,
    *,
    show_id: bool = False,
    include_current: bool = False,
) -> str:
    label = "必读" if announcement.required else "普通"
    current = " · 当前" if include_current and announcement.is_current else ""
    identifier = f" #{announcement.id}" if show_id else ""
    created = datetime.fromtimestamp(announcement.created_at).strftime(
        "%Y-%m-%d %H:%M"
    )
    header = f"【{label}公告{identifier}{current}】"
    lines = [header, f"发布时间：{created}"]
    if announcement.revision > 1:
        updated = datetime.fromtimestamp(announcement.updated_at).strftime(
            "%Y-%m-%d %H:%M"
        )
        lines.append(f"更新时间：{updated} · 版本 {announcement.revision}")
    lines.append(announcement.content)
    return "\n".join(lines)


announcement_db = AnnouncementDatabase()
