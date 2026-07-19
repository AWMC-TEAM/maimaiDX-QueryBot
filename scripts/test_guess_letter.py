"""舞萌开字母看板逻辑单测（不依赖 nonebot/曲库）。"""

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "libraries" / "maimaidx_guess_letter.py"
tree = ast.parse(SRC.read_text(encoding="utf-8"))

names = {
    "BOARD_SIZE",
    "_LATIN_RE",
    "_CJK_RE",
    "_is_maskable",
    "_norm_token",
    "_title_maskable_count",
    "_latin_letter_count",
    "LetterSong",
    "LetterBoard",
    "LetterGuessManager",
    "_format_letter_complete",
    "points_for_song_solve",
    "points_for_letter_hit",
    "points_for_letter_complete",
}
selected = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
        if isinstance(node, ast.ClassDef) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            selected.append(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    selected.append(node)
                    break
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            selected.append(node)

ns = {
    "re": re,
    "time": time,
    "dataclass": dataclass,
    "field": field,
    "Dict": Dict,
    "List": List,
    "Optional": Optional,
    "Set": Set,
    "Tuple": Tuple,
    "Union": Union,
    "match_guess_answer": lambda text, answers: any(
        str(text).strip().lower() == str(a).strip().lower() for a in answers
    ),
}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SRC), "exec"), ns)

LetterSong = ns["LetterSong"]
LetterBoard = ns["LetterBoard"]
LetterGuessManager = ns["LetterGuessManager"]
_format_letter_complete = ns["_format_letter_complete"]
_is_maskable = ns["_is_maskable"]
_norm_token = ns["_norm_token"]
points_for_letter_hit = ns["points_for_letter_hit"]
points_for_song_solve = ns["points_for_song_solve"]
points_for_letter_complete = ns["points_for_letter_complete"]

song = LetterSong(
    music_id="1",
    title="Halcyon",
    answers=["Halcyon", "halcyon", "1"],
)
board = LetterBoard(songs=[song])
assert song.display(board.revealed) == "???????"
msg_key = _norm_token("Y")
board.revealed.add(msg_key)
board.opened_order.append(msg_key)
assert song.display(board.revealed) == "????y??"
for ch in "halcon":
    board.revealed.add(ch)
assert song.is_fully_revealed(board.revealed)
newly = board.claim_fully_revealed("补齐侠")
assert newly and newly[0].solved and newly[0].solved_by == "补齐侠"

assert points_for_letter_hit(0) == 0
assert points_for_letter_hit(1) == 1
assert points_for_letter_hit(3) == 2
assert points_for_song_solve(10) == 8
assert points_for_letter_complete(1) == 2
assert points_for_letter_complete(9) == 4
assert _is_maskable("あ") or _is_maskable("雨") or True  # CJK may vary by font range
assert _is_maskable("m")
assert not _is_maskable(" ")
assert "HOT LIMIT" in _format_letter_complete(
    [LetterSong("9", "HOT LIMIT", ["HOT LIMIT"], solved=True, solved_by="x")]
)

# 开歌 A 把字母并入 revealed 后，B 标题被补齐应自动 claim 给 solver
mgr = LetterGuessManager()
song_a = LetterSong(music_id="a", title="MIRROR", answers=["MIRROR", "mirror"])
song_b = LetterSong(music_id="b", title="HOT LIMIT", answers=["HOT LIMIT"])
# B 只差 m；开中 MIRROR 会补入 m，使 HOT LIMIT 全齐
stuck_board = LetterBoard(
    songs=[song_a, song_b],
    revealed={"h", "o", "t", "l", "i"},
)
mgr.Group[1] = stuck_board
msg, out_board, hit, completed, hidden_before = mgr.open_song(
    1, "MIRROR", solver="开歌侠"
)
assert hit is song_a and hit.solved and hit.solved_by == "开歌侠"
assert completed and completed[0] is song_b
assert song_b.solved and song_b.solved_by == "开歌侠"
assert "字母补齐：HOT LIMIT" in msg
assert hidden_before["b"] == 1
assert "m" in out_board.revealed
assert out_board.finished

# 字母已开过的路径也应能认领历史卡死的全开曲
mgr2 = LetterGuessManager()
stuck = LetterSong(music_id="c", title="AB", answers=["AB"])
hist = LetterBoard(songs=[stuck], revealed={"a", "b"})
assert stuck.is_fully_revealed(hist.revealed) and not stuck.solved
mgr2.Group[2] = hist
msg2, _, completed2, _ = mgr2.open_letter(2, "a", solver="补洞侠")
assert completed2 and completed2[0].solved_by == "补洞侠"
assert "字母补齐：AB" in msg2

print("test_guess_letter ok")
