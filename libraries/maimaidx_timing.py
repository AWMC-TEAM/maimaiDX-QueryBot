"""
成绩图生成耗时统计（异步安全，基于 contextvar）。

用法：
    在 finish 助手里用 reset() 重置，await 生成协程，再用 summary() 取统计。
    数据获取阶段在数据层（maiApi / 落雪 datasource）用 measure('fetch') 埋点。
    渲染时间由「总时间 - fetch」推算，无需逐个 draw 类埋点。
"""

import contextvars
import time
from typing import Optional

_timings: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    'mai_timings', default=None
)


def reset() -> None:
    """开始一次新的计时（在生成协程 await 之前调用）。"""
    _timings.set({'fetch': 0.0})


def record(phase: str, seconds: float) -> None:
    """累加某阶段耗时。未 reset 时静默忽略。"""
    d = _timings.get()
    if d is None:
        return
    d[phase] = d.get(phase, 0.0) + seconds


class measure:
    """上下文管理器：测量代码块耗时并累加到指定阶段。"""

    def __init__(self, phase: str):
        self.phase = phase
        self._t = 0.0

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        record(self.phase, time.perf_counter() - self._t)
        return False


def get_fetch() -> float:
    """取当前累计的数据获取耗时（秒）。"""
    d = _timings.get()
    return d.get('fetch', 0.0) if d else 0.0


def format_summary(total: float, fetch: float) -> str:
    """
    格式化耗时文案。render = total - fetch（钳到 >= 0）。
    """
    render = max(total - fetch, 0.0)
    return f'⏱️ 数据获取 {fetch:.2f}s · 图片渲染 {render:.2f}s · 共 {total:.2f}s'
