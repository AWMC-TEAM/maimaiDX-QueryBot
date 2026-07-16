"""插件真实功能请求的滚动窗口计数（按消息去重）。"""

from __future__ import annotations

import time
from collections import deque
from threading import RLock
from typing import Deque, Dict, Optional


class RollingRequestMeter:
    def __init__(self) -> None:
        self._timestamps: Deque[float] = deque()
        self._seen: Dict[str, float] = {}
        self._lock = RLock()

    def record(
        self,
        event_key: str,
        *,
        window_seconds: float = 60.0,
        now: Optional[float] = None,
    ) -> Optional[int]:
        """记录唯一消息并返回窗口内序号；重复 matcher 返回 ``None``。"""
        current = time.monotonic() if now is None else float(now)
        window = max(1.0, float(window_seconds))
        cutoff = current - window
        with self._lock:
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            expired = [key for key, seen_at in self._seen.items() if seen_at <= cutoff]
            for key in expired:
                self._seen.pop(key, None)
            if event_key in self._seen:
                return None
            self._seen[event_key] = current
            self._timestamps.append(current)
            return len(self._timestamps)


request_meter = RollingRequestMeter()
