"""
幻之成绩（Phantom Score）生成器

根据目标 Rating 自动生成一张"虚构但可行"的 B50 成绩单。
B50 = B35（旧版前35首最高ra）+ B15（最新两代后15首最高ra）。
"""
import random
from typing import List, Optional, Tuple

from ..config import log
from .maimaidx_best_50 import (
    computeRa,
    _song_is_new,
)
from .maimaidx_model import ChartInfo
from .maimaidx_music import mai


# ---- 评级档位（用于展示） ----
_RATE_ORDER = ['D', 'C', 'B', 'BB', 'BBB', 'A', 'AA', 'AAA', 'S', 'Sp', 'SS', 'SSp', 'SSS', 'SSSp']


def _get_all_candidate_songs() -> Tuple[List[Tuple], List[Tuple]]:
    """
    从曲库中提取所有可用的谱面，分离 B35 和 B15 池。

    Returns:
        (b35_pool, b15_pool)：每个元素为 (music_obj, level_index, ds)
    """
    b35_pool: List[Tuple] = []
    b15_pool: List[Tuple] = []

    for music in mai.total_list:
        # 跳过宴谱
        try:
            if int(str(music.id)) >= 100000:
                continue
        except (TypeError, ValueError):
            pass

        is_new = _song_is_new(music.id)
        for level_idx, ds in enumerate(music.ds):
            if ds <= 0:
                continue
            entry = (music, level_idx, ds)
            if is_new:
                b15_pool.append(entry)
            else:
                b35_pool.append(entry)

    # 按 ds 降序排列
    b35_pool.sort(key=lambda x: -x[2])
    b15_pool.sort(key=lambda x: -x[2])

    return b35_pool, b15_pool


def _get_achievement_for_ra(ds: float, target_ra: int) -> float:
    """
    对于给定的定数和目标 ra，找到最接近的达成率。
    遍历评级阈值，选择使 ra 最接近 target_ra 的达成率。
    """
    thresholds = [50.0, 60.0, 70.0, 75.0, 80.0, 90.0, 94.0, 97.0, 98.0, 99.0, 99.5, 100.0, 100.5]

    best_ach = 50.0
    best_diff = abs(computeRa(ds, 50.0) - target_ra)

    for ach in thresholds:
        ra = computeRa(ds, ach)
        diff = abs(ra - target_ra)
        if diff < best_diff:
            best_diff = diff
            best_ach = ach

    return best_ach


def _generate_achievement_distribution(
    candidates: List[Tuple],
    count: int,
    target_total_ra: float,
) -> List[Tuple]:
    """
    为候选谱面分配达成率，使总 ra 接近目标值。

    策略：
    1. 按 ds 降序排列，每个谱面根据自身 ds 和目标 ra 分配基础达成率
    2. 使用贪心调整使总 ra 精确贴近目标
    """
    thresholds = [50.0, 60.0, 70.0, 75.0, 80.0, 90.0, 94.0, 97.0, 98.0, 99.0, 99.5, 100.0, 100.5]

    # 取前 count 首
    selected = candidates[:count]

    # 第一阶段：计算理想 ra 分布
    # 使较高 ds 的谱面 ra 略低（因为更难），较低 ds 的谱面 ra 略高（因为更容易）
    ds_values = [ds for _, _, ds in selected]
    avg_ds = sum(ds_values) / len(ds_values)
    avg_target_ra = target_total_ra / count

    results = []
    for music, level_idx, ds in selected:
        # ds 高于平均值 → target ra 略低于平均值；反之亦然
        ds_bias = (avg_ds - ds) / max(avg_ds, 1.0) * avg_target_ra * 0.15
        song_target_ra = max(10, avg_target_ra + ds_bias)
        ach = _get_achievement_for_ra(ds, int(song_target_ra))
        ra = computeRa(ds, ach)
        results.append([music, level_idx, ds, ach, ra])

    # 第二阶段：贪心调整使总 ra 精确贴近目标
    target_total = int(target_total_ra)
    max_iter = 200

    for _ in range(max_iter):
        current_total = sum(r[4] for r in results)
        gap = target_total - current_total
        if gap == 0:
            break

        best_idx = -1
        best_new_ach = None
        best_new_ra = None
        best_abs_gap_after = abs(gap)

        for idx, (music, level_idx, ds, ach, ra) in enumerate(results):
            try:
                tier = thresholds.index(ach)
            except ValueError:
                continue

            if gap > 0 and tier < len(thresholds) - 1:
                new_ach = thresholds[tier + 1]
                new_ra = computeRa(ds, new_ach)
                gap_after = abs(gap - (new_ra - ra))
                if gap_after < best_abs_gap_after:
                    best_abs_gap_after = gap_after
                    best_idx = idx
                    best_new_ach = new_ach
                    best_new_ra = new_ra
            elif gap < 0 and tier > 0:
                new_ach = thresholds[tier - 1]
                new_ra = computeRa(ds, new_ach)
                gap_after = abs(gap - (new_ra - ra))
                if gap_after < best_abs_gap_after:
                    best_abs_gap_after = gap_after
                    best_idx = idx
                    best_new_ach = new_ach
                    best_new_ra = new_ra

        if best_idx < 0:
            break  # 无法继续调整

        results[best_idx][3] = best_new_ach
        results[best_idx][4] = best_new_ra

    return [tuple(r) for r in results]


def _assign_fc_fs(achievement: float) -> Tuple[str, str]:
    """
    根据达成率分配 FC/FS 状态，模拟真实玩家的成绩分布。

    Returns:
        (fc, fs) 元组
    """
    r = random.random()

    if achievement >= 100.5:
        # SSS+：多半 AP+
        if r < 0.55:
            fc = 'app'
        elif r < 0.85:
            fc = 'ap'
        else:
            fc = ''
        # FS 偶尔有
        fs = 'fsd' if random.random() < 0.15 else ''
    elif achievement >= 100.0:
        # SSS：大概 AP
        if r < 0.35:
            fc = 'ap'
        elif r < 0.70:
            fc = 'app'
        elif r < 0.90:
            fc = 'fcp'
        else:
            fc = ''
        fs = 'fsd' if random.random() < 0.10 else ''
    elif achievement >= 99.5:
        # SS+：FC+ 常见
        if r < 0.30:
            fc = 'fcp'
        elif r < 0.65:
            fc = 'fc'
        elif r < 0.75:
            fc = 'ap'
        else:
            fc = ''
        fs = 'fs' if random.random() < 0.05 else ''
    elif achievement >= 99.0:
        # SS：FC 常见
        if r < 0.45:
            fc = 'fc'
        elif r < 0.55:
            fc = 'fcp'
        else:
            fc = ''
        fs = ''
    elif achievement >= 98.0:
        # S+：可能 FC
        if r < 0.25:
            fc = 'fc'
        else:
            fc = ''
        fs = ''
    elif achievement >= 97.0:
        if r < 0.15:
            fc = 'fc'
        else:
            fc = ''
        fs = ''
    else:
        fc = ''
        fs = ''

    return fc, fs


def _ds_to_level_label(ds: float) -> str:
    """将定数转换为等级标签（如 '14+'）。"""
    level_map = {
        1: '1', 2: '2', 3: '3', 4: '4', 5: '5',
        6: '6', 7: '7', 8: '8', 9: '9', 10: '10',
        11: '11', 12: '12', 13: '13', 14: '14', 15: '15',
    }
    base = int(ds)
    if ds >= base + 0.7:
        suffix = '+'
    else:
        suffix = ''
    return level_map.get(base, str(base)) + suffix


def generate_phantom_score(target_rating: int, seed: Optional[int] = None) -> Tuple[List[ChartInfo], List[ChartInfo], int]:
    """
    生成幻之成绩单。

    Args:
        target_rating: 目标 Rating（如 15000）
        seed: 随机种子，相同种子 + 相同目标产生相同结果

    Returns:
        (b35_list, b15_list, actual_rating)
    """
    if seed is not None:
        random.seed(seed)
    else:
        random.seed(target_rating)  # 默认用 target_rating 作为种子保证可复现

    b35_pool, b15_pool = _get_all_candidate_songs()

    if len(b35_pool) < 35:
        raise ValueError(f'B35 候选谱面不足（需要 35，实际 {len(b35_pool)}）')
    if len(b15_pool) < 15:
        raise ValueError(f'B15 候选谱面不足（需要 15，实际 {len(b15_pool)}）')

    # 计算 B35 和 B15 各自的目标 ra
    b35_target = target_rating * 35 / 50
    b15_target = target_rating * 15 / 50

    # 生成成绩
    b35_results = _generate_achievement_distribution(b35_pool, 35, b35_target)
    b15_results = _generate_achievement_distribution(b15_pool, 15, b15_target)

    # 转换为 ChartInfo 列表
    b35_charts: List[ChartInfo] = []
    b15_charts: List[ChartInfo] = []

    for music, level_idx, ds, ach, ra in b35_results:
        fc, fs = _assign_fc_fs(ach)
        _, rate = computeRa(ds, ach, israte=True)

        chart = ChartInfo(
            achievements=ach,
            ds=round(ds, 1),
            level_index=level_idx,
            level_label=_ds_to_level_label(ds),
            title=music.title,
            type=music.type,
            song_id=int(music.id),
            ra=ra,
            rate=rate,
            fc=fc,
            fs=fs,
            dxScore=0,
        )
        b35_charts.append(chart)

    for music, level_idx, ds, ach, ra in b15_results:
        fc, fs = _assign_fc_fs(ach)
        _, rate = computeRa(ds, ach, israte=True)

        chart = ChartInfo(
            achievements=ach,
            ds=round(ds, 1),
            level_index=level_idx,
            level_label=_ds_to_level_label(ds),
            title=music.title,
            type=music.type,
            song_id=int(music.id),
            ra=ra,
            rate=rate,
            fc=fc,
            fs=fs,
            dxScore=0,
        )
        b15_charts.append(chart)

    actual_total = sum(c.ra for c in b35_charts) + sum(c.ra for c in b15_charts)

    return b35_charts, b15_charts, actual_total


def format_phantom_score_text(
    b35_list: List[ChartInfo],
    b15_list: List[ChartInfo],
    target_rating: int,
    actual_rating: int,
) -> str:
    """
    将幻之成绩格式化为文本表格。

    Returns:
        Markdown 表格格式的文本
    """
    lines = []
    lines.append(f'幻之成绩单 - 目标 Rating: {target_rating}，实际: {actual_rating}')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## B35（旧版前 35 首）')
    lines.append('')
    lines.append('| # | 曲名 | 定数 | 达成率 | 评级 | FC/FS | 单曲Rating |')
    lines.append('|---|------|------|--------|------|-------|------------|')

    for i, c in enumerate(b35_list, 1):
        fc_fs = c.fc or '-'
        if c.fs:
            fc_fs += f'/{c.fs}'
        lines.append(
            f'| {i} | {c.title} | {c.ds:.1f} | {c.achievements:.4f}% | {c.rate} | {fc_fs} | {c.ra} |'
        )

    lines.append('')
    lines.append('## B15（新版本前 15 首）')
    lines.append('')
    lines.append('| # | 曲名 | 定数 | 达成率 | 评级 | FC/FS | 单曲Rating |')
    lines.append('|---|------|------|--------|------|-------|------------|')

    for i, c in enumerate(b15_list, 1):
        fc_fs = c.fc or '-'
        if c.fs:
            fc_fs += f'/{c.fs}'
        lines.append(
            f'| {i} | {c.title} | {c.ds:.1f} | {c.achievements:.4f}% | {c.rate} | {fc_fs} | {c.ra} |'
        )

    lines.append('')
    b35_total = sum(c.ra for c in b35_list)
    b15_total = sum(c.ra for c in b15_list)

    # 统计评级分布
    rate_dist = {}
    for c in b35_list + b15_list:
        rate_dist[c.rate] = rate_dist.get(c.rate, 0) + 1
    sorted_rates = sorted(rate_dist.items(), key=lambda x: _RATE_ORDER.index(x[0]) if x[0] in _RATE_ORDER else 999)

    fc_count = sum(1 for c in b35_list + b15_list if c.fc)
    ap_count = sum(1 for c in b35_list + b15_list if c.fc in ('ap', 'app'))

    lines.append('---')
    lines.append('')
    lines.append('## 汇总统计')
    lines.append('')
    lines.append(f'- B35 Rating: {b35_total}')
    lines.append(f'- B15 Rating: {b15_total}')
    lines.append(f'- 总 Rating: {actual_rating}（目标: {target_rating}，误差: {actual_rating - target_rating:+d}）')
    lines.append(f'- FC 数: {fc_count}')
    lines.append(f'- AP 数: {ap_count}')
    lines.append('- 评级分布: ' + ', '.join(f'{r}×{c}' for r, c in sorted_rates))

    return '\n'.join(lines)
