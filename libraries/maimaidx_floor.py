"""B50 地板（最低有效 ra 门槛）查询。"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..config import footer_generated, log
from .maimaidx_best_50 import _is_latest_version, computeRa
from .maimaidx_difficulty_filter import DifficultyFilter
from .maimaidx_error import UserDisabledQueryError, UserNotExistsError, UserNotFoundError
from .maimaidx_model import ChartInfo, UserInfo


def _b50_lists(userinfo: UserInfo) -> Tuple[List[ChartInfo], List[ChartInfo]]:
    b35 = list((userinfo.charts and userinfo.charts.sd) or [])
    b15 = list((userinfo.charts and userinfo.charts.dx) or [])
    return b35, b15


def _fmt_song(r: ChartInfo, indent: str = '  └ ') -> str:
    return (
        f'{indent}{r.title} [{r.level}]'
        f'  定数 {float(r.ds):.1f}  {float(r.achievements):.4f}%'
        f'  ra {int(r.ra)}'
    )


def _min_achv_for_ra(ds: float, target_ra: int) -> Optional[float]:
    """估算达到 target_ra 所需的最低达成率（50~100.5）。"""
    if target_ra <= 0:
        return 50.0
    lo, hi = 50.0, 100.501
    best: Optional[float] = None
    for _ in range(64):
        mid = (lo + hi) / 2
        if int(computeRa(ds, mid)) >= target_ra:
            best = mid
            hi = mid
        else:
            lo = mid
    return best


def _raise_hint(r: ChartInfo, threshold_ra: int) -> str:
    """超过门槛大约还需多少达成率。"""
    need_ra = threshold_ra + 1
    achv = _min_achv_for_ra(float(r.ds), need_ra)
    if achv is None:
        return ''
    gap = achv - float(r.achievements)
    if gap <= 0.0001:
        return f'    当前已可超过地板（ra > {threshold_ra}）'
    return f'    大约再提 {gap:.4f}% 可超过地板（目标 ra ≥ {need_ra}）'


def _zone_label(r: ChartInfo) -> str:
    return 'B15（当前版本）' if _is_latest_version(r) else 'B35（旧版）'


def _floor_block(
    zone: str,
    records: List[ChartInfo],
    *,
    full_size: int,
    filter_label: Optional[str] = None,
) -> List[str]:
    lines: List[str] = []
    if not records:
        tag = f'{zone} · {filter_label}' if filter_label else zone
        lines.append(f'【{tag}】暂无符合条件成绩')
        return lines

    floor_rec = records[-1]
    floor_ra = int(floor_rec.ra)
    count_note = f'{len(records)}/{full_size} 首' if len(records) < full_size else f'{full_size} 首已满'
    tag = f'{zone} · {filter_label}' if filter_label else zone
    lines.append(f'【{tag} 地板】ra {floor_ra}（{count_note}）')
    lines.append(_fmt_song(floor_rec))
    lines.append(_raise_hint(floor_rec, floor_ra))
    return lines


def _build_overview(userinfo: UserInfo) -> List[str]:
    b35, b15 = _b50_lists(userinfo)
    if not b35 and not b15:
        return ['未读取到 B50 数据，请先绑定查分器或检查数据源。']

    lines = [
        '【B50 地板】',
        f'玩家：{userinfo.nickname or userinfo.username or "未知"}',
        f'Rating：{int(userinfo.rating or 0)}',
        '',
        '地板 = B35/B15 各分区末位成绩的 ra。',
        '只有新单曲 ra 超过对应地板，才会替换进 B50 并上涨总 Rating。',
        '若该曲已在 B50 内，则需超过它「当前这条成绩」的 ra。',
        '',
    ]

    if b35:
        lines.extend(_floor_block('B35（旧版曲）', b35, full_size=35))
        lines.append('')
    else:
        lines.append('【B35（旧版曲）】暂无成绩')
        lines.append('')

    if b15:
        lines.extend(_floor_block('B15（当前版本曲）', b15, full_size=15))
        lines.append('')
    else:
        lines.append('【B15（当前版本曲）】暂无成绩')
        lines.append('')

    floors = [int(r.ra) for r in (b35 + b15)]
    if floors:
        min_ra = min(floors)
        weakest = [r for r in (b35 + b15) if int(r.ra) == min_ra]
        lines.append(f'【全 B50 最低 ra】{min_ra}')
        if len(weakest) == 1:
            lines.append(f'  最容易被顶掉：{weakest[0].title}（{_zone_label(weakest[0])}）')
        lines.append('')

    b35_tail = int(b35[-1].ra) if b35 else None
    b15_tail = int(b15[-1].ra) if b15 else None
    lines.append('【怎么涨 Rating？】')
    if b35_tail is not None:
        lines.append(f'· 旧版曲新成绩：ra 需 > {b35_tail} 才有机会进 B35 涨分')
    if b15_tail is not None:
        lines.append(f'· 当前版本曲新成绩：ra 需 > {b15_tail} 才有机会进 B15 涨分')
    lines.append('· 优先推地板那首，或把高定数新曲刷进对应分区')

    return lines


def _build_filtered(userinfo: UserInfo, filt: DifficultyFilter) -> List[str]:
    b35, b15 = _b50_lists(userinfo)
    if not b35 and not b15:
        return ['未读取到 B50 数据，请先绑定查分器或检查数据源。']

    label = filt.display_name
    b35_hit = filt.filter_records(b35)
    b15_hit = filt.filter_records(b15)
    all_hit = b35_hit + b15_hit

    lines = [
        f'【{label} 地板】',
        f'玩家：{userinfo.nickname or userinfo.username or "未知"}',
        f'Rating：{int(userinfo.rating or 0)}',
        '',
        f'在 B50 中筛选「{label}」谱面，取各分区末位作为该难度的地板。',
        '若 B50 里没有这个难度，涨分仍受整体 B35/B15 地板限制（见下方）。',
        '',
    ]

    if all_hit:
        lines.append(f'B50 内符合 {label} 共 {len(all_hit)} 首')
        lines.append('')
        lines.extend(_floor_block('B35', b35_hit, full_size=35, filter_label=label))
        lines.append('')
        lines.extend(_floor_block('B15', b15_hit, full_size=15, filter_label=label))
        lines.append('')

        min_ra = min(int(r.ra) for r in all_hit)
        weakest = [r for r in all_hit if int(r.ra) == min_ra]
        lines.append(f'【{label} 在 B50 中最低 ra】{min_ra}')
        if len(weakest) == 1:
            lines.append(f'  {_fmt_song(weakest[0], indent="  ")}')
        lines.append('')
    else:
        lines.append(f'B50 内暂无「{label}」谱面。')
        lines.append('想靠该难度新曲涨分，需先超过下方整体地板：')
        lines.append('')

    b35_tail = int(b35[-1].ra) if b35 else None
    b15_tail = int(b15[-1].ra) if b15 else None
    lines.append('【整体地板（所有难度通用）】')
    if b35_tail is not None:
        lines.append(f'· B35 地板 ra {b35_tail}')
        lines.append(_fmt_song(b35[-1], indent='  '))
    if b15_tail is not None:
        lines.append(f'· B15 地板 ra {b15_tail}')
        lines.append(_fmt_song(b15[-1], indent='  '))

    return lines


async def generate_floor_query(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
    *,
    filter_text: str = '',
) -> str:
    """
    查询 B50 地板；filter_text 为空则查整体，否则按 DifficultyFilter 解析（如 14+、紫13）。
    """
    try:
        if username:
            qqid = None
        from .maimaidx_datasource import get_user_b50

        userinfo = await get_user_b50(qqid=qqid, username=username)
        raw = (filter_text or '').strip()
        if raw:
            try:
                filt = DifficultyFilter.parse(raw)
            except ValueError as e:
                return (
                    f'无法解析难度条件「{raw}」：{e}\n'
                    '示例：地板 14+、地板 紫13、地板 master、地板 13-14'
                )
            lines = _build_filtered(userinfo, filt)
        else:
            lines = _build_overview(userinfo)

        log.debug(f'[floor] qq={qqid} user={username} filter={raw!r}')
        return '\n'.join(lines) + f'\n\n{footer_generated()}'
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        return str(e)
    except Exception as e:
        log.exception('[floor] query failed')
        return f'查询失败：{type(e).__name__}'
