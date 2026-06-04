"""友人对战连战（N 连）结果图，与单局友人对战界面分离。"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw

from ..config import SIYUAN, footer_generated, maimaidir
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64
from .maimaidx_api_data import maiApi
from .maimaidx_timing import measure
from .maimaidx_best_50 import changeColumnWidth, coloumWidth
from .maimaidx_friend_battle import FriendBattleBatchOutcome
from .maimaidx_friend_battle_class import TIER_RULES
from .maimaidx_progress_report import _get_report_bg

W = 1000
M = 40
INSET = 24
PANEL_PAD = 20
GAP = 14
FOOTER_ZONE = 56
PANEL_A = 0.46
ROW_H = 42

RUSH = (255, 152, 64, 255)
RUSH_BG = (255, 243, 230, 255)
TEXT = (55, 48, 42, 255)
SUBTEXT = (110, 98, 88, 255)
MUTED = (130, 120, 112, 255)
WHITE_STROKE = (255, 255, 255, 240)
WIN = (46, 125, 82, 255)
WIN_BG = (228, 248, 236, 245)
LOSE = (198, 72, 82, 255)
LOSE_BG = (255, 236, 238, 245)
TIE = (90, 98, 130, 255)
TIE_BG = (242, 244, 250, 245)


def _short_title(title: str, max_w: int = 22) -> str:
    if coloumWidth(title) <= max_w:
        return title
    return changeColumnWidth(title, max_w - 2) + '…'


def _short_name(name: str, max_w: int = 10) -> str:
    if coloumWidth(name) <= max_w:
        return name
    return changeColumnWidth(name, max_w - 2) + '…'


def _frosted_panel(im: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    return generate_frosted_card(im, box, alpha=PANEL_A)


def _accent_strip(im: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int, int] = RUSH) -> None:
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


def _tier_line(idx: int, cp: int) -> str:
    rule = TIER_RULES[idx]
    if rule.cp_to_next is None:
        return f'{rule.name}  CP {cp}'
    return f'{rule.name}  CP {cp}/{rule.cp_to_next}'


def _batch_label(n: int) -> str:
    labels = {2: '二连', 3: '三连', 5: '五连', 10: '十连', 20: '二十连'}
    return labels.get(n, f'{n}连')


def _draw_rush_hero(im: Image.Image, dt: DrawText, batch: FriendBattleBatchOutcome) -> None:
    h = 100
    banner = Image.new('RGBA', (W - M * 2, h), (0, 0, 0, 0))
    bdr = ImageDraw.Draw(banner)
    for i in range(h):
        t = i / max(h - 1, 1)
        r = int(255 - 40 * t)
        g = int(178 - 30 * t)
        b = int(90 + 40 * t)
        bdr.line([(0, i), (W - M * 2, i)], fill=(r, g, b, 215))
    mask = Image.new('L', (W - M * 2, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - M * 2, h), radius=18, fill=255)
    banner.putalpha(mask)
    im.alpha_composite(banner, (M, 12))

    try:
        from .maimaidx_theme import Theme, resolve_theme_path
        logo = Image.open(resolve_theme_path(maimaidir, Theme.get_default().value, 'logo.png')).convert('RGBA')
        im.alpha_composite(logo.resize((int(249 * 0.44), int(120 * 0.44))), (M + 8, 20))
    except Exception:
        pass

    label = _batch_label(batch.requested)
    dt.draw(M + 168, 26, 26, f'友人对战 · {label}', TEXT, 'lt', 2, (255, 255, 255, 255))
    sub = f'FRIEND BATTLE RUSH · {batch.completed}/{batch.requested} 局'
    if batch.skipped:
        sub += f' · 跳过 {batch.skipped}'
    if batch.rating_cap is not None:
        sub += f' · ±{batch.rating_cap}'
    dt.draw(M + 168, 56, 12, sub, (255, 255, 255, 230), 'lt', 1, (255, 255, 255, 200))


def _draw_stat_boxes(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    box: tuple[int, int, int, int],
    batch: FriendBattleBatchOutcome,
) -> None:
    _accent_strip(im, box)
    x0, y0, x1, y1 = box
    inner_l, inner_r = x0 + INSET, x1 - INSET
    dt.draw(inner_l, y0 + PANEL_PAD, 19, '本批战绩', RUSH, 'lt', 2, WHITE_STROKE)

    total = max(1, batch.wins + batch.losses + batch.ties)
    rate = batch.wins / total * 100.0
    gap = 12
    cell_w = (inner_r - inner_l - gap * 3) // 4
    cy = y0 + PANEL_PAD + 44
    ch = y1 - cy - PANEL_PAD
    stats = [
        ('胜', str(batch.wins), WIN, WIN_BG),
        ('负', str(batch.losses), LOSE, LOSE_BG),
        ('平', str(batch.ties), TIE, TIE_BG),
        ('胜率', f'{rate:.0f}%', RUSH, RUSH_BG),
    ]
    for i, (lab, val, fg, bg) in enumerate(stats):
        cx = inner_l + i * (cell_w + gap)
        dr.rounded_rectangle((cx, cy, cx + cell_w, cy + ch), radius=14, fill=bg, outline=fg, width=2)
        dt.draw(cx + cell_w // 2, cy + ch // 2 - 14, 22, val, fg, 'mm', 1, (255, 255, 255, 255))
        dt.draw(cx + cell_w // 2, cy + ch // 2 + 18, 13, lab, SUBTEXT, 'mm', 1, (255, 255, 255, 220))


def _draw_tier_panel(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    im: Image.Image,
    box: tuple[int, int, int, int],
    batch: FriendBattleBatchOutcome,
    avatar: Optional[Image.Image],
) -> None:
    _accent_strip(im, box, (255, 193, 7, 255))
    x0, y0, x1, y1 = box
    inner_l = x0 + INSET
    av_sz = 56
    av_y = y0 + PANEL_PAD + 36
    if avatar is not None:
        im.alpha_composite(avatar, (inner_l, av_y))
    else:
        dr.ellipse(
            (inner_l, av_y, inner_l + av_sz, av_y + av_sz),
            fill=(255, 255, 255, 230),
            outline=RUSH,
            width=2,
        )

    tx = inner_l + av_sz + 18
    dt.draw(tx, y0 + PANEL_PAD, 19, _short_name(batch.my_name, 14), TEXT, 'lt', 2, WHITE_STROKE)
    start_s = _tier_line(batch.tier_start_idx, batch.tier_start_cp)
    end_s = _tier_line(batch.tier_end_idx, batch.tier_end_cp)
    dt.draw(tx, y0 + PANEL_PAD + 34, 15, f'段位  {start_s}', SUBTEXT, 'lt', 1, (255, 255, 255, 220))
    dt.draw(tx, y0 + PANEL_PAD + 58, 16, f'→  {end_s}', TEXT, 'lt', 1, (255, 255, 255, 235))
    streak = batch.end_streak
    if streak:
        dt.draw(tx, y0 + PANEL_PAD + 84, 14, f'当前友対连胜 {streak}', MUTED, 'lt', 1, (255, 255, 255, 210))


def _result_badge(dr: ImageDraw.ImageDraw, dt: DrawText, x: int, y: int, side: str) -> None:
    if side == 'me':
        text, fg, bg = '胜', WIN, WIN_BG
    elif side == 'opp':
        text, fg, bg = '负', LOSE, LOSE_BG
    else:
        text, fg, bg = '平', TIE, TIE_BG
    bw, bh = 36, 26
    dr.rounded_rectangle((x, y, x + bw, y + bh), radius=10, fill=bg, outline=fg, width=2)
    dt.draw(x + bw // 2, y + bh // 2, 14, text, fg, 'mm', 1, (255, 255, 255, 255))


def _draw_rounds_list(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    im: Image.Image,
    box: tuple[int, int, int, int],
    batch: FriendBattleBatchOutcome,
) -> None:
    _accent_strip(im, box)
    x0, y0, x1, y1 = box
    inner_l, inner_r = x0 + INSET, x1 - INSET
    dt.draw(inner_l, y0 + PANEL_PAD, 19, '逐局明细', RUSH, 'lt', 2, WHITE_STROKE)
    ly = y0 + PANEL_PAD + 38
    for r in batch.rounds:
        if ly + ROW_H > y1 - PANEL_PAD:
            remain = len(batch.rounds) - (r.round_no - 1)
            dt.draw(inner_l, ly + 4, 13, f'… 其余 {remain} 局略', MUTED, 'lt', 1, (255, 255, 255, 200))
            break
        if r.round_no % 2 == 0:
            dr.rounded_rectangle(
                (inner_l, ly, inner_r, ly + ROW_H - 4),
                radius=10,
                fill=(255, 255, 255, 120),
            )
        _result_badge(dr, dt, inner_l + 4, ly + 8, r.winner_side)
        dt.draw(inner_l + 48, ly + 6, 14, f'#{r.round_no}', MUTED, 'lt', 1, (255, 255, 255, 210))
        song = f'{_short_title(r.title)} · {r.diff_name}'
        dt.draw(inner_l + 88, ly + 6, 15, song, TEXT, 'lt', 1, (255, 255, 255, 230))
        vs = f'vs {_short_name(r.opp_name)}  {r.my_achv:.4f}% / {r.o_achv:.4f}%'
        dt.draw(inner_l + 88, ly + 24, 13, vs, SUBTEXT, 'lt', 1, (255, 255, 255, 200))
        ly += ROW_H


async def draw_friend_battle_batch_image(batch: FriendBattleBatchOutcome) -> MessageSegment:
    visible_rows = min(len(batch.rounds), 20)
    list_h = PANEL_PAD * 2 + 38 + visible_rows * ROW_H + 8
    if len(batch.rounds) > visible_rows:
        list_h += 28

    hero_h = 112
    stat_h = 150
    tier_h = 130
    y_stat = hero_h
    y_tier = y_stat + stat_h + GAP
    y_list = y_tier + tier_h + GAP
    y_footer = y_list + list_h + GAP
    height = y_footer + FOOTER_ZONE

    stat_box = (M, y_stat, W - M, y_stat + stat_h)
    tier_box = (M, y_tier, W - M, y_tier + tier_h)
    list_box = (M, y_list, W - M, y_list + list_h)
    footer_box = (M, y_footer, W - M, height - 6)

    avatar = await _fetch_avatar(batch.my_qq, 56)

    im = _get_report_bg(W, height)
    im = _frosted_panel(im, stat_box)
    im = _frosted_panel(im, tier_box)
    im = _frosted_panel(im, list_box)
    im = _frosted_panel(im, footer_box)

    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)

    _draw_rush_hero(im, dt, batch)
    _draw_stat_boxes(im, dr, dt, stat_box, batch)
    _draw_tier_panel(dr, dt, im, tier_box, batch, avatar)
    _draw_rounds_list(dr, dt, im, list_box, batch)

    draw_centered_design_footer(
        im, dt, footer_generated(),
        color=RUSH,
        margin_x=M + 12,
        start_font_size=14,
        min_font_size=10,
        bottom_gap=24,
    )
    return MessageSegment.image(image_to_base64(im))
