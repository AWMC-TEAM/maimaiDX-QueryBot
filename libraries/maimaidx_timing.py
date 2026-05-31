"""
成绩图生成耗时统计（异步安全，基于 contextvar）。

用法：
    在 finish 助手里用 reset() 重置，await 生成协程，再用 summary() 取统计。
    数据获取阶段在数据层（maiApi / 落雪 datasource）用 measure('fetch') 埋点。
    渲染时间由「总时间 - fetch」推算，无需逐个 draw 类埋点。

    finish_timed(matcher, coro) — 异步生成并追加 ⏱️ 耗时 footer
    finish_timed_sync(matcher, fn) — 同步生成（如本地 PIL 绘制）
"""

import contextvars
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar, Union

T = TypeVar('T')
ImageResult = Union[str, Any]  # MessageSegment / Message / str

_timings: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    'mai_timings', default=None
)


def reset() -> None:
    """开始一次新的计时（在生成协程 await 之前调用）。"""
    _timings.set({'fetch': 0.0})
    try:
        from .maimaidx_b50_warnings import clear_b50_warnings
        clear_b50_warnings()
    except ImportError:
        pass


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


def timing_text(total: float) -> str:
    """当前 context 下的耗时一行文案。"""
    return format_summary(total, get_fetch())


async def run_timed(coro: Awaitable[T]) -> tuple[T, float]:
    """reset 后执行协程并返回 (结果, 总秒数)。"""
    reset()
    t0 = time.perf_counter()
    result = await coro
    return result, time.perf_counter() - t0


def run_timed_call(fn: Callable[..., T], /, *args, **kwargs) -> tuple[T, float]:
    """reset 后执行同步函数并返回 (结果, 总秒数)。"""
    reset()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - t0


def attach_timing(result: ImageResult, total: float, *, extra: str = '') -> ImageResult:
    """给图片消息追加耗时 footer；字符串（错误提示）原样返回。"""
    if isinstance(result, str):
        return result
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    footer = timing_text(total)
    text = f'\n{extra}\n{footer}' if extra else f'\n{footer}'
    if isinstance(result, Message):
        return result + Message(text)
    if isinstance(result, MessageSegment):
        return result + MessageSegment.text(text)
    return result + MessageSegment.text(text)


async def finish_timed(
    matcher,
    coro: Awaitable[ImageResult],
    *,
    extra: str = '',
    reply_message: bool = True,
) -> None:
    """计时执行生成协程，成功时追加 ⏱️ 耗时后 finish。"""
    result, total = await run_timed(coro)
    if isinstance(result, str):
        await matcher.finish(result, reply_message=reply_message)
        return
    await matcher.finish(
        attach_timing(result, total, extra=extra),
        reply_message=reply_message,
    )


async def finish_timed_sync(
    matcher,
    fn: Callable[..., ImageResult],
    /,
    *args,
    extra: str = '',
    reply_message: bool = True,
    **kwargs,
) -> None:
    """计时执行同步生成函数，成功时追加 ⏱️ 耗时后 finish。"""
    result, total = run_timed_call(fn, *args, **kwargs)
    if isinstance(result, str):
        await matcher.finish(result, reply_message=reply_message)
        return
    await matcher.finish(
        attach_timing(result, total, extra=extra),
        reply_message=reply_message,
    )
