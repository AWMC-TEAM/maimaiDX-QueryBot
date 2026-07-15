"""合并自 maibot 的舞萌账号绑定存储。

QueryBot 只保存调用 AWMC/sw-api 所需的最小状态：二维码、街机 UID、
水鱼 Token、落雪导入 Token及最近一次账号预览。BREAK 仍由
``maimaidx_break`` 独立管理。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Optional


DB_DIR = Path(__file__).resolve().parent.parent / "data" / "account"
DB_PATH = DB_DIR / "account.db"


@dataclass
class AccountBinding:
    user_key: str
    mai_uid: str = ""
    qrcode: str = ""
    user_name: str = ""
    rating: int = 0
    fish_token: str = ""
    lxns_token: str = ""
    bound_at: float = 0.0
    updated_at: float = 0.0
    last_upload_at: Optional[float] = None

    @property
    def is_bound(self) -> bool:
        return bool(self.qrcode)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_bindings (
    user_key       TEXT PRIMARY KEY,
    mai_uid        TEXT NOT NULL DEFAULT '',
    qrcode         TEXT NOT NULL DEFAULT '',
    user_name      TEXT NOT NULL DEFAULT '',
    rating         INTEGER NOT NULL DEFAULT 0,
    fish_token     TEXT NOT NULL DEFAULT '',
    lxns_token     TEXT NOT NULL DEFAULT '',
    bound_at       REAL NOT NULL,
    updated_at     REAL NOT NULL,
    last_upload_at REAL
);
CREATE INDEX IF NOT EXISTS idx_account_mai_uid ON account_bindings(mai_uid);

CREATE TABLE IF NOT EXISTS account_operation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id      TEXT NOT NULL UNIQUE,
    user_key    TEXT NOT NULL,
    operation   TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_log_user
    ON account_operation_log(user_key, created_at DESC);
"""


class AccountDatabase:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> Optional[AccountBinding]:
        if row is None:
            return None
        return AccountBinding(**dict(row))

    def get(self, user_key: str) -> Optional[AccountBinding]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM account_bindings WHERE user_key = ?", (str(user_key),)
            ).fetchone()
        return self._from_row(row)

    def bind(
        self,
        user_key: str,
        qrcode: str,
        *,
        mai_uid: str = "",
        user_name: str = "",
        rating: int = 0,
    ) -> AccountBinding:
        now = time.time()
        key = str(user_key)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO account_bindings
                    (user_key, mai_uid, qrcode, user_name, rating, bound_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_key) DO UPDATE SET
                    mai_uid = excluded.mai_uid,
                    qrcode = excluded.qrcode,
                    user_name = excluded.user_name,
                    rating = excluded.rating,
                    bound_at = excluded.bound_at,
                    updated_at = excluded.updated_at
                """,
                (key, str(mai_uid), qrcode, user_name, int(rating or 0), now, now),
            )
            self._conn.commit()
        return self.get(key)  # type: ignore[return-value]

    def bind_verified(
        self,
        user_key: str,
        qrcode: str,
        *,
        mai_uid: str,
        user_name: str = "",
        rating: int = 0,
    ) -> tuple[AccountBinding, list[str]]:
        """绑定已验真的街机账号，并认领同一 ``mai_uid`` 的旧记录。

        能读出账号预览的最新 SGWCMAID 被视为本次认领凭证。认领时旧记录
        不再保留二维码，并把其中已有、而当前记录尚未设置的上传 Token 一并
        转给当前用户，避免 Koishi/旧 Bot 迁移后要求用户重新配置。
        """
        now = time.time()
        key = str(user_key)
        uid = str(mai_uid)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                current = self._conn.execute(
                    "SELECT * FROM account_bindings WHERE user_key = ?", (key,)
                ).fetchone()
                previous = self._conn.execute(
                    """SELECT * FROM account_bindings
                       WHERE mai_uid = ? AND mai_uid != '' AND user_key != ?
                       ORDER BY updated_at DESC""",
                    (uid, key),
                ).fetchall()

                fish_token = str(current["fish_token"] or "") if current else ""
                lxns_token = str(current["lxns_token"] or "") if current else ""
                last_upload_at = current["last_upload_at"] if current else None
                for row in previous:
                    fish_token = fish_token or str(row["fish_token"] or "")
                    lxns_token = lxns_token or str(row["lxns_token"] or "")
                    candidate = row["last_upload_at"]
                    if candidate is not None and (
                        last_upload_at is None or candidate > last_upload_at
                    ):
                        last_upload_at = candidate

                claimed_keys = [str(row["user_key"]) for row in previous]
                if claimed_keys:
                    placeholders = ",".join("?" for _ in claimed_keys)
                    self._conn.execute(
                        f"DELETE FROM account_bindings WHERE user_key IN ({placeholders})",
                        claimed_keys,
                    )

                self._conn.execute(
                    """
                    INSERT INTO account_bindings
                        (user_key, mai_uid, qrcode, user_name, rating, fish_token,
                         lxns_token, bound_at, updated_at, last_upload_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_key) DO UPDATE SET
                        mai_uid = excluded.mai_uid,
                        qrcode = excluded.qrcode,
                        user_name = excluded.user_name,
                        rating = excluded.rating,
                        fish_token = excluded.fish_token,
                        lxns_token = excluded.lxns_token,
                        bound_at = excluded.bound_at,
                        updated_at = excluded.updated_at,
                        last_upload_at = excluded.last_upload_at
                    """,
                    (
                        key, uid, qrcode, user_name, int(rating or 0), fish_token,
                        lxns_token, now, now, last_upload_at,
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        binding = self.get(key)
        if binding is None:  # pragma: no cover - transaction guarantees this row
            raise RuntimeError("verified account binding was not persisted")
        return binding, claimed_keys

    def refresh_preview(
        self, user_key: str, *, mai_uid: str, user_name: str, rating: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE account_bindings
                   SET mai_uid = ?, user_name = ?, rating = ?, updated_at = ?
                   WHERE user_key = ?""",
                (str(mai_uid), user_name, int(rating or 0), time.time(), str(user_key)),
            )
            self._conn.commit()

    def unbind_account(self, user_key: str) -> bool:
        """仅清除街机账号，保留水鱼/落雪 Token，方便重新绑定。"""
        with self._lock:
            cur = self._conn.execute(
                """UPDATE account_bindings
                   SET mai_uid = '', qrcode = '', user_name = '', rating = 0,
                       updated_at = ? WHERE user_key = ? AND qrcode != ''""",
                (time.time(), str(user_key)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_token(self, user_key: str, kind: str, token: str) -> None:
        column = {"fish": "fish_token", "lxns": "lxns_token"}.get(kind)
        if not column:
            raise ValueError(f"unknown token kind: {kind}")
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO account_bindings
                   (user_key, bound_at, updated_at) VALUES (?, ?, ?)""",
                (str(user_key), now, now),
            )
            self._conn.execute(
                f"UPDATE account_bindings SET {column} = ?, updated_at = ? WHERE user_key = ?",
                (token.strip(), now, str(user_key)),
            )
            self._conn.commit()

    def mark_uploaded(self, user_key: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE account_bindings SET last_upload_at = ?, updated_at = ? WHERE user_key = ?",
                (now, now, str(user_key)),
            )
            self._conn.commit()

    def list_accounts(
        self, *, limit: int = 100, offset: int = 0, search: str = ""
    ) -> list[AccountBinding]:
        clauses, params = [], []
        if search:
            clauses.append("(user_key LIKE ? OR user_name LIKE ? OR mai_uid LIKE ?)")
            q = f"%{search}%"
            params.extend([q, q, q])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM account_bindings" + where
                + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows if row is not None]  # type: ignore[misc]

    def count_accounts(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM account_bindings"
            ).fetchone()
        return int(row["c"]) if row else 0

    def append_log(
        self, ref_id: str, user_key: str, operation: str, status: str, detail: str = ""
    ) -> bool:
        """写入幂等操作日志；ref_id 已存在时返回 False。"""
        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO account_operation_log
                       (ref_id, user_key, operation, status, detail, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ref_id, str(user_key), operation, status, detail, time.time()),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False


account_db = AccountDatabase()
