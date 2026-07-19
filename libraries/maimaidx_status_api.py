"""AWMC Uptime Kuma 公开状态页客户端。

文档：https://wiki.awmc.team/dev/status-api
- GET /api/status-page/{slug}
- GET /api/status-page/heartbeat/{slug}
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Optional

import httpx
from PIL import Image, ImageDraw

from ..config import SIYUAN, log, maiconfig
from .image import DrawText, image_to_base64

# 公开心跳接口通常只有约 1 小时窗口；本地落盘后可拼出 48h 曲线。
HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "status"
HISTORY_PATH = HISTORY_DIR / "heartbeat_history.json"
HISTORY_HOURS = 48
BUCKET_MINUTES = 30
BUCKET_COUNT = HISTORY_HOURS * 60 // BUCKET_MINUTES  # 96
_history_lock = RLock()

_STATUS_LABELS = {
    0: "离线",
    1: "正常",
    2: "异常",
    3: "维护",
}
_STATUS_ICONS = {
    0: "🔴",
    1: "🟢",
    2: "🟡",
    3: "🔧",
}
_TYPE_WEIGHT = {
    "NET服务器": 500,
    "游戏标题": 500,
    "机台服务器": 400,
    "会员服务器": 300,
    "会员": 300,
    "二维码服务器": 200,
    "二维码": 200,
    "ALL.NET": 100,
}

_BG = (18, 24, 42, 255)
_PANEL = (28, 36, 58, 255)
_ACCENT = (124, 129, 255, 255)
_FAIL = (255, 123, 134, 255)
_TEXT = (237, 242, 255, 255)
_MUTED = (156, 171, 201, 255)
_GRID = (255, 255, 255, 48)

_cache_lock = asyncio.Lock()
_cache_payload: Optional[dict] = None
_cache_at = 0.0


def _status_base() -> str:
    return str(
        getattr(maiconfig, "awmc_status_page_base", None) or "https://status.awmc.cc"
    ).rstrip("/")


def _status_slug() -> str:
    return str(getattr(maiconfig, "awmc_status_page_slug", None) or "maimai").strip() or "maimai"


def _cache_seconds() -> float:
    return max(0.0, float(getattr(maiconfig, "awmc_status_cache_seconds", 30.0) or 0.0))


def _monitor_sort_score(name: str) -> int:
    score = 0
    for key, weight in _TYPE_WEIGHT.items():
        if key in name:
            score = max(score, weight)
    if "舞萌DX状态" in name or name in {"Overall", "总览"}:
        score += 2000
    return score


def _latest_heartbeat(beats: list) -> dict:
    if not beats:
        return {}
    return beats[-1] if isinstance(beats[-1], dict) else {}


def _heartbeat_success_rate(beats: list) -> Optional[float]:
    if not beats:
        return None
    total = 0
    ok = 0
    for row in beats:
        if not isinstance(row, dict):
            continue
        status = row.get("status")
        if status is None:
            continue
        total += 1
        if int(status) == 1:
            ok += 1
    if total <= 0:
        return None
    return 100.0 * ok / total


def _uptime_percent(uptime_list: dict, monitor_id: int | str, hours: int = 24) -> Optional[float]:
    key = f"{monitor_id}_{hours}"
    value = uptime_list.get(key)
    if value is None:
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= ratio <= 1.0:
        return ratio * 100.0
    if 1.0 < ratio <= 100.0:
        return ratio
    return None


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _format_ping(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{int(round(float(value)))}ms"
    except (TypeError, ValueError):
        return "—"


def _parse_heartbeat_time(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip().replace("T", " ")
    if not text:
        return None
    for candidate in (text[:26], text[:19]):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def _bucket_floor(dt: datetime, minutes: int = 30) -> datetime:
    minutes = max(1, int(minutes))
    minute = (dt.minute // minutes) * minutes
    return dt.replace(minute=minute, second=0, microsecond=0)


def collect_monitor_catalog(page: dict) -> list[tuple[Any, str, str]]:
    """返回 [(monitor_id, name, group_name), ...]。"""
    rows: list[tuple[Any, str, str]] = []
    for group in page.get("publicGroupList") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "未分组")
        for item in group.get("monitorList") or []:
            if not isinstance(item, dict):
                continue
            mid = item.get("id")
            name = str(item.get("name") or f"#{mid}")
            rows.append((mid, name, group_name))
    rows.sort(key=lambda row: (-_monitor_sort_score(row[1]), row[2], row[1]))
    return rows


def _history_cutoff(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now()
    return current - timedelta(hours=HISTORY_HOURS)


def _load_history() -> dict[str, dict[str, int]]:
    """读取本地心跳：{monitor_id: {iso_time: status}}。"""
    with _history_lock:
        if not HISTORY_PATH.exists():
            return {}
        try:
            raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            log.warning(f"[status-api] 读取心跳历史失败：{type(exc).__name__}: {exc}")
            return {}
    samples = raw.get("samples") if isinstance(raw, dict) else None
    if not isinstance(samples, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    for mid, rows in samples.items():
        if not isinstance(rows, dict):
            continue
        cleaned: dict[str, int] = {}
        for ts, status in rows.items():
            try:
                cleaned[str(ts)] = int(status)
            except (TypeError, ValueError):
                continue
        if cleaned:
            result[str(mid)] = cleaned
    return result


def _save_history(samples: dict[str, dict[str, int]]) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "hours": HISTORY_HOURS,
        "minutes": BUCKET_MINUTES,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "samples": samples,
    }
    tmp = HISTORY_PATH.with_suffix(".tmp")
    with _history_lock:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(HISTORY_PATH)


def ingest_heartbeat_history(heartbeat: dict, *, now: Optional[datetime] = None) -> dict[str, dict[str, int]]:
    """合并本次心跳到本地历史，并裁剪到近 48 小时。"""
    current = now or datetime.now()
    cutoff = _history_cutoff(current)
    samples = _load_history()
    heartbeat_list = heartbeat.get("heartbeatList") or {}
    if isinstance(heartbeat_list, dict):
        for mid, rows in heartbeat_list.items():
            if not isinstance(rows, list):
                continue
            bucket = samples.setdefault(str(mid), {})
            for row in rows:
                if not isinstance(row, dict):
                    continue
                dt = _parse_heartbeat_time(row.get("time"))
                if dt is None or dt < cutoff:
                    continue
                try:
                    status_i = int(row.get("status"))
                except (TypeError, ValueError):
                    continue
                bucket[dt.strftime("%Y-%m-%dT%H:%M:%S")] = status_i

    pruned: dict[str, dict[str, int]] = {}
    for mid, rows in samples.items():
        kept = {
            ts: status
            for ts, status in rows.items()
            if (_parse_heartbeat_time(ts) or datetime.min) >= cutoff
        }
        if kept:
            pruned[mid] = kept
    _save_history(pruned)
    return pruned


def _select_monitor_ids(page: dict, *, overall_only: bool) -> set[str]:
    catalog = collect_monitor_catalog(page)
    if not catalog:
        return set()
    if overall_only:
        overall_ids = {
            str(mid)
            for mid, name, _ in catalog
            if "舞萌DX状态" in name or name in {"Overall", "总览"}
        }
        if overall_ids:
            return overall_ids
    return {str(mid) for mid, _, _ in catalog}


def _window_bucket_starts(
    *,
    now: Optional[datetime] = None,
    hours: int = HISTORY_HOURS,
    minutes: int = BUCKET_MINUTES,
) -> list[datetime]:
    end = _bucket_floor(now or datetime.now(), minutes)
    start = end - timedelta(hours=hours) + timedelta(minutes=minutes)
    count = max(1, hours * 60 // max(1, minutes))
    return [start + timedelta(minutes=minutes * i) for i in range(count)]


def bucket_failure_rates(
    page: dict,
    heartbeat: dict,
    *,
    minutes: int = BUCKET_MINUTES,
    hours: int = HISTORY_HOURS,
    overall_only: bool = False,
    history: Optional[dict[str, dict[str, int]]] = None,
    now: Optional[datetime] = None,
) -> list[tuple[datetime, Optional[float], int, int]]:
    """按半小时聚合近 ``hours`` 小时失败率，固定产出完整时间轴。

    返回 [(bucket_start, failure_rate_pct|None, fail_count, total), ...]。
    无样本的时间桶 rate=None，便于折线留空。
    """
    monitor_ids = _select_monitor_ids(page, overall_only=overall_only)
    if not monitor_ids:
        return []

    samples = history if history is not None else ingest_heartbeat_history(heartbeat, now=now)
    buckets: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
    for mid in monitor_ids:
        for ts, status in (samples.get(str(mid)) or {}).items():
            dt = _parse_heartbeat_time(ts)
            if dt is None:
                continue
            try:
                status_i = int(status)
            except (TypeError, ValueError):
                continue
            key = _bucket_floor(dt, minutes)
            buckets[key][1] += 1
            if status_i != 1:
                buckets[key][0] += 1

    result: list[tuple[datetime, Optional[float], int, int]] = []
    for key in _window_bucket_starts(now=now, hours=hours, minutes=minutes):
        fail, total = buckets.get(key, [0, 0])
        rate = (100.0 * fail / total) if total > 0 else None
        result.append((key, rate, fail, total))
    return result


def latest_sampled_bucket(
    series: list[tuple[datetime, Optional[float], int, int]],
) -> Optional[tuple[datetime, float, int, int]]:
    for bucket, rate, fail, total in reversed(series):
        if rate is not None and total > 0:
            return bucket, float(rate), fail, total
    return None


def draw_failure_rate_chart(
    series: list[tuple[datetime, Optional[float], int, int]],
    *,
    title: str = "游玩情况 · 失败率",
    subtitle: str = "半小时切分 · 近 48 小时",
) -> Image.Image:
    """绘制近 48 小时失败率折线图。"""
    width, height = 1280, 640
    im = Image.new("RGBA", (width, height), _BG)
    dr = ImageDraw.Draw(im)
    dt = DrawText(dr, SIYUAN)

    dr.rounded_rectangle((28, 28, width - 28, height - 28), radius=24, fill=_PANEL)
    dt.draw(56, 52, 34, title, _ACCENT, "lt", 2, (255, 255, 255, 230))
    dt.draw(56, 96, 18, subtitle, _MUTED, "lt")

    left, top, right, bottom = 90, 150, width - 70, height - 96
    dr.line((left, top, left, bottom), fill=_ACCENT, width=2)
    dr.line((left, bottom, right, bottom), fill=_ACCENT, width=2)

    for i in range(5):
        yy = int(top + (bottom - top) * i / 4)
        pct = 100 - i * 25
        dr.line((left, yy, right, yy), fill=_GRID, width=1)
        dt.draw(left - 12, yy, 16, f"{pct}%", _MUTED, "rm")

    if not series:
        dt.draw((left + right) // 2, (top + bottom) // 2, 24, "暂无心跳数据", _MUTED, "mm")
        return im

    n = len(series)
    coords: list[Optional[tuple[int, int]]] = []
    for i, (_bucket, value, _fail, total) in enumerate(series):
        px = left if n == 1 else int(left + (right - left) * i / (n - 1))
        if value is None or total <= 0:
            coords.append(None)
            continue
        py = int(bottom - max(0.0, min(100.0, float(value))) / 100.0 * (bottom - top))
        coords.append((px, py))

    # 分段连线，跳过无样本桶
    segment: list[tuple[int, int]] = []
    for point in coords + [None]:
        if point is None:
            if len(segment) >= 2:
                area = segment + [(segment[-1][0], bottom), (segment[0][0], bottom)]
                dr.polygon(area, fill=(255, 123, 134, 40))
                dr.line(segment, fill=_FAIL, width=3)
            elif len(segment) == 1:
                px, py = segment[0]
                dr.ellipse((px - 4, py - 4, px + 4, py + 4), fill=_FAIL)
            segment = []
            continue
        segment.append(point)

    # 点太密时只标最近有数据的点
    sampled = [p for p in coords if p is not None]
    mark_step = max(1, len(sampled) // 12)
    marked = 0
    for point in coords:
        if point is None:
            continue
        if marked % mark_step == 0 or point is sampled[-1]:
            px, py = point
            dr.ellipse((px - 4, py - 4, px + 4, py + 4), fill=_FAIL, outline=(255, 255, 255, 220), width=1)
        marked += 1

    label_idx = {0, n - 1, n // 4, n // 2, 3 * n // 4}
    for i in sorted(label_idx):
        if i < 0 or i >= n:
            continue
        bucket, rate, _fail, total = series[i]
        px = left if n == 1 else int(left + (right - left) * i / (n - 1))
        label = bucket.strftime("%m-%d %H:%M")
        dt.draw(px, bottom + 12, 14, label, _MUTED, "mt")
        if rate is not None and total > 0 and i in (0, n - 1):
            py = int(bottom - max(0.0, min(100.0, float(rate))) / 100.0 * (bottom - top))
            dt.draw(px, py - 12, 15, f"{rate:.0f}%", _TEXT, "mb")

    latest = latest_sampled_bucket(series)
    filled = sum(1 for _b, rate, _f, total in series if rate is not None and total > 0)
    if latest:
        summary = (
            f"最近半小时失败率 {latest[1]:.1f}%（{latest[2]}/{latest[3]}）"
            f"  ·  近 {HISTORY_HOURS}h / {BUCKET_MINUTES}min 桶 {filled}/{len(series)}"
        )
    else:
        summary = f"近 {HISTORY_HOURS} 小时暂无可用心跳样本（持续调用后将自动补齐）"
    dt.draw(56, height - 58, 17, summary, _TEXT, "lt")
    return im


def build_server_status_sections(page: dict, heartbeat: dict) -> list[str]:
    """把状态页 + 心跳整理成可合并转发的文本段（展示全部监控，不区分运营商）。"""
    heartbeat_list = heartbeat.get("heartbeatList") or {}
    uptime_list = heartbeat.get("uptimeList") or {}
    incidents = [
        row for row in (page.get("incidents") or [])
        if isinstance(row, dict) and row.get("active")
    ]
    maintenances = [
        row for row in (page.get("maintenanceList") or [])
        if isinstance(row, dict) and row.get("active")
    ]

    monitors: list[tuple[dict, str, dict, Optional[float], Optional[float]]] = []
    for group in page.get("publicGroupList") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "未分组")
        for item in group.get("monitorList") or []:
            if not isinstance(item, dict):
                continue
            mid = item.get("id")
            beats = heartbeat_list.get(str(mid)) or heartbeat_list.get(mid) or []
            if not isinstance(beats, list):
                beats = []
            latest = _latest_heartbeat(beats)
            rate = _heartbeat_success_rate(beats)
            uptime = _uptime_percent(uptime_list, mid, 24)
            monitors.append((item, group_name, latest, rate, uptime))

    monitors.sort(
        key=lambda row: (
            -_monitor_sort_score(str(row[0].get("name") or "")),
            str(row[1]),
            str(row[0].get("name") or ""),
        )
    )

    header = [
        "🖥️ 服务器状态（实时）",
        "━━━━━━━━━━━━━━",
        f"数据源：{_status_base()}/status/{_status_slug()}",
    ]
    up_count = sum(1 for _, _, latest, _, _ in monitors if latest.get("status") == 1)
    header.append(f"监控点：{up_count}/{len(monitors)} 正常（全部线路）")

    if incidents:
        header.append("")
        header.append("📢 进行中公告")
        for row in incidents[:3]:
            header.append(f"  · {row.get('title') or '未命名公告'}")

    if maintenances:
        header.append("")
        header.append("🛠️ 计划维护")
        for row in maintenances[:3]:
            header.append(f"  · {row.get('title') or '维护中'}")

    lines = list(header)
    lines.append("")
    lines.append("📡 监控明细（状态 · 心跳延迟 · 近窗成功率 · 24h）")

    current_group = None
    for item, group_name, latest, rate, uptime in monitors:
        if group_name != current_group:
            current_group = group_name
            lines.append("")
            lines.append(f"【{group_name}】")
        status = latest.get("status")
        try:
            status_i = int(status) if status is not None else -1
        except (TypeError, ValueError):
            status_i = -1
        icon = _STATUS_ICONS.get(status_i, "⚪")
        label = _STATUS_LABELS.get(status_i, "未知")
        name = str(item.get("name") or f"#{item.get('id')}")
        hb_time = str(latest.get("time") or "")[:19]
        lines.append(
            f"{icon} {name}\n"
            f"   {label} · 延迟 {_format_ping(latest.get('ping'))}"
            f" · 成功率 {_format_pct(rate)} · 24h {_format_pct(uptime)}"
            + (f"\n   心跳 {hb_time}" if hb_time else "")
        )

    if not monitors:
        lines.append("暂无监控数据")

    text = "\n".join(lines)
    if len(text) <= 1100:
        return [text]
    summary = "\n".join(header + ["", f"明细见下一条（共 {len(monitors)} 个监控点）"])
    detail_lines = ["🖥️ 服务器监控明细", "━━━━━━━━━━━━━━"]
    current_group = None
    for item, group_name, latest, rate, uptime in monitors:
        if group_name != current_group:
            current_group = group_name
            detail_lines.append("")
            detail_lines.append(f"【{group_name}】")
        status = latest.get("status")
        try:
            status_i = int(status) if status is not None else -1
        except (TypeError, ValueError):
            status_i = -1
        icon = _STATUS_ICONS.get(status_i, "⚪")
        label = _STATUS_LABELS.get(status_i, "未知")
        name = str(item.get("name") or f"#{item.get('id')}")
        detail_lines.append(
            f"{icon} {name} · {label} · {_format_ping(latest.get('ping'))}"
            f" · {_format_pct(rate)} / 24h {_format_pct(uptime)}"
        )
    return [summary, "\n".join(detail_lines)]


async def fetch_status_bundle(*, force: bool = False) -> tuple[dict, dict]:
    """拉取状态页与心跳；短缓存避免刷接口。"""
    global _cache_payload, _cache_at
    ttl = _cache_seconds()
    now = time.monotonic()
    async with _cache_lock:
        if (
            not force
            and _cache_payload is not None
            and ttl > 0
            and now - _cache_at < ttl
        ):
            cached = _cache_payload
            return cached["page"], cached["heartbeat"]

        base = _status_base()
        slug = _status_slug()
        timeout = httpx.Timeout(
            max(3.0, float(getattr(maiconfig, "awmc_status_timeout_seconds", 8.0) or 8.0))
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            page_res, hb_res = await asyncio.gather(
                client.get(f"{base}/api/status-page/{slug}"),
                client.get(f"{base}/api/status-page/heartbeat/{slug}"),
            )
            page_res.raise_for_status()
            hb_res.raise_for_status()
            page = page_res.json()
            heartbeat = hb_res.json()

        if not isinstance(page, dict):
            raise RuntimeError("状态页返回格式异常")
        if not isinstance(heartbeat, dict):
            raise RuntimeError("心跳接口返回格式异常")

        _cache_payload = {"page": page, "heartbeat": heartbeat}
        _cache_at = time.monotonic()
        return page, heartbeat


async def build_live_status_payload(*, force: bool = False) -> dict[str, Any]:
    """组装舞萌状态：近 48h 失败率图 + 服务器文本段。"""
    page, heartbeat = await fetch_status_bundle(force=force)
    history = ingest_heartbeat_history(heartbeat)
    series = bucket_failure_rates(
        page,
        heartbeat,
        minutes=BUCKET_MINUTES,
        hours=HISTORY_HOURS,
        overall_only=True,
        history=history,
    )
    source = "舞萌DX状态"
    if latest_sampled_bucket(series) is None:
        series = bucket_failure_rates(
            page,
            heartbeat,
            minutes=BUCKET_MINUTES,
            hours=HISTORY_HOURS,
            overall_only=False,
            history=history,
        )
        source = "全部监控聚合"
    chart = draw_failure_rate_chart(
        series,
        title="游玩情况 · 失败率",
        subtitle=f"半小时切分 · 近 {HISTORY_HOURS} 小时 · {source}",
    )
    return {
        "chart": chart,
        "chart_b64": image_to_base64(chart),
        "series": series,
        "server_sections": build_server_status_sections(page, heartbeat),
    }


async def format_server_status_sections(*, force: bool = False) -> list[str]:
    """兼容旧调用：仅返回服务器文本。"""
    try:
        page, heartbeat = await fetch_status_bundle(force=force)
        return build_server_status_sections(page, heartbeat)
    except Exception as exc:
        log.warning(f"[status-api] 拉取失败：{type(exc).__name__}: {exc}")
        return [
            "🖥️ 服务器状态（实时）\n"
            "━━━━━━━━━━━━━━\n"
            f"暂时无法获取 Uptime 状态：{type(exc).__name__}\n"
            f"可稍后重试，或打开 {_status_base()}/status/{_status_slug()}"
        ]
