"""
maimaidx 白名单模块

提供给本插件以及其他插件调用，用于限制功能的使用范围。
"""
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import List, Optional

from loguru import logger as log

WL_DIR = Path(__file__).parent.parent / "data" / "whitelist"
WL_FILE = WL_DIR / "whitelist.db"

VERIFY_KEYWORD = "/verify baka86.love"


class WhitelistDatabase:
    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        WL_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(WL_FILE), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS whitelist (
                qqid INTEGER PRIMARY KEY,
                added_at REAL NOT NULL,
                note TEXT DEFAULT ''
            );
        """)
        self._conn.commit()

    def add(self, qqid: int, note: str = "") -> bool:
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO whitelist (qqid, added_at, note) VALUES (?, ?, ?)",
                (qqid, time.time(), note),
            )
            self._conn.commit()
            return True
        except Exception as e:
            log.error(f"[Whitelist] 添加失败 {qqid}: {e}")
            return False

    def remove(self, qqid: int) -> bool:
        try:
            cur = self._conn.execute("DELETE FROM whitelist WHERE qqid = ?", (qqid,))
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            log.error(f"[Whitelist] 移除失败 {qqid}: {e}")
            return False

    def is_in_whitelist(self, qqid: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM whitelist WHERE qqid = ?", (qqid,)
        ).fetchone()
        return row is not None

    def get_all(self) -> List[int]:
        rows = self._conn.execute("SELECT qqid FROM whitelist").fetchall()
        return [r["qqid"] for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM whitelist").fetchone()
        return row["c"] or 0


whitelist_db = WhitelistDatabase()


def is_whitelisted(qqid: int) -> bool:
    """对外 API：判断 QQ 是否在白名单中。供其他插件调用。"""
    return whitelist_db.is_in_whitelist(qqid)


def add_to_whitelist(qqid: int, note: str = "") -> bool:
    """对外 API：添加 QQ 到白名单。供其他插件调用。"""
    return whitelist_db.add(qqid, note)


def remove_from_whitelist(qqid: int) -> bool:
    """对外 API：移除 QQ 出白名单。供其他插件调用。"""
    return whitelist_db.remove(qqid)


def get_whitelist() -> List[int]:
    """对外 API：获取所有白名单 QQ 列表。供其他插件调用。"""
    return whitelist_db.get_all()
