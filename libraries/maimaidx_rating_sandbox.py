"""目标 Rating 沙盘：计算达到目标分所需的最少曲目改动方案。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import footer_generated
from .maimaidx_best_50 import (
    _is_latest_version,
    _upgrade_achievement,
    computeRa,
    filter_utage_records,
)
from .maimaidx_error import UserDisabledQueryError, UserNotFoundError, UserNotExistsError
from .maimaidx_music import mai


@dataclass
class _Change:
    title: str
    level: str
    ds: float
    achv_now: float
    achv_target: float
    rate_now: str
    rate_target: str
    ra_gain: int
    effort: float
    in_b50: bool
    zone: str
    score: float


def _song_key(r) -> Tuple[int, int]:
    return int(r.song_id), int(r.level_index)


def _best_per_song(records) -> list:
    """每曲只保留 ra 最高的一条（与游戏 B50 规则一致）。"""
    best: Dict[int, object] = {}
    for r in records:
        sid = int(r.song_id)
        prev = best.get(sid)
        if prev is None or int(r.ra) > int(prev.ra):
            best[sid] = r
    return list(best.values())


def _build_b50(records) -> Tuple[list, list, Dict[Tuple[int, int], object]]:
    pool = _best_per_song(records)
    sorted_records = sorted(pool, key=lambda x: int(x.ra), reverse=True)
    b15 = sorted([r for r in sorted_records if _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:15]
    b35 = sorted([r for r in sorted_records if not _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:35]
    b50_map = {_song_key(r): r for r in (b35 + b15)}
    return b35, b15, b50_map


def _title_for(r) -> str:
    music = mai.total_list.by_id(str(r.song_id))
    return (getattr(music, 'title', None) or getattr(r, 'title', '') or '未知').strip()


def _zone_for(r) -> str:
    return 'B15' if _is_latest_version(r) else 'B35'


def _floor_ra(b35: list, b15: list, zone: str) -> int:
    if zone == 'B15':
        return int(b15[-1].ra) if b15 else 0
    return int(b35[-1].ra) if b35 else 0


def _candidate_changes(records, b35, b15, b50_map) -> List[_Change]:
    picks: List[_Change] = []
    seen: set[Tuple[int, int, float]] = set()

    for r in _best_per_song(records):
        key = _song_key(r)
        achv_now = float(r.achievements)
        if achv_now >= 100.5:
            continue
        ds = float(r.ds)
        in_b50 = key in b50_map
        zone = _zone_for(r)
        floor = _floor_ra(b35, b15, zone)

        _, rate_now = computeRa(ds, achv_now, israte=True)
        cur_ra = int(r.ra)

        achv = achv_now
        rate = rate_now
        for _ in range(6):
            new_achv, _, new_rate = _upgrade_achievement(achv)
            if new_achv <= achv + 1e-9:
                break
            new_ra = int(computeRa(ds, new_achv))
            if in_b50:
                ra_gain = new_ra - cur_ra
            else:
                if new_ra <= floor:
                    achv = new_achv
                    rate = new_rate
                    continue
                ra_gain = new_ra - floor
            if ra_gain <= 0:
                achv = new_achv
                rate = new_rate
                continue

            effort = new_achv - achv_now
            dedupe = (key[0], key[1], new_achv)
            if dedupe in seen:
                break
            seen.add(dedupe)
            bonus = 1.5 if in_b50 else 1.0
            picks.append(
                _Change(
                    title=_title_for(r),
                    level=r.level,
                    ds=ds,
                    achv_now=achv_now,
                    achv_target=new_achv,
                    rate_now=rate_now,
                    rate_target=new_rate,
                    ra_gain=ra_gain,
                    effort=effort,
                    in_b50=in_b50,
                    zone=zone,
                    score=ra_gain / max(effort, 0.01) * bonus,
                )
            )
            break
    return picks


def _greedy_plan(changes: List[_Change], gap: int, max_steps: int = 8) -> List[_Change]:
    remaining = gap
    used_keys: set[Tuple[str, str]] = set()
    plan: List[_Change] = []
    pool = sorted(changes, key=lambda x: (-x.score, -x.ra_gain))

    for c in pool:
        if remaining <= 0 or len(plan) >= max_steps:
            break
        dedupe_key = (c.title, c.level)
        if dedupe_key in used_keys:
            continue
        if c.ra_gain <= 0:
            continue
        plan.append(c)
        used_keys.add(dedupe_key)
        remaining -= c.ra_gain
    return plan


async def generate_rating_sandbox(
    qqid: Optional[int],
    target: int,
    username: Optional[str] = None,
) -> str:
    if target < 0 or target > 30000:
        return '目标 Rating 请在 0～30000 之间。'

    from .maimaidx_datasource import get_user_records

    try:
        userinfo, records = await get_user_records(qqid=qqid, username=username)
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        return str(e)

    records = filter_utage_records(records or [])
    if not records:
        return '没有成绩数据（需开发者 Token 获取全量成绩）。'

    b35, b15, b50_map = _build_b50(records)
    b50_sum = int(sum(int(r.ra) for r in b35) + sum(int(r.ra) for r in b15))
    current = int(userinfo.rating or 0) or b50_sum
    nickname = userinfo.nickname or userinfo.username or '未知'

    if target <= current:
        extra = ''
        if b50_sum and abs(b50_sum - current) > 3:
            extra = f'\n（B50 重算合计 {b50_sum}，与查分器略有偏差）'
        return (
            f'{nickname} 当前 DX Rating {current}，已达成或超过目标 {target}。{extra}\n\n{footer_generated()}'
        )

    gap = target - current
    changes = _candidate_changes(records, b35, b15, b50_map)
    if not changes:
        return '未找到可提升的候选曲目。'

    plan = _greedy_plan(changes, gap)
    if not plan:
        return (
            f'当前 DX Rating {current}，距离目标 {target} 还差 {gap}。\n'
            '暂未找到单步可执行的改动方案，建议配合「今日吃分推荐」查看。'
            f'\n\n{footer_generated()}'
        )

    projected = current + sum(c.ra_gain for c in plan)
    lines = [
        f'目标 Rating 沙盘 · {nickname}',
        f'当前 DX Rating：{current}  →  目标：{target}  （差 {gap}）',
        f'以下方案为贪心估算（每曲取一档提升，独立计算不进 B50 替换联动）：',
        '',
    ]
    for i, c in enumerate(plan, 1):
        scope = f'{c.zone}·已在B50' if c.in_b50 else f'{c.zone}·进B50'
        lines.append(
            f'{i}. {c.title} [{c.level}]  {scope}'
        )
        lines.append(
            f'   {c.achv_now:.4f}% ({c.rate_now}) → {c.achv_target:.4f}% ({c.rate_target})  '
            f'定数{c.ds:.1f}  预计+{c.ra_gain}ra'
        )

    lines.append('')
    lines.append(f'按以上 {len(plan)} 项估算可达 {projected}（{"已覆盖目标" if projected >= target else f"仍差 {target - projected}"}）')
    if projected < target:
        lines.append('提示：目标较高时可多推地板曲或配合吃分推荐继续规划。')
    lines.append('')
    lines.append(footer_generated())
    return '\n'.join(lines)
