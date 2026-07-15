"""落雪 OAuth 新旧响应与 PC 成绩转换回归测试（无需启动 NoneBot）。"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "libraries" / "maimaidx_lxns_client.py"
NAMES = {
    "LxnsApiError",
    "_error_message",
    "_parse_oauth_token_response",
    "_parse_user_api_response",
    "convert_sega_music_scores",
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
    "httpx": httpx,
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
        }
    ]
)
assert scores == [
    {
        "id": 834,
        "type": "dx",
        "level_index": 4,
        "achievements": 100.5,
        "fc": "app",
        "fs": "fsd",
        "dx_score": 1234,
    }
]

print("LXNS OAuth tests: ok")
