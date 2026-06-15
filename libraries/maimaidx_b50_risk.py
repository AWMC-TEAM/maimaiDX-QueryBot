"""B50 风险预警：结合存档历史分析地板、下滑、寸止/锁血等挤出风险。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw

from ..config import SIYUAN, achievementList, footer_generated
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64
from .maimaidx_best_50 import _is_latest_version
from .maimaidx_data_storage import DailySnapshot, ScoreRecord, data_storage

ACCENT = (124, 129, 255, 255)
TEXT = (45, 50, 95, 255)
SUBTEXT = (90, 95, 140, 255)
MUTED = (120, 126, 145, 255)
WARN = (220, 120, 90, 255)
HIGH = (220, 80, 100, 255)


@dataclass
class _RiskItem:
    title: str
    level: str
    ra: int
    achv: float
    zone: str
    score: int
    reasons: List[str]


def _song_key(r: ScoreRecord) -> Tuple[int, int]:
    return int(r.song_id), int(r.level_index)


def _build_b50(records: List[ScoreRecord]) -> Tuple[List[ScoreRecord], List[ScoreRecord], List[ScoreRecord]]:
    sorted_records = sorted(records, key=lambda x: int(x.ra), reverse=True)
    b15 = sorted([r for r in sorted_records if _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:15]
    b35 = sorted([r for r in sorted_records if not _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:35]
    return b35, b15, b35 + b15


def _is_sun(a: float) -> bool:
    for x in achievementList:
        if (x - 0.1) < a <= x:
            return True
    return False


def _is_lock(a: float) -> bool:
    for x in achievementList:
        step = 0.01 if x != int(x) else 0.1
        if x <= a < (x + step):
            return True
    return False


def _zone_label(r: ScoreRecord) -> str:
    return 'B15' if _is_latest_version(r) else 'B35'


def _short_title(title: str, n: int = 18) -> str:
    t = title.strip()
    return t if len(t) <= n else t[: n - 1] + '…'


def _analyze_risks(snaps: List[DailySnapshot]) -> Tuple[str, List[_RiskItem]]:
    latest = snaps[-1]
    b35, b15, b50 = _build_b50(latest.records)
    if not b50:
        return latest.nickname or str(latest.qqid), []

    b35_floor = int(b35[-1].ra) if b35 else 0
    b15_floor = int(b15[-1].ra) if b15 else 0

    history: List[Dict[Tuple[int, int], ScoreRecord]] = []
    for s in snaps:
        _, _, cur_b50 = _build_b50(s.records)
        history.append({_song_key(r): r for r in cur_b50})

    items: List[_RiskItem] = []
    for r in b50:
        key = _song_key(r)
        zone = _zone_label(r)
        floor = b15_floor if zone == 'B15' else b35_floor
        ra = int(r.ra)
        achv = float(r.achievements)
        reasons: List[str] = []
        score = 0

        if floor and ra <= floor:
            reasons.append('地板位')
            score += 40
        elif floor and ra - floor <= 3:
            reasons.append(f'贴地板(差{ra - floor})')
            score += 28
        elif floor and ra - floor <= 8:
            reasons.append(f'近地板(差{ra - floor})')
            score += 14

        if _is_sun(achv):
            reasons.append('寸止')
            score += 22
        if _is_lock(achv):
            reasons.append('锁血')
            score += 18

        if len(history) >= 2:
            prev_ra = int(history[-2].get(key, r).ra)
            if ra < prev_ra:
                reasons.append(f'较上次-{prev_ra - ra}ra')
                score += 20
        if len(history) >= 3:
            oldest_ra = int(history[0].get(key, r).ra)
            if ra < oldest_ra - 2:
                reasons.append(f'较早期-{oldest_ra - ra}ra')
                score += 12

        if not reasons:
            continue
        items.append(
            _RiskItem(
                title=r.title,
                level=r.level,
                ra=ra,
                achv=achv,
                zone=zone,
                score=score,
                reasons=reasons,
            )
        )

    items.sort(key=lambda x: (-x.score, x.ra))
    return latest.nickname or str(latest.qqid), items[:15]


def _draw_risk_report(nickname: str, items: List[_RiskItem], snap_days: int) -> Image.Image:
    width = 920
    row_h = 36
    list_h = max(1, len(items)) * row_h + 72
    footer_h = 40
    height = 96 + list_h + footer_h

    im = Image.new('RGBA', (width, height), (245, 247, 255, 255))
    im = generate_frosted_card(im, (24, 80, width - 24, height - footer_h), alpha=0.52)
    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)

    dt.draw(32, 28, 30, 'B50 风险预警', ACCENT, 'lt', 2, (255, 255, 255, 240))
    dt.draw(
        32, 62, 17,
        f'{nickname}  ·  基于近 {snap_days} 天存档  ·  地板/寸止/锁血/下滑综合评分',
        SUBTEXT, 'lt', 1, (255, 255, 255, 220),
    )

    y = 104
    if not items:
        dt.draw(44, y, 18, '当前 B50 暂无明显挤出风险，继续保持！', MUTED, 'lt')
    else:
        dt.draw(44, y, 20, '高风险曲目', HIGH if any(i.score >= 40 for i in items) else WARN, 'lt', 2, (255, 255, 255, 230))
        y += 34
        for i, item in enumerate(items, 1):
            color = HIGH if item.score >= 40 else (WARN if item.score >= 25 else TEXT)
            reason_txt = '、'.join(item.reasons)
            line = (
                f'{i}. {_short_title(item.title)} [{item.level}] {item.zone}  '
                f'{item.achv:.4f}%  ra{item.ra}  风险{item.score}  {reason_txt}'
            )
            dt.draw(48, y, 15, line, color, 'lt', 1, (255, 255, 255, 220))
            y += row_h

    draw_centered_design_footer(
        im, dt, footer_generated(),
        color=SUBTEXT,
        margin_x=48,
        start_font_size=14,
        min_font_size=10,
        bottom_gap=8,
    )
    return im


async def generate_b50_risk_warning(qqid: int) -> Union[str, MessageSegment]:
    if not data_storage.is_enabled(qqid):
        return '你尚未开启数据存储，请先发送「开启存储数据」后再使用 B50 风险预警。'

    metas = data_storage.list_snapshots(qqid, limit=30)
    if len(metas) < 2:
        return (
            '存档数量不足（至少需要 2 份快照）。\n'
            '请等待每日自动存档，或发送「立即存储数据」积累历史后再试。'
        )

    snaps: List[DailySnapshot] = []
    for m in reversed(metas):
        sid = m.get('snapshot_id', '')
        snap = data_storage.load_snapshot_by_id(qqid, sid) if sid else None
        if snap:
            snaps.append(snap)

    if len(snaps) < 2:
        return '无法读取有效存档，请稍后再试。'

    nickname, items = _analyze_risks(snaps)
    im = _draw_risk_report(nickname, items, len(snaps))
    return MessageSegment.image(image_to_base64(im))
