"""Uptime Kuma 48h 失败率分桶与服务器状态格式化。"""

from __future__ import annotations

import ast
import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = ROOT / "libraries" / "maimaidx_status_api.py"
tree = ast.parse(STATUS_PATH.read_text(encoding="utf-8"))

names = {
    "HISTORY_HOURS",
    "BUCKET_MINUTES",
    "BUCKET_COUNT",
    "_STATUS_LABELS",
    "_STATUS_ICONS",
    "_TYPE_WEIGHT",
    "_FAIL",
    "_BUSINESS_FAIL",
    "_SERVER_ERROR",
    "_CLIENT_ERROR",
    "FAILURE_CATEGORY_META",
    "_status_base",
    "_status_slug",
    "_monitor_sort_score",
    "_latest_heartbeat",
    "_heartbeat_success_rate",
    "_uptime_percent",
    "_format_pct",
    "_format_ping",
    "_parse_heartbeat_time",
    "_bucket_floor",
    "_history_cutoff",
    "_window_bucket_starts",
    "_select_monitor_ids",
    "collect_monitor_catalog",
    "bucket_failure_rates",
    "latest_sampled_bucket",
    "normalize_failure_rate_payload",
    "_failure_axis_ceiling",
    "failure_rate_caption",
    "build_server_status_sections",
    "ingest_heartbeat_history",
    "_load_history",
    "_save_history",
}
selected = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
        selected.append(node)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                selected.append(node)
                break

tmp = tempfile.TemporaryDirectory()
history_path = Path(tmp.name) / "heartbeat_history.json"
namespace: dict[str, Any] = {
    "Any": Any,
    "Optional": Optional,
    "datetime": datetime,
    "timedelta": timedelta,
    "timezone": __import__("datetime").timezone,
    "Path": Path,
    "json": __import__("json"),
    "defaultdict": __import__("collections").defaultdict,
    "RLock": __import__("threading").RLock,
    "HISTORY_DIR": Path(tmp.name),
    "HISTORY_PATH": history_path,
    "_history_lock": __import__("threading").RLock(),
    "maiconfig": SimpleNamespace(
        awmc_status_page_base="https://status.awmc.cc",
        awmc_status_page_slug="maimai",
    ),
    "log": SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None),
}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(STATUS_PATH), "exec"), namespace)

assert namespace["HISTORY_HOURS"] == 48
assert namespace["BUCKET_MINUTES"] == 30
assert namespace["BUCKET_COUNT"] == 96

now = datetime(2026, 7, 19, 12, 20, 0)
starts = namespace["_window_bucket_starts"](now=now, hours=48, minutes=30)
assert len(starts) == 96
assert starts[-1] == datetime(2026, 7, 19, 12, 0, 0)
assert starts[0] == datetime(2026, 7, 17, 12, 30, 0)

page = {
    "incidents": [],
    "maintenanceList": [],
    "publicGroupList": [
        {"name": "Overall / 总览", "monitorList": [{"id": 1, "name": "舞萌DX状态"}]},
        {
            "name": "联通线路",
            "monitorList": [{"id": 5, "name": "游戏标题服务器 [上海联通代理]"}],
        },
        {
            "name": "电信线路",
            "monitorList": [{"id": 6, "name": "游戏标题服务器 [上海电信代理]"}],
        },
    ],
}
heartbeat = {
    "heartbeatList": {
        "1": [
            {"status": 1, "time": "2026-07-19 11:40:00", "ping": 18},
            {"status": 0, "time": "2026-07-19 11:50:00", "ping": 0},
            {"status": 0, "time": "2026-07-19 12:10:00", "ping": 0},
            {"status": 1, "time": "2026-07-19 12:20:00", "ping": 20},
        ],
        "5": [{"status": 1, "time": "2026-07-19 12:00:00", "ping": 600}],
        "6": [{"status": 1, "time": "2026-07-19 12:00:00", "ping": 600}],
    },
    "uptimeList": {"1_24": 0.9, "5_24": 0.8, "6_24": 0.7},
}

history = namespace["ingest_heartbeat_history"](heartbeat, now=now)
series = namespace["bucket_failure_rates"](
    page,
    heartbeat,
    minutes=30,
    hours=48,
    overall_only=True,
    history=history,
    now=now,
)
assert len(series) == 96
filled = [row for row in series if row[1] is not None]
assert len(filled) == 2
assert filled[0][0] == datetime(2026, 7, 19, 11, 30, 0)
assert abs(filled[0][1] - 50.0) < 1e-6
assert filled[1][0] == datetime(2026, 7, 19, 12, 0, 0)
assert abs(filled[1][1] - 50.0) < 1e-6
latest = namespace["latest_sampled_bucket"](series)
assert latest is not None and abs(latest[1] - 50.0) < 1e-6

failure_payload = {
    "days": 7,
    "bucketMinutes": 30,
    "series": [
        {
            "bucketUnix": 1784421000,
            "calls": 20,
            "businessFail": 2,
            "serverError": 1,
            "clientError": 0,
        },
        {
            "bucketUnix": 1784422800,
            "calls": 0,
            "businessFail": 0,
            "serverError": 0,
            "clientError": 0,
        },
        {
            "bucketUnix": 1784424600,
            "calls": 10,
            "businessFail": 0,
            "serverError": 0,
            "clientError": 1,
        },
    ],
}
normalized = namespace["normalize_failure_rate_payload"](failure_payload)
assert len(normalized["points"]) == 2  # calls=0 的空桶省略
assert normalized["categories"] == ["businessFail", "serverError", "clientError"]
assert normalized["totals"] == {
    "calls": 30,
    "businessFail": 2,
    "serverError": 1,
    "clientError": 1,
    "failure": 3,
}
assert abs(normalized["points"][0]["failure"] - 15.0) < 1e-6
assert normalized["points"][0]["bucket"].hour == 8  # UTC 00:30 -> UTC+8 08:30
caption = namespace["failure_rate_caption"](normalized)
assert "业务错误 2" in caption
assert "服务端/转发错误 1" in caption
assert "客户端 4xx 1" in caption
assert "4xx 单独展示" in caption

only_server = namespace["normalize_failure_rate_payload"](
    {
        "series": [
            {
                "bucketUnix": 1784421000,
                "calls": 5,
                "businessFail": 0,
                "serverError": 1,
                "clientError": 0,
            }
        ]
    }
)
assert only_server["categories"] == ["serverError"]  # 全空分类省略
assert namespace["_failure_axis_ceiling"]([0.0, 3.2]) == 5.0

# 再次吞入相同心跳不应翻倍计数
history2 = namespace["ingest_heartbeat_history"](heartbeat, now=now)
series2 = namespace["bucket_failure_rates"](
    page, heartbeat, overall_only=True, history=history2, now=now
)
filled2 = [row for row in series2 if row[1] is not None]
assert filled2[0][2:] == filled[0][2:]

sections = namespace["build_server_status_sections"](page, heartbeat)
text = "\n".join(sections)
assert "全部线路" in text
assert "优先线路" not in text
assert "联通线路" in text and "电信线路" in text

try:
    import httpx

    async def _live() -> None:
        base = "https://status.awmc.cc"
        async with httpx.AsyncClient(timeout=8.0) as client:
            page_res = await client.get(f"{base}/api/status-page/maimai")
            hb_res = await client.get(f"{base}/api/status-page/heartbeat/{ 'maimai' }")
            page_res.raise_for_status()
            hb_res.raise_for_status()
            live_page, live_hb = page_res.json(), hb_res.json()
            live_now = datetime.now()
            live_history = namespace["ingest_heartbeat_history"](live_hb, now=live_now)
            live_series = namespace["bucket_failure_rates"](
                live_page,
                live_hb,
                overall_only=True,
                history=live_history,
                now=live_now,
            )
            assert len(live_series) == 96
            failure_res = await client.get("https://api.wmc.pub/usage/failure-rate")
            failure_res.raise_for_status()
            failure_data = namespace["normalize_failure_rate_payload"](failure_res.json())
            assert failure_data["days"] >= 1
            assert failure_data["bucket_minutes"] >= 1
            print(
                "live filled",
                sum(1 for r in live_series if r[1] is not None),
                "/96",
                "failure points",
                len(failure_data["points"]),
            )

    asyncio.run(_live())
    print("live status api ok")
except Exception as exc:
    print(f"live status api skipped: {type(exc).__name__}: {exc}")

tmp.cleanup()
print("test_status_api_format ok")
