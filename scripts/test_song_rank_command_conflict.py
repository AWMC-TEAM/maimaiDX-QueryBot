"""歌曲排行不得抢占全局 Rating 排名命令。"""

import ast
import re
from pathlib import Path


source_path = Path(__file__).parents[1] / "command" / "mai_song_rank.py"
tree = ast.parse(source_path.read_text(encoding="utf-8"))

pattern = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "_SONG_RANK_PATTERN"
        for target in node.targets
    ):
        pattern = ast.literal_eval(node.value)
        break

assert pattern is not None
song_rank_re = re.compile(pattern, flags=re.IGNORECASE)

for reserved in (
    "我的排名",
    "我的 排名",
    "查看排名",
    "查看 排名",
    "查看排名 2",
):
    assert song_rank_re.fullmatch(reserved) is None, reserved

for song_command in (
    "我的白潘排名",
    "我的 白 潘 排名",
    "白潘排名",
    "潘 排名 白 20",
):
    assert song_rank_re.fullmatch(song_command) is not None, song_command

print("song rank command conflict tests: ok")
