"""弱项处方单：根据 B50 底力短板标签，推荐未 SSS+ 且标签匹配的练习曲目。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw

from ..config import SIYUAN, footer_generated
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64
from .maimaidx_api_data import maiApi
from .maimaidx_best_50 import filter_utage_records
from .maimaidx_error import UserDisabledQueryError, UserNotFoundError, UserNotExistsError
from .maimaidx_music import mai
from .maimaidx_music_info import get_b50_tag_stats, get_chart_tags_by_group
from .maimaidx_tag_analysis import CONFIG_TAGS_ORDER

ACCENT = (124, 129, 255, 255)
TEXT = (45, 50, 95, 255)
SUBTEXT = (90, 95, 140, 255)
MUTED = (120, 126, 145, 255)
TAG_FILL = (255, 200, 120, 220)
TAG_STROKE = (230, 150, 60, 255)

_SSS_THRESHOLD = 97.0
_MAX_PICKS = 12


@dataclass
class _Pick:
    title: str
    level: str
    ds: float
    achv: float
    ra: int
    tags: List[str]
    score: float


def _identify_weak_tags(stats: Dict[str, Dict[str, int]], top_n: int = 3) -> List[Tuple[str, int]]:
    counts = stats.get('配置') or {}
    ranked = sorted(
        ((t, counts.get(t, 0)) for t in CONFIG_TAGS_ORDER),
        key=lambda x: (x[1], CONFIG_TAGS_ORDER.index(x[0])),
    )
    return ranked[:top_n]


def _short_title(title: str, n: int = 20) -> str:
    t = title.strip()
    return t if len(t) <= n else t[: n - 1] + '…'


def _draw_prescription(
    nickname: str,
    weak_tags: List[Tuple[str, int]],
    picks: List[_Pick],
) -> Image.Image:
    width = 920
    row_h = 34
    tag_panel_h = 120
    list_h = max(1, len(picks)) * row_h + 56
    footer_h = 40
    height = 88 + tag_panel_h + 24 + list_h + footer_h

    im = Image.new('RGBA', (width, height), (245, 247, 255, 255))
    im = generate_frosted_card(im, (24, 72, width - 24, 72 + tag_panel_h), alpha=0.52)
    im = generate_frosted_card(im, (24, 72 + tag_panel_h + 24, width - 24, height - footer_h), alpha=0.52)

    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)
    dt.draw(32, 28, 30, '弱项处方单', ACCENT, 'lt', 2, (255, 255, 255, 240))
    dt.draw(32, 62, 18, f'{nickname}  ·  针对 B50 配置标签短板推荐练习曲目（未达 SSS）', SUBTEXT, 'lt', 1, (255, 255, 255, 220))

    y = 92
    dt.draw(44, y, 22, '短板标签', ACCENT, 'lt', 2, (255, 255, 255, 230))
    x = 44
    y += 36
    for tag, cnt in weak_tags:
        label = f'{tag}（B50×{cnt}）'
        tw = int(dt.get_box(label, 16)[2]) + 28
        dr.rounded_rectangle([x, y, x + tw, y + 30], radius=10, fill=TAG_FILL, outline=TAG_STROKE, width=2)
        dt.draw(x + tw // 2, y + 15, 16, label, TEXT, 'mm', 1, (255, 255, 255, 240))
        x += tw + 12

    y = 72 + tag_panel_h + 44
    dt.draw(44, y, 22, '推荐练习', ACCENT, 'lt', 2, (255, 255, 255, 230))
    y += 36
    if not picks:
        dt.draw(48, y, 18, '暂无匹配曲目（可能标签库未配置或相关谱面已达 SSS）', MUTED, 'lt')
    else:
        for i, p in enumerate(picks, 1):
            tag_txt = '、'.join(p.tags[:3])
            line = (
                f'{i}. {_short_title(p.title)} [{p.level}]  {p.achv:.4f}%  '
                f'定数{p.ds:.1f}  ra{p.ra}  标签:{tag_txt}'
            )
            dt.draw(48, y, 16, line, TEXT, 'lt', 1, (255, 255, 255, 220))
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


async def generate_weakness_prescription(qqid: int) -> Union[str, MessageSegment]:
    try:
        userinfo = await maiApi.query_user_b50(qqid=qqid)
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        return str(e)

    stats = get_b50_tag_stats(userinfo)
    if not any(stats.get('配置', {}).values()):
        return (
            '无法生成弱项处方：未加载谱面标签数据。\n'
            '请配置 MAIMAIDX_DXRATING_TOKEN 或本地标签 JSON（dxrating_tags_json_path）。'
        )

    weak_tags = _identify_weak_tags(stats)
    weak_set = {t for t, _ in weak_tags}

    from .maimaidx_datasource import get_user_records

    try:
        _ui, records = await get_user_records(qqid=qqid)
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        return str(e)

    records = filter_utage_records(records or [])
    if not records:
        return '未读取到全量成绩，无法推荐练习曲目（需开发者 Token）。'

    b50_keys = set()
    for chart_list in (
        getattr(userinfo.charts, 'sd', None) or [],
        getattr(userinfo.charts, 'dx', None) or [],
    ):
        for c in chart_list:
            b50_keys.add((int(c.song_id), int(c.level_index)))

    picks: List[_Pick] = []
    for r in records:
        achv = float(r.achievements)
        if achv >= _SSS_THRESHOLD:
            continue
        music = mai.total_list.by_id(str(r.song_id))
        title = (getattr(music, 'title', None) or getattr(r, 'title', '') or '').strip()
        if not title:
            continue
        groups = get_chart_tags_by_group(title, int(r.level_index))
        cfg_tags = groups.get('配置') or []
        matched = [t for t in cfg_tags if t in weak_set]
        if not matched:
            continue
        ds = float(r.ds)
        in_b50 = (int(r.song_id), int(r.level_index)) in b50_keys
        bonus = 2.0 if in_b50 else 0.0
        score = len(matched) * 10 + ds + bonus
        picks.append(
            _Pick(
                title=title,
                level=r.level,
                ds=ds,
                achv=achv,
                ra=int(r.ra),
                tags=matched,
                score=score,
            )
        )

    picks.sort(key=lambda x: (-x.score, -x.ds))
    picks = picks[:_MAX_PICKS]

    nickname = userinfo.nickname or userinfo.username or '未知'
    im = _draw_prescription(nickname, weak_tags, picks)
    return MessageSegment.image(image_to_base64(im))
