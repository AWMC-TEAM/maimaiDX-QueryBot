"""个人猜歌数据图：五模式趋势 + 雷达 + 记录明细。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from PIL import Image, ImageDraw

from ..config import SIYUAN, TBFONT
from .image import DrawText, image_to_base64
from .maimaidx_guess_score import GuessScoreManager

_BG = (28, 34, 48, 255)
_CARD = (40, 48, 66, 255)
_PANEL = (48, 58, 78, 255)
_TITLE = (255, 232, 200, 255)
_TEXT = (236, 240, 248, 255)
_MUTED = (150, 162, 184, 255)
_LINE = (70, 82, 108, 255)
_GRID = (58, 68, 92, 255)
_ACCENT = (120, 196, 220, 255)

_MODE_COLORS = {
    'song': (74, 144, 217, 255),
    'pic': (230, 140, 70, 255),
    'audio': (72, 180, 120, 255),
    'chart': (60, 180, 170, 255),
    'letter': (200, 120, 200, 255),
}


def _font() -> Path:
    for candidate in (
        SIYUAN,
        TBFONT,
        Path('/System/Library/Fonts/PingFang.ttc'),
        Path('/System/Library/Fonts/STHeiti Light.ttc'),
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    ):
        try:
            path = Path(candidate)
            if path.exists():
                return path
        except Exception:
            continue
    return Path(SIYUAN)


def _rounded(dr: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], radius: int, fill) -> None:
    dr.rounded_rectangle(box, radius=radius, fill=fill)


def _draw_multi_line_chart(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    *,
    labels: Sequence[str],
    series: Dict[str, List[int]],
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    # 标题与图例分行，避免「近 30 日…」与五模式色点重叠
    dt.draw(x + 20, y + 14, 24, '近 30 日积分趋势（五模式）', _TITLE, 'lt', 1, (0, 0, 0, 100))
    lx = x + 20
    ly = y + 48
    for mode in GuessScoreManager.GUESS_MODES:
        color = _MODE_COLORS[mode]
        label = GuessScoreManager.MODE_LABELS[mode]
        dr.ellipse((lx, ly - 5, lx + 10, ly + 5), fill=color)
        dt.draw(lx + 16, ly, 14, label, color, 'lm')
        lx += 96

    left, top, right, bottom = x + 52, y + 76, x + w - 24, y + h - 40
    dr.line((left, top, left, bottom), fill=_LINE, width=2)
    dr.line((left, bottom, right, bottom), fill=_LINE, width=2)

    all_vals = [v for mode in GuessScoreManager.GUESS_MODES for v in series.get(mode, [])]
    if not all_vals or max(all_vals) <= 0:
        dt.draw(
            (left + right) // 2, (top + bottom) // 2, 20,
            '暂无明细（猜对 / 开字母结算后会记入趋势）', _MUTED, 'mm',
        )
        return

    max_v = max(all_vals)
    min_v = 0
    if max_v == min_v:
        max_v = min_v + 1

    for gy in range(5):
        yy = int(top + (bottom - top) * gy / 4)
        dr.line((left, yy, right, yy), fill=_GRID, width=1)
        val = int(max_v - (max_v - min_v) * gy / 4)
        dt.draw(left - 8, yy, 13, str(val), _MUTED, 'rm')

    n = max(len(labels), 1)
    for mode in GuessScoreManager.GUESS_MODES:
        pts = series.get(mode) or [0] * n
        if len(pts) < n:
            pts = list(pts) + [0] * (n - len(pts))
        color = _MODE_COLORS[mode]
        coords: List[Tuple[int, int]] = []
        for i, v in enumerate(pts[:n]):
            px = left if n == 1 else int(left + (right - left) * i / (n - 1))
            ratio = (v - min_v) / (max_v - min_v)
            py = int(bottom - ratio * (bottom - top))
            coords.append((px, py))
        if len(coords) >= 2:
            dr.line(coords, fill=color, width=3)
        for i, (px, py) in enumerate(coords):
            if pts[i] > 0:
                dr.ellipse((px - 3, py - 3, px + 3, py + 3), fill=color)

    if labels:
        tick_idx = sorted({0, (n - 1) // 2, n - 1})
        for i in tick_idx:
            px = left if n == 1 else int(left + (right - left) * i / (n - 1))
            dt.draw(px, bottom + 8, 13, labels[i], _MUTED, 'mt')


def _draw_radar(
    dr: ImageDraw.ImageDraw,
    dt: DrawText,
    *,
    cx: int,
    cy: int,
    radius: int,
    labels: Sequence[str],
    norms: Sequence[float],
    raw_values: Sequence[int],
    modes: Sequence[str],
    title: str,
    unit: str,
    fill: Tuple[int, int, int, int],
    line: Tuple[int, int, int, int],
) -> None:
    n = len(labels)
    if n < 3:
        return

    # 小标题放在雷达圈外上方，与顶点轴标签留足空隙
    dt.draw(cx, cy - radius - 78, 17, title, _TITLE, 'mm', 1, (0, 0, 0, 80))

    for r_frac in (0.25, 0.5, 0.75, 1.0):
        r = int(radius * r_frac)
        ring = []
        for i in range(n + 1):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            ring.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        dr.line(ring, fill=_GRID, width=1)

    for i in range(n):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        color = _MODE_COLORS.get(modes[i] if i < len(modes) else '', _LINE)
        dr.line(
            [(cx, cy), (cx + radius * math.cos(ang), cy + radius * math.sin(ang))],
            fill=(*color[:3], 90),
            width=1,
        )

    pts: List[Tuple[float, float]] = []
    for i in range(n):
        v = max(0.0, min(1.0, float(norms[i]) if i < len(norms) else 0.0))
        ang = -math.pi / 2 + 2 * math.pi * i / n
        pts.append((cx + radius * v * math.cos(ang), cy + radius * v * math.sin(ang)))

    if len(pts) >= 3:
        dr.polygon(pts, fill=fill)
        dr.line(pts + [pts[0]], fill=line, width=3)
        for i, (px, py) in enumerate(pts):
            mode = modes[i] if i < len(modes) else ''
            color = _MODE_COLORS.get(mode, line)
            dr.ellipse((px - 5, py - 5, px + 5, py + 5), fill=color, outline=(255, 255, 255, 220))

    # 轴标签外推；模式名与数值分半径绘制，避免压住顶点
    for i in range(n):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        # 顶部轴再外推一点，躲开小标题
        top_bias = 18 if abs(ang + math.pi / 2) < 0.25 else 0
        name_r = radius + 42 + top_bias
        val_r = radius + 62 + top_bias
        nx = int(cx + name_r * math.cos(ang))
        ny = int(cy + name_r * math.sin(ang))
        vx = int(cx + val_r * math.cos(ang))
        vy = int(cy + val_r * math.sin(ang))
        mode = modes[i] if i < len(modes) else ''
        color = _MODE_COLORS.get(mode, _TEXT)
        raw = int(raw_values[i]) if i < len(raw_values) else 0
        dt.draw(nx, ny, 14, labels[i], color, 'mm')
        dt.draw(vx, vy, 12, f'{raw}{unit}', _MUTED, 'mm')

    max_raw = max(raw_values) if raw_values else 0
    if max_raw > 0:
        # 刻度写在轴线右侧，避开顶部模式名
        for frac in (0.5, 1.0):
            r = int(radius * frac)
            tx = int(cx + 16)
            ty = int(cy - r)
            dt.draw(tx, ty, 11, str(int(max_raw * frac)), _MUTED, 'lm')


def draw_personal_guess_stats(stats: dict) -> Image.Image:
    """根据 GuessScoreManager.build_user_guess_stats 结果出图。"""
    width = 1080
    header_h = 150
    chart_h = 340
    mode_h = 200
    radar_h = 460
    recent_rows = max(1, len(stats.get('recent') or []))
    recent_h = 56 + recent_rows * 36 + 24
    footer_h = 56
    margin = 28
    gap = 18
    height = header_h + chart_h + mode_h + radar_h + recent_h + footer_h + margin * 2 + gap * 4

    im = Image.new('RGBA', (width, height), _BG)
    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, _font())

    y = margin
    _rounded(dr, (margin, y, width - margin, y + header_h), 18, _CARD)
    name = stats.get('name') or stats.get('uid') or '玩家'
    dt.draw(margin + 28, y + 28, 36, f'{name} 的猜歌数据', _TITLE, 'lt', 2, (0, 0, 0, 120))
    total = int(stats.get('total_score') or 0)
    rank = int(stats.get('total_rank') or 0)
    dt.draw(
        margin + 28, y + 78, 20,
        f'本群总分 {total}  ·  总榜第 {rank} 名',
        _TEXT, 'lt', 1, (0, 0, 0, 80),
    )
    period = stats.get('period_snapshot') or {}
    period_bits: List[str] = []
    for key, label in (
        ('daily', '今日'),
        ('weekly', '本周'),
        ('monthly', '本月'),
        ('season', '赛季'),
    ):
        score, prank = period.get(key, (0, 0))
        period_bits.append(f'{label} {score}（#{prank}）')
    dt.draw(margin + 28, y + 112, 16, '  ·  '.join(period_bits), _MUTED, 'lt')

    y += header_h + gap
    _rounded(dr, (margin, y, width - margin, y + chart_h), 18, _CARD)
    daily = stats.get('daily_series') or {}
    _draw_multi_line_chart(
        dr, dt,
        labels=daily.get('labels') or [],
        series={m: daily.get(m) or [] for m in GuessScoreManager.GUESS_MODES},
        x=margin, y=y, w=width - margin * 2, h=chart_h,
    )

    y += chart_h + gap
    _rounded(dr, (margin, y, width - margin, y + mode_h), 18, _CARD)
    dt.draw(margin + 28, y + 20, 26, '五模式记录', _TITLE, 'lt', 1, (0, 0, 0, 100))
    modes = stats.get('modes') or {}
    card_gap = 12
    card_w = (width - margin * 2 - 40 - card_gap * 4) // 5
    card_x0 = margin + 20
    card_y = y + 58
    for i, mode in enumerate(GuessScoreManager.GUESS_MODES):
        cx = card_x0 + i * (card_w + card_gap)
        color = _MODE_COLORS[mode]
        _rounded(dr, (cx, card_y, cx + card_w, card_y + 118), 14, _PANEL)
        info = modes.get(mode) or {}
        dt.draw(cx + 12, card_y + 16, 18, GuessScoreManager.MODE_LABELS[mode], color, 'lt')
        dt.draw(cx + 12, card_y + 48, 16, f'次数 {int(info.get("count") or 0)}', _TEXT, 'lt')
        dt.draw(cx + 12, card_y + 74, 16, f'积分 {int(info.get("points") or 0)}', _TEXT, 'lt')
        last_at = info.get('last_at') or '—'
        dt.draw(cx + 12, card_y + 98, 13, f'最近 {last_at}', _MUTED, 'lt')

    y += mode_h + gap
    _rounded(dr, (margin, y, width - margin, y + radar_h), 18, _CARD)
    dt.draw(margin + 28, y + 16, 26, '五维能力雷达', _TITLE, 'lt', 1, (0, 0, 0, 100))
    radar = stats.get('radar') or {}
    labels = radar.get('labels') or [GuessScoreManager.MODE_LABELS[m] for m in GuessScoreManager.GUESS_MODES]
    mode_keys = radar.get('modes') or list(GuessScoreManager.GUESS_MODES)
    points = radar.get('points') or [0] * 5
    counts = radar.get('counts') or [0] * 5
    points_norm = radar.get('points_norm') or [0.0] * 5
    counts_norm = radar.get('counts_norm') or [0.0] * 5
    mid = width // 2
    # 圆心下移、半径略减，给小标题与轴标签留白
    radar_cy = y + 250
    has_data = any(int(v) > 0 for v in points) or any(int(v) > 0 for v in counts)
    if not has_data:
        dt.draw(mid, radar_cy, 20, '暂无雷达数据（结算后自动生成）', _MUTED, 'mm')
    else:
        _draw_radar(
            dr, dt,
            cx=margin + 250, cy=radar_cy, radius=95,
            labels=labels, norms=points_norm, raw_values=points, modes=mode_keys,
            title='积分分布', unit='分',
            fill=(120, 196, 220, 55), line=_ACCENT,
        )
        _draw_radar(
            dr, dt,
            cx=width - margin - 250, cy=radar_cy, radius=95,
            labels=labels, norms=counts_norm, raw_values=counts, modes=mode_keys,
            title='次数分布', unit='次',
            fill=(200, 160, 90, 55), line=(230, 180, 100, 255),
        )
        dt.draw(
            mid, y + radar_h - 22, 13,
            '各轴相对个人最高维归一；顶点数值为实际积分 / 次数',
            _MUTED, 'mm',
        )

    y += radar_h + gap
    _rounded(dr, (margin, y, width - margin, y + recent_h), 18, _CARD)
    dt.draw(margin + 28, y + 20, 26, '近期明细', _TITLE, 'lt', 1, (0, 0, 0, 100))
    recent = stats.get('recent') or []
    ry = y + 58
    if not recent:
        dt.draw(margin + 28, ry, 18, '暂无明细记录', _MUTED, 'lt')
    else:
        for row in recent:
            mode = row.get('mode') or 'song'
            label = GuessScoreManager.MODE_LABELS.get(mode, mode)
            color = _MODE_COLORS.get(mode, _TEXT)
            at = row.get('at') or ''
            pts = int(row.get('points') or 0)
            dt.draw(margin + 28, ry, 17, at, _MUTED, 'lt')
            dt.draw(margin + 200, ry, 17, label, color, 'lt')
            dt.draw(margin + 320, ry, 17, f'+{pts} 分', _TEXT, 'lt')
            ry += 36

    note = stats.get('note') or ''
    dt.draw(margin + 28, height - footer_h + 8, 14, note, _MUTED, 'lt')
    dt.draw(width - margin - 28, height - footer_h + 8, 14, '猜歌数据 · 本群个人', _MUTED, 'rt')
    return im


def personal_guess_stats_image_b64(stats: dict) -> str:
    return image_to_base64(draw_personal_guess_stats(stats))
