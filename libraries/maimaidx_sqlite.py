"""SQLite 连接的低延迟默认值。"""

from __future__ import annotations

import sqlite3


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    """启用本地 Bot 数据库适用的并发与低 fsync 配置。"""
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
