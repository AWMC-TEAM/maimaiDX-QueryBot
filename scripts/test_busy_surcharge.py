"""高负载滚动窗口计数回归测试（无需启动 NoneBot）。"""

import ast
import importlib.util
from pathlib import Path


path = Path(__file__).resolve().parent.parent / "libraries" / "maimaidx_request_rate.py"
spec = importlib.util.spec_from_file_location("maimaidx_request_rate_test", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

meter = module.RollingRequestMeter()
assert meter.record("same", now=0, window_seconds=60) == 1
assert meter.record("same", now=1, window_seconds=60) is None
for index in range(2, 31):
    assert meter.record(str(index), now=float(index), window_seconds=60) == index
assert meter.record("31", now=31, window_seconds=60) == 31
# t=1 的请求仍在窗口内；t=0 已过期，因此窗口内仍是 31 个请求。
assert meter.record("later", now=61, window_seconds=60) == 31

root = path.parent.parent
runtime_source = (root / "command" / "mai_admin_runtime.py").read_text(
    encoding="utf-8"
)
qq_bind_source = (root / "command" / "mai_qq_bind.py").read_text(encoding="utf-8")
assert '_maimaidx_passive_recorder' in runtime_source
assert "setattr(_qq_member_recorder, '_maimaidx_passive_recorder', True)" in qq_bind_source
assert '_maimaidx_busy_surcharge_exempt' in runtime_source
assert 'module.endswith(".mai_guess")' in runtime_source
assert 'busy_surcharge_exempt = _busy_surcharge_exempt(matcher)' in runtime_source

runtime_tree = ast.parse(runtime_source)
exempt_node = next(
    node
    for node in runtime_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == '_busy_surcharge_exempt'
)
runtime_namespace = {'Matcher': object}
exec(
    compile(ast.Module(body=[exempt_node], type_ignores=[]), "mai_admin_runtime.py", "exec"),
    runtime_namespace,
)
is_exempt = runtime_namespace['_busy_surcharge_exempt']


class GuessMatcher:
    module = 'nonebot_plugin_maimaidx.command.mai_guess'


class ScoreMatcher:
    module = 'nonebot_plugin_maimaidx.command.mai_score'


class FlagMatcher:
    module = 'custom.module'
    _maimaidx_busy_surcharge_exempt = True


assert is_exempt(GuessMatcher())
assert is_exempt(FlagMatcher())
assert not is_exempt(ScoreMatcher())

print("busy surcharge meter tests: ok")
