"""友人对战结果图渲染。"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Optional, Tuple

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw

from ..config import SIYUAN, footer_generated, maimaidir
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64, music_picture, rounded_corners
from .maimaidx_api_data import maiApi
from .maimaidx_timing import measure
from .maimaidx_best_50 import changeColumnWidth, coloumWidth
from .maimaidx_friend_battle import FriendBattleOutcome
from .maimaidx_progress_report import _get_report_bg

W = 1000
M = 40
INSET = 24
PANEL_PAD = 20
GAP = 14
FOOTER_ZONE = 56
PANEL_A = 0.46

ACCENT = (124, 129, 255, 255)
TEXT = (45, 50, 95, 255)
SUBTEXT = (90, 95, 140, 255)
MUTED = (120, 126, 145, 255)
WHITE_STROKE = (255, 255, 255, 240)
WIN = (46, 125, 82, 255)
WIN_BG = (228, 248, 236, 245)
LOSE = (198, 72, 82, 255)
LOSE_BG = (255, 236, 238, 245)
TIE = (90, 98, 130, 255)
TIE_BG = (242, 244, 250, 245)

DIFF_COLORS = [
    (76, 175, 80, 255),
    (255, 193, 7, 255),
    (244, 67, 54, 255),
    (156, 39, 176, 255),
    (224, 64, 251, 255),
]
DIFF_BG = [
    (232, 248, 233, 255),
    (255, 248, 225, 255),
    (255, 235, 238, 255),
    (243, 229, 245, 255),
    (248, 235, 252, 255),
]


def _short_title(title: str, max_w: int = 28) -> str:
    if coloumWidth(title) <= max_w:
        return title
    return changeColumnWidth(title, max_w - 2) + '…'


def _short_name(name: str, max_w: int = 14) -> str:
    if coloumWidth(name) <= max_w:
        return name
    return changeColumnWidth(name, max_w - 2) + '…'


def _hero_verdict(outcome: FriendBattleOutcome) -> Tuple[str, Optional[str], tuple, tuple]:
    """主标题、副标题（可空）、前景色、背景色。"""
    if outcome.winner_side == 'me':
        return '你赢了', None, WIN, WIN_BG
    if outcome.winner_side == 'opp':
        sub = f'对手：{_short_name(outcome.opp_name, 16)}'
        return '你败了', sub, LOSE, LOSE_BG
    return '平手', None, TIE, TIE_BG


def _cp_panel_height(lines: list[str]) -> int:
    """按行数估算 CP 面板内容高度（含上下留白）。"""
    h = PANEL_PAD * 2 + 34
    for line in lines:
        if line.startswith('──'):
            h += 18
        else:
            h += 28
    return h


def _frosted_panel(im: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    return generate_frosted_card(im, box, alpha=PANEL_A)


def _accent_strip(im: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int, int] = ACCENT) -> None:
    x0, y0, x1, y1 = box
    strip = Image.new('RGBA', (5, max(0, y1 - y0 - 16)), color)
    im.alpha_composite(strip, (x0 + 5, y0 + PANEL_PAD))


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img.convert('RGBA'), (0, 0), mask)
    return out


async def _fetch_avatar(qqid: int, size: int) -> Optional[Image.Image]:
    try:
        with measure('fetch'):
            raw = await maiApi.qqlogo(qqid=qqid)
        if not raw:
            return None
        return _circle_avatar(Image.open(BytesIO(raw)), size)
    except Exception:
        return None


def _paste_avatar(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    avatar: Optional[Image.Image],
    cx: int,
    cy: int,
    size: int,
    *,
    ring: tuple[int, int, int, int] = ACCENT,
) -> None:
    x, y = cx - size // 2, cy - size // 2
    if avatar is not None:
        im.alpha_composite(avatar, (x, y))
    else:
        dr.ellipse((x, y, x + size, y + size), fill=(248, 249, 255, 255), outline=ring, width=2)
        dt.draw(cx, cy, max(14, size // 3), '?', MUTED, 'mm', 1, (255, 255, 255, 200))
    dr.ellipse((x - 2, y - 2, x + size + 2, y + size + 2), outline=(*ring[:3], 180), width=2)


def _draw_chip(dr: ImageDraw.ImageDraw, dt: DrawText, x: int, y: int, text: str, fill: tuple[int, int, int, int]) -> int:
    pad_x = 14
    fs = 14
    tw = int(dt.get_box(text, fs)[2] - dt.get_box(text, fs)[0]) + pad_x * 2
    th = 30
    dr.rounded_rectangle((x, y, x + tw, y + th), radius=15, fill=fill, outline=(*ACCENT[:3], 70), width=1)
    dt.draw(x + pad_x, y + 7, fs, text, TEXT, 'lt', 1, (255, 255, 255, 230))
    return tw


def _draw_hero(im: Image.Image, dt: DrawText, outcome: FriendBattleOutcome) -> None:
    h = 96
    banner = Image.new('RGBA', (W - M * 2, h), (0, 0, 0, 0))
    bdr = ImageDraw.Draw(banner)
    for i in range(h):
        t = i / max(h - 1, 1)
        r = int(118 + (188 - 118) * t * 0.3)
        g = int(124 + (205 - 124) * t * 0.3)
        b = int(252 - (252 - 210) * t * 0.15)
        bdr.line([(0, i), (W - M * 2, i)], fill=(r, g, b, 210))
    mask = Image.new('L', (W - M * 2, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - M * 2, h), radius=18, fill=255)
    banner.putalpha(mask)
    im.alpha_composite(banner, (M, 12))

    try:
        from .maimaidx_theme import Theme, resolve_theme_path
        logo = Image.open(resolve_theme_path(maimaidir, Theme.get_default().value, 'logo.png')).convert('RGBA')
        im.alpha_composite(logo.resize((int(249 * 0.48), int(120 * 0.48))), (M + 8, 18))
    except Exception:
        pass

    dt.draw(M + 168, 28, 25, '友人对战', TEXT, 'lt', 2, (255, 255, 255, 255))
    dt.draw(M + 168, 54, 13, 'FRIEND BATTLE', SUBTEXT, 'lt', 1, (255, 255, 255, 220))

    main, sub, fg, bg = _hero_verdict(outcome)
    dr = ImageDraw.Draw(im)
    main_w = int(dt.get_box(main, 26)[2] - dt.get_box(main, 26)[0]) + 36
    pill_h = 54 if sub else 44
    vx = W - M - main_w - 10
    vy = 22
    dr.rounded_rectangle((vx, vy, vx + main_w, vy + pill_h), radius=20, fill=bg, outline=fg, width=2)
    dt.draw(vx + main_w // 2, vy + (16 if sub else 22), 26, main, fg, 'mm', 1, (255, 255, 255, 255))
    if sub:
        dt.draw(vx + main_w // 2, vy + 36, 14, sub, SUBTEXT, 'mm', 1, (255, 255, 255, 230))


def _draw_duel_row(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    outcome: FriendBattleOutcome,
    box: tuple[int, int, int, int],
    my_av: Optional[Image.Image],
    opp_av: Optional[Image.Image],
) -> None:
    _accent_strip(im, box)
    x0, y0, x1, y1 = box
    inner_l, inner_r = x0 + INSET, x1 - INSET
    content_w = inner_r - inner_l

    dt.draw(inner_l, y0 + PANEL_PAD, 19, '对战双方', ACCENT, 'lt', 2, WHITE_STROKE)

    av_sz = 72
    av_cy = y0 + PANEL_PAD + 44 + av_sz // 2
    col_cx_l = inner_l + content_w // 4
    col_cx_r = inner_l + content_w * 3 // 4

    my_ring = WIN if outcome.winner_side == 'me' else ACCENT
    opp_ring = WIN if outcome.winner_side == 'opp' else ACCENT
    _paste_avatar(im, dr, dt, my_av, col_cx_l, av_cy, av_sz, ring=my_ring)
    _paste_avatar(im, dr, dt, opp_av, col_cx_r, av_cy, av_sz, ring=opp_ring)

    # 中间分隔线（替代 VS 圆球）
    mid_x = (col_cx_l + col_cx_r) // 2
    dr.line((mid_x, av_cy - av_sz // 2 - 4, mid_x, av_cy + av_sz // 2 + 4), fill=(*ACCENT[:3], 90), width=2)

    name_y = av_cy + av_sz // 2 + 16
    dt.draw(col_cx_l, name_y, 17, _short_name(outcome.my_name or '你', 14), TEXT, 'mm', 1, (255, 255, 255, 235))
    dt.draw(col_cx_r, name_y, 17, _short_name(outcome.opp_name, 14), TEXT, 'mm', 1, (255, 255, 255, 235))
    ra_y = name_y + 26
    dt.draw(col_cx_l, ra_y, 15, f'Rating {outcome.my_rating}', SUBTEXT, 'mm', 1, (255, 255, 255, 220))
    dt.draw(col_cx_r, ra_y, 15, f'Rating {outcome.opp_rating}', SUBTEXT, 'mm', 1, (255, 255, 255, 220))

    chip_y = ra_y + 34
    cx = inner_l
    cw = _draw_chip(dr, dt, cx, chip_y, outcome.used_pool, (240, 242, 252, 255))
    _draw_chip(
        dr, dt, cx + cw + 10, chip_y,
        f'|Δrating| {outcome.rating_delta}  ·  允许 ±{outcome.rating_limit}',
        (235, 237, 248, 255),
    )
    rel_y = y1 - PANEL_PAD - 6
    dt.draw(inner_l, rel_y, 14, f'段位关系 · {outcome.rel_zh}', MUTED, 'lb', 1, (255, 255, 255, 210))


def _draw_score_card(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    box: tuple[int, int, int, int],
    label: str,
    achv: float,
    dx: int,
    *,
    is_winner: bool,
) -> None:
    x0, y0, x1, y1 = box
    if is_winner:
        dr.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=WIN_BG, outline=WIN, width=2)
        dt.draw(x0 + 14, y0 + 10, 13, '胜', WIN, 'lt', 1, (255, 255, 255, 255))
        name_y = y0 + 30
    else:
        dr.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=(255, 255, 255, 230), outline=(*ACCENT[:3], 60), width=1)
        name_y = y0 + 14

    dt.draw(x0 + 14, name_y, 16, _short_name(label, 12), TEXT, 'lt', 1, (255, 255, 255, 230))
    achv_color = WIN if is_winner else TEXT
    dt.draw(x0 + 14, (y0 + y1) // 2 - 4, 26, f'{achv:.4f}%', achv_color, 'lt', 2, (255, 255, 255, 255))
    dt.draw(x0 + 14, (y0 + y1) // 2 + 30, 14, f'DX {dx}', SUBTEXT, 'lt', 1, (255, 255, 255, 210))


async def draw_friend_battle_image(outcome: FriendBattleOutcome) -> MessageSegment:
    hero_h = 112
    duel_h = 268
    cp_inner = _cp_panel_height(outcome.cp_lines)
    battle_h = 300
    card_h = 118

    y_duel = hero_h
    y_battle = y_duel + duel_h + GAP
    y_cp = y_battle + battle_h + GAP
    y_footer = y_cp + cp_inner + GAP
    height = y_footer + FOOTER_ZONE

    duel_box = (M, y_duel, W - M, y_duel + duel_h)
    battle_box = (M, y_battle, W - M, y_battle + battle_h)
    cp_box = (M, y_cp, W - M, y_cp + cp_inner)
    footer_box = (M, y_footer, W - M, height - 6)

    my_av, opp_av = await asyncio.gather(
        _fetch_avatar(outcome.my_qq, 72),
        _fetch_avatar(outcome.opp_qq, 72),
    )

    im = _get_report_bg(W, height)
    im = _frosted_panel(im, duel_box)
    im = _frosted_panel(im, battle_box)
    im = _frosted_panel(im, cp_box)
    im = _frosted_panel(im, footer_box)

    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)

    _draw_hero(im, dt, outcome)
    _draw_duel_row(im, dr, dt, outcome, duel_box, my_av, opp_av)

    bx0 = M + INSET
    by0 = y_battle + PANEL_PAD
    _accent_strip(im, battle_box, (156, 39, 176, 255))
    li = min(max(0, outcome.level_index), 4)
    dt.draw(bx0, by0, 19, '本局随机谱面', ACCENT, 'lt', 2, WHITE_STROKE)

    cover_sz = 100
    cy_cover = by0 + 34
    try:
        cover = Image.open(music_picture(outcome.music_id)).convert('RGBA').resize((cover_sz, cover_sz))
        cover = rounded_corners(cover, 12, (True, True, True, True))
        im.alpha_composite(cover, (bx0, cy_cover))
    except Exception:
        dr.rounded_rectangle(
            (bx0, cy_cover, bx0 + cover_sz, cy_cover + cover_sz),
            radius=12, fill=(255, 255, 255, 200), outline=DIFF_COLORS[li], width=2,
        )

    ix = bx0 + cover_sz + 18
    dt.draw(ix, cy_cover + 2, 22, _short_title(outcome.title, 26), TEXT, 'lt', 1, (255, 255, 255, 235))
    pill_w = _draw_chip(dr, dt, ix, cy_cover + 40, outcome.level or '?', DIFF_BG[li])
    _draw_chip(dr, dt, ix + pill_w + 8, cy_cover + 40, outcome.diff_name, (*DIFF_COLORS[li][:3], 45))
    dt.draw(ix, cy_cover + 78, 13, '先比达成率，相同再比 DX', MUTED, 'lt', 1, (255, 255, 255, 200))

    inner_l = M + INSET
    inner_r = W - M - INSET
    card_gap = 16
    card_w = (inner_r - inner_l - card_gap) // 2
    card_y = y_battle + battle_h - PANEL_PAD - card_h
    me_box = (inner_l, card_y, inner_l + card_w, card_y + card_h)
    opp_box = (inner_r - card_w, card_y, inner_r, card_y + card_h)

    dt.draw((inner_l + inner_r) // 2, card_y - 14, 13, '成绩对比', MUTED, 'mm', 1, (255, 255, 255, 195))

    me_win = outcome.winner_side == 'me'
    opp_win = outcome.winner_side == 'opp'
    _draw_score_card(dr, dt, me_box, outcome.my_name or '你', outcome.my_achv, outcome.my_dx, is_winner=me_win)
    _draw_score_card(dr, dt, opp_box, outcome.opp_name, outcome.o_achv, outcome.o_dx, is_winner=opp_win)

    _accent_strip(im, cp_box, (255, 193, 7, 255))
    cpx = M + INSET
    cpy = y_cp + PANEL_PAD
    dt.draw(cpx, cpy, 19, '段位 · CP', ACCENT, 'lt', 2, WHITE_STROKE)
    ly = cpy + 38
    for line in outcome.cp_lines:
        if line.startswith('──'):
            dr.line((cpx, ly + 8, W - M - INSET, ly + 8), fill=(*ACCENT[:3], 50), width=1)
            ly += 18
            continue
        color = TEXT
        if line.startswith('你:') and '胜' in line:
            color = WIN
        elif line.startswith('你:') and '败' in line:
            color = LOSE
        elif line.startswith('对手') and '胜' in line:
            color = WIN
        elif line.startswith('对手') and '败' in line:
            color = LOSE
        elif '升段' in line or '掉段' in line:
            color = ACCENT
        show = line if coloumWidth(line) <= 70 else changeColumnWidth(line, 68) + '…'
        dt.draw(cpx + 4, ly, 15, show, color, 'lt', 1, (255, 255, 255, 225))
        ly += 28

    draw_centered_design_footer(
        im, dt, footer_generated(),
        color=ACCENT,
        margin_x=M + 12,
        start_font_size=14,
        min_font_size=10,
        bottom_gap=24,
    )
    return MessageSegment.image(image_to_base64(im))
