"""落雪 OAuth 新旧响应与 PC 成绩转换回归测试（无需启动 NoneBot）。"""

import re
import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "libraries" / "maimaidx_lxns_client.py"
NAMES = {
    "LxnsApiError",
    "_error_message",
    "_parse_oauth_token_response",
    "_parse_user_api_response",
    "_lxns_song_id_type",
    "_parse_invalid_score",
    "_dx_raw_id_fallback",
    "_public_score_payload",
    "convert_sega_music_scores",
    "convert_pc_records_to_lxns_scores",
}

tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
selected = [
    node
    for node in tree.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in NAMES
]
assert {node.name for node in selected} == NAMES
namespace = {
    "Any": Any,
    "Dict": Dict,
    "List": List,
    "Optional": Optional,
    "Tuple": Tuple,
    "httpx": httpx,
    "_INVALID_SCORE_RE": re.compile(
        r'invalid score \(id:\s*(\d+),\s*type:\s*([a-zA-Z_]+),\s*level_index:\s*(\d+)\)',
        re.IGNORECASE,
    ),
}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)

parse_token = namespace["_parse_oauth_token_response"]
new_token = parse_token(
    httpx.Response(
        200,
        json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 900,
            "scope": "read_player write_player",
        },
    ),
    operation="test",
)
assert new_token["access_token"] == "new-access"

legacy_token = parse_token(
    httpx.Response(
        200,
        json={"success": True, "data": {"access_token": "legacy-access"}},
    ),
    operation="test",
)
assert legacy_token["access_token"] == "legacy-access"

try:
    parse_token(
        httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "token expired"},
        ),
        operation="test",
    )
except namespace["LxnsApiError"] as exc:
    assert exc.status_code == 400
    assert "token expired" in str(exc)
else:
    raise AssertionError("OAuth error response was not rejected")

convert = namespace["convert_sega_music_scores"]
scores = convert(
    [
        {
            "musicId": 10834,
            "level": 4,
            "achievement": 1005000,
            "comboStatus": 4,
            "syncStatus": 3,
            "deluxscoreMax": 1234,
        },
        {
            "musicId": 1,
            "level": 0,
            "achievement": 970000,
            "comboStatus": 0,
            "syncStatus": 0,
            "deluxscoreMax": 0,
        },
    ]
)
assert scores == [
    {
        "id": 834,
        "type": "dx",
        "level_index": 4,
        "achievements": 100.5,
        "dx_score": 1234,
        "fc": "app",
        "fs": "fsd",
        "_raw_music_id": 10834,
    },
    {
        "id": 1,
        "type": "standard",
        "level_index": 0,
        "achievements": 97.0,
        "dx_score": 0,
        "_raw_music_id": 1,
    },
]

class _Rec:
    def __init__(self, **kw):
        self.__dict__.update(kw)


pc_convert = namespace["convert_pc_records_to_lxns_scores"]
pc_scores = pc_convert(
    [
        _Rec(
            song_id=11407,
            level_index=0,
            achievements=100.5,
            dx_score=12,
            fc="ap",
            fs="",
        )
    ]
)
assert pc_scores[0]["id"] == 1407
assert pc_scores[0]["type"] == "dx"
assert pc_scores[0]["_raw_music_id"] == 11407

invalid = namespace["_parse_invalid_score"](
    "invalid score (id: 1407, type: dx, level_index: 0): song not found"
)
assert invalid == (1407, "dx", 0)
alt = namespace["_dx_raw_id_fallback"](pc_scores[0])
assert alt is not None and alt["id"] == 11407
public = namespace["_public_score_payload"](alt)
assert "_raw_music_id" not in public
assert public["id"] == 11407

print("LXNS OAuth tests: ok")
