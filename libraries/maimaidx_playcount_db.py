import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from loguru import logger as log

DB_DIR = Path(__file__).parent.parent / "data" / "playcount"
DB_FILE = DB_DIR / "playcount.db"


@dataclass
class PlayCountRecord:
    song_id: int
    title: str
    level: str
    level_index: int
    play_count: int
    achievements: float
    rate: str
    dx_score: int
    dx_rating: float
    fc: str
    fs: str
    updated_at: float


@dataclass
class ArcadeCredential:
    qqid: int
    credential_type: str
    credential_data: str
    created_at: float
    expires_at: float


class PlayCountDatabase:
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
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_credentials (
                qqid INTEGER PRIMARY KEY,
                credential_type TEXT NOT NULL,
                credential_data TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS play_count_records (
                qqid INTEGER NOT NULL,
                song_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                level TEXT NOT NULL,
                level_index INTEGER NOT NULL,
                play_count INTEGER NOT NULL DEFAULT 0,
                achievements REAL NOT NULL DEFAULT 0,
                rate TEXT NOT NULL DEFAULT '',
                dx_score INTEGER NOT NULL DEFAULT 0,
                dx_rating REAL NOT NULL DEFAULT 0,
                fc TEXT NOT NULL DEFAULT '',
                fs TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (qqid, song_id, level_index)
            );

            CREATE INDEX IF NOT EXISTS idx_pc_qqid ON play_count_records(qqid);
            CREATE INDEX IF NOT EXISTS idx_pc_updated ON play_count_records(updated_at);

            CREATE TABLE IF NOT EXISTS user_prober_tokens (
                qqid INTEGER PRIMARY KEY,
                fish_token TEXT,
                lxns_code TEXT,
                updated_at REAL NOT NULL
            );
        """)
        self._conn.commit()

    def save_credential(self, cred: ArcadeCredential):
        self._conn.execute(
            "INSERT OR REPLACE INTO user_credentials (qqid, credential_type, credential_data, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (cred.qqid, cred.credential_type, cred.credential_data, cred.created_at, cred.expires_at),
        )
        self._conn.commit()

    def get_credential(self, qqid: int) -> Optional[ArcadeCredential]:
        row = self._conn.execute(
            "SELECT * FROM user_credentials WHERE qqid = ?",
            (qqid,),
        ).fetchone()
        if row is None:
            return None
        return ArcadeCredential(
            qqid=row["qqid"],
            credential_type=row["credential_type"],
            credential_data=row["credential_data"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def delete_credential(self, qqid: int):
        self._conn.execute("DELETE FROM user_credentials WHERE qqid = ?", (qqid,))
        self._conn.commit()

    def is_credential_valid(self, qqid: int) -> bool:
        row = self._conn.execute(
            "SELECT expires_at FROM user_credentials WHERE qqid = ?",
            (qqid,),
        ).fetchone()
        if row is None:
            return False
        return row["expires_at"] > time.time()

    def save_play_count_records(self, qqid: int, records: List[PlayCountRecord]):
        data = [
            (
                qqid,
                r.song_id,
                r.title,
                r.level,
                r.level_index,
                r.play_count,
                r.achievements,
                r.rate,
                r.dx_score,
                r.dx_rating,
                r.fc,
                r.fs,
                r.updated_at,
            )
            for r in records
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO play_count_records (qqid, song_id, title, level, level_index, play_count, achievements, rate, dx_score, dx_rating, fc, fs, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            data,
        )
        self._conn.commit()
        log.info(f"[PlayCountDB] 用户 {qqid} 保存了 {len(records)} 条 PC 记录")

    def get_user_play_counts(self, qqid: int) -> List[PlayCountRecord]:
        rows = self._conn.execute(
            "SELECT * FROM play_count_records WHERE qqid = ? ORDER BY play_count DESC",
            (qqid,),
        ).fetchall()
        return [
            PlayCountRecord(
                song_id=r["song_id"],
                title=r["title"],
                level=r["level"],
                level_index=r["level_index"],
                play_count=r["play_count"],
                achievements=r["achievements"],
                rate=r["rate"],
                dx_score=r["dx_score"],
                dx_rating=r["dx_rating"],
                fc=r["fc"],
                fs=r["fs"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def get_song_play_counts_all_users(self, song_id: int, level_index: int) -> List[PlayCountRecord]:
        rows = self._conn.execute(
            "SELECT * FROM play_count_records WHERE song_id = ? AND level_index = ? ORDER BY play_count DESC",
            (song_id, level_index),
        ).fetchall()
        return [
            PlayCountRecord(
                song_id=r["song_id"],
                title=r["title"],
                level=r["level"],
                level_index=r["level_index"],
                play_count=r["play_count"],
                achievements=r["achievements"],
                rate=r["rate"],
                dx_score=r["dx_score"],
                dx_rating=r["dx_rating"],
                fc=r["fc"],
                fs=r["fs"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def get_all_users_with_data(self) -> List[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT qqid FROM play_count_records"
        ).fetchall()
        return [r["qqid"] for r in rows]

    def get_user_total_plays(self, qqid: int) -> int:
        row = self._conn.execute(
            "SELECT SUM(play_count) as total FROM play_count_records WHERE qqid = ?",
            (qqid,),
        ).fetchone()
        return row["total"] or 0

    def save_prober_token(self, qqid: int, fish_token: Optional[str] = None, lxns_code: Optional[str] = None):
        """保存用户查分器 token"""
        existing = self._conn.execute(
            "SELECT fish_token, lxns_code FROM user_prober_tokens WHERE qqid = ?",
            (qqid,),
        ).fetchone()

        if existing:
            new_fish = fish_token if fish_token is not None else existing["fish_token"]
            new_lxns = lxns_code if lxns_code is not None else existing["lxns_code"]
        else:
            new_fish = fish_token
            new_lxns = lxns_code

        self._conn.execute(
            "INSERT OR REPLACE INTO user_prober_tokens (qqid, fish_token, lxns_code, updated_at) VALUES (?, ?, ?, ?)",
            (qqid, new_fish, new_lxns, time.time()),
        )
        self._conn.commit()

    def get_prober_token(self, qqid: int) -> tuple[Optional[str], Optional[str]]:
        """获取用户查分器 token，返回 (fish_token, lxns_code)"""
        row = self._conn.execute(
            "SELECT fish_token, lxns_code FROM user_prober_tokens WHERE qqid = ?",
            (qqid,),
        ).fetchone()
        if row is None:
            return None, None
        return row["fish_token"], row["lxns_code"]

    def close(self):
        self._conn.close()


pc_db = PlayCountDatabase()