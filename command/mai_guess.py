import asyncio
import json
from pathlib import Path
from typing import Optional

from loguru import logger as log
from nonebot import get_bot, on_command, on_message, on_regex
from nonebot.adapters.onebot.v11 import Bot, GROUP_ADMIN, GROUP_OWNER, GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, RegexMatched
from nonebot.permission import SUPERUSER

from ..libraries.maimaidx_guess_match import match_guess_answer
from ..libraries.maimaidx_group_rating import build_forward_node
from ..libraries.maimaidx_guess_score import guess_score
from ..libraries.maimaidx_guess_audio import (
    STAGE_FINAL_GRACE,
    STAGE_INTERVAL,
    STAGE_LABELS,
    build_hot_audio_cache,
    get_audio_manifest_entry,
    request_hot_batch_cancel,
)
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_model import GuessAudioData, GuessData, GuessDefaultData, GuessPicData
from ..libraries.maimaidx_music_info import *
from ..libraries.maimaidx_update_plate import *


def is_now_playing_guess_music(event: GroupMessageEvent) -> bool:
    return event.group_id in guess.Group

guess_music_start   = on_command('猜歌')
guess_music_pic     = on_command('猜曲绘')
guess_music_audio   = on_command('猜曲子')
update_guess_audio  = on_regex(r'^更新猜曲音频(?:\s+(-full))?\s*$', permission=SUPERUSER)
guess_music_solve   = on_message(rule=is_now_playing_guess_music)
guess_music_reset   = on_command('重置猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_music_enable  = on_command('开启mai猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_music_disable = on_command('关闭mai猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_score_rank    = on_command('猜歌积分排行')
guess_score_daily   = on_command('猜歌积分日榜')
guess_score_weekly  = on_command('猜歌积分周榜')
guess_score_monthly = on_command('猜歌积分月榜')
guess_score_yearly  = on_command('猜歌积分年榜')
guess_score_season  = on_command('猜歌积分赛季榜')
guess_score_hist_daily   = on_command('猜歌历史日榜')
guess_score_hist_weekly  = on_command('猜歌历史周榜')
guess_score_hist_monthly = on_command('猜歌历史月榜')
guess_score_hist_yearly  = on_command('猜歌历史年榜')
guess_score_hist_season  = on_command('猜歌历史赛季榜')


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


async def _award_guess_points(
    event: GroupMessageEvent,
    data: GuessData,
    *,
    first_stage: bool,
    first_guess: bool,
) -> str:
    if isinstance(data, GuessPicData):
        raw_base = guess_score.pic_points_for(data)
    elif isinstance(data, GuessAudioData):
        raw_base = guess_score.audio_points_for(data.hint_step)
    elif isinstance(data, GuessDefaultData):
        raw_base = guess_score.song_points_for(data.hint_step)
    else:
        raw_base = 1
    multiplier, multiplier_tags = guess_score.get_score_multiplier(
        first_stage=first_stage,
        first_guess=first_guess,
    )
    if isinstance(data, GuessAudioData) and guess_score.audio_season_double_active():
        multiplier *= 2
        multiplier_tags.append('赛季限时双倍得分')
    (
        added, raw_base, combo, streak, total, rank, period_snapshot,
    ) = await guess_score.award_correct_guess(
        event.group_id,
        event.user_id,
        _sender_name(event),
        raw_base,
        multiplier,
    )
    return guess_score.format_settlement_lines(
        added, raw_base, combo, multiplier, streak, total, rank, period_snapshot,
        multiplier_tags,
    )


async def _safe_matcher_send(
    matcher: Matcher,
    event: GroupMessageEvent,
    message,
    *,
    reply: bool = False,
) -> None:
    try:
        await matcher.send(message, reply_message=reply)
    except Exception as e:
        log.warning(f'[maimai] 猜歌消息发送失败: {type(e).__name__}: {e}')
        if reply:
            await matcher.send(message, reply_message=False)


async def _send_guess_answer_bundle(
    matcher: Matcher,
    event: GroupMessageEvent,
    data: GuessData,
    *,
    header: str,
    settlement: str = '',
    reply: bool = False,
) -> None:
    lines = [line for line in (header, settlement) if line]
    if lines:
        await _safe_matcher_send(
            matcher, event,
            MessageSegment.text('\n'.join(lines)),
            reply=reply,
        )
    await _safe_matcher_send(matcher, event, await draw_music_info(data.music))
    if isinstance(data, GuessPicData):
        await _safe_matcher_send(
            matcher, event,
            MessageSegment.image(guess.render_pic_reveal(data)),
        )
    if (
        isinstance(data, GuessAudioData)
        and data.hint_step < data.stage_count
        and data.stage_paths
    ):
        final_idx = data.stage_count - 1
        stage_path = Path(data.stage_paths[final_idx]).resolve()
        label = (
            STAGE_LABELS[final_idx]
            if final_idx < len(STAGE_LABELS)
            else '完整混音'
        )
        await _safe_matcher_send(
            matcher, event,
            MessageSegment.text(f'[{label}]\n')
            + MessageSegment.record(str(stage_path)),
        )


async def _send_guess_score_forward(
    matcher: Matcher,
    bot: Bot,
    event: GroupMessageEvent,
    title: str,
    nodes: list,
) -> None:
    if not nodes:
        await matcher.finish(title, reply_message=True)
    nickname = str(getattr(bot, 'nickname', None) or 'Bot')
    title_node = build_forward_node(str(event.self_id), nickname, title)
    all_nodes = [title_node] + nodes
    try:
        messages = json.loads(json.dumps(all_nodes, ensure_ascii=False))
        await bot.call_api(
            'send_group_forward_msg',
            group_id=event.group_id,
            messages=messages,
        )
    except TypeError as e:
        log.warning(f'[maimai] 猜歌积分排行 合并转发序列化失败: {e}')
        await matcher.finish('合并转发序列化失败，请稍后再试。', reply_message=True)
    except Exception as e:
        log.warning(f'[maimai] 猜歌积分排行 合并转发发送失败: {type(e).__name__}: {e}')
        await matcher.finish('合并转发发送失败，请稍后再试。', reply_message=True)
    await matcher.finish(reply_message=True)


_GUESS_BUSY_HINT = '该群已有正在进行的猜歌、猜曲绘或猜曲子'


@guess_music_start.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_start.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
    if gid in guess.Group:
        await guess_music_start.finish(_GUESS_BUSY_HINT)
    guess.start(gid)
    await guess_music_start.send(
        dedent('''\
            我将从热门乐曲中选择一首歌，每隔8秒描述它的特征，
            请输入歌曲的 id 标题 或 别名（需bot支持，无需大小写）进行猜歌（DX乐谱和标准乐谱视为两首歌）。
            猜歌时查歌等其他命令依然可用。
        ''')
    )
    await asyncio.sleep(4)
    for cycle in range(7):
        if event.group_id not in guess.switch.enable or gid not in guess.Group or guess.Group[gid].end:
            break
        if cycle < 6:
            await guess_music_start.send(f'{cycle + 1}/7 这首歌{guess.Group[gid].options[cycle]}')
            guess.Group[gid].hint_step = cycle + 1
            await asyncio.sleep(8)
        else:
            await guess_music_start.send(
                MessageSegment.text('7/7 这首歌封面的一部分是：\n') + 
                MessageSegment.image(guess.Group[gid].img) + 
                MessageSegment.text('答案将在30秒后揭晓')
            )
            guess.Group[gid].hint_step = 7
            for _ in range(30):
                await asyncio.sleep(1)
                if gid in guess.Group:
                    if event.group_id not in guess.switch.enable or guess.Group[gid].end:
                        await guess_music_start.finish()
                else:
                    await guess_music_start.finish()
            guess.Group[gid].end = True
            await guess_score.reset_all_streaks(gid)
            answer = MessageSegment.text('答案是：\n') + await draw_music_info(guess.Group[gid].music)
            guess.end(gid)
            await guess_music_start.finish(answer)


@guess_music_pic.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_pic.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if gid in guess.Group:
        await guess_music_pic.finish(_GUESS_BUSY_HINT, reply_message=True)
    guess.startpic(gid)
    data = guess.Group[gid]
    await guess_music_pic.send(
        dedent(f'''\
            开始猜曲绘！可以直接发送答案！
            每隔10秒会给出进一步提示。发送 重置猜歌 可结束游戏。
            当前难度：{data.difficulty}，当前干扰类型：{'、'.join(data.interference_labels)}
        ''')
    )
    await guess_music_pic.send(MessageSegment.image(guess.render_pic_crop(data)))

    hint_interval = 10
    timeout_after_clear = 30
    clear_at = (data.expansion_count + 2) * hint_interval
    total_duration = clear_at + timeout_after_clear

    for elapsed in range(1, total_duration + 1):
        await asyncio.sleep(1)
        if gid not in guess.Group:
            await guess_music_pic.finish()
        data = guess.Group[gid]
        if gid not in guess.switch.enable or data.end:
            await guess_music_pic.finish()

        if elapsed % hint_interval != 0:
            continue

        step = elapsed // hint_interval
        if step <= data.expansion_count:
            guess.expand_pic_crop(data)
            await guess_music_pic.send(
                MessageSegment.text('[区域扩增!]\n') +
                MessageSegment.image(guess.render_pic_crop(data))
            )
            data.hint_step += 1
        elif step == data.expansion_count + 1 and not data.global_shown:
            data.global_shown = True
            await guess_music_pic.send(
                MessageSegment.text('[全局视野!]\n') +
                MessageSegment.image(guess.render_pic_global(data))
            )
            data.hint_step += 1
        elif step == data.expansion_count + 2 and not data.interference_cleared:
            data.interference_cleared = True
            await guess_music_pic.send(
                MessageSegment.text('[干扰消除!]\n') +
                MessageSegment.image(guess.render_pic_clear(data))
            )
            data.hint_step += 1

    data.end = True
    await guess_score.reset_all_streaks(gid)
    guess.end(gid)
    await _send_guess_answer_bundle(
        guess_music_pic, event, data, header='答案是：',
    )
    await guess_music_pic.finish()


@guess_music_audio.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_audio.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if gid in guess.Group:
        await guess_music_audio.finish(_GUESS_BUSY_HINT, reply_message=True)

    await guess_music_audio.send('正在准备猜曲音频，请稍候…')
    log.info(f'[GuessAudio] 猜曲子开局 gid={gid}')
    data = await guess.prepare_audio_round()
    if data is None:
        log.warning(f'[GuessAudio] 猜曲子无可用音频 gid={gid}')
        await guess_music_audio.finish(
            '暂无可用猜曲音频（CDN 无资源或分轨失败）。'
            '管理员可运行 scripts/build_guess_audio_cache.py 预烘焙，或安装 demucs 后重试。',
            reply_message=True,
        )

    guess.startaudio(gid, data)
    stage_count = data.stage_count
    audio_meta = get_audio_manifest_entry(data.music.id)
    log.info(
        f'[GuessAudio] 猜曲子开始 gid={gid} music_id={data.music.id} '
        f'title={data.music.title} stages={stage_count} mode={audio_meta.get("mode", "?")}'
    )
    season_line = (
        '\n【赛季限时双倍得分】猜曲子积分 ×2（截至 6/30）'
        if guess_score.audio_season_double_active()
        else ''
    )
    await guess_music_audio.send(
        dedent(f'''\
            猜曲子开始！共 {stage_count} 个阶段，每段约 30 秒，
            每隔 {STAGE_INTERVAL} 秒会放出更完整的混音。
            第四阶段结束后仍有 {STAGE_FINAL_GRACE} 秒作答时间。{season_line}
            请输入歌曲 id、标题或别名猜歌（DX 与标准视为不同曲目）。
            发送 重置猜歌 可结束本局。
        ''')
    )

    for stage_idx in range(stage_count):
        if gid not in guess.Group:
            await guess_music_audio.finish()
        cur = guess.Group[gid]
        if gid not in guess.switch.enable or cur.end:
            await guess_music_audio.finish()

        label = STAGE_LABELS[stage_idx] if stage_idx < len(STAGE_LABELS) else '更多乐器'
        stage_path = Path(cur.stage_paths[stage_idx]).resolve()
        log.info(
            f'[GuessAudio] 发送阶段 {stage_idx + 1}/{stage_count} gid={gid} '
            f'file={stage_path.name} size={stage_path.stat().st_size}'
        )
        await guess_music_audio.send(
            MessageSegment.text(f'{stage_idx + 1}/{stage_count} [{label}]\n')
            + MessageSegment.record(str(stage_path))
        )
        cur.hint_step = stage_idx + 1

        if stage_idx == stage_count - 1:
            await guess_music_audio.send(
                f'第四阶段已放出，最后 {STAGE_FINAL_GRACE} 秒作答时间！'
            )

        if stage_idx < stage_count - 1:
            for _ in range(STAGE_INTERVAL):
                await asyncio.sleep(1)
                if gid not in guess.Group:
                    await guess_music_audio.finish()
                cur = guess.Group[gid]
                if gid not in guess.switch.enable or cur.end:
                    await guess_music_audio.finish()

    for _ in range(STAGE_FINAL_GRACE):
        await asyncio.sleep(1)
        if gid not in guess.Group:
            await guess_music_audio.finish()
        cur = guess.Group[gid]
        if gid not in guess.switch.enable or cur.end:
            await guess_music_audio.finish()

    cur.end = True
    await guess_score.reset_all_streaks(gid)
    guess.end(gid)
    await _send_guess_answer_bundle(
        guess_music_audio, event, data, header='答案是：',
    )
    await guess_music_audio.finish()


@update_guess_audio.handle()
async def _(event: PrivateMessageEvent, match=RegexMatched()):
    force = match.group(1) is not None
    log.info(f'[GuessAudio] 收到「更新猜曲音频」qq={event.user_id} force={force}')
    hint = '强制重建' if force else '增量烘焙'
    await update_guess_audio.send(
        f'开始{hint}猜曲音频（热门池），耗时取决于曲目数量与是否安装 demucs，请稍候…'
    )
    try:
        report = await build_hot_audio_cache(force=force)
    except asyncio.CancelledError:
        request_hot_batch_cancel()
        log.warning(f'[GuessAudio] 「更新猜曲音频」被取消 qq={event.user_id}')
        raise
    log.info(f'[GuessAudio] 「更新猜曲音频」完成 qq={event.user_id} force={force}')
    await update_guess_audio.finish(report)


@guess_music_solve.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.Group:
        await guess_music_solve.finish()
    data = guess.Group[gid]
    ans = event.get_plaintext().strip()
    if not ans:
        await guess_music_solve.finish()
    uid_key = str(event.user_id)
    data.user_attempts[uid_key] = data.user_attempts.get(uid_key, 0) + 1
    first_guess = data.user_attempts[uid_key] == 1
    pic_difficulty = data.difficulty if isinstance(data, GuessPicData) else None
    if match_guess_answer(ans, data.answer, pic_difficulty=pic_difficulty):
        data.end = True
        settlement = await _award_guess_points(
            event,
            data,
            first_stage=data.hint_step == 0,
            first_guess=first_guess,
        )
        guess.end(gid)
        await _send_guess_answer_bundle(
            guess_music_solve, event, data,
            header='猜对了！',
            settlement=settlement,
            reply=True,
        )
        await guess_music_solve.finish()


@guess_music_reset.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid in guess.Group:
        data = guess.Group[gid]
        data.end = True
        await guess_score.reset_all_streaks(gid)
        guess.end(gid)
        await _send_guess_answer_bundle(
            guess_music_reset, event, data,
            header='已重置该群猜歌，答案是：',
            reply=True,
        )
        await guess_music_reset.finish()
    else:
        await guess_music_reset.finish('该群未处在猜歌状态', reply_message=True)


async def _handle_guess_score_board(
    event: GroupMessageEvent,
    matcher: Matcher,
    *,
    period: str,
) -> None:
    if event.group_id not in guess.switch.enable:
        await matcher.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    title, nodes = guess_score.build_ranking_forward(
        event.group_id,
        int(event.self_id),
        period=period,
    )
    await _send_guess_score_forward(matcher, bot, event, title, nodes)


@guess_score_rank.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_rank, period='total')


@guess_score_daily.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_daily, period='daily')


@guess_score_weekly.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_weekly, period='weekly')


@guess_score_monthly.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_monthly, period='monthly')


@guess_score_yearly.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_yearly, period='yearly')


@guess_score_season.handle()
async def _(event: GroupMessageEvent):
    await _handle_guess_score_board(event, guess_score_season, period='season')


async def _handle_guess_history_board(
    event: GroupMessageEvent,
    matcher: Matcher,
    *,
    period: str,
    period_key: Optional[str] = None,
) -> None:
    if event.group_id not in guess.switch.enable:
        await matcher.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if not period_key:
        period_key = guess_score.previous_period_key(period)
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    title, nodes = guess_score.build_ranking_forward(
        event.group_id,
        int(event.self_id),
        period=period,
        period_key=period_key,
    )
    await _send_guess_score_forward(matcher, bot, event, title, nodes)


@guess_score_hist_daily.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plaintext().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_daily, period='daily', period_key=key or None,
    )


@guess_score_hist_weekly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plaintext().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_weekly, period='weekly', period_key=key or None,
    )


@guess_score_hist_monthly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plaintext().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_monthly, period='monthly', period_key=key or None,
    )


@guess_score_hist_yearly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plaintext().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_yearly, period='yearly', period_key=key or None,
    )


@guess_score_hist_season.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plaintext().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_season, period='season', period_key=key or None,
    )


@guess_music_enable.handle()
@guess_music_disable.handle()
async def _(matcher: Matcher, event: GroupMessageEvent):
    gid = event.group_id
    if type(matcher) is guess_music_enable:
        msg = await guess.on(gid)
    elif type(matcher) is guess_music_disable:
        msg = await guess.off(gid)
    else:
        raise ValueError('matcher type error')
    await guess_music_enable.finish(msg, reply_message=True)


from ..libraries import maimaidx_guess_scheduler  # noqa: F401
