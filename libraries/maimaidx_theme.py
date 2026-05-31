"""
主题切换：管理 B50 等图片的背景主题。

使用方式：
  - 用户通过「主题 xxx」指令切换主题
  - 主题偏好存储在 lxns_users 表的 theme 字段
  - DrawBest 通过 theme 参数选择背景图
  - 主题专属图片位于 static/mai/pic/{theme}/ 子目录
  - 非主题图片（难度卡、aurora 等）从 static/mai/pic/ 直接加载
"""

from enum import Enum
from pathlib import Path
from typing import Optional


class Theme(str, Enum):
    """可用主题枚举（值即 static/mai/pic/ 下的子目录名）。"""
    PRISM_PLUS = 'prism_plus'
    CIRCLE = 'circle'

    @classmethod
    def get_default(cls) -> 'Theme':
        """默认主题。"""
        return cls.PRISM_PLUS

    @classmethod
    def get_by_name(cls, name: str) -> Optional['Theme']:
        """通过中文名/英文名获取主题。"""
        _map = {
            '棱镜': cls.PRISM_PLUS, 'prism_plus': cls.PRISM_PLUS, 'prism+': cls.PRISM_PLUS,
            '圆环': cls.CIRCLE, 'circle': cls.CIRCLE,
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
    'prism_plus': '棱镜',
    'circle': '圆环',
}


# 主题专属图片（标准名 → 主题子目录实际文件名见 _THEME_FILENAME_MAP）
THEME_SPECIFIC_IMAGES = [
    'title.png',
    'title-lengthen.png',
    'design.png',
    'b50_bg.png',
]


def get_theme_display_name(theme: str) -> str:
    """获取主题的中文显示名。"""
    return _THEME_NAMES.get(theme, theme)


# 主题子目录中的文件名映射（key=标准名，value=主题子目录中的实际文件名）
_THEME_FILENAME_MAP = {
    'b50_bg.png': 'b50.png',
}


def resolve_theme_path(maimaidir: Path, theme: str, filename: str) -> Path:
    """
    解析主题图片路径：
    1. 优先当前主题子目录（应用文件名映射）
    2. 不存在则遍历其他主题子目录
    3. 都没有则回退到根目录原名
    """
    mapped = _THEME_FILENAME_MAP.get(filename, filename)

    # 当前主题子目录
    theme_path = maimaidir / theme / mapped
    if theme_path.exists():
        return theme_path

    # 遍历其他主题子目录
    for t in Theme:
        if t.value != theme:
            candidate = maimaidir / t.value / mapped
            if candidate.exists():
                return candidate

    # 回退到根目录原名
    root_path = maimaidir / filename
    if root_path.exists():
        return root_path

    # 都找不到，返回主题路径让调用方报明确错误
    return theme_path


def get_user_theme(qqid: int) -> str:
    """获取用户主题偏好（从 DB）。"""
    from .maimaidx_lxns_db import lxns_db
    t = lxns_db.get_theme(qqid)
    return t if t != 'default' else Theme.get_default().value


def set_user_theme(qqid: int, theme: str):
    """设置用户主题偏好（写入 DB）。"""
    from .maimaidx_lxns_db import lxns_db
    lxns_db.set_theme(qqid, theme)
