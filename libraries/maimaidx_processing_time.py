"""二维码与成绩上传耗时统计及动态预计时间。"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Optional


DB_DIR = Path(__file__).resolve().parent.parent / "data" / "timing"
DB_PATH = DB_DIR / "processing_time.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_time_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    operation   TEXT NOT NULL,
    duration    REAL NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processing_time_operation
    ON processing_time_samples(operation, id DESC);
"""


class ProcessingTimeEstimator:
    def __init__(self, path: Path = DB_PATH, *, sample_limit: int = 50) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._sample_limit = max(3, int(sample_limit))
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(self, operation: str, duration: float) -> None:
        value = float(duration)
        if not operation or value <= 0:
            return
        with self._lock:
            self._conn.execute(
                """INSERT INTO processing_time_samples
                   (operation, duration, created_at) VALUES (?, ?, ?)""",
                (str(operation), value, time.time()),
            )
            # 每种流程只保留近期样本，避免数据库无限增长。
            self._conn.execute(
                """DELETE FROM processing_time_samples
                   WHERE operation = ? AND id NOT IN (
                       SELECT id FROM processing_time_samples
                       WHERE operation = ? ORDER BY id DESC LIMIT ?
                   )""",
                (str(operation), str(operation), self._sample_limit),
            )
            self._conn.commit()

    def estimate(
        self, operation: str, *, fallback_seconds: float
    ) -> tuple[int, int]:
        """返回 ``(预计秒数, 历史样本数)``；无历史时使用流程回退值。"""
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS samples, AVG(duration) AS average
                   FROM processing_time_samples WHERE operation = ?""",
                (str(operation),),
            ).fetchone()
        samples = int(row["samples"] or 0) if row else 0
        average: Optional[float] = float(row["average"]) if row and row["average"] else None
        seconds = average if average is not None else max(1.0, float(fallback_seconds))
        return max(1, int(math.ceil(seconds))), samples


def auto_qrcode_workflow_key(*, pc: bool, fish: bool, lxns: bool) -> str:
    stages = ["pc" if pc else "verify"]
    if fish:
        stages.append("fish")
    if lxns:
        stages.append("lxns")
    return "auto_qrcode:" + "+".join(stages)


def auto_qrcode_fallback_seconds(*, pc: bool, fish: bool, lxns: bool) -> int:
    # 首次无历史样本时按各阶段的保守经验值估算；后续自动改用最近 50 次平均。
    seconds = 25 if pc else 12
    if fish:
        seconds += 20
    if lxns:
        seconds += 20
    if fish or lxns:
        seconds += 3
    if fish and lxns:
        seconds += 3
    return seconds


def upload_workflow_key(*, fish: bool, lxns: bool) -> str:
    if fish and lxns:
        channel = "all"
    elif fish:
        channel = "fish"
    elif lxns:
        channel = "lxns"
    else:
        channel = "none"
    return f"explicit_upload:{channel}"


def upload_fallback_seconds(*, fish: bool, lxns: bool) -> int:
    """无历史样本时，按上传渠道给出保守的首次预计时间。"""
    if fish and lxns:
        return 70
    if lxns:
        return 50
    if fish:
        return 40
    return 10


def format_processing_estimate(seconds: int, samples: int) -> str:
    if samples:
        return f"根据最近 {samples} 次同类处理的平均耗时，预计约 {seconds} 秒完成。"
    return f"暂无同类历史样本，首次预计约 {seconds} 秒完成。"


processing_time_estimator = ProcessingTimeEstimator()
