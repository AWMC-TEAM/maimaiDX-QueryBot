"""
成绩格式化器：统一封装成绩展示格式。

提供：
- FC/FS 状态图标映射
- 成绩行格式化
- 排名差距计算
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# FC 状态图标映射
FC_ICONS: Dict[str, str] = {
    "fc": "⭐FC",
    "fcp": "⭕FC+",
    "ap": "🌟AP",
    "app": "🔥AP+",
}

# FS 状态图标映射
FS_ICONS: Dict[str, str] = {
    "fs": "📶FS",
    "fsp": "📶FS+",
    "fdx": "🌈FDX",
    "fdxp": "🌈FDX+",
}

# 难度名称映射
DIFFICULTY_NAMES = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]


def get_fc_icon(fc: Optional[str]) -> str:
    """获取 FC 状态图标。"""
    return FC_ICONS.get(fc or "", "")


def get_fs_icon(fs: Optional[str]) -> str:
    """获取 FS 状态图标。"""
    return FS_ICONS.get(fs or "", "")


def get_difficulty_name(level_index: int) -> str:
    """获取难度名称。"""
    if 0 <= level_index < len(DIFFICULTY_NAMES):
        return DIFFICULTY_NAMES[level_index]
    return "Master"


def format_score_line(
    rank: int,
    name: str,
    achievements: float,
    fc: Optional[str] = None,
    fs: Optional[str] = None,
    is_self: bool = False,
) -> str:
    """
    格式化单条成绩行。

    Args:
        rank: 排名
        name: 显示名称
        achievements: 达成率
        fc: FC 状态
        fs: FS 状态
        is_self: 是否当前用户

    Returns:
        格式化后的字符串，如 "▶1. 你 100.5000% 🌟AP 🌈FDX+"
    """
    icons = " ".join([i for i in [get_fc_icon(fc), get_fs_icon(fs)] if i])
    prefix = "▶" if is_self else ""
    return f"{prefix}{rank}. {name} {achievements:.4f}% {icons}".strip()


def format_score_line_from_dict(
    rank: int,
    name: str,
    score_info: dict,
    is_self: bool = False,
) -> str:
    """
    从字典格式化成绩行。

    score_info 格式：
        {
            'achievements': float,
            'fc': str,
            'fs': str,
            ...
        }
    """
    return format_score_line(
        rank=rank,
        name=name,
        achievements=score_info.get("achievements", 0.0),
        fc=score_info.get("fc"),
        fs=score_info.get("fs"),
        is_self=is_self,
    )


def format_rank_gaps(
    rows: List[Tuple[int, str, dict]],
    my_rank: int,
) -> List[str]:
    """
    计算与前后名的差距。

    Args:
        rows: [(user_id, name, score_info), ...]，已按达成率降序
        my_rank: 当前用户排名（1-based）

    Returns:
        差距描述列表，如 ["与第1名相差 0.5000%", "超过第3名 1.2000%"]
    """
    lines: List[str] = []
    total = len(rows)
    my_idx = my_rank - 1

    if my_idx < 0 or my_idx >= total:
        return lines

    my_score = rows[my_idx][2]["achievements"]

    if my_rank > 1:
        prev_score = rows[my_idx - 1][2]["achievements"]
        diff = prev_score - my_score
        lines.append(f"与第{my_rank - 1}名相差 {diff:.4f}%")

    if my_rank < total:
        next_score = rows[my_idx + 1][2]["achievements"]
        diff = my_score - next_score
        lines.append(f"超过第{my_rank + 1}名 {diff:.4f}%")

    return lines


def format_leaderboard_text(
    music_title: str,
    diff_name: str,
    top_n: int,
    total: int,
    user_rank: Optional[int] = None,
) -> str:
    """
    格式化排行榜标题文本。

    Args:
        music_title: 歌曲标题
        diff_name: 难度名称
        top_n: 显示前 N 名
        total: 总人数
        user_rank: 当前用户排名（None 表示未上榜）

    Returns:
        标题文本
    """
    text = f"本群「{music_title}」{diff_name}难度成绩排名（前 {top_n} 名，共 {total} 人）："
    if user_rank is not None:
        text += f"\n您在群里这首歌{diff_name}难度排名为第{user_rank}名"
    else:
        text += f"\n您尚未游玩过这首歌的{diff_name}难度或未绑定查分器"
    return text


def format_my_rank_text(
    music_title: str,
    diff_name: str,
    rank: int,
    total: int,
    gaps: List[str],
) -> str:
    """
    格式化我的排名文本。

    Returns:
        如 "你在本群「歌曲」Master难度的成绩排名第3/10名\n与第2名相差 0.5000%"
    """
    text = f"你在本群「{music_title}」{diff_name}难度的成绩排名第{rank}/{total}名"
    if gaps:
        text += "\n" + "\n".join(gaps)
    return text
