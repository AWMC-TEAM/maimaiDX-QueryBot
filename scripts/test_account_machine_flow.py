"""账号状态兼容解析与落雪 OAuth 刷新回归测试（无需启动 NoneBot）。"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent


def load_functions(path: Path, names: set[str], namespace: dict) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert {node.name for node in selected} == names
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


account = load_functions(
    ROOT / "command" / "mai_account.py",
    {
        "_nested_preview",
        "_merged_preview",
        "_pick",
        "_normalize_preview",
        "_normalize_charge_payload",
    },
    {"Any": Any},
)

uid, name, rating, preview = account["_normalize_preview"](
    {
        "userId": 123456,
        "banState": 2,
        "returnCode": 1,
        "userData": {
            "userName": "TEST",
            "playerRating": 15000,
            "playCount": 123,
        },
    }
)
assert (uid, name, rating) == ("123456", "TEST", 15000)
assert preview["banState"] == 2
assert preview["playCount"] == 123

ok, tickets, free_tickets = account["_normalize_charge_payload"](
    {
        "returnCode": 1,
        "userCharge": {
            "userChargeList": [{"chargeId": 2, "stock": 1}],
            "userFreeChargeList": [{"chargeId": 1, "stock": 2}],
        },
    }
)
assert ok
assert tickets[0]["chargeId"] == 2
assert free_tickets[0]["stock"] == 2

# 新接口无票时列表可能为 null，但 userId + list 字段仍代表有效响应。
ok, tickets, free_tickets = account["_normalize_charge_payload"](
    {"userId": 123456, "length": 0, "userChargeList": None}
)
assert ok and tickets == [] and free_tickets == []


class FakeLxnsDb:
    def __init__(self):
        self.saved = None

    def update_tokens(self, qqid: int, **values):
        self.saved = (qqid, values)


async def fake_refresh_token(_token: str) -> dict:
    # 模拟服务端只返回新 access_token，不轮换 refresh_token/scope。
    return {"access_token": "new-access", "expires_in": 900}


fake_db = FakeLxnsDb()
lxns = load_functions(
    ROOT / "command" / "mai_lxns.py",
    {"_do_token_refresh"},
    {
        "Optional": Optional,
        "refresh_token": fake_refresh_token,
        "lxns_db": fake_db,
        "log": SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    },
)
token = asyncio.run(
    lxns["_do_token_refresh"](
        10001,
        {
            "refresh_token": "old-refresh",
            "scope": "read_player write_player",
            "token_type": "Bearer",
        },
    )
)
assert token == "new-access"
assert fake_db.saved is not None
assert fake_db.saved[1]["refresh_token"] == "old-refresh"
assert fake_db.saved[1]["scope"] == "read_player write_player"

print("account machine flow tests: ok")
