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

# ---------- 绘图颜色（温暖夕阳色系，适配背景） ----------
# 主题色：温暖的橙粉色系，与夕阳背景协调
FILL_COLOR = (255, 182, 120, 160)        # 填充色：温暖的橙色半透明
LINE_COLOR = (220, 100, 80, 255)         # 边框色：深橙红色
TEXT_COLOR = (80, 40, 30, 255)           # 文字色：深棕色
TITLE_COLOR = (100, 50, 40, 255)         # 标题色：更深的棕色
GRID_COLOR = (255, 200, 150, 100)        # 网格线：浅橙色半透明
AXIS_TEXT_COLOR = (150, 90, 70, 255)     # 坐标轴文字：中棕色

# 渲染精度：先按 scale 倍尺寸绘制再缩回，提高清晰度
RENDER_SCALE = 2

# 统一字号配置
FONT_SIZE_TITLE = 20   # 标题字号（加大）
FONT_SIZE_TEXT = 15    # 正文字号（标签、数字、底部署名等）
FONT_SIZE_AXIS = 12    # 坐标轴字号

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
        from .maimaidx_theme import Theme, resolve_theme_path
        _theme = Theme.get_default().value
        bg = Image.open(resolve_theme_path(maimaidir, _theme, 'b50_bg.png')).convert('RGBA')
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
    """绘制雷达图，优化视觉效果，添加阴影和光晕"""
    n = len(labels)
    if n == 0:
        return
    
    # 绘制中心光晕效果（多层渐变圆）
    for i in range(5, 0, -1):
        alpha = int(30 * (i / 5))
        glow_r = int(radius * 0.15 * (i / 5))
        dr.ellipse(
            [cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r],
            fill=(255, 220, 180, alpha)
        )
    
    # 绘制网格圆圈（3层）- 使用更柔和的线条
    for r_frac in [0.33, 0.67, 1.0]:
        r = int(radius * r_frac)
        pts = []
        for i in range(n + 1):
            angle = -math.pi / 2 + 2 * math.pi * i / n
            pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        # 绘制阴影
        shadow_pts = [(x + 2, y + 2) for x, y in pts]
        dr.line(shadow_pts, fill=(0, 0, 0, 30), width=3)
        # 绘制主线
        dr.line(pts, fill=GRID_COLOR, width=2)
    
    # 绘制从中心到各顶点的射线
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        ex = cx + radius * math.cos(angle)
        ey = cy + radius * math.sin(angle)
        # 阴影
        dr.line([(cx + 2, cy + 2), (ex + 2, ey + 2)], fill=(0, 0, 0, 30), width=2)
        # 主线
        dr.line([(cx, cy), (ex, ey)], fill=GRID_COLOR, width=2)
    
    # 绘制数据多边形
    pts = []
    for i in range(n):
        v = max(0, min(1, values[i] if i < len(values) else 0))
        angle = -math.pi / 2 + 2 * math.pi * i / n
        r = radius * v
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    
    if len(pts) >= 3:
        # 绘制阴影
        shadow_pts = [(x + 3, y + 3) for x, y in pts]
        dr.polygon(shadow_pts, fill=(0, 0, 0, 40), outline=None)
        
        # 绘制填充（带渐变效果的模拟）
        dr.polygon(pts, fill=FILL_COLOR, outline=None)
        
        # 绘制边框（加粗，带高光）
        pts_closed = pts + [pts[0]]
        # 阴影边框
        shadow_closed = [(x + 2, y + 2) for x, y in pts_closed]
        dr.line(shadow_closed, fill=(0, 0, 0, 50), width=4)
        # 主边框
        dr.line(pts_closed, fill=LINE_COLOR, width=4)
        
        # 绘制顶点（带光晕）
        for pt in pts:
            # 外层光晕
            dr.ellipse([pt[0]-8, pt[1]-8, pt[0]+8, pt[1]+8], fill=(255, 220, 180, 80), outline=None)
            # 主圆点
            dr.ellipse([pt[0]-5, pt[1]-5, pt[0]+5, pt[1]+5], fill=LINE_COLOR, outline=(255, 255, 255, 200))
    
    # 绘制标题（带阴影）
    draw_text.draw(cx + 2, title_y + 2, title_size, title, (0, 0, 0, 60), 'mm')
    draw_text.draw(cx, title_y, title_size, title, TITLE_COLOR, 'mm')
    
    # 绘制标签（带阴影）
    label_r = radius + 45
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        lx = cx + label_r * math.cos(angle)
        ly = cy + label_r * math.sin(angle)
        # 阴影
        draw_text.draw(int(lx + 1), int(ly + 1), font_size, labels[i] if i < len(labels) else '', (0, 0, 0, 80), 'mm')
        # 主文字
        draw_text.draw(int(lx), int(ly), font_size, labels[i] if i < len(labels) else '', TEXT_COLOR, 'mm')
    
    # 绘制百分比刻度（在第一条射线上）
    axis_angle = -math.pi / 2
    axis_font_size = max(10, font_size - 3)
    for frac, r_val in [(0.33, int(radius * 0.33)), (0.67, int(radius * 0.67)), (1.0, radius)]:
        tx = cx + (r_val + 18) * math.cos(axis_angle)
        ty = cy + (r_val + 18) * math.sin(axis_angle)
        pct = f'{frac * 100:.0f}%'
        # 背景框
        bbox = draw_text.get_box(pct, axis_font_size)
        box_w = bbox[2] - bbox[0] + 8
        box_h = bbox[3] - bbox[1] + 6
        dr.rounded_rectangle(
            [tx - box_w//2, ty - box_h//2, tx + box_w//2, ty + box_h//2],
            radius=4,
            fill=(255, 255, 255, 180),
            outline=AXIS_TEXT_COLOR,
            width=1
        )
        draw_text.draw(int(tx), int(ty), axis_font_size, pct, AXIS_TEXT_COLOR, 'mm')


def _draw_bar_chart(dr, x, y, width, bar_height, gap, labels, values, draw_text, title, title_y, font_size, title_size, max_val=None):
    """绘制水平条形图：左侧标签、条形、右侧数值，添加阴影和渐变效果"""
    if not labels:
        return
    
    max_val = max_val or max(values or [1]) or 1
    
    # 绘制标题（带阴影）
    draw_text.draw(x + width // 2 + 2, title_y + 2, title_size, title, (0, 0, 0, 60), 'mm')
    draw_text.draw(x + width // 2, title_y, title_size, title, TITLE_COLOR, 'mm')
    
    # 布局参数
    label_area_w = 95
    bar_start_x = x + label_area_w + 15
    num_area_w = 60
    right_margin = 20
    bar_max_w = width - label_area_w - 15 - num_area_w - right_margin
    
    # 绘制每个条形
    for i, (label, val) in enumerate(zip(labels, values)):
        yy = y + i * (bar_height + gap)
        
        # 绘制标签（带阴影）
        draw_text.draw(x + 1, yy + bar_height // 2 + 1, font_size, label, (0, 0, 0, 80), 'lm')
        draw_text.draw(x, yy + bar_height // 2, font_size, label, TEXT_COLOR, 'lm')
        
        # 计算条形宽度
        bar_w = int((val / max_val) * bar_max_w) if max_val else 0
        
        # 绘制条形背景（浅色，带阴影）
        # 阴影
        dr.rounded_rectangle(
            [bar_start_x + 2, yy + 2, bar_start_x + bar_max_w + 2, yy + bar_height + 2],
            radius=8,
            fill=(0, 0, 0, 30),
            outline=None
        )
        # 背景
        dr.rounded_rectangle(
            [bar_start_x, yy, bar_start_x + bar_max_w, yy + bar_height],
            radius=8,
            fill=(255, 240, 220, 120),
            outline=GRID_COLOR,
            width=1
        )
        
        # 绘制条形（带渐变模拟和高光）
        if bar_w > 0:
            # 主条形阴影
            dr.rounded_rectangle(
                [bar_start_x + 2, yy + 2, bar_start_x + bar_w + 2, yy + bar_height + 2],
                radius=8,
                fill=(0, 0, 0, 50),
                outline=None
            )
            
            # 主条形
            dr.rounded_rectangle(
                [bar_start_x, yy, bar_start_x + bar_w, yy + bar_height],
                radius=8,
                fill=FILL_COLOR,
                outline=LINE_COLOR,
                width=2
            )
            
            # 顶部高光效果
            highlight_h = bar_height // 3
            dr.rounded_rectangle(
                [bar_start_x + 4, yy + 4, bar_start_x + bar_w - 4, yy + highlight_h],
                radius=4,
                fill=(255, 255, 255, 60),
                outline=None
            )
            
            # 左侧光晕
            if bar_w > 20:
                for i in range(3):
                    glow_x = bar_start_x + 8 + i * 2
                    glow_alpha = 40 - i * 10
                    dr.line(
                        [(glow_x, yy + 6), (glow_x, yy + bar_height - 6)],
                        fill=(255, 255, 255, glow_alpha),
                        width=2
                    )
        
        # 绘制数值（带背景框和阴影）
        num_x = bar_start_x + bar_max_w + 10
        num_str = str(val)
        
        # 数值背景框
        bbox = draw_text.get_box(num_str, font_size)
        box_w = bbox[2] - bbox[0] + 12
        box_h = bbox[3] - bbox[1] + 8
        
        # 阴影
        dr.rounded_rectangle(
            [num_x - 2, yy + bar_height // 2 - box_h // 2 + 2, 
             num_x + box_w - 2, yy + bar_height // 2 + box_h // 2 + 2],
            radius=6,
            fill=(0, 0, 0, 40),
            outline=None
        )
        
        # 背景框
        dr.rounded_rectangle(
            [num_x - 4, yy + bar_height // 2 - box_h // 2, 
             num_x + box_w - 4, yy + bar_height // 2 + box_h // 2],
            radius=6,
            fill=(255, 255, 255, 200),
            outline=LINE_COLOR,
            width=2
        )
        
        # 数值文字
        draw_text.draw(num_x + box_w // 2 - 4, yy + bar_height // 2, font_size, num_str, TEXT_COLOR, 'mm')


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
