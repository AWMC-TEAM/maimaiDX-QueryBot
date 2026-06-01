"""友人对战结果图渲染（排版美化版）。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw, ImageFilter

from ..config import SHANGGUMONO, SIYUAN, footer_generated, maimaidir
from .image import DrawText, draw_centered_design_footer, generate_frosted_card, image_to_base64, music_picture, rounded_corners
from .maimaidx_best_50 import changeColumnWidth, coloumWidth
from .maimaidx_friend_battle import FriendBattleOutcome
from .maimaidx_progress_report import _get_report_bg

W = 1000
M = 40
FOOTER_H = 52
PANEL_A = 0.48

ACCENT = (124, 129, 255, 255)
ACCENT_SOFT = (124, 129, 255, 90)
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


def _short_title(title: str, max_w: int = 26) -> str:
    if coloumWidth(title) <= max_w:
        return title
    return changeColumnWidth(title, max_w - 2) + '…'


def _short_name(name: str, max_w: int = 12) -> str:
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


def _draw_chip(dr: ImageDraw.ImageDraw, dt: DrawText, x: int, y: int, text: str, fill: tuple[int, int, int, int]) -> int:
    """圆角标签，返回标签宽度。"""
    pad_x, pad_y = 12, 6
    tw = int(dt.get_box(text, 15)[2] - dt.get_box(text, 15)[0]) + pad_x * 2
    th = 28
    dr.rounded_rectangle((x, y, x + tw, y + th), radius=14, fill=fill, outline=(*ACCENT[:3], 100), width=1)
    dt.draw(x + pad_x, y + pad_y + 1, 15, text, TEXT, 'lt', 1, (255, 255, 255, 220))
    return tw


def _draw_hero_banner(im: Image.Image, dt: DrawText, outcome: FriendBattleOutcome) -> int:
    """顶部横幅，返回占用高度。"""
    h = 108
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
    im.alpha_composite(banner, (M, 16))

    try:
        from .maimaidx_theme import Theme, resolve_theme_path
        logo = Image.open(resolve_theme_path(maimaidir, Theme.get_default().value, 'logo.png')).convert('RGBA')
        im.alpha_composite(logo.resize((int(249 * 0.55), int(120 * 0.55))), (M + 12, 22))
    except Exception:
        pass

    dt.draw(M + 200, 36, 26, '友人对战', WHITE_STROKE, 'lt', 2, (255, 255, 255, 255))
    dt.draw(M + 200, 64, 15, 'FRIEND BATTLE', (*ACCENT[:3], 180), 'lt', 1, (255, 255, 255, 255))

    v_fg, v_bg, badge = _verdict_palette(outcome.winner_side)
    verdict_text = outcome.verdict
    vw = int(dt.get_box(verdict_text, 28)[2]) + 48
    vx = W - M - vw - 8
    vy = 30
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((vx, vy, vx + vw, vy + 48), radius=24, fill=v_bg, outline=v_fg, width=2)
    dt.draw(vx + 24, vy + 10, 14, badge, v_fg, 'lt', 1, (255, 255, 255, 255))
    dt.draw(vx + 24, vy + 28, 22, verdict_text if len(verdict_text) <= 14 else verdict_text[:13] + '…', v_fg, 'lt', 1, (255, 255, 255, 255))
    return h + 24


def _draw_rating_compare(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    x: int,
    y: int,
    w: int,
    my_ra: int,
    opp_ra: int,
    my_label: str,
    opp_label: str,
) -> None:
    bar_h = 14
    mid = (my_ra + opp_ra) // 2 or my_ra
    span = max(abs(my_ra - mid), abs(opp_ra - mid), 1) * 2 + 200
    lo = mid - span // 2
    hi = mid + span // 2

    def pos(ra: int) -> int:
        return x + int((ra - lo) / max(hi - lo, 1) * w)

    dr.rounded_rectangle((x, y + 28, x + w, y + 28 + bar_h), radius=7, fill=(230, 232, 245, 255))
    px_me, px_opp = pos(my_ra), pos(opp_ra)
    left, right = min(px_me, px_opp), max(px_me, px_opp)
    dr.rounded_rectangle((left, y + 28, right, y + 28 + bar_h), radius=7, fill=(*ACCENT[:3], 140))
    dr.ellipse((px_me - 7, y + 28 + bar_h // 2 - 7, px_me + 7, y + 28 + bar_h // 2 + 7), fill=WIN)
    dr.ellipse((px_opp - 7, y + 28 + bar_h // 2 - 7, px_opp + 7, y + 28 + bar_h // 2 + 7), fill=LOSE)

    dt.draw(x, y, 17, my_label, TEXT, 'lt', 1, (255, 255, 255, 225))
    dt.draw(x + w, y, 17, opp_label, TEXT, 'rt', 1, (255, 255, 255, 225))
    dt.draw(px_me, y + 48, 15, str(my_ra), WIN, 'mm', 1, (255, 255, 255, 230))
    dt.draw(px_opp, y + 48, 15, str(opp_ra), LOSE, 'mm', 1, (255, 255, 255, 230))


def _draw_score_card(
    im: Image.Image,
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    box: tuple[int, int, int, int],
    label: str,
    achv: float,
    dx: int,
    *,
    is_winner: bool,
    side: str,
) -> None:
    x0, y0, x1, y1 = box
    fg, bg, _ = _verdict_palette('me' if is_winner else ('opp' if side == 'opp' else 'tie'))
    if not is_winner:
        fg, bg = (SUBTEXT if side == 'me' else SUBTEXT), (255, 255, 255, 210)

    if is_winner:
        glow = Image.new('RGBA', (x1 - x0 + 8, y1 - y0 + 8), (0, 0, 0, 0))
        gdr = ImageDraw.Draw(glow)
        gdr.rounded_rectangle((0, 0, x1 - x0 + 8, y1 - y0 + 8), radius=18, fill=(*fg[:3], 40))
        glow = glow.filter(ImageFilter.GaussianBlur(6))
        im.alpha_composite(glow, (x0 - 4, y0 - 4))
        dr.rounded_rectangle((x0, y0, x1, y1), radius=16, fill=bg, outline=fg, width=3)
        _draw_chip(dr, dt, x0 + 12, y0 + 10, 'WIN', (*fg[:3], 50))
    else:
        dr.rounded_rectangle((x0, y0, x1, y1), radius=16, fill=(255, 255, 255, 215), outline=(*ACCENT[:3], 80), width=1)

    dt.draw(x0 + 16, y0 + (44 if is_winner else 16), 18, _short_name(label, 14), TEXT, 'lt', 1, (255, 255, 255, 225))
    dt.draw((x0 + x1) // 2, y0 + (78 if is_winner else 50), 28, f'{achv:.4f}%', fg if is_winner else TEXT, 'mm', 2, (255, 255, 255, 255))
    dt.draw((x0 + x1) // 2, y0 + (112 if is_winner else 84), 16, f'DX  {dx}', SUBTEXT, 'mm', 1, (255, 255, 255, 210))


def _draw_vs_orb(im: Image.Image, cx: int, cy: int, r: int = 28) -> None:
    orb = Image.new('RGBA', (r * 2 + 4, r * 2 + 4), (0, 0, 0, 0))
    od = ImageDraw.Draw(orb)
    od.ellipse((2, 2, r * 2 + 2, r * 2 + 2), fill=ACCENT)
    od.ellipse((8, 8, r * 2 - 4, r * 2 - 4), fill=(255, 255, 255, 230))
    im.alpha_composite(orb, (cx - r - 2, cy - r - 2))
    dt = DrawText(ImageDraw.Draw(im), SIYUAN)
    dt.draw(cx, cy, 20, 'VS', WHITE_STROKE, 'mm', 2, (255, 255, 255, 255))


def draw_friend_battle_image(outcome: FriendBattleOutcome) -> MessageSegment:
    cp_line_h = 24
    cp_inner = max(72, 40 + len(outcome.cp_lines) * cp_line_h)
    hero_h = 132
    match_h = 168
    song_h = 392
    height = hero_h + match_h + song_h + cp_inner + FOOTER_H + 28

    im = _get_report_bg(W, height)
    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)
    dt_sm = DrawText(dr, SHANGGUMONO)

    y = _draw_hero_banner(im, dt, outcome)

    # ── 匹配面板 ──
    match_box = (M, y, W - M, y + match_h)
    im = _frosted_panel(im, match_box)
    _accent_strip(im, match_box)
    mx, my0 = M + 28, y + 20
    dt.draw(mx, my0, 22, '匹配与水平', ACCENT, 'lt', 2, WHITE_STROKE)
    cx = mx
    cy = my0 + 38
    cw = _draw_chip(dr, dt_sm, cx, cy, outcome.used_pool, (235, 238, 255, 255))
    _draw_chip(
        dr, dt_sm, cx + cw + 10, cy,
        f'|Δ| {outcome.rating_delta}  /  ±{outcome.rating_limit}',
        (225, 228, 255, 255),
    )
    bar_w = W - M * 2 - 56
    _draw_rating_compare(
        dr, dt_sm, mx, cy + 44, bar_w,
        outcome.my_rating, outcome.opp_rating,
        f'你  {outcome.my_rating}',
        f'{_short_name(outcome.opp_name, 16)}  {outcome.opp_rating}',
    )
    dt_sm.draw(mx, cy + 100, 16, f'段位关系 · {outcome.rel_zh}', MUTED, 'lt', 1, (255, 255, 255, 200))
    y += match_h + 14

    # ── 谱面与成绩 ──
    song_box = (M, y, W - M, y + song_h)
    im = _frosted_panel(im, song_box)
    _accent_strip(im, song_box, (156, 39, 176, 255))
    sx, sy0 = M + 28, y + 20
    li = min(max(0, outcome.level_index), 4)
    dt.draw(sx, sy0, 22, '随机谱面', ACCENT, 'lt', 2, WHITE_STROKE)

    cover_sz = 132
    cy_cover = sy0 + 36
    try:
        cover = Image.open(music_picture(outcome.music_id)).convert('RGBA').resize((cover_sz, cover_sz))
        cover = rounded_corners(cover, 16, (True, True, True, True))
        frame = Image.new('RGBA', (cover_sz + 8, cover_sz + 8), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rounded_rectangle(
            (0, 0, cover_sz + 8, cover_sz + 8), radius=18, fill=(*DIFF_COLORS[li][:3], 200),
        )
        im.alpha_composite(frame, (sx - 4, cy_cover - 4))
        im.alpha_composite(cover, (sx, cy_cover))
    except Exception:
        dr.rounded_rectangle(
            (sx, cy_cover, sx + cover_sz, cy_cover + cover_sz),
            radius=16, fill=(255, 255, 255, 180), outline=DIFF_COLORS[li], width=2,
        )

    ix = sx + cover_sz + 24
    dt.draw(ix, cy_cover + 8, 26, _short_title(outcome.title, 24), TEXT, 'lt', 1, (255, 255, 255, 235))
    pill_w = _draw_chip(dr, dt_sm, ix, cy_cover + 48, outcome.level, DIFF_BG[li])
    _draw_chip(dr, dt_sm, ix + pill_w + 8, cy_cover + 48, outcome.diff_name, (*DIFF_COLORS[li][:3], 45))
    dt_sm.draw(ix, cy_cover + 88, 14, '先比达成率 · 相同再比 DX', MUTED, 'lt', 1, (255, 255, 255, 195))

    card_y = sy0 + 36 + cover_sz + 18
    inner_w = W - M * 2 - 56
    vs_slot = 52
    card_w = (inner_w - vs_slot) // 2
    me_box = (sx, card_y, sx + card_w, card_y + 130)
    opp_x0 = sx + card_w + vs_slot
    opp_box = (opp_x0, card_y, opp_x0 + card_w, card_y + 130)
    vs_cy = card_y + 65

    me_win = outcome.winner_side == 'me'
    opp_win = outcome.winner_side == 'opp'
    _draw_score_card(
        im, dr, dt, me_box, '你', outcome.my_achv, outcome.my_dx,
        is_winner=me_win, side='me',
    )
    _draw_score_card(
        im, dr, dt, opp_box, outcome.opp_name, outcome.o_achv, outcome.o_dx,
        is_winner=opp_win, side='opp',
    )
    _draw_vs_orb(im, sx + card_w + vs_slot // 2, vs_cy)

    y += song_h + 14

    # ── CP 面板 ──
    cp_box = (M, y, W - M, y + cp_inner)
    im = _frosted_panel(im, cp_box)
    _accent_strip(im, cp_box, (255, 193, 7, 255))
    cpx, cpy = M + 28, y + 20
    dt.draw(cpx, cpy, 22, '段位 · CP', ACCENT, 'lt', 2, WHITE_STROKE)
    ly = cpy + 40
    for line in outcome.cp_lines:
        if line.startswith('──'):
            dr.line((cpx, ly + 8, W - M - 28, ly + 8), fill=(*ACCENT[:3], 60), width=1)
            ly += 12
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
        dt_sm.draw(cpx + 8, ly, 16, show, color, 'lt', 1, (255, 255, 255, 215))
        ly += cp_line_h

    draw_centered_design_footer(
        im, dt_sm, footer_generated(),
        color=SUBTEXT,
        margin_x=M,
        start_font_size=14,
        min_font_size=9,
        bottom_gap=18,
    )
    return MessageSegment.image(image_to_base64(im))
