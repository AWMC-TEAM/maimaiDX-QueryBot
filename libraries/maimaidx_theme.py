"""
主题切换：管理 B50 等图片的背景主题。

使用方式：
  - 用户通过「主题 xxx」指令切换主题
  - 主题偏好存储在 lxns_users 表的 theme 字段
  - DrawBest 通过 theme 参数选择背景图
  - 主题背景图位于 static 目录的 theme/ 子目录
"""

from enum import Enum
from pathlib import Path
from typing import Optional

from ..config import maiconfig


class Theme(str, Enum):
    """可用主题枚举。"""
    DEFAULT = 'default'
    DARK = 'dark'
    SAKURA = 'sakura'
    OCEAN = 'ocean'

    @classmethod
    def get_by_name(cls, name: str) -> Optional['Theme']:
        """通过中文名/英文名获取主题。"""
        _map = {
            '默认': cls.DEFAULT, 'default': cls.DEFAULT,
            '暗黑': cls.DARK, 'dark': cls.DARK,
            '樱花': cls.SAKURA, 'sakura': cls.SAKURA,
            '海洋': cls.OCEAN, 'ocean': cls.OCEAN,
        }
        return _map.get(name.lower())

    @classmethod
    def get_help(cls) -> str:
        """返回主题帮助文本。"""
        lines = ['可用主题：']
        for t in cls:
            lines.append(f'  {t.value} — {_THEME_NAMES.get(t.value, t.value)}')
        lines.append('用法：主题 <名称>')
        return '\n'.join(lines)


_THEME_NAMES = {
    'default': '默认',
    'dark': '暗黑',
    'sakura': '樱花',
    'ocean': '海洋',
}


def get_theme_display_name(theme: str) -> str:
    """获取主题的中文显示名。"""
    return _THEME_NAMES.get(theme, theme)


def resolve_theme_bg(theme: str) -> Path:
    """
    解析主题对应的 B50 背景图路径。
    优先使用主题专属背景，不存在则回退到默认背景。
    """
    from .maimaidx_best_50 import maimaidir  # 延迟导入避免循环

    theme_dir = maimaidir / 'theme'
    theme_bg = theme_dir / f'{theme}.png'
    if theme_bg.exists():
        return theme_bg
    return maimaidir / 'b50_bg.png'


def get_user_theme(qqid: int) -> str:
    """获取用户主题偏好（从 DB）。"""
    from .maimaidx_lxns_db import lxns_db
    return lxns_db.get_theme(qqid)


def set_user_theme(qqid: int, theme: str):
    """设置用户主题偏好（写入 DB）。"""
    from .maimaidx_lxns_db import lxns_db
    lxns_db.set_theme(qqid, theme)
