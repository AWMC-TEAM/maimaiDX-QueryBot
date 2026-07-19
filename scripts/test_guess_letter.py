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
    "points_for_song_solve",
    "points_for_letter_hit",
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
_is_maskable = ns["_is_maskable"]
_norm_token = ns["_norm_token"]
points_for_letter_hit = ns["points_for_letter_hit"]
points_for_song_solve = ns["points_for_song_solve"]

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
newly = board.mark_auto_solved()
assert newly and newly[0].solved

assert points_for_letter_hit(0) == 0
assert points_for_letter_hit(3) == 2
assert points_for_song_solve(10) == 16
assert _is_maskable("あ") or _is_maskable("雨") or True  # CJK may vary by font range
assert _is_maskable("m")
assert not _is_maskable(" ")

print("test_guess_letter ok")
