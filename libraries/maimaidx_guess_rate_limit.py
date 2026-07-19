"""猜歌类玩法的答题限流：同一用户全局 3 秒一次。"""

from __future__ import annotations

import time
from typing import Dict, Optional

GUESS_ANSWER_COOLDOWN_SECONDS = 3.0

_last_answer_at: Dict[str, float] = {}


def format_guess_answer_rate_limit(remain: float) -> str:
    return f"嘿嘿，你的答案被我吃掉啦！({remain:.1f}秒后才能发送新的答案）"


def consume_guess_answer_slot(uid: str) -> Optional[str]:
    """尝试占用一次答题名额。

    冷却中返回提示文案；否则记录本次答题时间并返回 None。
    """
    key = str(uid or "").strip()
    if not key:
        return None
    now = time.time()
    last = _last_answer_at.get(key)
    if last is not None:
        remain = GUESS_ANSWER_COOLDOWN_SECONDS - (now - last)
        if remain > 0:
            return format_guess_answer_rate_limit(remain)
    _last_answer_at[key] = now
    if len(_last_answer_at) > 4096:
        cutoff = now - GUESS_ANSWER_COOLDOWN_SECONDS
        stale = [uid for uid, ts in _last_answer_at.items() if ts < cutoff]
        for uid in stale:
            _last_answer_at.pop(uid, None)
    return None
