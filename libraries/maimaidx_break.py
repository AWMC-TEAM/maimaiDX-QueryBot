"""
AWMC BREAK 积分：签到、查分扣费、账号统计。

- SQLite 持久化：data/break/break.db
- 签到倍率加算叠加；查分仅在实际 API 请求时扣费
"""

from __future__ import annotations

import contextvars
import json
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import BOT_QQ_GROUP, log
from .maimaidx_error import BreakInsufficientError

DB_DIR = Path(__file__).resolve().parent.parent / 'data' / 'break'
DB_PATH = DB_DIR / 'break.db'

DEFAULT_CONFIG: Dict[str, str] = {
    'checkin_base_min': '1',
    'checkin_base_max': '5',
    'query_cost': '1',
    'analysis_cost': '3',
    'streak_bonus': '3,5,8,12,20',
    'bonus_group_1072033605': '0.5',
    'bonus_thursday': '1.0',
    'bonus_group_first': '1.0',
}

BONUS_GROUP_ID = int(BOT_QQ_GROUP)

_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS break_users (
    qqid                    INTEGER PRIMARY KEY,
    balance                 INTEGER NOT NULL DEFAULT 0,
    streak                  INTEGER NOT NULL DEFAULT 0,
    last_checkin_date       TEXT,
    total_query_count       INTEGER NOT NULL DEFAULT 0,
    total_analysis_count    INTEGER NOT NULL DEFAULT 0,
    last_query_at           REAL,
    last_analysis_at        REAL,
    created_at              REAL NOT NULL,
    updated_at              REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS break_daily_usage (
    qqid            INTEGER NOT NULL,
    date            TEXT NOT NULL,
    free_used       INTEGER NOT NULL DEFAULT 0,
    query_count     INTEGER NOT NULL DEFAULT 0,
    analysis_count  INTEGER NOT NULL DEFAULT 0,
    break_spent     INTEGER NOT NULL DEFAULT 0,
    break_gained    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (qqid, date)
);
CREATE TABLE IF NOT EXISTS break_group_checkin (
    group_id    INTEGER NOT NULL,
    date        TEXT NOT NULL,
    first_qqid  INTEGER NOT NULL,
    PRIMARY KEY (group_id, date)
);
CREATE TABLE IF NOT EXISTS break_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS break_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    qqid        INTEGER NOT NULL,
    delta       INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    meta        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_break_log_qqid ON break_log(qqid, created_at DESC);
"""


class BreakLogEntry(BaseModel):
    delta: int
    reason: str
    created_at: float
    meta: Optional[str] = None


class AccountProfile(BaseModel):
    qqid: int
    balance: int = 0
    streak: int = 0
    last_checkin_date: Optional[str] = None
    checked_in_today: bool = False
    today_query_count: int = 0
    today_analysis_count: int = 0
    today_break_spent: int = 0
    today_break_gained: int = 0
    free_used_today: bool = False
    total_query_count: int = 0
    total_analysis_count: int = 0
    last_query_at: Optional[float] = None
    last_analysis_at: Optional[float] = None
    data_source: str = 'divingfish'
    theme: str = 'default'
    storage_enabled: bool = False
    recent_logs: List[BreakLogEntry] = Field(default_factory=list)


@dataclass
class CheckinResult:
    qqid: int
    reward: int
    balance: int
    streak: int
    streak_bonus: int
    base: int
    multiplier_sum: float
    base_min: int = 1
    base_max: int = 5
    bonus_labels: List[str] = field(default_factory=list)
    already_checked: bool = False


def _parse_config_int(raw: str, default: int) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


class BreakDatabase:
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
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()
        self._seed_config()

    def _seed_config(self):
        for key, value in DEFAULT_CONFIG.items():
            self._conn.execute(
                'INSERT OR IGNORE INTO break_config (key, value) VALUES (?, ?)',
                (key, value),
            )
        self._conn.commit()

    def get_config(self, key: str, default: str = '') -> str:
        row = self._conn.execute(
            'SELECT value FROM break_config WHERE key = ?', (key,)
        ).fetchone()
        return row['value'] if row else default

    def set_config(self, key: str, value: str) -> None:
        self._conn.execute(
            'INSERT INTO break_config (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )
        self._conn.commit()

    def _ensure_user(self, qqid: int) -> None:
        now = time.time()
        self._conn.execute(
            """INSERT OR IGNORE INTO break_users
               (qqid, balance, streak, created_at, updated_at)
               VALUES (?, 0, 0, ?, ?)""",
            (qqid, now, now),
        )
        self._conn.commit()

    def _today(self) -> str:
        return date.today().isoformat()

    def _ensure_daily(self, qqid: int) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO break_daily_usage
               (qqid, date, free_used, query_count, analysis_count, break_spent, break_gained)
               VALUES (?, ?, 0, 0, 0, 0, 0)""",
            (qqid, self._today()),
        )

    def get_balance(self, qqid: int) -> int:
        self._ensure_user(qqid)
        row = self._conn.execute(
            'SELECT balance FROM break_users WHERE qqid = ?', (qqid,)
        ).fetchone()
        return int(row['balance']) if row else 0

    def _append_log(
        self,
        qqid: int,
        delta: int,
        reason: str,
        *,
        meta: Optional[dict] = None,
    ) -> None:
        self._conn.execute(
            'INSERT INTO break_log (qqid, delta, reason, meta, created_at) VALUES (?, ?, ?, ?, ?)',
            (qqid, delta, reason, json.dumps(meta, ensure_ascii=False) if meta else None, time.time()),
        )

    def is_daily_free_available(self, qqid: int) -> bool:
        self._ensure_user(qqid)
        self._ensure_daily(qqid)
        row = self._conn.execute(
            'SELECT free_used FROM break_daily_usage WHERE qqid = ? AND date = ?',
            (qqid, self._today()),
        ).fetchone()
        return not row or int(row['free_used']) == 0

    def mark_daily_free_used(self, qqid: int) -> None:
        self._ensure_user(qqid)
        self._ensure_daily(qqid)
        self._conn.execute(
            """UPDATE break_daily_usage SET free_used = 1
               WHERE qqid = ? AND date = ?""",
            (qqid, self._today()),
        )
        self._conn.commit()

    def record_usage(
        self,
        qqid: int,
        kind: str,
        *,
        break_delta: int = 0,
    ) -> None:
        """kind: query | analysis"""
        self._ensure_user(qqid)
        self._ensure_daily(qqid)
        now = time.time()
        if kind == 'query':
            self._conn.execute(
                """UPDATE break_users SET
                   total_query_count = total_query_count + 1,
                   last_query_at = ?,
                   updated_at = ?
                   WHERE qqid = ?""",
                (now, now, qqid),
            )
            self._conn.execute(
                """UPDATE break_daily_usage SET
                   query_count = query_count + 1,
                   break_spent = break_spent + ?
                   WHERE qqid = ? AND date = ?""",
                (max(0, -break_delta), qqid, self._today()),
            )
        elif kind == 'analysis':
            self._conn.execute(
                """UPDATE break_users SET
                   total_analysis_count = total_analysis_count + 1,
                   last_analysis_at = ?,
                   updated_at = ?
                   WHERE qqid = ?""",
                (now, now, qqid),
            )
            self._conn.execute(
                """UPDATE break_daily_usage SET
                   analysis_count = analysis_count + 1,
                   break_spent = break_spent + ?
                   WHERE qqid = ? AND date = ?""",
                (max(0, -break_delta), qqid, self._today()),
            )
        self._conn.commit()

    def try_consume(
        self,
        qqid: int,
        amount: int,
        reason: str,
        *,
        meta: Optional[dict] = None,
    ) -> bool:
        if amount <= 0:
            return True
        self._ensure_user(qqid)
        self._ensure_daily(qqid)
        with self._lock:
            row = self._conn.execute(
                'SELECT balance FROM break_users WHERE qqid = ?', (qqid,)
            ).fetchone()
            balance = int(row['balance']) if row else 0
            if balance < amount:
                return False
            now = time.time()
            self._conn.execute(
                'UPDATE break_users SET balance = balance - ?, updated_at = ? WHERE qqid = ?',
                (amount, now, qqid),
            )
            self._conn.execute(
                """UPDATE break_daily_usage SET break_spent = break_spent + ?
                   WHERE qqid = ? AND date = ?""",
                (amount, qqid, self._today()),
            )
            self._append_log(qqid, -amount, reason, meta=meta)
            self._conn.commit()
        return True

    def add_balance(
        self,
        qqid: int,
        delta: int,
        reason: str,
        *,
        meta: Optional[dict] = None,
    ) -> int:
        self._ensure_user(qqid)
        self._ensure_daily(qqid)
        now = time.time()
        self._conn.execute(
            'UPDATE break_users SET balance = balance + ?, updated_at = ? WHERE qqid = ?',
            (delta, now, qqid),
        )
        if delta > 0:
            self._conn.execute(
                """UPDATE break_daily_usage SET break_gained = break_gained + ?
                   WHERE qqid = ? AND date = ?""",
                (delta, qqid, self._today()),
            )
        self._append_log(qqid, delta, reason, meta=meta)
        self._conn.commit()
        return self.get_balance(qqid)

    def admin_set_balance(self, qqid: int, balance: int) -> int:
        self._ensure_user(qqid)
        row = self._conn.execute(
            'SELECT balance FROM break_users WHERE qqid = ?', (qqid,)
        ).fetchone()
        old = int(row['balance']) if row else 0
        delta = balance - old
        self._conn.execute(
            'UPDATE break_users SET balance = ?, updated_at = ? WHERE qqid = ?',
            (balance, time.time(), qqid),
        )
        self._append_log(qqid, delta, 'admin_set', meta={'old': old, 'new': balance})
        self._conn.commit()
        return balance

    def get_user_row(self, qqid: int) -> dict:
        self._ensure_user(qqid)
        row = self._conn.execute('SELECT * FROM break_users WHERE qqid = ?', (qqid,)).fetchone()
        return dict(row) if row else {}

    def get_daily_row(self, qqid: int) -> dict:
        self._ensure_daily(qqid)
        row = self._conn.execute(
            'SELECT * FROM break_daily_usage WHERE qqid = ? AND date = ?',
            (qqid, self._today()),
        ).fetchone()
        return dict(row) if row else {}

    def get_recent_logs(self, qqid: int, limit: int = 5) -> List[BreakLogEntry]:
        rows = self._conn.execute(
            'SELECT delta, reason, meta, created_at FROM break_log WHERE qqid = ? '
            'ORDER BY created_at DESC LIMIT ?',
            (qqid, limit),
        ).fetchall()
        return [
            BreakLogEntry(
                delta=int(r['delta']),
                reason=str(r['reason']),
                created_at=float(r['created_at']),
                meta=r['meta'],
            )
            for r in rows
        ]

    def is_checked_in_today(self, qqid: int) -> bool:
        row = self.get_user_row(qqid)
        return row.get('last_checkin_date') == self._today()

    def _streak_bonus(self, streak: int) -> int:
        raw = self.get_config('streak_bonus', DEFAULT_CONFIG['streak_bonus'])
        parts = [int(x.strip()) for x in raw.split(',') if x.strip().isdigit()]
        if not parts:
            return 0
        idx = min(max(streak - 1, 0), len(parts) - 1)
        return parts[idx]

    def _checkin_base_range(self) -> tuple[int, int]:
        lo = _parse_config_int(self.get_config('checkin_base_min', DEFAULT_CONFIG['checkin_base_min']), 1)
        hi = _parse_config_int(self.get_config('checkin_base_max', DEFAULT_CONFIG['checkin_base_max']), 5)
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def _roll_checkin_base(self) -> tuple[int, int, int]:
        lo, hi = self._checkin_base_range()
        if lo == hi:
            return lo, lo, hi
        return random.randint(lo, hi), lo, hi

    def _is_group_first_today(self, group_id: Optional[int]) -> bool:
        if not group_id:
            return False
        row = self._conn.execute(
            'SELECT 1 FROM break_group_checkin WHERE group_id = ? AND date = ?',
            (group_id, self._today()),
        ).fetchone()
        return row is None

    def checkin(self, qqid: int, group_id: Optional[int] = None) -> CheckinResult:
        self._ensure_user(qqid)
        today = self._today()
        user = self.get_user_row(qqid)
        if user.get('last_checkin_date') == today:
            return CheckinResult(
                qqid=qqid,
                reward=0,
                balance=int(user.get('balance', 0)),
                streak=int(user.get('streak', 0)),
                streak_bonus=0,
                base=0,
                multiplier_sum=0,
                already_checked=True,
            )

        base, base_min, base_max = self._roll_checkin_base()
        bonus_labels: List[str] = []
        multiplier_sum = 0.0

        if group_id == BONUS_GROUP_ID:
            bonus = float(self.get_config('bonus_group_1072033605', '0.5'))
            multiplier_sum += bonus
            bonus_labels.append(f'指定群 +{int(bonus * 100)}%')

        if date.today().weekday() == 3:
            bonus = float(self.get_config('bonus_thursday', '1.0'))
            multiplier_sum += bonus
            bonus_labels.append(f'周四 +{int(bonus * 100)}%')

        group_first = self._is_group_first_today(group_id)
        if group_first and group_id:
            bonus = float(self.get_config('bonus_group_first', '1.0'))
            multiplier_sum += bonus
            bonus_labels.append(f'群内首签 +{int(bonus * 100)}%')

        last = user.get('last_checkin_date')
        streak = int(user.get('streak', 0))
        if last:
            yesterday = (date.today().fromordinal(date.today().toordinal() - 1)).isoformat()
            streak = streak + 1 if last == yesterday else 1
        else:
            streak = 1

        streak_bonus = self._streak_bonus(streak)
        reward = int(round(base * (1 + multiplier_sum) + streak_bonus))

        now = time.time()
        self._conn.execute(
            """UPDATE break_users SET
               balance = balance + ?,
               streak = ?,
               last_checkin_date = ?,
               updated_at = ?
               WHERE qqid = ?""",
            (reward, streak, today, now, qqid),
        )
        self._ensure_daily(qqid)
        self._conn.execute(
            """UPDATE break_daily_usage SET break_gained = break_gained + ?
               WHERE qqid = ? AND date = ?""",
            (reward, qqid, today),
        )
        if group_first and group_id:
            self._conn.execute(
                'INSERT OR IGNORE INTO break_group_checkin (group_id, date, first_qqid) VALUES (?, ?, ?)',
                (group_id, today, qqid),
            )
        self._append_log(
            qqid,
            reward,
            'checkin',
            meta={
                'streak': streak,
                'labels': bonus_labels,
                'group_id': group_id,
                'base': base,
                'base_range': [base_min, base_max],
            },
        )
        self._conn.commit()

        return CheckinResult(
            qqid=qqid,
            reward=reward,
            balance=self.get_balance(qqid),
            streak=streak,
            streak_bonus=streak_bonus,
            base=base,
            multiplier_sum=multiplier_sum,
            base_min=base_min,
            base_max=base_max,
            bonus_labels=bonus_labels,
        )


break_db = BreakDatabase()


@dataclass
class _BreakChargeSession:
    spent: int = 0
    used_free: bool = False
    balance: int = 0


_billing_qqid: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    'break_billing_qqid', default=None
)
_charge_session: contextvars.ContextVar[Optional[_BreakChargeSession]] = contextvars.ContextVar(
    'break_charge_session', default=None
)
_pending_charge_footer: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    'break_pending_footer', default=None
)


def get_billing_qqid() -> Optional[int]:
    return _billing_qqid.get()


@asynccontextmanager
async def break_billing(qqid: Optional[int]):
    """指令级扣费上下文：查分器/落雪成绩 API 成功后会在此 qq 上结算 BREAK。"""
    payer = int(qqid) if qqid else None
    if payer and is_superuser_exempt(payer):
        payer = None
    t1 = _billing_qqid.set(payer)
    t2 = _charge_session.set(
        _BreakChargeSession(balance=break_db.get_balance(payer) if payer else 0)
    )
    try:
        yield
    finally:
        session = _charge_session.get()
        if session and (session.spent > 0 or session.used_free):
            if session.spent == 0:
                lines = [f'💳 今日首次查分免费 · 余额 {session.balance} BREAK']
            else:
                hint = '（含今日免费）' if session.used_free else ''
                lines = [f'💳 消耗 {session.spent} BREAK{hint} · 余额 {session.balance} BREAK']
            _pending_charge_footer.set(lines)
        _billing_qqid.reset(t1)
        _charge_session.reset(t2)


def take_break_charge_footer() -> List[str]:
    lines = _pending_charge_footer.get() or []
    _pending_charge_footer.set(None)
    return lines


def format_break_insufficient_message(
    qqid: Optional[int],
    required: int,
    current: int,
) -> str:
    checked = break_db.is_checked_in_today(qqid) if qqid else False
    lines = [f'❌ BREAK 不足（需要 {required}，当前 {current}）']
    if checked:
        lines.append('今日已签到，请明天再试。')
    else:
        lines.append('发送「AWMC签到」获取 BREAK；每日首次查分免费哦~')
    return '\n'.join(lines)


def _config_int(key: str, default: int) -> int:
    try:
        return int(float(break_db.get_config(key, str(default))))
    except (TypeError, ValueError):
        return default


def is_superuser_exempt(qqid: int) -> bool:
    try:
        from nonebot import get_driver
        return str(qqid) in get_driver().config.superusers
    except Exception:
        return False


def query_cost() -> int:
    return _config_int('query_cost', 1)


_ANALYSIS_PEAK_WINDOWS_UTC8 = (
    (9, 0, 12, 0),   # 09:00–12:00
    (14, 0, 18, 0),  # 14:00–18:00
)


def is_analysis_peak_hour() -> bool:
    """锐评峰时（UTC+8）：09:00–12:00、14:00–18:00，与 DeepSeek 峰谷策略对齐。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=8)))
    minutes = now.hour * 60 + now.minute
    for h1, m1, h2, m2 in _ANALYSIS_PEAK_WINDOWS_UTC8:
        start = h1 * 60 + m1
        end = h2 * 60 + m2
        if start <= minutes < end:
            return True
    return False


def analysis_base_cost() -> int:
    return _config_int('analysis_cost', 3)


def analysis_cost() -> int:
    base = analysis_base_cost()
    return base * 2 if is_analysis_peak_hour() else base


def format_analysis_cost_line(*, charged: Optional[int] = None, balance: Optional[int] = None) -> str:
    """锐评扣费说明（含峰时 ×2 标注）。"""
    cost = charged if charged is not None else analysis_cost()
    base = analysis_base_cost()
    peak = is_analysis_peak_hour() and cost > base
    if balance is None:
        if peak:
            return f'分析消耗 {cost} BREAK（峰时 ×2，基础 {base}）'
        return f'分析消耗 {cost} BREAK'
    if peak:
        return f'💳 分析消耗 {cost} BREAK（峰时 ×2，基础 {base}） · 余额 {balance} BREAK'
    return f'💳 分析消耗 {cost} BREAK · 余额 {balance} BREAK'


def format_analysis_pricing_help() -> str:
    base = analysis_base_cost()
    return (
        f'· 分析b50 / 锐评一下 — 每次成功消耗 {base} BREAK（峰时 09:00–12:00、14:00–18:00 双倍）\n'
    )


def ensure_query_affordable(qqid: Optional[int]) -> None:
    """查分器/落雪成绩 API 即将发起前：余额或免费额度检查。"""
    if not qqid or is_superuser_exempt(qqid):
        return
    cost = query_cost()
    balance = break_db.get_balance(qqid)
    if break_db.is_daily_free_available(qqid):
        return
    if balance < cost:
        raise BreakInsufficientError(cost, balance, qqid=qqid)


def ensure_analysis_affordable(qqid: int) -> None:
    if is_superuser_exempt(qqid):
        return
    cost = analysis_cost()
    balance = break_db.get_balance(qqid)
    if balance < cost:
        raise BreakInsufficientError(cost, balance, qqid=qqid)


def settle_prober_fetch(qqid: Optional[int]) -> None:
    """单次查分器/落雪成绩 API 成功后结算（免费额度或扣 BREAK）。"""
    if not qqid or is_superuser_exempt(qqid):
        return
    session = _charge_session.get()
    cost = query_cost()
    if break_db.is_daily_free_available(qqid):
        break_db.mark_daily_free_used(qqid)
        break_db.record_usage(qqid, 'query', break_delta=0)
        if session:
            session.used_free = True
            session.balance = break_db.get_balance(qqid)
        log.debug(f'[BREAK] qq={qqid} daily free query')
        return
    if not break_db.try_consume(qqid, cost, 'query', meta={'kind': 'prober_api'}):
        log.warning(f'[BREAK] qq={qqid} query consume failed after fetch')
        return
    break_db.record_usage(qqid, 'query', break_delta=-cost)
    if session:
        session.spent += cost
        session.balance = break_db.get_balance(qqid)


def settle_query_api_charge(qqid: Optional[int]) -> None:
    """兼容旧调用：在扣费上下文中等价于 settle_prober_fetch。"""
    if get_billing_qqid() is not None:
        settle_prober_fetch(qqid)
        return
    if not qqid or is_superuser_exempt(qqid):
        return
    from .maimaidx_player_cache import peek_fetch_meta

    meta = peek_fetch_meta()
    if meta is None or meta.origin != 'api':
        return
    cost = query_cost()
    if break_db.is_daily_free_available(qqid):
        break_db.mark_daily_free_used(qqid)
        break_db.record_usage(qqid, 'query', break_delta=0)
        return
    if not break_db.try_consume(qqid, cost, 'query', meta={'kind': 'prober_api'}):
        return
    break_db.record_usage(qqid, 'query', break_delta=-cost)


def settle_analysis_charge(qqid: int) -> None:
    if is_superuser_exempt(qqid):
        break_db.record_usage(qqid, 'analysis', break_delta=0)
        return
    cost = analysis_cost()
    if not break_db.try_consume(qqid, cost, 'b50_analysis', meta={'kind': 'llm'}):
        log.warning(f'[BREAK] qq={qqid} analysis consume failed')
        return
    break_db.record_usage(qqid, 'analysis', break_delta=-cost)


def get_account_profile(qqid: int) -> AccountProfile:
    from .maimaidx_data_storage import data_storage
    from .maimaidx_lxns_db import lxns_db

    user = break_db.get_user_row(qqid)
    daily = break_db.get_daily_row(qqid)
    today = break_db._today()
    return AccountProfile(
        qqid=qqid,
        balance=int(user.get('balance', 0)),
        streak=int(user.get('streak', 0)),
        last_checkin_date=user.get('last_checkin_date'),
        checked_in_today=user.get('last_checkin_date') == today,
        today_query_count=int(daily.get('query_count', 0)),
        today_analysis_count=int(daily.get('analysis_count', 0)),
        today_break_spent=int(daily.get('break_spent', 0)),
        today_break_gained=int(daily.get('break_gained', 0)),
        free_used_today=bool(int(daily.get('free_used', 0))),
        total_query_count=int(user.get('total_query_count', 0)),
        total_analysis_count=int(user.get('total_analysis_count', 0)),
        last_query_at=user.get('last_query_at'),
        last_analysis_at=user.get('last_analysis_at'),
        data_source=lxns_db.get_source(qqid),
        theme=lxns_db.get_theme(qqid),
        storage_enabled=data_storage.is_enabled(qqid),
        recent_logs=break_db.get_recent_logs(qqid, 5),
    )


def format_account_profile(profile: AccountProfile, *, title: str = '我的 AWMC 账号') -> str:
    def _ts(val: Optional[float]) -> str:
        if not val:
            return '暂无'
        return datetime.fromtimestamp(val).strftime('%m-%d %H:%M')

    src = '落雪' if profile.data_source == 'lxns' else '水鱼'
    storage = '已开启' if profile.storage_enabled else '未开启'
    checkin = '已完成' if profile.checked_in_today else '未签到'
    free = '已用' if profile.free_used_today else '可用'

    lines = [
        f'📋 {title}',
        '━━━━━━━━━━━━━━',
        f'🆔 QQ：{profile.qqid}',
        f'💳 BREAK 余额：{profile.balance}',
        f'📅 连续签到：{profile.streak} 天 · 上次签到：{profile.last_checkin_date or "暂无"}',
        f'🎁 今日签到：{checkin}',
        '',
        '📊 今日使用',
        f'  · 查分器 API：{profile.today_query_count} 次（消耗 {profile.today_break_spent} BREAK 合计含分析）',
        f'  · 分析 b50：{profile.today_analysis_count} 次',
        f'  · 今日 BREAK 获得：+{profile.today_break_gained}',
        f'  · 每日免费查分：{free}',
        '',
        '📈 累计统计',
        f'  · 查分 API 总计：{profile.total_query_count} 次',
        f'  · 分析 b50 总计：{profile.total_analysis_count} 次',
        f'  · 上次查分：{_ts(profile.last_query_at)}',
        f'  · 上次分析：{_ts(profile.last_analysis_at)}',
        '',
        '⚙️ 插件偏好',
        f'  · 查分数据源：{src}',
        f'  · B50 主题：{profile.theme}',
        f'  · 数据存储：{storage}',
    ]
    if profile.recent_logs:
        lines.append('')
        lines.append('📝 最近 BREAK 记录（最多 5 条）')
        reason_map = {
            'query': '查分',
            'checkin': '签到',
            'b50_analysis': '分析b50',
            'admin_set': '管理员设置',
            'admin_add': '管理员调整',
        }
        for entry in profile.recent_logs:
            ts = datetime.fromtimestamp(entry.created_at).strftime('%m-%d %H:%M')
            sign = '+' if entry.delta >= 0 else ''
            label = reason_map.get(entry.reason, entry.reason)
            lines.append(f'  · {ts}  {sign}{entry.delta}  {label}')
    return '\n'.join(lines)


def format_checkin_result(result: CheckinResult) -> str:
    if result.already_checked:
        return f'今天已经签到过啦~ 当前 BREAK：{result.balance}'
    bonus = ' · '.join(result.bonus_labels) if result.bonus_labels else '无额外加成'
    streak_extra = f'（+{result.streak_bonus} BREAK）' if result.streak_bonus else ''
    range_hint = (
        f'{result.base} BREAK（随机 {result.base_min}~{result.base_max}）'
        if result.base_min != result.base_max
        else f'{result.base} BREAK'
    )
    return (
        '✅ AWMC 签到成功！\n'
        '━━━━━━━━━━━━━━\n'
        f'📅 连续签到：{result.streak} 天{streak_extra}\n'
        f'🎲 随机基础：{range_hint}\n'
        f'✨ 今日加成：{bonus}\n'
        f'💰 获得：{result.reward} BREAK\n'
        f'💳 当前余额：{result.balance} BREAK'
    )
