"""
底力分析：根据用户 B50 曲目列表统计配置/难度/评价标签并绘图。

- 支持自定义背景：配置 maimaidx_tag_analysis_bg 时使用指定图片，未配置时使用常规 B50 背景 b50_bg.png。
- 绘图内容：配置标签雷达图、难度条形图、评价雷达图及底部署名。
"""
import math
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageDraw

from ..config import SIYUAN, maiconfig, maimaidir, static
from .image import DrawText, image_to_base64

# ---------- 标签顺序（与统计结果 key 一致，用于雷达图/条形图顺序） ----------
CONFIG_TAGS_ORDER: List[str] = [
    '交互', '散打', '扫键', '绝赞段', '转圈', '大位移', '定拍', '拆弹',
    '爆发', '纵连', '跳拍', '错位', '一笔画', '反手',
]
DIFFICULTY_TAGS_ORDER: List[str] = ['正常谱', '水', '诈称谱']
EVAL_TAGS_ORDER: List[str] = ['体力谱', '底力谱', '星星谱', '键盘谱', '高物量']

# ---------- 绘图颜色（半透明填充、边框、文字等） ----------
FILL_COLOR = (173, 216, 230, 180)
LINE_COLOR = (100, 130, 180, 255)
TEXT_COLOR = (50, 70, 100, 255)
TITLE_COLOR = (40, 60, 90, 255)

# 渲染精度：先按 scale 倍尺寸绘制再缩回，提高清晰度
RENDER_SCALE = 2

# 统一字号配置
FONT_SIZE_TITLE = 18  # 标题字号
FONT_SIZE_TEXT = 14   # 正文字号（标签、数字、底部署名等）

# 源泉圓體：常见文件名（将字体放入 static 目录即可）
GENWAN_FONT_NAMES = [
    'GenSenRounded-TW-Regular.ttf', 'GenSenRounded.ttf',
    'GenSenRounded-TW-Medium.ttf', '源泉圓體.ttf', 'GenwanRounded-Regular.otf',
]


def _get_analysis_bg(width: int, height: int) -> Image.Image:
    """
    获取底力分析图背景，使用与含金量类似的平铺背景图。
    """
    im = Image.new('RGBA', (width, height))
    
    try:
        bg = Image.open(maimaidir / 'b50_bg.png').convert('RGBA')
    except FileNotFoundError:
        # If b50_bg.png is not found, return a plain white background
        im.paste((255, 255, 255, 255), (0, 0, width, height))
        return im

    bg_w, bg_h = bg.size
    
    # Tile vertically
    for i in range(height // bg_h + 1):
        im.alpha_composite(bg, (0, i * bg_h))

    # Tile horizontally if needed
    if width > bg_w:
        # Get a 1px-wide strip from the right edge of the background
        bg_right = bg.crop((bg_w - 1, 0, bg_w, bg_h))
        # Tile this strip to fill the remaining width
        for i in range(width - bg_w):
            for j in range(height // bg_h + 1):
                im.alpha_composite(bg_right, (bg_w + i, j * bg_h))
    
    return im


def _font_path_for_analysis():
    """底力分析图使用源泉圓體，若不存在则回退到 SIYUAN。"""
    static_dir = SIYUAN.parent if hasattr(SIYUAN, 'parent') else Path(str(SIYUAN)).parent
    for name in GENWAN_FONT_NAMES:
        p = static_dir / name
        if p.exists():
            return str(p)
    return str(SIYUAN)


def _draw_radar(dr, cx, cy, radius, labels, values, draw_text, title, title_y, font_size, title_size):
    n = len(labels)
    if n == 0:
        return
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        ex = cx + radius * math.cos(angle)
        ey = cy + radius * math.sin(angle)
        dr.line([(cx, cy), (ex, ey)], fill=LINE_COLOR, width=1)
    for r in (radius // 3, 2 * radius // 3, radius):
        pts = [(cx + r * math.cos(-math.pi / 2 + 2 * math.pi * i / n), cy + r * math.sin(-math.pi / 2 + 2 * math.pi * i / n)) for i in range(n + 1)]
        dr.line(pts, fill=LINE_COLOR, width=1)
    pts = []
    for i in range(n):
        v = max(0, min(1, values[i] if i < len(values) else 0))
        angle = -math.pi / 2 + 2 * math.pi * i / n
        r = radius * v
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    if len(pts) >= 3:
        dr.polygon(pts, fill=FILL_COLOR, outline=LINE_COLOR)
    draw_text.draw(cx, title_y, title_size, title, TITLE_COLOR, 'mm')
    label_r = radius + 35
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        lx = cx + label_r * math.cos(angle)
        ly = cy + label_r * math.sin(angle)
        draw_text.draw(int(lx), int(ly), font_size, labels[i] if i < len(labels) else '', TEXT_COLOR, 'mm')
    # 只在第一条射线上标数轴（配置=交互，评价=体力谱），准确百分比 0%, 33.3%, 66.7%, 100%，最后绘制保证在最上层
    axis_angle = -math.pi / 2
    for frac, r_val in [(0, 0), (1 / 3, radius // 3), (2 / 3, 2 * radius // 3), (1, radius)]:
        tx = cx + (r_val + 12) * math.cos(axis_angle)
        ty = cy + (r_val + 12) * math.sin(axis_angle)
        pct = '0%' if frac == 0 else f'{frac * 100:.1f}%'
        draw_text.draw(int(tx), int(ty), max(10, font_size - 2), pct, TEXT_COLOR, 'mm')


def _draw_bar_chart(dr, x, y, width, bar_height, gap, labels, values, draw_text, title, title_y, font_size, title_size, max_val=None):
    """绘制水平条形图：左侧标签、条形、右侧数值（难度图不标数轴）。"""
    if not labels:
        return
    max_val = max_val or max(values or [1]) or 1
    draw_text.draw(x + width // 2, title_y, title_size, title, TITLE_COLOR, 'mm')
    label_area_w = 85
    bar_start_x = x + label_area_w + 10
    num_area_w = 50
    right_margin = 20
    bar_max_w = width - label_area_w - 10 - num_area_w - right_margin
    n_bars = len(labels)
    for i, (label, val) in enumerate(zip(labels, values)):
        yy = y + i * (bar_height + gap)
        draw_text.draw(x, yy + bar_height // 2, font_size, label, TEXT_COLOR, 'lm')
        bar_w = int((val / max_val) * bar_max_w) if max_val else 0
        dr.rounded_rectangle([bar_start_x, yy, bar_start_x + bar_w, yy + bar_height], radius=4, fill=FILL_COLOR, outline=LINE_COLOR)
        num_x = bar_start_x + bar_w + 5
        draw_text.draw(num_x, yy + bar_height // 2, font_size, str(val), TEXT_COLOR, 'lm')


def draw_analysis(stats: dict[str, dict[str, float]]):
    s = RENDER_SCALE
    panel_w = 420 * s
    panel_h = 380 * s
    title_size = FONT_SIZE_TITLE * s
    text_size = FONT_SIZE_TEXT * s
    title_bar_gap = 20 * s
    title_y = 36 * s
    content_top = title_y + title_size + title_bar_gap
    footer_h = 36 * s
    # 增加左右边距，避免雷达图标签与画布边缘重合
    side_margin = 30 * s
    total_w = (420 * 3 + 40 + side_margin * 2) * s
    total_h = content_top + panel_h + footer_h
    im = _get_analysis_bg(int(total_w), int(total_h))
    dr = ImageDraw.Draw(im)
    font_path = _font_path_for_analysis()
    draw_text = DrawText(dr, font_path)
    cfg = stats.get('配置') or {}
    diff = stats.get('难度') or {}
    ev = stats.get('评价') or {}
    r1 = (min(420, 380) // 2 - 50) * s
    cx1 = (side_margin + 40 + 210) * s
    cy1 = content_top + panel_h // 2
    vals_cfg = [cfg.get(t, 0) for t in CONFIG_TAGS_ORDER]
    max_cfg = max(vals_cfg) or 1
    vals_cfg_n = [v / max_cfg for v in vals_cfg]
    # 计算"扫键"标签的Y坐标（索引2）
    saojian_idx = 2
    saojian_angle = -math.pi / 2 + 2 * math.pi * saojian_idx / len(CONFIG_TAGS_ORDER)
    saojian_label_r = r1 + 35  # 与 _draw_radar 中的 label_r 保持一致
    saojian_y = int(cy1 + saojian_label_r * math.sin(saojian_angle))
    _draw_radar(dr, cx1, cy1, r1, CONFIG_TAGS_ORDER, vals_cfg_n, draw_text, '配置标签统计', title_y, font_size=text_size, title_size=title_size)
    x2 = (side_margin + 420 + 30) * s
    # 条形图第一个条形的Y坐标对齐到扫键的Y坐标
    bar_height = 36 * s
    y2_bar = saojian_y - bar_height // 2
    diff_pairs = [(t, diff.get(t, 0)) for t in DIFFICULTY_TAGS_ORDER]
    diff_pairs.sort(key=lambda x: -x[1])
    labels_diff = [x[0] for x in diff_pairs]
    values_diff = [x[1] for x in diff_pairs]
    _draw_bar_chart(dr, x2, y2_bar, panel_w - 20 * s, bar_height, 12 * s, labels_diff, values_diff, draw_text, '难度标签统计', title_y, font_size=text_size, title_size=title_size, max_val=max(values_diff) or 1)
    cx3 = (side_margin + 420 * 2 + 40 + 210) * s
    cy3 = content_top + panel_h // 2
    r3 = r1
    vals_ev = [ev.get(t, 0) for t in EVAL_TAGS_ORDER]
    max_ev = max(vals_ev) or 1
    vals_ev_n = [v / max_ev for v in vals_ev]
    _draw_radar(dr, cx3, cy3, r3, EVAL_TAGS_ORDER, vals_ev_n, draw_text, '评价标签统计', title_y, font_size=text_size, title_size=title_size)
    draw_text.draw(total_w // 2, total_h - footer_h // 2, text_size, f'Generated by {maiconfig.botName} BOT', TITLE_COLOR, 'mm')
    out_w = 420 * 3 + 40 + int(side_margin * 2 / s)
    out_h = int(total_h / s)
    im = im.resize((out_w, out_h), Image.LANCZOS)
    return im


def image_to_message_segment(im):
    """将 PIL 图转为 OneBot 可发送的 base64 图片。"""
    return image_to_base64(im)
