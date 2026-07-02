"""平台适配：OneBot / 官方 QQ，查分 QQ 解析与消息形态。"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any, Optional, Union

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


def build_image_message(image: Union[bytes, BytesIO, str, Any]) -> Any:
    """按平台与配置构建图片消息。"""
    if isinstance(image, BytesIO):
        image = image.getvalue()
    if isinstance(image, bytes):
        b64 = 'base64://' + base64.b64encode(image).decode()
        return MessageSegment.image(b64)
    if isinstance(image, str) and image.startswith('base64://'):
        return MessageSegment.image(image)
    if isinstance(image, MessageSegment):
        return image
    return MessageSegment.image(image)


async def finish_with_image(matcher, image_msg, *, footer: str = '', reply: bool = True) -> None:
    """统一 finish：可选 QQ 卡片模式（当前为图片 + 文本，卡片预留）。"""
    if footer:
        payload = image_msg + MessageSegment.text(footer)
    else:
        payload = image_msg
    if use_qq_card_message():
        try:
            from nonebot.adapters.qq.message import MessageSegment as QQMsg  # type: ignore
            # 官方适配器暂无统一 Ark 封装，先走图片消息；后续可在此接 markdown/ark
            if isinstance(payload, Message):
                await matcher.finish(payload, reply_message=reply)
                return
        except ImportError:
            pass
    await matcher.finish(payload, reply_message=reply)
