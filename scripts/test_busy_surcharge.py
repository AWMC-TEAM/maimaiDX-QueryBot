"""高负载滚动窗口计数回归测试（无需启动 NoneBot）。"""

import ast
import importlib.util
import types
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
guess_source = (root / "command" / "mai_guess.py").read_text(encoding="utf-8")
letter_source = (root / "command" / "mai_letter.py").read_text(encoding="utf-8")
assert '_maimaidx_passive_recorder' in runtime_source
assert "setattr(_qq_member_recorder, '_maimaidx_passive_recorder', True)" in qq_bind_source
assert '_maimaidx_busy_surcharge_exempt' in runtime_source
assert '_matcher_module_name' in runtime_source
assert 'module.endswith(".mai_guess")' in runtime_source
assert 'busy_surcharge_exempt = _busy_surcharge_exempt(matcher)' in runtime_source
assert "setattr(_guess_matcher, '_maimaidx_busy_surcharge_exempt', True)" in guess_source
assert 'setattr(_letter_matcher, "_maimaidx_busy_surcharge_exempt", True)' in letter_source
assert "guess_music_solve" in guess_source
assert "letter_quick" in letter_source

runtime_tree = ast.parse(runtime_source)
needed = {
    node.name: node
    for node in runtime_tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {'_matcher_module_name', '_busy_surcharge_exempt', '_plugin_matcher'}
}
assert set(needed) == {
    '_matcher_module_name',
    '_busy_surcharge_exempt',
    '_plugin_matcher',
}
runtime_namespace = {'Matcher': object}
exec(
    compile(
        ast.Module(
            body=[
                needed['_matcher_module_name'],
                needed['_plugin_matcher'],
                needed['_busy_surcharge_exempt'],
            ],
            type_ignores=[],
        ),
        "mai_admin_runtime.py",
        "exec",
    ),
    runtime_namespace,
)
is_exempt = runtime_namespace['_busy_surcharge_exempt']
is_plugin = runtime_namespace['_plugin_matcher']
module_name_of = runtime_namespace['_matcher_module_name']


class GuessMatcher:
    # 旧测试假设：module 已是字符串（不应再依赖）
    module = 'nonebot_plugin_maimaidx.command.mai_guess'


class ScoreMatcher:
    module_name = 'nonebot_plugin_maimaidx.command.mai_score'


class FlagMatcher:
    module = 'custom.module'
    _maimaidx_busy_surcharge_exempt = True


class NB25GuessMatcher:
    """模拟 NoneBot 2.5：module 是 ModuleType，真正名字在 module_name。"""

    module_name = 'nonebot_plugin_maimaidx.command.mai_guess'
    module = types.ModuleType('nonebot_plugin_maimaidx.command.mai_guess')


class NB25LetterMatcher:
    module_name = 'nonebot_plugin_maimaidx.command.mai_letter'
    module = types.ModuleType('nonebot_plugin_maimaidx.command.mai_letter')


class NB25ScoreMatcher:
    module_name = 'nonebot_plugin_maimaidx.command.mai_score'
    module = types.ModuleType('nonebot_plugin_maimaidx.command.mai_score')


class ModuleOnlyGuessMatcher:
    """只有 ModuleType.module、没有 module_name 时，应回退到 __name__。"""

    module = types.ModuleType('nonebot_plugin_maimaidx.command.mai_guess')


assert module_name_of(NB25GuessMatcher()) == 'nonebot_plugin_maimaidx.command.mai_guess'
assert module_name_of(ModuleOnlyGuessMatcher()) == 'nonebot_plugin_maimaidx.command.mai_guess'
# 关键回归：str(ModuleType) 不能 endswith('.mai_guess')
assert not str(NB25GuessMatcher.module).endswith('.mai_guess')

assert is_exempt(GuessMatcher())
assert is_exempt(FlagMatcher())
assert is_exempt(NB25GuessMatcher())
assert is_exempt(NB25LetterMatcher())
assert is_exempt(ModuleOnlyGuessMatcher())
assert not is_exempt(ScoreMatcher())
assert not is_exempt(NB25ScoreMatcher())

assert is_plugin(NB25GuessMatcher())
assert is_plugin(NB25ScoreMatcher())
assert not is_plugin(FlagMatcher())

print("busy surcharge meter tests: ok")
