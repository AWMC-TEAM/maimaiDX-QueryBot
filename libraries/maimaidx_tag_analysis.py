"""
底力分析：根据用户 B50 曲目列表统计配置/难度/评价标签并绘图。

- 支持自定义背景：配置 maimaidx_tag_analysis_bg 时使用指定图片，未配置时使用 B50 背景。
- 绘图内容：配置标签雷达图、难度条形图、评价雷达图及底部署名。
"""
import math
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw

from ..config import SIYUAN, TAG_PILL_COLORS, footer_generated, maiconfig, static
from .image import DrawText, generate_frosted_card, image_to_base64

CONFIG_TAGS_ORDER: List[str] = [
    '交互', '散打', '扫键', '绝赞段', '转圈', '大位移', '定拍', '拆弹',
    '爆发', '纵连', '跳拍', '错位', '一笔画', '反手',
]
DIFFICULTY_TAGS_ORDER: List[str] = ['正常谱', '水', '诈称谱']
EVAL_TAGS_ORDER: List[str] = ['体力谱', '底力谱', '星星谱', '键盘谱', '高物量']

ACCENT = (124, 129, 255, 255)
TEXT = (45, 50, 95, 255)
SUBTEXT = (90, 95, 140, 255)
GRID = (124, 129, 255, 70)
GRID_AXIS = (124, 129, 255, 110)

# 条形图：不透明高饱和色 + 白底轨道，避免在毛玻璃上发灰
DIFF_BAR_COLORS: Dict[str, Tuple[int, int, int]] = {
    '正常谱': (100, 190, 120),
    '水': (140, 110, 210),
    '诈称谱': (240, 120, 140),
}

RENDER_SCALE = 2
FONT_SIZE_TITLE = 18
FONT_SIZE_TEXT = 14
FONT_SIZE_BAR_VAL = 15

GENWAN_FONT_NAMES = [
    'GenSenRounded-TW-Regular.ttf', 'GenSenRounded.ttf',
    'GenSenRounded-TW-Medium.ttf', '源泉圓體.ttf', 'GenwanRounded-Regular.otf',
]

PANEL_W = 420
PANEL_H = 380
PANEL_GAP = 20
SIDE_MARGIN = 24


def _section_style(name: str) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]:
    rgb = TAG_PILL_COLORS.get(name, (173, 216, 230))
    fill = (*rgb, 175)
    stroke = (*tuple(min(255, c + 25) for c in rgb), 255)
    return fill, stroke


def _resolve_bg_path() -> Path | None:
    custom = maiconfig.maimaidx_tag_analysis_bg
    if custom:
        p = Path(custom)
        if not p.is_absolute():
            p = static / custom
        if p.is_file():
            return p
    try:
        from .maimaidx_theme import Theme, resolve_theme_path
        from ..config import maimaidir

        p = resolve_theme_path(maimaidir, Theme.get_default().value, 'b50_bg.png')
        return p if p.exists() else None
    except Exception:
        return None


def _get_analysis_bg(width: int, height: int) -> Image.Image:
    im = Image.new('RGBA', (width, height), (245, 247, 255, 255))
    bg_path = _resolve_bg_path()
    if not bg_path:
        return im
    bg = Image.open(bg_path).convert('RGBA')
    bg_w, bg_h = bg.size
    for j in range(height // bg_h + 1):
        im.alpha_composite(bg, (0, j * bg_h))
    if width > bg_w:
        strip = bg.crop((bg_w - 1, 0, bg_w, bg_h))
        for i in range(width - bg_w):
            for j in range(height // bg_h + 1):
                im.alpha_composite(strip, (bg_w + i, j * bg_h))
    return im


def _font_path_for_analysis() -> str:
    static_dir = SIYUAN.parent
    for name in GENWAN_FONT_NAMES:
        p = static_dir / name
        if p.exists():
            return str(p)
    return str(SIYUAN)


def _draw_panel(im: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
    # 略提高不透明度，图表底色更干净
    return generate_frosted_card(im, (x, y, x + w, y + h), alpha=0.52)


def _draw_radar(
    dr: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    radius: int,
    labels: List[str],
    values: List[float],
    draw_text: DrawText,
    title: str,
    title_y: int,
    font_size: int,
    title_size: int,
    fill_color: Tuple[int, int, int, int],
    line_color: Tuple[int, int, int, int],
) -> None:
    n = len(labels)
    if n == 0:
        return

    for r_frac in (1 / 3, 2 / 3, 1.0):
        r = int(radius * r_frac)
        pts = [
            (cx + r * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
             cy + r * math.sin(-math.pi / 2 + 2 * math.pi * i / n))
            for i in range(n + 1)
        ]
        dr.line(pts, fill=GRID, width=1)

    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        dr.line(
            [(cx, cy), (cx + radius * math.cos(angle), cy + radius * math.sin(angle))],
            fill=GRID_AXIS,
            width=1,
        )

    pts = []
    for i in range(n):
        v = max(0.0, min(1.0, values[i] if i < len(values) else 0))
        angle = -math.pi / 2 + 2 * math.pi * i / n
        pts.append((cx + radius * v * math.cos(angle), cy + radius * v * math.sin(angle)))

    if len(pts) >= 3:
        dr.polygon(pts, fill=fill_color)
        dr.line(pts + [pts[0]], fill=line_color, width=3)
        for px, py in pts:
            dr.ellipse([px - 4, py - 4, px + 4, py + 4], fill=line_color, outline=(255, 255, 255, 255))

    draw_text.draw(cx, title_y, title_size, title, ACCENT, 'mm', 2, (255, 255, 255, 255))

    label_r = radius + 40
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        lx = cx + label_r * math.cos(angle)
        ly = cy + label_r * math.sin(angle)
        draw_text.draw(int(lx), int(ly), font_size, labels[i], TEXT, 'mm', 1, (255, 255, 255, 220))

    axis_angle = -math.pi / 2
    for frac, r_val in ((1 / 3, radius // 3), (2 / 3, 2 * radius // 3), (1, radius)):
        tx = cx + (r_val + 16) * math.cos(axis_angle)
        ty = cy + (r_val + 16) * math.sin(axis_angle)
        draw_text.draw(
            int(tx), int(ty), max(11, font_size - 1),
            f'{frac * 100:.0f}%', SUBTEXT, 'mm', 1, (255, 255, 255, 200),
        )


def _draw_bar_chart(
    dr: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    bar_height: int,
    gap: int,
    labels: List[str],
    values: List[int],
    draw_text: DrawText,
    title: str,
    title_y: int,
    font_size: int,
    title_size: int,
    val_font_size: int,
    max_val: int | None = None,
) -> None:
    if not labels:
        return
    max_val = max_val or max(values or [1]) or 1
    draw_text.draw(x + width // 2, title_y, title_size, title, ACCENT, 'mm', 2, (255, 255, 255, 255))

    label_w = 92
    bar_x = x + label_w + 10
    badge_w = 52
    bar_max_w = width - label_w - 10 - badge_w - 12
    pad = 4

    for label, val in zip(labels, values):
        yy = y
        rgb = DIFF_BAR_COLORS.get(label, TAG_PILL_COLORS.get('难度', (200, 162, 220)))
        fill = (*rgb, 255)
        stroke = (*tuple(max(0, c - 35) for c in rgb), 255)

        draw_text.draw(x, yy + bar_height // 2, font_size, label, TEXT, 'lm', 1, (255, 255, 255, 220))

        # 白底轨道 + 主题色描边
        dr.rounded_rectangle(
            [bar_x, yy + pad, bar_x + bar_max_w, yy + bar_height - pad],
            radius=10,
            fill=(255, 255, 255, 245),
            outline=(*ACCENT[:3], 140),
            width=2,
        )

        ratio = val / max_val if max_val else 0
        bar_w = max(int(bar_max_w * ratio), 12 if val > 0 else 0)
        if bar_w > 0:
            dr.rounded_rectangle(
                [bar_x + 2, yy + pad + 2, bar_x + bar_w - 2, yy + bar_height - pad - 2],
                radius=8,
                fill=fill,
                outline=stroke,
                width=1,
            )
            if bar_w > 36:
                draw_text.draw(
                    bar_x + bar_w // 2, yy + bar_height // 2, val_font_size,
                    str(val), (255, 255, 255, 255), 'mm', 1, (*stroke[:3], 180),
                )

        # 右侧数值徽章
        badge_x = bar_x + bar_max_w + 8
        dr.rounded_rectangle(
            [badge_x, yy + pad, badge_x + badge_w, yy + bar_height - pad],
            radius=8,
            fill=fill,
            outline=stroke,
            width=1,
        )
        draw_text.draw(
            badge_x + badge_w // 2, yy + bar_height // 2, val_font_size,
            str(val), (255, 255, 255, 255), 'mm', 1, (*stroke[:3], 200),
        )

        y += bar_height + gap


def draw_analysis(stats: dict[str, dict[str, float]]) -> Image.Image:
    s = RENDER_SCALE
    title_size = FONT_SIZE_TITLE * s
    text_size = FONT_SIZE_TEXT * s
    val_size = FONT_SIZE_BAR_VAL * s
    title_y = 28 * s
    content_top = title_y + title_size + 24 * s
    footer_h = 32 * s
    panel_w = PANEL_W * s
    panel_h = PANEL_H * s
    side = SIDE_MARGIN * s
    gap = PANEL_GAP * s
    total_w = int(side * 2 + panel_w * 3 + gap * 2)
    total_h = int(content_top + panel_h + footer_h)

    im = _get_analysis_bg(total_w, total_h)
    for i in range(3):
        px = int(side + i * (panel_w + gap))
        im = _draw_panel(im, px, int(content_top), int(panel_w), int(panel_h))

    dr = ImageDraw.Draw(im)
    draw_text = DrawText(dr, _font_path_for_analysis())

    cfg = stats.get('配置') or {}
    diff = stats.get('难度') or {}
    ev = stats.get('评价') or {}

    cfg_fill, cfg_line = _section_style('配置')
    ev_fill, ev_line = _section_style('评价')

    r = (min(PANEL_W, PANEL_H) // 2 - 54) * s
    cy = content_top + panel_h // 2

    cx1 = side + panel_w // 2
    vals_cfg = [cfg.get(t, 0) for t in CONFIG_TAGS_ORDER]
    max_cfg = max(vals_cfg) or 1
    _draw_radar(
        dr, cx1, cy, r, CONFIG_TAGS_ORDER, [v / max_cfg for v in vals_cfg],
        draw_text, '配置标签', title_y, text_size, title_size, cfg_fill, cfg_line,
    )

    x2 = side + panel_w + gap
    diff_pairs = sorted([(t, diff.get(t, 0)) for t in DIFFICULTY_TAGS_ORDER], key=lambda x: -x[1])
    bar_h = 44 * s
    bar_gap = 22 * s
    n_bars = len(diff_pairs)
    bars_total_h = n_bars * bar_h + (n_bars - 1) * bar_gap
    y2 = content_top + (panel_h - bars_total_h) // 2 + title_size // 2
    _draw_bar_chart(
        dr, x2 + 20 * s, y2, panel_w - 40 * s, bar_h, bar_gap,
        [p[0] for p in diff_pairs], [p[1] for p in diff_pairs],
        draw_text, '难度标签', title_y, text_size, title_size, val_size,
        max_val=max((p[1] for p in diff_pairs), default=1),
    )

    cx3 = side + (panel_w + gap) * 2 + panel_w // 2
    vals_ev = [ev.get(t, 0) for t in EVAL_TAGS_ORDER]
    max_ev = max(vals_ev) or 1
    _draw_radar(
        dr, cx3, cy, r, EVAL_TAGS_ORDER, [v / max_ev for v in vals_ev],
        draw_text, '评价标签', title_y, text_size, title_size, ev_fill, ev_line,
    )

    draw_text.draw(
        total_w // 2, total_h - footer_h // 2, text_size,
        footer_generated(), SUBTEXT, 'mm',
    )

    out_w = SIDE_MARGIN * 2 + PANEL_W * 3 + PANEL_GAP * 2
    out_h = int(total_h / s)
    return im.resize((out_w, out_h), Image.LANCZOS)


def image_to_message_segment(im: Image.Image) -> str:
    return image_to_base64(im)
