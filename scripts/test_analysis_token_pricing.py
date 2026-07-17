"""锐评 Token usage 提取与计费回归测试（无需启动 NoneBot）。"""

import ast
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent


def load_functions(path: Path, names: set[str], namespace: dict) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in selected} == names
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


pricing_config = {
    "analysis_input_tokens_per_break": 8000,
    "analysis_output_tokens_per_break": 2000,
    "analysis_min_cost": 2,
    "analysis_max_cost": 6,
    "analysis_fallback_cost": 3,
}


def config_int(key: str, default: int) -> int:
    return int(pricing_config.get(key, default))


pricing = load_functions(
    ROOT / "libraries" / "maimaidx_break.py",
    {"analysis_token_cost", "format_analysis_cost_line"},
    {"Optional": Optional, "math": math, "_config_int": config_int},
)
cost = pricing["analysis_token_cost"]
assert cost(0, 0) == 2
assert cost(8000, 2000) == 2
assert cost(8001, 2000) == 3
assert cost(16000, 4000) == 4
assert cost(999999, 999999) == 6
assert cost(0, 0, usage_available=False) == 3

line = pricing["format_analysis_cost_line"](
    charged=4,
    balance=21,
    input_tokens=16000,
    output_tokens=4000,
)
assert "锐评消耗 4 BREAK" in line
assert "输入 16,000 / 输出 4,000 Token" in line
assert "最低 2、最高 6" in line

usage_helpers = load_functions(
    ROOT / "libraries" / "b50_analysis" / "llm.py",
    {"_i", "_response_token_usage"},
    {"Any": Any},
)
usage = usage_helpers["_response_token_usage"](
    SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=12345,
            completion_tokens=2345,
            total_tokens=14690,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
        )
    )
)
assert usage == {
    "available": True,
    "input_tokens": 12345,
    "output_tokens": 2345,
    "total_tokens": 14690,
    "cached_input_tokens": 1000,
}
assert not usage_helpers["_response_token_usage"](
    SimpleNamespace(usage=None)
)["available"]
assert not usage_helpers["_response_token_usage"](
    {"usage": {"total_tokens": 12000}}
)["available"]
dict_usage = usage_helpers["_response_token_usage"](
    {
        "usage": {
            "input_tokens": 9000,
            "output_tokens": 1200,
            "total_tokens": 10200,
        }
    }
)
assert dict_usage["input_tokens"] == 9000
assert dict_usage["output_tokens"] == 1200


class FakeBreakInsufficientError(RuntimeError):
    pass


class FakeBreakDb:
    def __init__(self):
        self.consumed = None
        self.usage = None

    def try_consume(self, qqid, amount, reason, *, meta=None):
        self.consumed = (qqid, amount, reason, meta)
        return True

    def record_usage(self, qqid, kind, break_delta=0):
        self.usage = (qqid, kind, break_delta)

    def get_balance(self, _qqid):
        return 99


fake_db = FakeBreakDb()
settlement = load_functions(
    ROOT / "libraries" / "maimaidx_break.py",
    {"settle_analysis_charge"},
    {
        "Optional": Optional,
        "break_db": fake_db,
        "is_superuser_exempt": lambda _qqid: False,
        "BreakInsufficientError": FakeBreakInsufficientError,
        "log": SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    },
)
charged = settlement["settle_analysis_charge"](
    10001,
    4,
    token_usage={"input_tokens": 16000, "output_tokens": 4000},
)
assert charged == 4
assert fake_db.consumed[1:3] == (4, "b50_analysis")
assert fake_db.consumed[3]["pricing"] == "token"
assert fake_db.usage == (10001, "analysis", -4)

print("analysis token pricing tests: ok")
