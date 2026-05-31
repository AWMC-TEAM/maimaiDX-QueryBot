"""
主题切换：管理 B50 等图片的背景主题。

beta 仓库的目录结构（pic_dir = static/mai/pic）：
  pic_dir/{theme}/             ← 主题专属图片
    b50.png                    ← b50 背景
    logo.png                   ← logo
    chart_info.png             ← 谱面信息
    play_info.png              ← 单曲游玩信息
    ra_dx.png                  ← rating 等级 dx
    UI_TTR_Rank_*.png          ← 所有评级图标（SSS+/SSSp/SS/S/A 等）
  pic_dir/                     ← 共享图片
    b50_score_*.png            ← 难度卡背景
    rise_score_*.png
    UI_MSS_*.png               ← FC/FS 图标
    UI_GAM_*.png               ← DX 星图标
    UI_CMN_TabTitle_*.png      ← 新曲标识
    SD.png / DX.png            ← 类型标识
    {version}.png              ← 版本图标
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
            '棱镜': cls.PRISM_PLUS, 'prism_plus': cls.PRISM_PLUS, 'prism+': cls.PRISM_PLUS, 'prism': cls.PRISM_PLUS,
            '圆环': cls.CIRCLE, 'circle': cls.CIRCLE, '环形': cls.CIRCLE,
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


# 主题子目录中的文件名映射（key=本地代码使用的文件名，value=主题子目录中的实际文件名）
_THEME_FILENAME_MAP = {
    'b50_bg.png': 'b50.png',
}


# 主题专属图片的精确文件名集合（用于快速判断）
_THEME_EXACT_FILES = {
    'b50.png',
    'b50_bg.png',
    'logo.png',
    'chart_info.png',
    'play_info.png',
    'ra_dx.png',
    'ra.png',
    'ra-dx.png',
    'song_bg.png',
    'info_bg.png',
}


# 主题专属图片的文件名前缀（用于动态匹配）
_THEME_PREFIXES = (
    'UI_TTR_Rank_',       # 评级图标
    'UI_CMN_DXRating_',   # rating 等级图
    'UI_CMN_DXRating_Star_',  # rating 星图
)


def get_theme_display_name(theme: str) -> str:
    """获取主题的中文显示名。"""
    return _THEME_NAMES.get(theme, theme)


def is_theme_specific(filename: str) -> bool:
    """判断某文件名是否为主题专属图片。"""
    if filename in _THEME_EXACT_FILES:
        return True
    return any(filename.startswith(p) for p in _THEME_PREFIXES)


def resolve_theme_path(maimaidir: Path, theme: str, filename: str) -> Path:
    """
    解析图片路径，自动判断是否走主题子目录。

    查找顺序：
      1. 若是主题专属图片：当前主题子目录 → 其他主题子目录 → 根目录
      2. 否则：直接根目录 → 当前主题子目录（兜底）

    Args:
        maimaidir: static/mai/pic 路径
        theme:    主题名（如 'prism_plus'）
        filename: 文件名（如 'UI_TTR_Rank_SSSp.png'）

    Returns:
        Path：找到则为存在的路径；找不到则返回最可能的路径让调用方报明确错误
    """
    if is_theme_specific(filename):
        mapped = _THEME_FILENAME_MAP.get(filename, filename)
        # 1. 当前主题子目录
        p = maimaidir / theme / mapped
        if p.exists():
            return p
        # 2. 其他主题子目录
        for t in Theme:
            if t.value != theme:
                q = maimaidir / t.value / mapped
                if q.exists():
                    return q
        # 3. 根目录原名
        r = maimaidir / filename
        if r.exists():
            return r
        return p

    # 非主题专属：根目录优先
    r = maimaidir / filename
    if r.exists():
        return r
    # 兜底：当前主题子目录
    p = maimaidir / theme / filename
    if p.exists():
        return p
    # 其他主题子目录
    for t in Theme:
        if t.value != theme:
            q = maimaidir / t.value / filename
            if q.exists():
                return q
    return r

    # 2. 其他主题子目录
    for t in Theme:
        if t.value != theme:
            q = maimaidir / t.value / mapped
            if q.exists():
                return q

    # 3. 根目录原名
    r = maimaidir / filename
    if r.exists():
        return r

    # 都找不到，返回当前主题路径让调用方报明确错误
    return p


def get_user_theme(qqid: int) -> str:
    """获取用户主题偏好（从 DB），默认 prism_plus。"""
    from .maimaidx_lxns_db import lxns_db
    t = lxns_db.get_theme(qqid)
    if t in (None, '', 'default'):
        return Theme.get_default().value
    return t


def set_user_theme(qqid: int, theme: str):
    """设置用户主题偏好（写入 DB）。"""
    from .maimaidx_lxns_db import lxns_db
    lxns_db.set_theme(qqid, theme)


def pic(filename: str, theme: str = None) -> Path:
    """
    全局便捷函数：解析图片路径，默认主题。
    用法：Image.open(pic('Name.png'))
    """
    from ..config import maimaidir
    if theme is None:
        theme = Theme.get_default().value
    return resolve_theme_path(maimaidir, theme, filename)
