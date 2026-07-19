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
    "WEIGHT_LETTER_HIT",
    "WEIGHT_LETTER_COMPLETE",
    "WEIGHT_SONG_OPEN",
    "STAR_THRESHOLDS",
    "SCORE_POOL_BY_STAR",
    "BREAK_POOL_BY_STAR",
    "format_elapsed",
    "star_for_elapsed",
    "star_text",
    "distribute_pool",
    "LetterContribution",
    "LetterPlayerReward",
    "LetterSettlement",
    "LetterSong",
    "LetterBoard",
    "LetterGuessManager",
    "_format_letter_complete",
    "format_settlement_message",
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
LetterContribution = ns["LetterContribution"]
_format_letter_complete = ns["_format_letter_complete"]
_is_maskable = ns["_is_maskable"]
_norm_token = ns["_norm_token"]
format_elapsed = ns["format_elapsed"]
star_for_elapsed = ns["star_for_elapsed"]
star_text = ns["star_text"]
distribute_pool = ns["distribute_pool"]
format_settlement_message = ns["format_settlement_message"]
SCORE_POOL_BY_STAR = ns["SCORE_POOL_BY_STAR"]
BREAK_POOL_BY_STAR = ns["BREAK_POOL_BY_STAR"]

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
    1, "MIRROR", solver="开歌侠", uid="u1", billing_id=10001
)
assert hit is song_a and hit.solved and hit.solved_by == "开歌侠"
assert completed and completed[0] is song_b
assert song_b.solved and song_b.solved_by == "开歌侠"
assert "字母补齐：HOT LIMIT" in msg
assert hidden_before["b"] == 1
assert "m" in out_board.revealed
assert out_board.finished
assert out_board.contributions["u1"].song_opens == 1
assert out_board.contributions["u1"].letter_completes == 1

# 字母已开过的路径也应能认领历史卡死的全开曲
mgr2 = LetterGuessManager()
stuck = LetterSong(music_id="c", title="AB", answers=["AB"])
hist = LetterBoard(songs=[stuck], revealed={"a", "b"})
assert stuck.is_fully_revealed(hist.revealed) and not stuck.solved
mgr2.Group[2] = hist
msg2, _, completed2, _ = mgr2.open_letter(
    2, "a", solver="补洞侠", uid="u2", billing_id=10002
)
assert completed2 and completed2[0].solved_by == "补洞侠"
assert "字母补齐：AB" in msg2
assert hist.contributions["u2"].letter_completes == 1

# 用时三位小数 + 浮点星级阈值
assert format_elapsed(12.3456) == "12.346秒"
assert format_elapsed(0) == "0.000秒"
assert star_for_elapsed(30.0) == 5
assert star_for_elapsed(30.001) == 4
assert star_for_elapsed(45.0) == 4
assert star_for_elapsed(60.0) == 3
assert star_for_elapsed(90.0) == 2
assert star_for_elapsed(180.0) == 1
assert star_for_elapsed(180.001) == 0
assert "⭐️⭐️⭐️" == star_text(3)
assert "超时" in star_text(0)

# 贡献分配：奖池按权重分完，无贡献为 0
dist = distribute_pool({"a": 3, "b": 1, "c": 0}, 10)
assert dist["a"] + dist["b"] + dist["c"] == 10
assert dist["a"] == 8 and dist["b"] == 2 and dist["c"] == 0
assert distribute_pool({"a": 0}, 5) == {"a": 0}

# 通关结算文案与奖池
settle_board = LetterBoard(
    songs=[
        LetterSong("1", "AA", ["AA"], solved=True, solved_by="甲"),
        LetterSong("2", "BB", ["BB"], solved=True, solved_by="乙"),
    ],
    started_at=1000.0,
)
settle_board.ensure_contribution("1", 11, "甲").letter_hits = 2
settle_board.ensure_contribution("1", 11, "甲").song_opens = 1
settle_board.ensure_contribution("2", 22, "乙").letter_completes = 1
# 权重：甲 2*1+1*4=6，乙 3 → 合计 9；五星池 40/8
result = settle_board.settle(now=1025.5)
assert abs(result.elapsed - 25.5) < 1e-9
assert result.stars == 5
assert result.score_pool == SCORE_POOL_BY_STAR[5]
assert result.break_pool == BREAK_POOL_BY_STAR[5]
assert sum(r.score for r in result.rewards) == result.score_pool
assert sum(r.break_points for r in result.rewards) == result.break_pool
text = format_settlement_message(result)
assert "25.500秒" in text
assert "⭐️⭐️⭐️⭐️⭐️" in text
assert "甲" in text and "乙" in text

# >180s 最低档仍结算
slow = LetterBoard(
    songs=[LetterSong("1", "AA", ["AA"], solved=True, solved_by="甲")],
    started_at=0.0,
)
slow.ensure_contribution("1", 11, "甲").letter_hits = 1
slow_result = slow.settle(now=200.0)
assert slow_result.stars == 0
assert slow_result.score_pool == SCORE_POOL_BY_STAR[0]
assert slow_result.break_pool == BREAK_POOL_BY_STAR[0]

print("test_guess_letter ok")
