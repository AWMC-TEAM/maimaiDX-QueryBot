from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from nonebot.adapters.onebot.v11 import MessageSegment
from PIL import Image, ImageDraw

from ..config import SIYUAN, achievementList
from ..config import log
from .image import DrawText, image_to_base64, music_picture, rounded_corners
from .maimaidx_best_50 import _is_latest_version
from .maimaidx_data_storage import DailySnapshot, ScoreRecord, data_storage


@dataclass
class _DiffEntry:
    title: str
    level: str
    ra_delta: int
    achv_delta: float
    achv_now: float


def _song_key(r: ScoreRecord) -> Tuple[int, int]:
    return int(r.song_id), int(r.level_index)


def _build_b50(records: List[ScoreRecord]) -> Tuple[List[ScoreRecord], List[ScoreRecord], List[ScoreRecord]]:
    sorted_records = sorted(records, key=lambda x: int(x.ra), reverse=True)
    b15 = sorted([r for r in sorted_records if _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:15]
    b35 = sorted([r for r in sorted_records if not _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:35]
    b50 = b35 + b15
    return b35, b15, b50


def _is_sun(a: float) -> bool:
    for x in achievementList:
        if (x - 0.1) < a <= x:
            return True
    return False


def _is_lock(a: float) -> bool:
    for x in achievementList:
        step = 0.01 if x != int(x) else 0.1
        if x <= a < (x + step):
            return True
    return False


def _collect_snapshots(qqid: int, days: int) -> List[DailySnapshot]:
    metas = data_storage.list_snapshots(qqid, limit=240)
    if not metas:
        return []
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    selected: List[DailySnapshot] = []
    for m in metas:
        stored_at = m.get("stored_at") or ""
        if not stored_at:
            continue
        try:
            dt = datetime.fromisoformat(stored_at)
        except Exception:
            continue
        if dt >= cutoff:
            snap = data_storage.load_snapshot_by_id(qqid, m.get("snapshot_id", ""))
            if snap:
                selected.append(snap)
    return selected


def _analyze(old: DailySnapshot, new: DailySnapshot) -> Dict:
    old_b35, old_b15, old_b50 = _build_b50(old.records)
    new_b35, new_b15, new_b50 = _build_b50(new.records)

    old_map = {_song_key(r): r for r in old_b50}
    new_map = {_song_key(r): r for r in new_b50}

    new_entries: List[ScoreRecord] = [r for k, r in new_map.items() if k not in old_map]
    improved: List[_DiffEntry] = []
    for k, nr in new_map.items():
        orr = old_map.get(k)
        if not orr:
            continue
        ra_delta = int(nr.ra) - int(orr.ra)
        achv_delta = float(nr.achievements) - float(orr.achievements)
        if ra_delta > 0 or achv_delta > 1e-6:
            improved.append(
                _DiffEntry(
                    title=nr.title,
                    level=nr.level,
                    ra_delta=ra_delta,
                    achv_delta=achv_delta,
                    achv_now=float(nr.achievements),
                )
            )
    improved.sort(key=lambda x: (x.ra_delta, x.achv_delta), reverse=True)

    return {
        "rating_delta": int(new.rating) - int(old.rating),
        "b35_delta": sum(int(r.ra) for r in new_b35) - sum(int(r.ra) for r in old_b35),
        "b15_delta": sum(int(r.ra) for r in new_b15) - sum(int(r.ra) for r in old_b15),
        "b35_tail_delta": (int(new_b35[-1].ra) - int(old_b35[-1].ra)) if (old_b35 and new_b35) else 0,
        "b15_tail_delta": (int(new_b15[-1].ra) - int(old_b15[-1].ra)) if (old_b15 and new_b15) else 0,
        "new_entries": new_entries,
        "improved": improved,
        "sun_list": [x for x in improved if _is_sun(x.achv_now)],
        "lock_list": [x for x in improved if _is_lock(x.achv_now)],
    }


def _draw_panel(dr: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, r: int = 18) -> None:
    dr.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=(255, 255, 255, 240), outline=(220, 224, 232, 255), width=2)


def _paste_cover_card(im: Image.Image, dt: DrawText, x: int, y: int, r: ScoreRecord, extra: str = "") -> None:
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((x, y, x + 320, y + 92), radius=14, fill=(248, 250, 255, 255), outline=(230, 234, 246, 255), width=2)
    try:
        cover = Image.open(music_picture(r.song_id)).convert("RGBA").resize((78, 78))
        cover = rounded_corners(cover, 12, (True, True, True, True))
        im.alpha_composite(cover, (x + 8, y + 7))
    except Exception:
        pass
    name = r.title if len(r.title) <= 16 else (r.title[:15] + "…")
    dt.draw(x + 96, y + 14, 20, name, (56, 66, 96, 255), "lt")
    dt.draw(x + 96, y + 43, 18, f"[{r.level}]  {r.achievements:.4f}%", (76, 84, 110, 255), "lt")
    info = f"ra {int(r.ra)}"
    if extra:
        info += f"  |  {extra}"
    dt.draw(x + 96, y + 66, 18, info, (96, 102, 124, 255), "lt")


def _draw_line_chart(dr: ImageDraw.ImageDraw, dt: DrawText, points: List[int], labels: List[str], x: int, y: int, w: int, h: int):
    _draw_panel(dr, x, y, w, h)
    dt.draw(x + 20, y + 16, 30, "Rating 曲线", (40, 45, 60, 255), "lt")
    if not points:
        dt.draw(x + 20, y + 62, 22, "暂无数据", (110, 110, 120, 255), "lt")
        return

    left, top, right, bottom = x + 56, y + 70, x + w - 24, y + h - 40
    min_v, max_v = min(points), max(points)
    if min_v == max_v:
        min_v -= 10
        max_v += 10

    dr.line((left, top, left, bottom), fill=(180, 188, 205, 255), width=2)
    dr.line((left, bottom, right, bottom), fill=(180, 188, 205, 255), width=2)

    coords: List[Tuple[int, int]] = []
    n = len(points)
    for i, v in enumerate(points):
        px = left if n == 1 else int(left + (right - left) * i / (n - 1))
        ratio = (v - min_v) / (max_v - min_v)
        py = int(bottom - ratio * (bottom - top))
        coords.append((px, py))

    for gy in range(5):
        yy = int(top + (bottom - top) * gy / 4)
        dr.line((left, yy, right, yy), fill=(236, 240, 248, 255), width=1)

    if len(coords) >= 2:
        dr.line(coords, fill=(99, 130, 255, 255), width=4)
    for i, (px, py) in enumerate(coords):
        dr.ellipse((px - 5, py - 5, px + 5, py + 5), fill=(99, 130, 255, 255))
        if i in (0, len(coords) - 1):
            dt.draw(px, py - 12, 18, str(points[i]), (65, 82, 150, 255), "mb")
    if labels:
        dt.draw(left, bottom + 10, 16, labels[0], (120, 120, 132, 255), "lt")
        dt.draw(right, bottom + 10, 16, labels[-1], (120, 120, 132, 255), "rt")


def _short_song(r: ScoreRecord) -> str:
    t = r.title.strip()
    return t if len(t) <= 22 else t[:21] + "…"


def _draw_report(title: str, nickname: str, points: List[int], labels: List[str], data: Dict, old_dt: str, new_dt: str) -> Image.Image:
    width, height = 1600, 1280
    im = Image.new("RGBA", (width, height), (242, 246, 255, 255))
    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, str(SIYUAN))

    dt.draw(56, 34, 48, title, (30, 40, 68, 255), "lt")
    dt.draw(56, 86, 24, f"{nickname}  |  {old_dt}  ->  {new_dt}", (96, 102, 120, 255), "lt")

    _draw_line_chart(dr, dt, points, labels, 46, 128, 1508, 320)

    _draw_panel(dr, 46, 470, 740, 260)
    dt.draw(70, 492, 30, "核心变化", (40, 45, 60, 255), "lt")
    core_lines = [
        f"Rating 增量: {data['rating_delta']:+d}",
        f"B35 增量: {data['b35_delta']:+d}",
        f"B15 增量: {data['b15_delta']:+d}",
        f"B35 末位增量: {data['b35_tail_delta']:+d}",
        f"B15 末位增量: {data['b15_tail_delta']:+d}",
    ]
    for i, line in enumerate(core_lines):
        dt.draw(74, 540 + i * 36, 24, line, (64, 72, 96, 255), "lt")

    _draw_panel(dr, 814, 470, 740, 260)
    dt.draw(838, 492, 30, "B50 新增曲目", (40, 45, 60, 255), "lt")
    added = data["new_entries"][:6]
    if not added:
        dt.draw(844, 546, 22, "无新增", (120, 126, 145, 255), "lt")
    for i, r in enumerate(added[:2]):
        _paste_cover_card(im, dt, 836 + i * 356, 532, r)
    if len(added) > 2:
        dt.draw(844, 636, 19, "其余新增：", (98, 104, 126, 255), "lt")
        for i, r in enumerate(added[2:6]):
            dt.draw(844, 660 + i * 24, 18, f"{i+3}. {_short_song(r)} [{r.level}] {int(r.ra)}", (64, 72, 96, 255), "lt")

    _draw_panel(dr, 46, 752, 740, 490)
    dt.draw(70, 774, 30, "B50 提升曲目", (40, 45, 60, 255), "lt")
    inc = data["improved"][:10]
    if not inc:
        dt.draw(74, 826, 22, "无提升", (120, 126, 145, 255), "lt")
    for i, e in enumerate(inc):
        dt.draw(
            74,
            822 + i * 38,
            20,
            f"{i+1}. {e.title[:18]} [{e.level}]  ra {e.ra_delta:+d}  达成 {e.achv_delta:+.4f}%",
            (64, 72, 96, 255),
            "lt",
        )
    # Top2 提升用封面卡展示
    improved_raw = data["improved"][:2]
    new_map = {_song_key(r): r for r in data.get("new_b50", [])}
    for i, e in enumerate(improved_raw):
        target = None
        for rr in data.get("new_b50", []):
            if rr.title == e.title and rr.level == e.level:
                target = rr
                break
        if target:
            _paste_cover_card(im, dt, 430, 826 + i * 102, target, f"ra {e.ra_delta:+d} / 达成 {e.achv_delta:+.4f}%")

    _draw_panel(dr, 814, 752, 740, 490)
    dt.draw(838, 774, 30, "锁血 / 寸止", (40, 45, 60, 255), "lt")
    lock_list = data["lock_list"][:5]
    sun_list = data["sun_list"][:5]
    dt.draw(844, 822, 23, "锁血命中", (72, 86, 140, 255), "lt")
    if not lock_list:
        dt.draw(844, 856, 20, "无", (120, 126, 145, 255), "lt")
    for i, e in enumerate(lock_list):
        dt.draw(844, 856 + i * 30, 19, f"{i+1}. {e.title[:14]}  {e.achv_now:.4f}%  ra {e.ra_delta:+d}", (64, 72, 96, 255), "lt")

    dt.draw(844, 1012, 23, "寸止命中", (72, 86, 140, 255), "lt")
    if not sun_list:
        dt.draw(844, 1046, 20, "无", (120, 126, 145, 255), "lt")
    for i, e in enumerate(sun_list):
        dt.draw(844, 1046 + i * 30, 19, f"{i+1}. {e.title[:14]}  {e.achv_now:.4f}%  ra {e.ra_delta:+d}", (64, 72, 96, 255), "lt")

    return im


def _fmt_date(s: DailySnapshot) -> str:
    return s.stored_at.replace("T", " ") if s.stored_at else s.date


async def generate_progress_report(qqid: int, period_days: int) -> MessageSegment | str:
    snaps = _collect_snapshots(qqid, period_days)
    if len(snaps) < 2:
        return f"近{period_days}天可用快照不足（至少需要2次存档）。请先使用「立即存储数据」并等待后续自动存档。"

    # snaps 按 list_snapshots 是新到旧，分析时需要首尾
    latest = snaps[0]
    oldest = snaps[-1]

    # 曲线按时间正序
    curve = list(reversed(snaps))
    points = [int(s.rating) for s in curve]
    labels = [s.date for s in curve]

    data = _analyze(oldest, latest)
    _, _, data["new_b50"] = _build_b50(latest.records)
    if period_days <= 1:
        title = "MAIMAI 日报"
    elif period_days <= 7:
        title = "MAIMAI 周报"
    else:
        title = "MAIMAI 月报"
    nickname = latest.nickname or str(qqid)
    im = _draw_report(title, nickname, points, labels, data, _fmt_date(oldest), _fmt_date(latest))
    log.debug(f"[progress_report] qq={qqid} period={period_days} snapshots={len(snaps)} delta={data['rating_delta']}")
    return MessageSegment.image(image_to_base64(im))


async def generate_progress_report_between(qqid: int, old_snapshot_id: str, new_snapshot_id: str) -> MessageSegment | str:
    old = data_storage.load_snapshot_by_id(qqid, old_snapshot_id.strip())
    new = data_storage.load_snapshot_by_id(qqid, new_snapshot_id.strip())
    if not old:
        return f"未找到起始存档：{old_snapshot_id}"
    if not new:
        return f"未找到结束存档：{new_snapshot_id}"
    if old.stored_at and new.stored_at and old.stored_at > new.stored_at:
        old, new = new, old

    # 指定ID对比时曲线只画两点，确保是精确复盘
    points = [int(old.rating), int(new.rating)]
    labels = [old.date, new.date]
    data = _analyze(old, new)
    _, _, data["new_b50"] = _build_b50(new.records)
    nickname = new.nickname or str(qqid)
    im = _draw_report("MAIMAI 存档对比", nickname, points, labels, data, _fmt_date(old), _fmt_date(new))
    return MessageSegment.image(image_to_base64(im))

