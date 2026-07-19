"""舞萌开字母命令。"""

from __future__ import annotations

import re

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..config import log
from ..libraries.maimaidx_guess_boost_card import guess_boost_card
from ..libraries.maimaidx_guess_letter import (
    BOARD_SIZE,
    _is_maskable,
    _norm_token,
    board_image_segment,
    letter_guess,
    points_for_letter_hit,
    points_for_song_solve,
)
from ..libraries.maimaidx_guess_score import guess_score
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_platform import (
    adapt_guess_outbound,
    billing_user_id,
    get_event_group_id,
    get_sender_display_name,
    is_group_message_event,
    platform_user_id,
    resolve_reply_message,
)


def _is_group_message(event) -> bool:
    return is_group_message_event(event)


def _is_letter_playing(event) -> bool:
    gid = get_event_group_id(event)
    return gid is not None and letter_guess.is_playing(gid)


GROUP_MESSAGE = Rule(_is_group_message)
LETTER_PLAYING = Rule(_is_group_message) & Rule(_is_letter_playing)

letter_start = on_command(
    "舞萌开字母", aliases={"开字母看板"}, rule=GROUP_MESSAGE, priority=5, block=True
)
letter_open = on_command("开字母", rule=GROUP_MESSAGE, priority=5, block=True)
letter_song = on_command("开歌", rule=LETTER_PLAYING, priority=5, block=True)
letter_quit = on_command(
    "不玩了", aliases={"结束开字母"}, rule=LETTER_PLAYING, priority=5, block=True
)

_HELP = (
    "【舞萌开字母】\n"
    f"· 发送「舞萌开字母」或「开字母」开局（{BOARD_SIZE} 首歌）\n"
    "· 开字母 x — 揭示字母/数字/汉字\n"
    "· 开歌 <曲名或别名> — 猜中整首歌\n"
    "· 不玩了 — 结束并揭晓剩余\n"
    "与猜歌等模式同群互斥；需先「开启mai猜歌」。"
)


async def _award_points(event: MessageEvent, gid, raw_base: int, *, tag: str) -> str:
    if raw_base <= 0:
        return ""
    multiplier, tags = guess_score.get_score_multiplier(
        first_stage=False, first_guess=False
    )
    uid = platform_user_id(event)
    if await guess_boost_card.consume_one(gid, uid):
        multiplier *= 2
        tags.append("限时加倍卡×2")
    tags.append(tag)
    (
        added,
        raw_base,
        combo,
        streak,
        total,
        rank,
        period_snapshot,
    ) = await guess_score.award_correct_guess(
        gid,
        uid,
        get_sender_display_name(event),
        raw_base,
        multiplier,
    )
    settlement = guess_score.format_settlement_lines(
        added,
        raw_base,
        combo,
        multiplier,
        streak,
        total,
        rank,
        period_snapshot,
        tags,
    )
    from ..libraries.maimaidx_break import break_db

    reward = break_db.award_guess_points(
        billing_user_id(event), added, group_id=str(gid)
    )
    if reward.break_added > 0:
        settlement += (
            f"\n💳 猜对奖励 +{reward.break_added} BREAK（余额 {reward.balance}）"
        )
    return settlement


async def _send_board(matcher, event: MessageEvent, board, *, text: str = "") -> None:
    msg = board_image_segment(board)
    if text:
        msg = text + msg
    await matcher.send(
        adapt_guess_outbound(msg, event=event),
        reply_message=resolve_reply_message(event, reply_message=True),
    )


def _ensure_enabled(gid) -> str | None:
    if gid not in guess.switch.enable:
        return "本群未开启猜歌功能，请管理员发送「开启mai猜歌」"
    return None


@letter_start.handle()
@letter_open.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    gid = get_event_group_id(event)
    if gid is None:
        return
    raw = args.extract_plain_text().strip()
    enabled_err = _ensure_enabled(gid)
    if enabled_err:
        await letter_open.finish(enabled_err, reply_message=True)

    # 进行中：开字母 x
    if letter_guess.is_playing(gid):
        if not raw:
            board = letter_guess.get(gid)
            await _send_board(
                letter_open,
                event,
                board,
                text="当前开字母进行中。用法：开字母 x\n",
            )
            await letter_open.finish()
        if len(raw) > 4:
            await letter_open.finish(
                "一次只开一个字符，例如：开字母 m", reply_message=True
            )
        # 统计命中数用于计分
        board = letter_guess.get(gid)
        assert board is not None
        key = raw.strip()[0]
        if not _is_maskable(key):
            await letter_open.finish("只能开字母、数字或日文/汉字字符哦", reply_message=True)
        norm = _norm_token(key)
        already = norm in board.revealed
        hit = 0
        if not already:
            for song in board.songs:
                if song.solved:
                    continue
                hit += sum(1 for c in song.title if _norm_token(c) == norm)
        msg, board = letter_guess.open_letter(gid, raw)
        parts = [msg]
        pts = 0 if already else points_for_letter_hit(hit)
        if pts > 0:
            settlement = await _award_points(event, gid, pts, tag="开字母")
            if settlement:
                parts.append(settlement)
        if board.finished:
            letter_guess.end(gid)
            parts.append("🎉 全部解开，本局结束！")
        await _send_board(letter_open, event, board, text="\n".join(parts) + "\n")
        await letter_open.finish()

    # 未开局：无参数则开局；有参数则提示先开局
    if raw:
        if re.fullmatch(r"[A-Za-z0-9]", raw) or len(raw) == 1:
            await letter_open.finish(
                "还没有开局。请先发送「舞萌开字母」或「开字母」。",
                reply_message=True,
            )
        await letter_open.finish(_HELP, reply_message=True)

    if guess.is_busy(gid):
        await letter_open.finish(
            "该群已有正在进行的猜歌/猜曲绘/猜曲子/猜铺面，请先结束或「重置猜歌」。",
            reply_message=True,
        )
    try:
        board = letter_guess.start(
            gid, starter=get_sender_display_name(event), count=BOARD_SIZE
        )
    except Exception as exc:
        log.warning(f"[LetterGuess] 开局失败：{type(exc).__name__}: {exc}")
        await letter_open.finish(f"开局失败：{exc}", reply_message=True)
    await _send_board(
        letter_open,
        event,
        board,
        text=(
            f"🎮 舞萌开字母开始！共 {len(board.songs)} 首歌。\n"
            "发送「开字母 x」开字符，「开歌 曲名」猜整首。\n"
        ),
    )
    await letter_open.finish()


@letter_song.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    gid = get_event_group_id(event)
    if gid is None:
        return
    if not letter_guess.is_playing(gid):
        await letter_song.finish(
            "当前没有开字母对局。发送「舞萌开字母」开始。", reply_message=True
        )
    enabled_err = _ensure_enabled(gid)
    if enabled_err:
        await letter_song.finish(enabled_err, reply_message=True)

    text = args.extract_plain_text().strip()
    board = letter_guess.get(gid)
    assert board is not None
    # 先算隐藏数再开歌
    hidden_map = {s.music_id: s.hidden_count(board.revealed) for s in board.songs}
    msg, board, song = letter_guess.open_song(
        gid, text, solver=get_sender_display_name(event)
    )
    parts = [msg]
    if song is not None:
        pts = points_for_song_solve(hidden_map.get(song.music_id, 0))
        settlement = await _award_points(event, gid, pts, tag="开歌")
        if settlement:
            parts.append(settlement)
    if board.finished:
        letter_guess.end(gid)
        parts.append("🎉 全部解开，本局结束！")
    await _send_board(letter_song, event, board, text="\n".join(parts) + "\n")
    await letter_song.finish()


@letter_quit.handle()
async def _(event: MessageEvent):
    gid = get_event_group_id(event)
    if gid is None:
        return
    board = letter_guess.get(gid)
    if board is None:
        await letter_quit.finish()
    letter_guess.reveal_all(board)
    letter_guess.end(gid)
    await _send_board(
        letter_quit,
        event,
        board,
        text="🔚 本局结束，剩余歌曲已揭晓。\n",
    )
    await letter_quit.finish()
