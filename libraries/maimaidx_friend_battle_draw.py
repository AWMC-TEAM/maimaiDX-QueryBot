"""友人对战结果图渲染（头像 + 分区排版）。"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Optional

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw, ImageFilter

from ..config import SHANGGUMONO, SIYUAN, footer_generated, maimaidir
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64, music_picture, rounded_corners
from .maimaidx_api_data import maiApi
from .maimaidx_best_50 import changeColumnWidth, coloumWidth
from .maimaidx_friend_battle import FriendBattleOutcome
from .maimaidx_progress_report import _get_report_bg

W = 1000
M = 40
PAD = 28
FOOTER_ZONE = 58
PANEL_A = 0.48

ACCENT = (124, 129, 255, 255)
TEXT = (45, 50, 95, 255)
SUBTEXT = (90, 95, 140, 255)
MUTED = (120, 126, 145, 255)
WHITE_STROKE = (255, 255, 255, 240)
WIN = (56, 168, 108, 255)
WIN_BG = (220, 245, 228, 235)
LOSE = (220, 88, 98, 255)
LOSE_BG = (255, 232, 234, 235)
TIE = (110, 118, 145, 255)
TIE_BG = (240, 242, 248, 230)

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


def _verdict_palette(side: str) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], str]:
    if side == 'me':
        return WIN, WIN_BG, 'WIN'
    if side == 'opp':
        return LOSE, LOSE_BG, 'LOSE'
    return TIE, TIE_BG, 'DRAW'


def _frosted_panel(im: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    return generate_frosted_card(im, box, alpha=PANEL_A)


def _accent_strip(im: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int, int] = ACCENT) -> None:
    x0, y0, x1, y1 = box
    strip = Image.new('RGBA', (6, y1 - y0), color)
    im.alpha_composite(strip, (x0 + 4, y0 + 8))


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img.convert('RGBA'), (0, 0), mask)
    return out


async def _fetch_avatar(qqid: int, size: int) -> Optional[Image.Image]:
    try:
        raw = await maiApi.qqlogo(qqid=qqid)
        if not raw:
            return None
        return _circle_avatar(Image.open(BytesIO(raw)), size)
    except Exception:
        return None


def _paste_avatar(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
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
        dr.ellipse((x, y, x + size, y + size), fill=(240, 242, 252, 255), outline=ring, width=2)
        dt = DrawText(dr, SIYUAN)
        dt.draw(cx, cy, size // 3, '?', MUTED, 'mm', 1, (255, 255, 255, 200))
    dr.ellipse((x - 3, y - 3, x + size + 3, y + size + 3), outline=(*ring[:3], 200), width=2)


def _draw_chip(dr: ImageDraw.ImageDraw, dt: DrawText, x: int, y: int, text: str, fill: tuple[int, int, int, int]) -> int:
    pad_x = 12
    tw = int(dt.get_box(text, 14)[2] - dt.get_box(text, 14)[0]) + pad_x * 2
    th = 26
    dr.rounded_rectangle((x, y, x + tw, y + th), radius=13, fill=fill, outline=(*ACCENT[:3], 90), width=1)
    dt.draw(x + pad_x, y + 5, 14, text, TEXT, 'lt', 1, (255, 255, 255, 220))
    return tw


def _draw_hero(im: Image.Image, dt: DrawText, outcome: FriendBattleOutcome) -> None:
    h = 100
    banner = Image.new('RGBA', (W - M * 2, h), (0, 0, 0, 0))
    bdr = ImageDraw.Draw(banner)
    for i in range(h):
        t = i / max(h - 1, 1)
        r = int(124 + (193 - 124) * t * 0.35)
        g = int(129 + (210 - 129) * t * 0.35)
        b = int(255 - (255 - 200) * t * 0.2)
        bdr.line([(0, i), (W - M * 2, i)], fill=(r, g, b, 200 if i < h - 8 else 120))
    mask = Image.new('L', (W - M * 2, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - M * 2, h), radius=20, fill=255)
    banner.putalpha(mask)
    im.alpha_composite(banner, (M, 14))

    try:
        from .maimaidx_theme import Theme, resolve_theme_path
        logo = Image.open(resolve_theme_path(maimaidir, Theme.get_default().value, 'logo.png')).convert('RGBA')
        im.alpha_composite(logo.resize((int(249 * 0.5), int(120 * 0.5))), (M + 10, 20))
    except Exception:
        pass

    dt.draw(M + 180, 32, 26, '友人对战', WHITE_STROKE, 'lt', 2, (255, 255, 255, 255))
    dt.draw(M + 180, 58, 14, 'FRIEND BATTLE', (*ACCENT[:3], 200), 'lt', 1, (255, 255, 255, 255))

    v_fg, v_bg, badge = _verdict_palette(outcome.winner_side)
    verdict_text = outcome.verdict
    vw = int(dt.get_box(verdict_text, 24)[2]) + 44
    vx = W - M - vw - 6
    vy = 26
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((vx, vy, vx + vw, vy + 46), radius=22, fill=v_bg, outline=v_fg, width=2)
    dt.draw(vx + 20, vy + 8, 13, badge, v_fg, 'lt', 1, (255, 255, 255, 255))
    show_v = verdict_text if len(verdict_text) <= 12 else verdict_text[:11] + '…'
    dt.draw(vx + 20, vy + 26, 20, show_v, v_fg, 'lt', 1, (255, 255, 255, 255))


def _draw_duel_row(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    dt_sm: DrawText,
    outcome: FriendBattleOutcome,
    box: tuple[int, int, int, int],
    my_av: Optional[Image.Image],
    opp_av: Optional[Image.Image],
) -> None:
    _accent_strip(im, box)
    x0, y0, x1, y1 = box
    inner_l = x0 + PAD
    inner_r = x1 - PAD
    av_sz = 68
    av_cy = y0 + 52 + av_sz // 2
    col_cx_l = inner_l + (inner_r - inner_l) // 4
    col_cx_r = inner_l + (inner_r - inner_l) * 3 // 4
    vs_cx = (inner_l + inner_r) // 2

    dt.draw(inner_l, y0 + PAD, 20, '对战双方', ACCENT, 'lt', 2, WHITE_STROKE)

    my_ring = WIN if outcome.winner_side == 'me' else ACCENT
    opp_ring = WIN if outcome.winner_side == 'opp' else ACCENT
    _paste_avatar(im, dr, my_av, col_cx_l, av_cy, av_sz, ring=my_ring)
    _paste_avatar(im, dr, opp_av, col_cx_r, av_cy, av_sz, ring=opp_ring)

    orb_r = 24
    orb = Image.new('RGBA', (orb_r * 2 + 4, orb_r * 2 + 4), (0, 0, 0, 0))
    od = ImageDraw.Draw(orb)
    od.ellipse((2, 2, orb_r * 2 + 2, orb_r * 2 + 2), fill=ACCENT)
    od.ellipse((7, 7, orb_r * 2 - 3, orb_r * 2 - 3), fill=(255, 255, 255, 235))
    im.alpha_composite(orb, (vs_cx - orb_r - 2, av_cy - orb_r - 2))
    DrawText(ImageDraw.Draw(im), SIYUAN).draw(vs_cx, av_cy, 17, 'VS', WHITE_STROKE, 'mm', 2, (255, 255, 255, 255))

    name_y = av_cy + av_sz // 2 + 14
    my_label = _short_name(outcome.my_name or '你', 12)
    opp_label = _short_name(outcome.opp_name, 12)
    dt.draw(col_cx_l, name_y, 17, my_label, TEXT, 'mm', 1, (255, 255, 255, 235))
    dt.draw(col_cx_r, name_y, 17, opp_label, TEXT, 'mm', 1, (255, 255, 255, 235))
    dt_sm.draw(col_cx_l, name_y + 24, 15, str(outcome.my_rating), WIN, 'mm', 1, (255, 255, 255, 225))
    dt_sm.draw(col_cx_r, name_y + 24, 15, str(outcome.opp_rating), LOSE, 'mm', 1, (255, 255, 255, 225))

    chip_y = name_y + 48
    cx = inner_l
    cw = _draw_chip(dr, dt_sm, cx, chip_y, outcome.used_pool, (235, 238, 255, 255))
    _draw_chip(
        dr, dt_sm, cx + cw + 8, chip_y,
        f'|Δrating| {outcome.rating_delta}  ·  允许 ±{outcome.rating_limit}',
        (225, 228, 255, 255),
    )
    dt_sm.draw(inner_l, y1 - PAD - 4, 14, f'段位关系 · {outcome.rel_zh}', MUTED, 'lb', 1, (255, 255, 255, 200))


def _draw_score_card(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    dt_sm: DrawText,
    box: tuple[int, int, int, int],
    label: str,
    achv: float,
    dx: int,
    avatar: Optional[Image.Image],
    *,
    is_winner: bool,
) -> None:
    x0, y0, x1, y1 = box
    fg, bg, badge = _verdict_palette('me' if is_winner else 'tie')
    if not is_winner:
        fg, bg = SUBTEXT, (255, 255, 255, 215)

    if is_winner:
        glow = Image.new('RGBA', (x1 - x0 + 10, y1 - y0 + 10), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((0, 0, x1 - x0 + 10, y1 - y0 + 10), radius=18, fill=(*WIN[:3], 45))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)), (x0 - 5, y0 - 5))
        dr.rounded_rectangle((x0, y0, x1, y1), radius=16, fill=bg, outline=WIN, width=2)
    else:
        dr.rounded_rectangle((x0, y0, x1, y1), radius=16, fill=bg, outline=(*ACCENT[:3], 70), width=1)

    av_sz = 44
    av_cx = x0 + 16 + av_sz // 2
    av_cy = (y0 + y1) // 2
    _paste_avatar(im, dr, avatar, av_cx, av_cy, av_sz, ring=fg if is_winner else ACCENT)

    text_x = x0 + 16 + av_sz + 10
    if is_winner:
        _draw_chip(dr, dt_sm, text_x, y0 + 8, badge, (*fg[:3], 55))
    dt.draw(text_x, y0 + (34 if is_winner else 12), 16, _short_name(label, 10), TEXT, 'lt', 1, (255, 255, 255, 230))
    dt.draw(x1 - 14, (y0 + y1) // 2 - 8, 24, f'{achv:.4f}%', fg if is_winner else TEXT, 'rt', 2, (255, 255, 255, 255))
    dt_sm.draw(x1 - 14, (y0 + y1) // 2 + 22, 14, f'DX {dx}', SUBTEXT, 'rt', 1, (255, 255, 255, 210))


async def draw_friend_battle_image(outcome: FriendBattleOutcome) -> MessageSegment:
    cp_line_h = 22
    cp_inner = max(80, 36 + len(outcome.cp_lines) * cp_line_h)
    hero_h = 118
    duel_h = 218
    battle_h = 318
    gap = 12

    y_duel = hero_h
    y_battle = y_duel + duel_h + gap
    y_cp = y_battle + battle_h + gap
    y_footer = y_cp + cp_inner + gap
    height = y_footer + FOOTER_ZONE

    duel_box = (M, y_duel, W - M, y_duel + duel_h)
    battle_box = (M, y_battle, W - M, y_battle + battle_h)
    cp_box = (M, y_cp, W - M, y_cp + cp_inner)
    footer_box = (M, y_footer, W - M, height - 8)

    my_av, opp_av = await asyncio.gather(
        _fetch_avatar(outcome.my_qq, 68),
        _fetch_avatar(outcome.opp_qq, 68),
    )
    my_av_sm = my_av.resize((44, 44), Image.Resampling.LANCZOS) if my_av else None
    opp_av_sm = opp_av.resize((44, 44), Image.Resampling.LANCZOS) if opp_av else None

    im = _get_report_bg(W, height)
    im = _frosted_panel(im, duel_box)
    im = _frosted_panel(im, battle_box)
    im = _frosted_panel(im, cp_box)
    im = _frosted_panel(im, footer_box)

    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)
    dt_sm = DrawText(dr, SHANGGUMONO)

    _draw_hero(im, dt, outcome)
    _draw_duel_row(im, dr, dt, dt_sm, outcome, duel_box, my_av, opp_av)

    # ── 谱面 + 成绩 ──
    _accent_strip(im, battle_box, (156, 39, 176, 255))
    bx0, by0 = M + PAD, y_battle + PAD
    li = min(max(0, outcome.level_index), 4)
    dt.draw(bx0, by0, 20, '本局随机谱面', ACCENT, 'lt', 2, WHITE_STROKE)

    cover_sz = 108
    cy_cover = by0 + 32
    try:
        cover = Image.open(music_picture(outcome.music_id)).convert('RGBA').resize((cover_sz, cover_sz))
        cover = rounded_corners(cover, 14, (True, True, True, True))
        frame = Image.new('RGBA', (cover_sz + 6, cover_sz + 6), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rounded_rectangle(
            (0, 0, cover_sz + 6, cover_sz + 6), radius=16, fill=(*DIFF_COLORS[li][:3], 210),
        )
        im.alpha_composite(frame, (bx0 - 3, cy_cover - 3))
        im.alpha_composite(cover, (bx0, cy_cover))
    except Exception:
        dr.rounded_rectangle(
            (bx0, cy_cover, bx0 + cover_sz, cy_cover + cover_sz),
            radius=14, fill=(255, 255, 255, 180), outline=DIFF_COLORS[li], width=2,
        )

    ix = bx0 + cover_sz + 20
    dt.draw(ix, cy_cover + 4, 24, _short_title(outcome.title, 26), TEXT, 'lt', 1, (255, 255, 255, 235))
    pill_w = _draw_chip(dr, dt_sm, ix, cy_cover + 44, outcome.level or '?', DIFF_BG[li])
    _draw_chip(dr, dt_sm, ix + pill_w + 8, cy_cover + 44, outcome.diff_name, (*DIFF_COLORS[li][:3], 50))
    dt_sm.draw(ix, cy_cover + 78, 14, '先比达成率，相同再比 DX', MUTED, 'lt', 1, (255, 255, 255, 195))

    card_y = y_battle + battle_h - PAD - 128
    inner_w = W - M * 2 - PAD * 2
    vs_slot = 48
    card_w = (inner_w - vs_slot) // 2
    me_box = (bx0, card_y, bx0 + card_w, card_y + 120)
    opp_x0 = bx0 + card_w + vs_slot
    opp_box = (opp_x0, card_y, opp_x0 + card_w, card_y + 120)
    me_win = outcome.winner_side == 'me'
    opp_win = outcome.winner_side == 'opp'

    _draw_score_card(
        im, dr, dt, dt_sm, me_box,
        outcome.my_name or '你', outcome.my_achv, outcome.my_dx, my_av_sm,
        is_winner=me_win,
    )
    _draw_score_card(
        im, dr, dt, dt_sm, opp_box,
        outcome.opp_name, outcome.o_achv, outcome.o_dx, opp_av_sm,
        is_winner=opp_win,
    )

    vs_cx = bx0 + card_w + vs_slot // 2
    vs_cy = card_y + 60
    orb_r = 22
    orb = Image.new('RGBA', (orb_r * 2 + 4, orb_r * 2 + 4), (0, 0, 0, 0))
    od = ImageDraw.Draw(orb)
    od.ellipse((2, 2, orb_r * 2 + 2, orb_r * 2 + 2), fill=ACCENT)
    im.alpha_composite(orb, (vs_cx - orb_r - 2, vs_cy - orb_r - 2))
    DrawText(ImageDraw.Draw(im), SIYUAN).draw(vs_cx, vs_cy, 16, 'VS', WHITE_STROKE, 'mm', 2, (255, 255, 255, 255))

    # ── CP ──
    _accent_strip(im, cp_box, (255, 193, 7, 255))
    cpx, cpy = M + PAD, y_cp + PAD
    dt.draw(cpx, cpy, 20, '段位 · CP', ACCENT, 'lt', 2, WHITE_STROKE)
    ly = cpy + 36
    for line in outcome.cp_lines:
        if line.startswith('──'):
            dr.line((cpx, ly + 6, W - M - PAD, ly + 6), fill=(*ACCENT[:3], 55), width=1)
            ly += 10
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
        show = line if coloumWidth(line) <= 72 else changeColumnWidth(line, 70) + '…'
        dt_sm.draw(cpx + 6, ly, 15, show, color, 'lt', 1, (255, 255, 255, 220))
        ly += cp_line_h

    draw_centered_design_footer(
        im, dt_sm, footer_generated(),
        color=ACCENT,
        margin_x=M + 8,
        start_font_size=15,
        min_font_size=10,
        bottom_gap=22,
    )
    return MessageSegment.image(image_to_base64(im))
