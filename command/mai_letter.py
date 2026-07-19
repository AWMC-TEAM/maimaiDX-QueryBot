"""舞萌开字母命令。"""

from __future__ import annotations

import json
import re

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..config import log, maiconfig
from ..libraries.maimaidx_group_rating import build_forward_node
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
from ..libraries.maimaidx_letter_rank_draw import (
    image_b64,
    render_contrib_board,
    render_round_boards,
    render_score_board,
    render_time_board,
)
from ..libraries.maimaidx_letter_stats import letter_stats
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_platform import (
    adapt_guess_outbound,
    billing_user_id,
    get_event_group_id,
    get_sender_display_name,
    is_group_message_event,
    is_likely_qq_group_id,
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
    "开字母排行",
    "开字母积分榜",
    "开字母贡献榜",
    "开字母时间榜",
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
letter_score_cmd = on_command(
    "开字母排行", aliases={"开字母积分榜"}, rule=GROUP_MESSAGE, priority=5, block=True
)
letter_contrib_cmd = on_command(
    "开字母贡献榜", rule=GROUP_MESSAGE, priority=5, block=True
)
letter_time_cmd = on_command(
    "开字母时间榜", rule=GROUP_MESSAGE, priority=5, block=True
)
# 对局中可直接发字母 / 别名，无需命令前缀
letter_quick = on_message(rule=LETTER_PLAYING, priority=9, block=False)

_HELP = (
    "【舞萌开字母】\n"
    f"· 发送「舞萌开字母」或「开字母」开局（{BOARD_SIZE} 首歌）\n"
    "· 对局中直接发字母（如 m）即可开字符\n"
    "· 直接发曲名/别名即可猜歌（也可「开歌 xxx」）\n"
    "· 局内不计分；全部解开后按用时星级 + 贡献结算积分/BREAK\n"
    "· 星级阈值自适应：默认 ≤30/45/60/90/180 秒；群通关变快后五星上限可降至最低 15 秒\n"
    "· 贡献：有效开字母×1、补齐曲×3、开歌×4；无贡献不得分\n"
    "· 开字母排行 / 开字母贡献榜 / 开字母时间榜 — 查看本群榜单图\n"
    "· 不玩了 — 结束并揭晓剩余（不发奖）\n"
    "与猜歌等模式同群互斥；需先「开启mai猜歌」。"
)


def _forward_image_node(user_id: str, nickname: str, image_b64_str: str, caption: str = "") -> dict:
    content: list[dict] = [{"type": "image", "data": {"file": image_b64_str}}]
    if caption.strip():
        content.append({"type": "text", "data": {"text": "\n" + caption.strip()}})
    return {
        "type": "node",
        "data": {
            "name": str(nickname),
            "uin": str(user_id),
            "content": content,
        },
    }


async def _send_forward(bot: Bot, event: MessageEvent, nodes: list[dict]) -> bool:
    gid = get_event_group_id(event)
    if gid is None or not nodes:
        return False
    messages = json.loads(json.dumps(nodes, ensure_ascii=False))
    try:
        if is_likely_qq_group_id(gid):
            await bot.call_api(
                "send_group_forward_msg", group_id=int(gid), messages=messages
            )
            return True
    except Exception as exc:
        log.warning(f"[LetterGuess] 合并转发失败：{type(exc).__name__}: {exc}")
    # 降级：逐张发图
    for node in nodes:
        try:
            data = node.get("data") or {}
            content = data.get("content") or []
            msg = Message()
            for seg in content:
                if seg.get("type") == "image":
                    msg += MessageSegment.image(seg["data"]["file"])
                elif seg.get("type") == "text":
                    msg += str(seg.get("data", {}).get("text", ""))
            if msg:
                await bot.send(event, adapt_guess_outbound(msg, event=event))
        except Exception as exc:
            log.warning(f"[LetterGuess] 降级发图失败：{type(exc).__name__}: {exc}")
    return False


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
    text = re.sub(r"^@\S+\s*", "", text).strip()
    return text


def _is_reserved_command(text: str) -> bool:
    if not text:
        return True
    for prefix in _RESERVED_PREFIXES:
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix):
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


async def _send_settlement_charts(
    bot: Bot, event: MessageEvent, settlement: LetterSettlement
) -> None:
    nickname = str(getattr(maiconfig, "botName", None) or "AWMC Bot")
    score_rows = [
        (r.uid, r.billing_id, r.name, r.score, r.weight) for r in settlement.rewards
    ]
    contrib_rows = [
        (r.uid, r.billing_id, r.name, r.weight) for r in settlement.rewards
    ]
    # 时间榜：本局参与者按权重排序，用时同一通关时间
    time_rows = [
        (r.uid, r.billing_id, r.name, settlement.elapsed)
        for r in sorted(settlement.rewards, key=lambda x: (-x.weight, x.name))
    ]
    try:
        score_im, contrib_im, time_im = await render_round_boards(
            score_rows=score_rows,
            contrib_rows=contrib_rows,
            time_rows=time_rows,
            elapsed_text=settlement.elapsed_text,
            stars_text=settlement.stars_text,
        )
    except Exception as exc:
        log.warning(f"[LetterGuess] 结算绘图失败：{type(exc).__name__}: {exc}")
        return
    nodes = [
        build_forward_node(
            str(event.self_id),
            nickname,
            format_settlement_message(settlement),
        ),
        _forward_image_node(
            str(event.self_id), nickname, image_b64(score_im), "本局积分"
        ),
        _forward_image_node(
            str(event.self_id), nickname, image_b64(contrib_im), "本局贡献"
        ),
        _forward_image_node(
            str(event.self_id), nickname, image_b64(time_im), "本局通关用时（含头像）"
        ),
    ]
    await _send_forward(bot, event, nodes)


async def _maybe_finish_board(
    matcher, event: MessageEvent, gid, board: LetterBoard, parts: list[str]
) -> None:
    if not board.finished:
        await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
        await matcher.finish()
        return
    th = letter_stats.thresholds_for(gid)
    settlement = board.settle(
        limits=th.limits,
        adaptive=th.adaptive,
        sample_count=th.sample_count,
    )
    letter_guess.end(gid)
    await letter_stats.record_clear(
        gid,
        elapsed=settlement.elapsed,
        stars=settlement.stars,
        score_pool=settlement.score_pool,
        break_pool=settlement.break_pool,
        rewards=[
            (r.uid, r.billing_id, r.name, r.score, r.weight)
            for r in settlement.rewards
        ],
    )
    parts.append(await _payout_settlement(event, gid, settlement))
    await _send_board(matcher, event, board, text="\n".join(parts) + "\n")
    try:
        bot = matcher.bot if hasattr(matcher, "bot") else None
        if bot is None:
            from nonebot import get_bot

            bot = get_bot()
        await _send_settlement_charts(bot, event, settlement)
    except Exception as exc:
        log.warning(f"[LetterGuess] 结算图发送失败：{type(exc).__name__}: {exc}")
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
    th = letter_stats.thresholds_for(gid)
    await _send_board(
        letter_open,
        event,
        board,
        text=(
            f"🎮 舞萌开字母开始！共 {len(board.songs)} 首歌。\n"
            "直接发字母开字符，直接发别名/曲名猜歌。\n"
            "全部解开后按用时与贡献结算；局内不计分。\n"
            f"{th.format_lines()}\n"
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


@letter_score_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    gid = get_event_group_id(event)
    if gid is None:
        return
    err = _ensure_enabled(gid)
    if err:
        await letter_score_cmd.finish(err, reply_message=True)
    rows = letter_stats.score_ranking(gid)
    if not rows:
        await letter_score_cmd.finish("本群暂无开字母积分记录。", reply_message=True)
    th = letter_stats.thresholds_for(gid)
    im = await render_score_board(rows, subtitle=th.format_lines())
    await letter_score_cmd.finish(
        adapt_guess_outbound(MessageSegment.image(image_b64(im)), event=event),
        reply_message=resolve_reply_message(event, reply_message=True),
    )


@letter_contrib_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    gid = get_event_group_id(event)
    if gid is None:
        return
    err = _ensure_enabled(gid)
    if err:
        await letter_contrib_cmd.finish(err, reply_message=True)
    rows = letter_stats.contrib_ranking(gid)
    if not rows:
        await letter_contrib_cmd.finish("本群暂无开字母贡献记录。", reply_message=True)
    im = await render_contrib_board(rows)
    await letter_contrib_cmd.finish(
        adapt_guess_outbound(MessageSegment.image(image_b64(im)), event=event),
        reply_message=resolve_reply_message(event, reply_message=True),
    )


@letter_time_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    gid = get_event_group_id(event)
    if gid is None:
        return
    err = _ensure_enabled(gid)
    if err:
        await letter_time_cmd.finish(err, reply_message=True)
    rows = letter_stats.time_ranking(gid)
    if not rows:
        await letter_time_cmd.finish("本群暂无开字母通关用时记录。", reply_message=True)
    im = await render_time_board(rows)
    await letter_time_cmd.finish(
        adapt_guess_outbound(MessageSegment.image(image_b64(im)), event=event),
        reply_message=resolve_reply_message(event, reply_message=True),
    )


@letter_quick.handle()
async def _(event: MessageEvent):
    """对局中：单字开字母，其它短文本尝试开歌。"""
    gid = get_event_group_id(event)
    if gid is None or not letter_guess.is_playing(gid):
        return
    text = _plain_guess_text(event)
    if not text or _is_reserved_command(text):
        return

    if len(text) == 1 and _is_maskable(text):
        await _apply_open_letter(letter_quick, event, gid, text)
        return

    if 2 <= len(text) <= 48:
        await _apply_open_song(letter_quick, event, gid, text)
