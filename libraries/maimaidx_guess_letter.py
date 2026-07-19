"""舞萌开字母：多曲标题 hangman 看板。

交互对齐常见 Bot：
- 开字母 / 舞萌开字母：开局（8 曲）
- 开字母 x：揭示字母/字符
- 开歌 <曲名|别名>：猜中整首歌
- 不玩了 / 结束开字母：结束并揭晓
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from PIL import Image, ImageDraw

from ..config import SIYUAN, TBFONT, log
from .image import DrawText, image_to_base64
from .maimaidx_guess_match import match_guess_answer
from .maimaidx_music import guess, mai
from .maimaidx_model import Music

GroupId = Union[int, str]

BOARD_SIZE = 8
_LATIN_RE = re.compile(r"[A-Za-z0-9]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_BG = (42, 28, 22, 255)
_CARD = (58, 40, 32, 255)
_TITLE = (255, 214, 170, 255)
_OK = (120, 210, 140, 255)
_WAIT = (255, 196, 120, 255)
_TEXT = (245, 236, 224, 255)
_MUTED = (180, 160, 145, 255)
_LINE = (90, 66, 54, 255)


def _is_maskable(ch: str) -> bool:
    if _LATIN_RE.fullmatch(ch) or _CJK_RE.fullmatch(ch):
        return True
    # 扩展拉丁（如 Ø）也按可开字符处理
    return len(ch) == 1 and ch.isalpha()


def _norm_token(ch: str) -> str:
    """比较用：拉丁字母小写，其余原样。"""
    if len(ch) != 1:
        return ch
    if ch.isalpha() and ch.isascii():
        return ch.lower()
    if ch.isalpha():
        return ch.casefold()
    return ch


def _title_maskable_count(title: str) -> int:
    return sum(1 for ch in title if _is_maskable(ch))


def _latin_letter_count(title: str) -> int:
    return sum(1 for ch in title if ch.isascii() and ch.isalpha())


@dataclass
class LetterSong:
    music_id: str
    title: str
    answers: List[str]
    solved: bool = False
    solved_by: str = ""

    def display(self, revealed: Set[str]) -> str:
        chars: List[str] = []
        for ch in self.title:
            if not _is_maskable(ch):
                chars.append(ch)
            elif _norm_token(ch) in revealed or self.solved:
                chars.append(ch)
            else:
                chars.append("?")
        return "".join(chars)

    def hidden_count(self, revealed: Set[str]) -> int:
        if self.solved:
            return 0
        return sum(
            1 for ch in self.title if _is_maskable(ch) and _norm_token(ch) not in revealed
        )

    def is_fully_revealed(self, revealed: Set[str]) -> bool:
        return self.hidden_count(revealed) == 0


@dataclass
class LetterBoard:
    songs: List[LetterSong]
    revealed: Set[str] = field(default_factory=set)
    opened_order: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    starter: str = ""
    # 标题字母已齐、等待下一回合抢开的曲目 id；下回合仍无人开歌则自动揭晓
    pending_auto: Set[str] = field(default_factory=set)

    @property
    def solved_count(self) -> int:
        return sum(1 for s in self.songs if s.solved)

    @property
    def finished(self) -> bool:
        return all(s.solved for s in self.songs)

    def flush_pending_auto(self) -> List[LetterSong]:
        """揭晓上一回合留下的、仍无人开歌的标题已齐曲目。"""
        newly: List[LetterSong] = []
        for song in self.songs:
            if song.music_id not in self.pending_auto or song.solved:
                continue
            song.solved = True
            song.solved_by = "字母揭完"
            newly.append(song)
        self.pending_auto.clear()
        return newly

    def queue_fully_revealed(self) -> List[LetterSong]:
        """本回合刚开齐的标题进入待抢开，不立刻自动揭晓。"""
        queued: List[LetterSong] = []
        for song in self.songs:
            if song.solved or song.music_id in self.pending_auto:
                continue
            if song.is_fully_revealed(self.revealed):
                self.pending_auto.add(song.music_id)
                queued.append(song)
        return queued


def _format_auto_reveal(songs: List[LetterSong]) -> str:
    if not songs:
        return ""
    names = "、".join(s.title for s in songs)
    return f"\n✅ 自动揭晓：{names}"


def _format_pending_claim(songs: List[LetterSong]) -> str:
    if not songs:
        return ""
    names = "、".join(s.title for s in songs)
    return f"\n⏳ 标题已齐，可抢开：{names}（下回合无人开则自动揭晓）"


class LetterGuessManager:
    """群开字母会话（与猜歌互斥）。"""

    Group: Dict[GroupId, LetterBoard] = {}

    def is_playing(self, gid: GroupId) -> bool:
        return gid in self.Group

    def is_busy_with_guess(self, gid: GroupId) -> bool:
        return guess.is_busy(gid) or self.is_playing(gid)

    def end(self, gid: GroupId) -> Optional[LetterBoard]:
        return self.Group.pop(gid, None)

    def get(self, gid: GroupId) -> Optional[LetterBoard]:
        return self.Group.get(gid)

    def _answers_for(self, music: Music) -> List[str]:
        answers: List[str] = []
        try:
            alias_rows = mai.total_alias_list.by_id(music.id)
            if alias_rows:
                answers.extend(str(a) for a in alias_rows[0].Alias)
        except Exception:
            pass
        answers.append(music.title)
        answers.append(str(music.id))
        # 去重保序
        seen: Set[str] = set()
        ordered: List[str] = []
        for a in answers:
            key = a.lower()
            if key in seen or not a:
                continue
            seen.add(key)
            ordered.append(a)
        return ordered

    def _pick_songs(self, count: int = BOARD_SIZE) -> List[Music]:
        pool = guess._guess_music_pool()
        # 优先拉丁字母较多的标题，玩法更接近截图
        latin_rich = [m for m in pool if _latin_letter_count(m.title) >= 4]
        maskable = [m for m in pool if _title_maskable_count(m.title) >= 3]
        primary = latin_rich or maskable or pool
        random.shuffle(primary)
        picked: List[Music] = []
        used_titles: Set[str] = set()
        for music in primary:
            title_key = music.title.strip().lower()
            if title_key in used_titles:
                continue
            if _title_maskable_count(music.title) < 2:
                continue
            picked.append(music)
            used_titles.add(title_key)
            if len(picked) >= count:
                break
        if len(picked) < count:
            rest = [m for m in pool if m not in picked]
            random.shuffle(rest)
            for music in rest:
                picked.append(music)
                if len(picked) >= count:
                    break
        return picked[:count]

    def start(self, gid: GroupId, *, starter: str = "", count: int = BOARD_SIZE) -> LetterBoard:
        musics = self._pick_songs(count)
        if len(musics) < 3:
            raise RuntimeError("可用曲目不足，暂时无法开局")
        songs = [
            LetterSong(
                music_id=str(m.id),
                title=m.title,
                answers=self._answers_for(m),
            )
            for m in musics
        ]
        board = LetterBoard(songs=songs, starter=starter)
        # 开局不预开任何字母
        self.Group[gid] = board
        titles = " / ".join(s.title for s in songs)
        log.info(f"[LetterGuess] 开局 gid={gid} songs={titles}")
        return board

    def open_letter(self, gid: GroupId, raw: str) -> Tuple[str, LetterBoard]:
        board = self.Group[gid]
        token = raw.strip()
        if not token:
            return "请发送要开的字母，例如：开字母 m", board
        # 只取第一个字符；兼容 "开字母 m" 已拆好的单字
        ch = token[0]
        if not _is_maskable(ch):
            return "只能开字母、数字或日文/汉字字符哦", board
        key = _norm_token(ch)

        # 新回合：先揭晓上一回合待抢开且无人开歌的曲
        flushed = board.flush_pending_auto()
        if key in board.revealed:
            msg = f"字母「{key}」已经开过了"
            msg += _format_auto_reveal(flushed)
            return msg, board

        board.revealed.add(key)
        board.opened_order.append(key)
        hit = 0
        for song in board.songs:
            if song.solved:
                continue
            hit += sum(1 for c in song.title if _norm_token(c) == key)
        queued = board.queue_fully_revealed()
        if hit <= 0:
            msg = f"没有「{key}」呢…"
        else:
            msg = f"开出「{key}」，揭示 {hit} 处"
        msg += _format_auto_reveal(flushed)
        msg += _format_pending_claim(queued)
        return msg, board

    def open_song(
        self, gid: GroupId, guess_text: str, *, solver: str = ""
    ) -> Tuple[str, LetterBoard, Optional[LetterSong], List[LetterSong]]:
        board = self.Group[gid]
        text = guess_text.strip()
        if not text:
            return "请发送歌名或别名，例如：开歌 conflict", board, None, []
        for song in board.songs:
            if song.solved:
                continue
            if match_guess_answer(text, song.answers):
                song.solved = True
                song.solved_by = solver or "开歌"
                board.pending_auto.discard(song.music_id)
                # 猜中时把该曲剩余字母也记入已开集合，便于展示
                for ch in song.title:
                    if _is_maskable(ch):
                        key = _norm_token(ch)
                        if key not in board.revealed:
                            board.revealed.add(key)
                            board.opened_order.append(key)
                # 本回合开到了歌：其余待抢开曲目视为无人开，自动揭晓
                flushed = board.flush_pending_auto()
                msg = f"✅ 猜中：{song.title}"
                msg += _format_auto_reveal(flushed)
                return msg, board, song, flushed
        # 未猜中也算一回合，揭晓上一回合遗留的待抢开
        flushed = board.flush_pending_auto()
        msg = "没有对上未解开的歌，再想想？"
        msg += _format_auto_reveal(flushed)
        return msg, board, None, flushed

    def reveal_all(self, board: LetterBoard) -> None:
        board.pending_auto.clear()
        for song in board.songs:
            song.solved = True
            if not song.solved_by:
                song.solved_by = "结束"


letter_guess = LetterGuessManager()


def _board_font() -> Path:
    for candidate in (
        SIYUAN,
        TBFONT,
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        try:
            path = Path(candidate)
            if path.exists():
                return path
        except Exception:
            continue
    return Path(SIYUAN)


def render_letter_board(board: LetterBoard) -> Image.Image:
    """绘制开字母看板图。"""
    width = 900
    row_h = 52
    header_h = 110
    footer_h = 150
    height = header_h + row_h * len(board.songs) + footer_h
    im = Image.new("RGBA", (width, height), _BG)
    dr = ImageDraw.Draw(im)
    # 曲名可能含中日文/扩展拉丁，必须用 CJK 字体；Torus 等西文字体会豆腐字。
    font_path = _board_font()
    title_font = DrawText(dr, font_path)

    dr.rounded_rectangle((24, 24, width - 24, height - 24), radius=22, fill=_CARD)
    title_font.draw(48, 44, 40, "舞萌开字母", _TITLE, "lt", 2, (0, 0, 0, 120))
    title_font.draw(
        48,
        92,
        18,
        f"进度 {board.solved_count}/{len(board.songs)}  ·  已开 {len(board.revealed)} 个字符",
        _MUTED,
        "lt",
    )

    y = header_h
    for idx, song in enumerate(board.songs, 1):
        if idx > 1:
            dr.line((48, y, width - 48, y), fill=_LINE, width=1)
        if song.solved:
            icon, color = "[OK]", _OK
        elif song.music_id in board.pending_auto:
            icon, color = "[抢]", _WAIT
        else:
            icon, color = "[??]", _WAIT
        shown = song.display(board.revealed)
        title_font.draw(56, y + 14, 22, f"{idx}.", _MUTED, "lt")
        title_font.draw(96, y + 14, 22, icon, color, "lt")
        title_font.draw(170, y + 14, 24, shown, _TEXT, "lt")
        y += row_h

    opened = ", ".join(board.opened_order) if board.opened_order else "（还没有）"
    title_font.draw(48, y + 18, 22, "已开出字母:", _TITLE, "lt")
    # 字母列表可能很长，自动换行
    max_w = width - 96
    line = ""
    lines: List[str] = []
    for part in opened.split(", "):
        trial = part if not line else f"{line}, {part}"
        # 粗略按字符数折行
        if len(trial) > 48:
            if line:
                lines.append(line)
            line = part
        else:
            line = trial
    if line:
        lines.append(line)
    ty = y + 52
    for row in lines[:4]:
        title_font.draw(48, ty, 18, row, _MUTED, "lt")
        ty += 26

    title_font.draw(
        48,
        height - 52,
        16,
        "直接发字母 / 别名  ·  不玩了",
        _MUTED,
        "lt",
    )
    return im


def board_image_segment(board: LetterBoard):
    from nonebot.adapters.onebot.v11 import MessageSegment

    return MessageSegment.image(image_to_base64(render_letter_board(board)))


def points_for_song_solve(hidden_before: int) -> int:
    return max(6, min(16, 6 + hidden_before))


def points_for_letter_hit(hit_count: int) -> int:
    if hit_count <= 0:
        return 0
    return min(6, 1 + hit_count // 2)
