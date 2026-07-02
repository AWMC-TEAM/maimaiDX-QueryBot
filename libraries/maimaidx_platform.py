"""平台适配：OneBot / 官方 QQ，查分 QQ 解析与消息形态。"""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from ..config import maiconfig
from .maimaidx_qq_bind import qq_bind_db


def get_platform() -> str:
    """onebot | qq_official"""
    raw = (getattr(maiconfig, 'maimaidx_platform', None) or 'onebot').strip().lower()
    if raw in ('qq', 'qq_official', 'official', 'qqbot'):
        return 'qq_official'
    return 'onebot'


def is_qq_official() -> bool:
    return get_platform() == 'qq_official'


def use_qq_card_message() -> bool:
    return bool(getattr(maiconfig, 'maimaidx_use_qq_card', False)) and is_qq_official()


def resolve_query_qqid(raw_id: Union[int, str], *, strict: bool = True) -> int:
    """
    查分水鱼/落雪用的 QQ 号。
    OneBot 下等于消息 user_id；官方 QQ 下读取 qbind 绑定的 legacy QQ。
    """
    if not is_qq_official():
        return int(raw_id)
    pid = str(raw_id).strip()
    bound = qq_bind_db.get_legacy_qq(pid)
    if bound is not None:
        return bound
    if strict:
        from .maimaidx_error import QBindRequiredError
        raise QBindRequiredError(pid)
    return int(raw_id) if str(raw_id).isdigit() else 0


def resolve_score_qqid(event, at_qq: Optional[int] = None) -> int:
    """成绩类指令：@ 他人时解析对方绑定 QQ，否则解析发送者。"""
    if at_qq is not None:
        return resolve_query_qqid(at_qq)
    return resolve_query_qqid(str(event.get_user_id()))


def platform_user_id(event) -> str:
    """Bot 内部功能（BREAK、猜歌积分等）始终用平台 user id。"""
    return str(event.get_user_id())


def billing_user_id(event) -> int:
    """BREAK 扣费主体：官方 QQ 优先用 qbind 的 legacy QQ，否则 openid 稳定哈希。"""
    if is_qq_official():
        pid = platform_user_id(event)
        bound = qq_bind_db.get_legacy_qq(pid)
        if bound is not None:
            return bound
        digest = hashlib.sha256(pid.encode()).hexdigest()[:15]
        return int(digest, 16)
    return int(event.get_user_id())


def _onebot_image_bytes(seg: MessageSegment) -> Optional[bytes]:
    if seg.type != 'image':
        return None
    raw = seg.data.get('file') or seg.data.get('url') or ''
    if not raw:
        return None
    s = str(raw)
    if s.startswith('base64://'):
        return base64.b64decode(s[9:])
    if s.startswith('file://'):
        return Path(s[7:]).read_bytes()
    return None


def _iter_onebot_segments(result: Any) -> Iterable[MessageSegment]:
    if isinstance(result, MessageSegment):
        yield result
    elif isinstance(result, Message):
        yield from result


def adapt_reply_payload(result: Any, *, footer: str = '') -> Any:
    """
    将插件内 OneBot 消息段转为当前平台可发送的形态。
    官方 QQ 需 file_image(bytes)，不能发 base64:// 的 OneBot 图。
    """
    if isinstance(result, str):
        if not is_qq_official():
            return result
        from nonebot.adapters.qq.message import Message as QQMessage
        from nonebot.adapters.qq.message import MessageSegment as QQSeg

        parts: List[Any] = []
        if result.strip():
            parts.append(QQSeg.text(result))
        if footer:
            parts.append(QQSeg.text(footer))
        return QQMessage(parts) if parts else QQMessage([QQSeg.text('（无内容）')])

    if not is_qq_official():
        if footer:
            return result + MessageSegment.text(footer)
        return result

    from nonebot.adapters.qq.message import Message as QQMessage
    from nonebot.adapters.qq.message import MessageSegment as QQSeg

    parts: List[Any] = []
    for seg in _iter_onebot_segments(result):
        if seg.type == 'image':
            data = _onebot_image_bytes(seg)
            if data:
                parts.append(QQSeg.file_image(data))
        elif seg.type == 'text':
            text = str(seg.data.get('text') or '')
            if text:
                parts.append(QQSeg.text(text))
    if footer:
        parts.append(QQSeg.text(footer))
    if not parts:
        return QQMessage([QQSeg.text('成绩图发送失败，请联系管理员。')])
    return QQMessage(parts)


def build_image_message(image: Union[bytes, BytesIO, str, Any]) -> Any:
    """按平台与配置构建图片消息。"""
    if isinstance(image, BytesIO):
        image = image.getvalue()
    if is_qq_official() and isinstance(image, bytes):
        from nonebot.adapters.qq.message import MessageSegment as QQSeg
        return QQSeg.file_image(image)
    if isinstance(image, bytes):
        b64 = 'base64://' + base64.b64encode(image).decode()
        return MessageSegment.image(b64)
    if isinstance(image, str) and image.startswith('base64://'):
        if is_qq_official():
            from nonebot.adapters.qq.message import MessageSegment as QQSeg
            return QQSeg.file_image(base64.b64decode(image[9:]))
        return MessageSegment.image(image)
    if isinstance(image, MessageSegment):
        return image
    return MessageSegment.image(image)


async def finish_reply(matcher, payload: Any, *, reply: bool = True) -> None:
    """统一 finish：官方 QQ 自动转换消息段。"""
    if isinstance(payload, str):
        await matcher.finish(adapt_reply_payload(payload), reply_message=reply)
        return
    await matcher.finish(payload if not is_qq_official() else adapt_reply_payload(payload), reply_message=reply)


async def finish_with_image(matcher, image_msg, *, footer: str = '', reply: bool = True) -> None:
    """统一 finish：可选 QQ 卡片模式（当前为图片 + 文本，卡片预留）。"""
    payload = adapt_reply_payload(image_msg, footer=footer)
    await matcher.finish(payload, reply_message=reply)
