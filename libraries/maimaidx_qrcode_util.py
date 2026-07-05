"""机台二维码 SGWCMAID 提取与日志脱敏。"""

from __future__ import annotations

import re
from typing import Optional

# 机台二维码：SGWCMAID 后接连续非空白字符（可嵌在「mai绑定 SGWCMAID…」等文本中）
SGWCMAID_PATTERN = re.compile(r'(SGWCMAID\S+)')

_QRCODE_QUICK_CHECK = 'SGWCMAID'


def message_may_contain_qrcode(text: str) -> bool:
    """快速预判，避免对每条群消息做正则。"""
    return _QRCODE_QUICK_CHECK in text


def extract_sgwcmaid_qrcode(text: str) -> Optional[str]:
    """从任意文本中提取首个 SGWCMAID 二维码字符串。"""
    if not text or _QRCODE_QUICK_CHECK not in text:
        return None
    match = SGWCMAID_PATTERN.search(text)
    return match.group(1) if match else None


def qrcode_log_preview(qrcode: str, *, head: int = 24, tail: int = 8) -> str:
    """日志用脱敏预览，避免完整泄露二维码。"""
    if len(qrcode) <= head + tail + 3:
        return f'{qrcode[:head]}…'
    return f'{qrcode[:head]}…{qrcode[-tail:]}'
