"""数据存储命令别名与首次同步提示回归测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / "command" / "mai_score.py").read_text(encoding="utf-8")

for command in (
    "开启存储数据",
    "开启储存数据",
    "开启数据存储",
    "开启数据储存",
):
    assert command in source

handler = source[
    source.index("async def _enable_data_storage("):
    source.index("@disable_data_storage.handle()")
]
assert "enable_data_storage.send(" in handler
assert handler.index("enable_data_storage.send(") < handler.index(
    "fetch_and_store_user_scores("
)

print("storage command alias tests: ok")
