"""舞萌开字母命令。"""

from __future__ import annotations

import re

from nonebot import on_command, on_message
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
    points_for_letter_complete,
    points_for_letter_hit,
    points_for_song_solve,
)
from ..libraries.maimaidx_guess_rate_limit import consume_guess_answer_slot
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

_RESERVED_PREFIXES = (
    "开字母",
    "开歌",
    "不玩了",
    "结束开字母",
    "舞萌开字母",
    "开字母看板",
    "重置猜歌",
    "猜歌",
    "猜曲绘",
    "猜曲子",
    "猜铺面",
    "猜谱面",
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
# 对局中可直接发字母 / 别名，无需命令前缀
letter_quick = on_message(rule=LETTER_PLAYING, priority=9, block=False)

_HELP = (
    "【舞萌开字母】\n"
    f"· 发送「舞萌开字母」或「开字母」开局（{BOARD_SIZE} 首歌）\n"
    "· 对局中直接发字母（如 m）即可开字符\n"
    "· 直接发曲名/别名即可猜歌（也可「开歌 xxx」）\n"
    "· 字母补齐标题时，得分记在补齐者身上\n"
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


def _plain_guess_text(event: MessageEvent) -> str:
    text = event.get_plaintext().strip()
    # 去掉开头 @机器人
    text = re.sub(r"^@\S+\s*", "", text).strip()
    return text


def _is_reserved_command(text: str) -> bool:
    if not text:
        return True
    for prefix in _RESERVED_PREFIXES:
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix):
            # 「开字母m」无空格也算命令，留给 command matcher
            if text.startswith(prefix):
                return True
    return False


async def _finish_rate_limited(matcher, event: MessageEvent) -> None:
    msg = consume_guess_answer_slot(platform_user_id(event))
    if msg:
        await matcher.finish(
            adapt_guess_outbound(msg, event=event),
            reply_message=resolve_reply_message(event, reply_message=True),
        )


async def _apply_open_letter(matcher, event: MessageEvent, gid, raw: str) -> None:
    await _finish_rate_limited(matcher, event)
    board = letter_guess.get(gid)
    assert board is not None
    key = raw.strip()[0]
    if not _is_maskable(key):
        await matcher.finish("只能开字母、数字或日文/汉字字符哦", reply_message=True)
    norm = _norm_token(key)
    already = norm in board.revealed
    hit = 0
    if not already:
        for song in board.songs:
            if song.solved:
                continue
            hit += sum(1 for c in song.title if _norm_token(c) == norm)
    solver = get_sender_display_name(event)
    msg, board, completed, hidden_before = letter_guess.open_letter(
        gid, raw, solver=solver
    )
    parts = [msg]
    pts = 0 if already else points_for_letter_hit(hit)
    for song in completed:
        pts += points_for_letter_complete(hidden_before.get(song.music_id, 0))
    if pts > 0:
        tag = "字母补齐" if completed else "开字母"
        settlement = await _award_points(event, gid, pts, tag=tag)
        if settlement:
            parts.append(settlement)
    if board.finished:
        letter_guess.end(gid)
        parts.append("🎉 全部解开，本局结束！")
    await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
    await matcher.finish()


async def _apply_open_song(matcher, event: MessageEvent, gid, text: str) -> bool:
    """尝试开歌；猜中返回 True，未中返回 False（由调用方决定是否提示）。"""
    await _finish_rate_limited(matcher, event)
    board = letter_guess.get(gid)
    if board is None:
        return False
    hidden_map = {s.music_id: s.hidden_count(board.revealed) for s in board.songs}
    msg, board, song = letter_guess.open_song(
        gid, text, solver=get_sender_display_name(event)
    )
    if song is None:
        return False
    parts = [msg]
    pts = points_for_song_solve(hidden_map.get(song.music_id, 0))
    settlement = await _award_points(event, gid, pts, tag="开歌")
    if settlement:
        parts.append(settlement)
    if board.finished:
        letter_guess.end(gid)
        parts.append("🎉 全部解开，本局结束！")
    await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
    await matcher.finish()
    return True


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

    if letter_guess.is_playing(gid):
        if not raw:
            board = letter_guess.get(gid)
            await _send_board(
                letter_open,
                event,
                board,
                text="当前开字母进行中。直接发字母或别名即可。\n",
            )
            await letter_open.finish()
        if len(raw) == 1 and _is_maskable(raw):
            await _apply_open_letter(letter_open, event, gid, raw)
        if await _apply_open_song(letter_open, event, gid, raw):
            return
        await letter_open.finish(
            "一次开一个字符（如：m / 开字母 m），或直接发曲名/别名猜歌。",
            reply_message=True,
        )

    if raw:
        if len(raw) == 1 and _is_maskable(raw):
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
            "直接发字母开字符，直接发别名/曲名猜歌。\n"
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
    if not text:
        await letter_song.finish("请发送歌名或别名，或对局中直接发别名。", reply_message=True)
    if await _apply_open_song(letter_song, event, gid, text):
        return
    board = letter_guess.get(gid)
    await _send_board(
        letter_song,
        event,
        board,
        text="没有对上未解开的歌，再想想？\n",
    )
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


@letter_quick.handle()
async def _(event: MessageEvent):
    """对局中：单字开字母，其它短文本尝试开歌。"""
    gid = get_event_group_id(event)
    if gid is None or not letter_guess.is_playing(gid):
        return
    text = _plain_guess_text(event)
    if not text or _is_reserved_command(text):
        return

    # 单字符 → 开字母
    if len(text) == 1 and _is_maskable(text):
        await _apply_open_letter(letter_quick, event, gid, text)
        return

    # 多字符 → 仅正确别名/曲名才开歌；未命中与猜曲绘一样静默
    if 2 <= len(text) <= 48:
        await _apply_open_song(letter_quick, event, gid, text)
