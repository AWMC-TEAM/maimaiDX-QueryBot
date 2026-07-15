"""机台二维码 SGWCMAID 提取与日志脱敏。"""

from __future__ import annotations

import re
from typing import Optional

# 机台二维码：SGWCMAID 后接连续非空白字符（可嵌在「mai绑定 SGWCMAID…」等文本中）
SGWCMAID_PATTERN = re.compile(r'(SGWCMAID\S+)')

# 舞萌二维码页面的两种官方链接。路径里的 MAID… 与 SGWCMAID… 仅差 SGWC 前缀。
WAHLAP_QR_URL_PATTERN = re.compile(
    r"https?://wq\.wahlap\.net/qrcode/"
    r"(?:(?:img/(?P<img>MAID[A-Z0-9]{20,160})\.png)"
    r"|(?:req/(?P<req>MAID[A-Z0-9]{20,160})\.html))"
    r"(?=[?#\s]|$)",
    re.IGNORECASE,
)

# 直发监听只接管消息开头的二维码凭据或官方二维码链接。
DIRECT_QRCODE_PREFIX_PATTERN = (
    r"^\s*(?:SGWCMAID|https?://wq\.wahlap\.net/qrcode/(?:img|req)/MAID)"
)

_QRCODE_QUICK_CHECK = 'SGWCMAID'


def message_may_contain_qrcode(text: str) -> bool:
    """快速预判，避免对每条群消息做正则。"""
    return _QRCODE_QUICK_CHECK in text or bool(WAHLAP_QR_URL_PATTERN.search(text))


def extract_sgwcmaid_qrcode(text: str) -> Optional[str]:
    """提取 SGWCMAID，兼容官方 img/req 二维码链接。"""
    if not text:
        return None
    if _QRCODE_QUICK_CHECK in text:
        match = SGWCMAID_PATTERN.search(text)
        if match:
            return match.group(1)
    link_match = WAHLAP_QR_URL_PATTERN.search(text)
    if not link_match:
        return None
    maid = link_match.group("img") or link_match.group("req")
    return "SGWC" + maid.upper()


def qrcode_log_preview(qrcode: str, *, head: int = 24, tail: int = 8) -> str:
    """日志用脱敏预览，避免完整泄露二维码。"""
    if len(qrcode) <= head + tail + 3:
        return f'{qrcode[:head]}…'
    return f'{qrcode[:head]}…{qrcode[-tail:]}'
