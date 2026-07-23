"""官方 QQ 机器人 openid 与水鱼查分 QQ 号绑定。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from ..config import log
from .maimaidx_sqlite import configure_sqlite_connection

DB_DIR = Path(__file__).parent.parent / 'data' / 'qq_bind'
DB_FILE = DB_DIR / 'qq_bind.db'


class QqBindDatabase:
    def __init__(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        configure_sqlite_connection(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS qq_bind (
                    platform_id   TEXT PRIMARY KEY,
                    legacy_qq     INTEGER NOT NULL,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qq_bind_legacy ON qq_bind(legacy_qq);
                '''
            )
            self._conn.commit()

    def bind(self, platform_id: str, legacy_qq: int) -> None:
        now = time.time()
        pid = str(platform_id).strip()
        with self._lock:
            self._conn.execute(
                '''
                INSERT INTO qq_bind (platform_id, legacy_qq, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(platform_id) DO UPDATE SET
                    legacy_qq = excluded.legacy_qq,
                    updated_at = excluded.updated_at
                ''',
                (pid, int(legacy_qq), now, now),
            )
            self._conn.commit()
        log.info(f'[QBind] platform={pid} -> qq={legacy_qq}')

    def unbind(self, platform_id: str) -> bool:
        pid = str(platform_id).strip()
        with self._lock:
            cur = self._conn.execute(
                'DELETE FROM qq_bind WHERE platform_id = ?', (pid,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_legacy_qq(self, platform_id: str) -> Optional[int]:
        pid = str(platform_id).strip()
        with self._lock:
            row = self._conn.execute(
                'SELECT legacy_qq FROM qq_bind WHERE platform_id = ?', (pid,)
            ).fetchone()
        return int(row['legacy_qq']) if row else None

    def get_platform_id(self, legacy_qq: int) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                'SELECT platform_id FROM qq_bind WHERE legacy_qq = ?', (int(legacy_qq),)
            ).fetchone()
        return str(row['platform_id']) if row else None


qq_bind_db = QqBindDatabase()
