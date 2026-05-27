import json
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = None


def _get_font_path() -> Path:
    global FONT_PATH
    if FONT_PATH is None:
        FONT_PATH = Path(__file__).parent.parent / "GenSenMaruGothicTW-Regular.ttf"
    return FONT_PATH


DIFF_COLORS = {
    "basic": (34, 187, 91),
    "advanced": (251, 156, 45),
    "expert": (246, 72, 97),
    "master": (158, 69, 226),
    "remaster": (186, 103, 248),
}

DIFF_LABEL = {
    "basic": "BASIC",
    "advanced": "ADVANCED",
    "expert": "EXPERT",
    "master": "MASTER",
    "remaster": "Re:MASTER",
}

BG_COLOR = (26, 26, 46)
GRID_COLOR = (50, 50, 80)
AXIS_COLOR = (150, 150, 180)
TITLE_COLOR = (255, 255, 255)


def _get_dxdata(dxdata_path: Optional[str] = None) -> dict:
    path = Path(dxdata_path) if dxdata_path else Path("dxdata.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_song_in_dxdata(song_id: str, dxdata: dict) -> Optional[dict]:
    song_id_int = int(song_id) if song_id.isdigit() else None
    for song in dxdata.get("songs", []):
        for sh in song.get("sheets", []):
            sid = sh.get("internalId")
            if sid is not None:
                if song_id_int is not None and sid == song_id_int:
                    return song
                if str(sid) == song_id:
                    return song
    return None


def draw_multiver_chart(song_id: str, dxdata_path: Optional[str] = None) -> Optional[Image.Image]:
    dxdata = _get_dxdata(dxdata_path)
    song = _find_song_in_dxdata(song_id, dxdata)
    if not song:
        return None

    versions_order: List[str] = []
    difficulty_series: dict[str, dict[str, float]] = {}

    for sh in song.get("sheets", []):
        mv = sh.get("multiverInternalLevelValue")
        if not mv or not isinstance(mv, dict) or len(mv) <= 1:
            continue
        diff = sh.get("difficulty", "")
        difficulty_series[diff] = dict(mv)
        for v in mv:
            if v not in versions_order:
                versions_order.append(v)

    if not difficulty_series:
        return None

    version_dates: dict[str, str] = {}
    for v in dxdata.get("versions", []):
        version_dates[v.get("version", "")] = v.get("releaseDate", "")
    versions_order.sort(key=lambda v: version_dates.get(v, "9999"))

    DIFF_ORDER = ["basic", "advanced", "expert", "master", "remaster"]
    active_diffs = [d for d in DIFF_ORDER if d in difficulty_series]

    W, H = 800, 500
    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 70, 30, 60, 80

    img = Image.new("RGBA", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_path = str(_get_font_path())
    try:
        font_title = ImageFont.truetype(font_path, 22)
        font_axis = ImageFont.truetype(font_path, 14)
        font_small = ImageFont.truetype(font_path, 12)
        font_legend = ImageFont.truetype(font_path, 13)
    except Exception:
        font_title = ImageFont.load_default()
        font_axis = font_title
        font_small = font_title
        font_legend = font_title

    title = song.get("title", f"ID {song_id}")
    draw.text((MARGIN_L, 8), f"定数变化曲线 - {title}", fill=TITLE_COLOR, font=font_title)

    all_values: List[float] = []
    for diff in active_diffs:
        all_values.extend(difficulty_series[diff].values())
    if not all_values:
        return None

    vmin = max(0.0, min(all_values) - 0.5)
    vmax = min(16.0, max(all_values) + 0.5)

    chart_w = W - MARGIN_L - MARGIN_R
    chart_h = H - MARGIN_T - MARGIN_B

    n_versions = len(versions_order)
    if n_versions < 2:
        return None

    def _x(i: int) -> float:
        return MARGIN_L + chart_w * i / (n_versions - 1)

    def _y(v: float) -> float:
        return MARGIN_T + chart_h - chart_h * (v - vmin) / (vmax - vmin)

    # grid
    y_ticks = 5
    for j in range(y_ticks + 1):
        y_val = vmin + (vmax - vmin) * j / y_ticks
        y_px = _y(y_val)
        draw.line([(MARGIN_L, y_px), (W - MARGIN_R, y_px)], fill=GRID_COLOR, width=1)
        draw.text((MARGIN_L - 6, y_px - 8), f"{y_val:.1f}", fill=AXIS_COLOR, font=font_small, anchor="ra")

    # x-axis labels
    for i, vname in enumerate(versions_order):
        x_px = _x(i)
        draw.line([(x_px, H - MARGIN_B), (x_px, H - MARGIN_B + 4)], fill=AXIS_COLOR, width=1)
        label = vname.replace("maimaiでらっくす", "でらっくす")
        draw.text((x_px, H - MARGIN_B + 8), label, fill=AXIS_COLOR, font=font_small, anchor="ma")

    # axis lines
    draw.line([(MARGIN_L, MARGIN_T), (MARGIN_L, H - MARGIN_B)], fill=AXIS_COLOR, width=2)
    draw.line([(MARGIN_L, H - MARGIN_B), (W - MARGIN_R, H - MARGIN_B)], fill=AXIS_COLOR, width=2)

    # legend
    legend_x = MARGIN_L
    legend_y = H - MARGIN_B + 28
    for diff in active_diffs:
        color = DIFF_COLORS.get(diff, (200, 200, 200))
        label = DIFF_LABEL.get(diff, diff)
        bbox = draw.textbbox((0, 0), label, font=font_legend)
        draw.rectangle(
            [legend_x - 1, legend_y - 1, legend_x + bbox[2] - bbox[0] + 14, legend_y + bbox[3] + 1],
            fill=BG_COLOR,
        )
        draw.line([(legend_x, legend_y + 6), (legend_x + 10, legend_y + 6)], fill=color, width=3)
        draw.text((legend_x + 14, legend_y), label, fill=color, font=font_legend)
        legend_x += bbox[2] - bbox[0] + 36

    # lines and points
    marker_r = 4
    for diff in active_diffs:
        color = DIFF_COLORS.get(diff, (200, 200, 200))
        series = difficulty_series[diff]
        points = []
        for i, vname in enumerate(versions_order):
            if vname in series:
                px = int(_x(i))
                py = int(_y(series[vname]))
                points.append((px, py))

        if len(points) >= 2:
            for j in range(len(points) - 1):
                draw.line([points[j], points[j + 1]], fill=color, width=2)

        for px, py in points:
            draw.ellipse(
                [px - marker_r, py - marker_r, px + marker_r, py + marker_r],
                fill=color,
                outline=BG_COLOR,
                width=1,
            )

        for i, vname in enumerate(versions_order):
            if vname in series:
                px = int(_x(i))
                py = int(_y(series[vname]))
                val_text = f"{series[vname]:.1f}"
                draw.text((px, py - 14), val_text, fill=color, font=font_small, anchor="ms")

    return img