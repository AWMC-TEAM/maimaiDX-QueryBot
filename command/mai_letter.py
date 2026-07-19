"""舞萌开字母命令。"""

from __future__ import annotations

import re

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..config import log
from ..libraries.maimaidx_guess_letter import (
    BOARD_SIZE,
    LetterBoard,
    LetterSettlement,
    _is_maskable,
    board_image_segment,
    format_elapsed,
    format_settlement_message,
    letter_guess,
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
    "· 局内不计分；全部解开后按用时星级 + 贡献结算积分/BREAK\n"
    "· 用时星级：≤30s⭐️×5 / ≤45s×4 / ≤60s×3 / ≤90s×2 / ≤180s×1；更慢为最低档\n"
    "· 贡献：有效开字母×1、补齐曲×3、开歌×4；无贡献不得分\n"
    "· 不玩了 — 结束并揭晓剩余（不发奖）\n"
    "与猜歌等模式同群互斥；需先「开启mai猜歌」。"
)


async def _payout_settlement(event: MessageEvent, gid, settlement: LetterSettlement) -> str:
    """通关结算发奖：固定积分 + 自定义 BREAK。"""
    from ..libraries.maimaidx_break import break_db

    text = format_settlement_message(settlement)
    if not settlement.rewards:
        return text
    detail_lines: list[str] = []
    for reward in settlement.rewards:
        if reward.score > 0:
            added, total, rank = await guess_score.award_fixed_points(
                gid, reward.uid, reward.name, reward.score
            )
            detail_lines.append(
                f"· {reward.name} 积分 +{added}（总分 {total}，总榜第 {rank}）"
            )
        if reward.break_points > 0:
            balance = break_db.add_balance(
                reward.billing_id,
                reward.break_points,
                "letter_settlement",
                meta={
                    "group_id": str(gid),
                    "elapsed": settlement.elapsed,
                    "stars": settlement.stars,
                    "weight": reward.weight,
                },
            )
            detail_lines.append(
                f"· {reward.name} BREAK +{reward.break_points}（余额 {balance}）"
            )
    if detail_lines:
        text += "\n" + "\n".join(detail_lines)
    return text


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


async def _maybe_finish_board(
    matcher, event: MessageEvent, gid, board: LetterBoard, parts: list[str]
) -> None:
    if not board.finished:
        await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
        await matcher.finish()
        return
    settlement = board.settle()
    letter_guess.end(gid)
    parts.append(await _payout_settlement(event, gid, settlement))
    await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
    await matcher.finish()


async def _apply_open_letter(matcher, event: MessageEvent, gid, raw: str) -> None:
    await _finish_rate_limited(matcher, event)
    board = letter_guess.get(gid)
    assert board is not None
    key = raw.strip()[0]
    if not _is_maskable(key):
        await matcher.finish("只能开字母、数字或日文/汉字字符哦", reply_message=True)
    solver = get_sender_display_name(event)
    msg, board, _completed, _hidden_before = letter_guess.open_letter(
        gid,
        raw,
        solver=solver,
        uid=platform_user_id(event),
        billing_id=billing_user_id(event),
    )
    await _maybe_finish_board(matcher, event, gid, board, [msg])


async def _apply_open_song(matcher, event: MessageEvent, gid, text: str) -> bool:
    """尝试开歌；猜中返回 True，未中返回 False（由调用方决定是否提示）。"""
    await _finish_rate_limited(matcher, event)
    board = letter_guess.get(gid)
    if board is None:
        return False
    msg, board, song, _completed, _hidden_before = letter_guess.open_song(
        gid,
        text,
        solver=get_sender_display_name(event),
        uid=platform_user_id(event),
        billing_id=billing_user_id(event),
    )
    if song is None:
        return False
    await _maybe_finish_board(matcher, event, gid, board, [msg])
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
            "全部解开后按用时与贡献结算；局内不计分。\n"
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
    elapsed_text = format_elapsed(board.elapsed())
    letter_guess.reveal_all(board)
    letter_guess.end(gid)
    await _send_board(
        letter_quit,
        event,
        board,
        text=(
            f"🔚 本局结束（用时 {elapsed_text}），剩余歌曲已揭晓。\n"
            "中途结束不发放速度奖与贡献奖。\n"
        ),
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
