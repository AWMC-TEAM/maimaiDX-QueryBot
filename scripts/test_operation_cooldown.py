"""抽奖概率、用户操作互斥与机台冷却回归测试（无需启动 NoneBot）。"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


break_tree = ast.parse(
    (ROOT / "libraries" / "maimaidx_break.py").read_text(encoding="utf-8")
)
constants = {}
for node in break_tree.body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        continue
    target = node.targets[0]
    if isinstance(target, ast.Name) and target.id in {
        "LOTTERY_PRIZES",
        "LOTTERY_WEIGHTS",
    }:
        constants[target.id] = ast.literal_eval(node.value)

assert constants["LOTTERY_PRIZES"] == (0, 1, 2, 5, 10)
assert constants["LOTTERY_WEIGHTS"] == (35, 30, 20, 12, 3)
expected_return = sum(
    prize * weight
    for prize, weight in zip(
        constants["LOTTERY_PRIZES"], constants["LOTTERY_WEIGHTS"]
    )
) / sum(constants["LOTTERY_WEIGHTS"])
assert expected_return == 1.6
assert sum(constants["LOTTERY_WEIGHTS"][1:]) == 65

runtime_path = ROOT / "command" / "mai_admin_runtime.py"
runtime_source = runtime_path.read_text(encoding="utf-8")
runtime_tree = ast.parse(runtime_source)
selected = [
    node
    for node in runtime_tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"_serial_user_operation", "_release_user_operation"}
]
namespace = {"Matcher": object, "T_State": dict, "_active_user_operations": set()}
exec(
    compile(ast.Module(body=selected, type_ignores=[]), str(runtime_path), "exec"),
    namespace,
)


class SerialMatcher:
    _maimaidx_serial_user_operation = True


class NormalMatcher:
    pass


assert namespace["_serial_user_operation"](SerialMatcher())
assert not namespace["_serial_user_operation"](NormalMatcher())
namespace["_active_user_operations"].add("10001")
state = {"__maimaidx_serial_user_operation": "10001"}
namespace["_release_user_operation"](state)
assert "10001" not in namespace["_active_user_operations"]
assert not state

for relative, matcher in (
    ("command/mai_break.py", "break_lottery"),
    ("command/mai_playcount.py", "update_pc"),
):
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert f"setattr({matcher}, '_maimaidx_serial_user_operation', True)" in source

account_source = (ROOT / "command" / "mai_account.py").read_text(encoding="utf-8")
assert "setattr(_serial_account_matcher, '_maimaidx_serial_user_operation', True)" in account_source
assert "MessageSegment.reply(event.message_id)" in runtime_source
assert "await asyncio.sleep(1.0)" in runtime_source

machine_source = (
    ROOT / "libraries" / "maimaidx_machine_session.py"
).read_text(encoding="utf-8")
assert "awmc_machine_operation_cooldown_seconds" in machine_source
assert "_last_machine_release_at = time.monotonic()" in machine_source

print("operation cooldown tests: ok")
