import asyncio
import json
from pathlib import Path
from typing import Literal, Optional, Union

from loguru import logger as log
from nonebot import get_bot, on_command, on_message, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, RegexMatched

from ..libraries.maimaidx_bot_admin import GUESS_GROUP_MANAGER, PLUGIN_ADMIN_ONLY
from ..libraries.maimaidx_guess_boost_card import (
    DEFAULT_CARD_HOURS,
    guess_boost_card,
)
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
update_guess_audio  = on_regex(r'^更新猜曲音频(?:\s+(-full))?\s*$', permission=PLUGIN_ADMIN_ONLY)
guess_boost_grant   = on_command('发加倍卡', permission=GUESS_GROUP_MANAGER)
guess_boost_query   = on_command('查加倍卡')
guess_music_solve   = on_message(
    rule=is_now_playing_guess_music,
    priority=10,
    block=False,
)
guess_music_reset   = on_command('重置猜歌', priority=4, block=True)
guess_music_enable  = on_command('开启mai猜歌', permission=GUESS_GROUP_MANAGER)
guess_music_disable = on_command('关闭mai猜歌', permission=GUESS_GROUP_MANAGER)
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


def _guess_first_stage(data: GuessData) -> bool:
    """猜曲子：第二段发出前（仅听过第一段）仍算首阶段。"""
    if isinstance(data, GuessAudioData):
        return data.hint_step < 2
    return data.hint_step == 0


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
    if await guess_boost_card.consume_one(event.group_id, event.user_id):
        multiplier *= 2
        multiplier_tags.append('限时加倍卡×2')
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


_GUESS_BUSY_HINT = '该群已有正在进行的猜歌、猜曲绘或猜曲子'
_GUESS_SEND_FAIL_MSG = '游戏数据获取失败，本游戏已结束。'
GUESS_SEND_TIMEOUT_TEXT = 15
GUESS_SEND_TIMEOUT_MEDIA = 60


def _guess_loop_should_stop(gid: int) -> bool:
    """猜歌主循环是否应退出（被重置、关闭或正常结束）。"""
    if gid not in guess.Group:
        return True
    if gid not in guess.switch.enable:
        return True
    return bool(guess.Group[gid].end)


async def _guess_sleep(gid: int, seconds: float) -> None:
    """可中断的 sleep：重置猜歌后尽快退出主循环。"""
    remaining = max(0.0, float(seconds))
    while remaining > 0 and not _guess_loop_should_stop(gid):
        step = min(1.0, remaining)
        await asyncio.sleep(step)
        remaining -= step


class GuessSendAborted(Exception):
    """猜歌局内消息发送失败，本局已强制结束。"""


async def _force_end_guess_round(gid: int) -> None:
    """强制结束本群猜歌局（可重复调用）。"""
    guess.Preparing.discard(gid)
    if gid not in guess.Group:
        return
    guess.Group[gid].end = True
    await guess_score.reset_all_streaks(gid)
    guess.end(gid)


async def _guess_notify(
    matcher: Matcher,
    event: GroupMessageEvent,
    message,
    *,
    reply: bool = False,
    timeout: int = GUESS_SEND_TIMEOUT_TEXT,
) -> None:
    """尽力发送通知，不修改游戏状态。"""
    try:
        await asyncio.wait_for(
            matcher.send(message, reply_message=reply),
            timeout=timeout,
        )
    except Exception as e:
        log.warning(
            f'[maimai] 猜歌通知发送失败 gid={event.group_id}: {type(e).__name__}: {e}'
        )


async def _safe_matcher_send(
    matcher: Matcher,
    event: GroupMessageEvent,
    message,
    gid: int,
    *,
    reply: bool = False,
    media: bool = False,
    fatal: bool = True,
    timeout: Optional[int] = None,
) -> None:
    if timeout is None:
        timeout = GUESS_SEND_TIMEOUT_MEDIA if media else GUESS_SEND_TIMEOUT_TEXT
    try:
        await asyncio.wait_for(
            matcher.send(message, reply_message=reply),
            timeout=timeout,
        )
    except Exception as e:
        log.warning(
            f'[maimai] 猜歌消息发送失败 gid={gid}: {type(e).__name__}: {e}'
        )
        if not fatal:
            return
        await _force_end_guess_round(gid)
        await _guess_notify(matcher, event, _GUESS_SEND_FAIL_MSG)
        raise GuessSendAborted() from e


async def _send_guess_answer_bundle(
    matcher: Matcher,
    event: GroupMessageEvent,
    data: GuessData,
    gid: int,
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
            gid,
            reply=reply,
            fatal=False,
        )
    await _safe_matcher_send(
        matcher, event, await draw_music_info(data.music), gid, fatal=False,
    )
    if isinstance(data, GuessPicData):
        await _safe_matcher_send(
            matcher, event,
            MessageSegment.image(guess.render_pic_reveal(data)),
            gid,
            media=True,
            fatal=False,
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
            gid,
            media=True,
            fatal=False,
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


def _get_at_qq(event: GroupMessageEvent) -> Optional[int]:
    for item in event.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data['qq'] != 'all':
            return int(item.data['qq'])
    return None


def _is_at_all(event: GroupMessageEvent) -> bool:
    for item in event.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data.get('qq') == 'all':
            return True
    return False


def _parse_grant_target(
    event: GroupMessageEvent, args: Message,
) -> Optional[Union[int, Literal['all']]]:
    if _is_at_all(event):
        return 'all'
    target = _get_at_qq(event)
    if target is not None:
        return target
    text = args.extract_plain_text().strip()
    if text == '全体' or text.startswith('全体 '):
        return 'all'
    return None


def _parse_grant_args(text: str, *, for_all: bool = False) -> tuple[int, float]:
    """解析 数量 [有效小时]，默认 1 张 / 24 小时。"""
    parts = text.strip().split()
    if for_all and parts and parts[0] == '全体':
        parts = parts[1:]
    count = 1
    hours = float(DEFAULT_CARD_HOURS)
    if parts:
        try:
            count = int(parts[0])
        except ValueError:
            return count, hours
    if len(parts) >= 2:
        try:
            hours = float(parts[1])
        except ValueError:
            pass
    return count, hours


@guess_boost_grant.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if event.group_id not in guess.switch.enable:
        await guess_boost_grant.finish(
            '该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True,
        )
    target = _parse_grant_target(event, args)
    if target is None:
        await guess_boost_grant.finish(
            '用法：发加倍卡 @用户 [数量] [有效小时]\n'
            '      发加倍卡 @全体成员 [数量] [有效小时]\n'
            '      发加倍卡 全体 [数量] [有效小时]\n'
            f'示例：发加倍卡 @某人 1 {DEFAULT_CARD_HOURS}；发加倍卡 全体 1 {DEFAULT_CARD_HOURS}',
            reply_message=True,
        )
    extra = args.extract_plain_text().strip()
    count, hours = _parse_grant_args(extra, for_all=(target == 'all'))

    if target == 'all':
        try:
            raw = await bot.call_api('get_group_member_list', group_id=event.group_id)
        except Exception as e:
            log.warning(f'[GuessBoost] 获取群成员失败 gid={event.group_id}: {e}')
            await guess_boost_grant.finish(f'获取群成员失败：{e}', reply_message=True)
        if not raw or not isinstance(raw, list):
            await guess_boost_grant.finish('群成员列表为空。', reply_message=True)
        self_id = int(bot.self_id)
        uids = [
            int(m['user_id']) for m in raw
            if m.get('user_id') is not None and int(m['user_id']) != self_id
        ]
        if not uids:
            await guess_boost_grant.finish('群成员列表为空。', reply_message=True)
        member_count, hours = await guess_boost_card.grant_many(
            event.group_id,
            uids,
            count=count,
            hours=hours,
            issuer_uid=event.user_id,
        )
        await guess_boost_grant.finish(
            f'已向本群 {member_count} 人各发放 {count} 张限时加倍卡'
            f'（{hours:g} 小时内有效，猜对消耗 1 张积分 ×2）。',
            reply_message=True,
        )
        return

    granted, hours = await guess_boost_card.grant(
        event.group_id,
        target,
        count=count,
        hours=hours,
        issuer_uid=event.user_id,
    )
    remain = guess_boost_card.active_count(event.group_id, target)
    await guess_boost_grant.finish(
        MessageSegment.at(target)
        + MessageSegment.text(
            f'\n已发放 {granted} 张限时加倍卡（{hours:g} 小时内有效，猜对消耗 1 张积分 ×2）。'
            f'当前剩余 {remain} 张。'
        ),
        reply_message=True,
    )


@guess_boost_query.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await guess_boost_query.finish('请在群内使用。', reply_message=True)
    target = _get_at_qq(event) or event.user_id
    count = guess_boost_card.active_count(event.group_id, target)
    if count <= 0:
        if target == event.user_id:
            await guess_boost_query.finish('你当前没有可用的限时加倍卡。', reply_message=True)
        else:
            await guess_boost_query.finish(
                MessageSegment.at(target) + MessageSegment.text(' 当前没有可用的限时加倍卡。'),
                reply_message=True,
            )
    nearest = guess_boost_card.nearest_expiry_hours(event.group_id, target)
    hint = f'最近一张约 {nearest:.1f} 小时后过期' if nearest is not None else ''
    prefix = '你' if target == event.user_id else ''
    msg = f'{prefix}当前有 {count} 张限时加倍卡（猜对消耗，积分 ×2）'
    if hint:
        msg += f'，{hint}'
    msg += '。'
    if target != event.user_id:
        await guess_boost_query.finish(
            MessageSegment.at(target) + MessageSegment.text(f'\n{msg}'),
            reply_message=True,
        )
    await guess_boost_query.finish(msg, reply_message=True)


@guess_music_start.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_start.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
    if guess.is_busy(gid):
        await guess_music_start.finish(_GUESS_BUSY_HINT)
    guess.start(gid)
    try:
        await _safe_matcher_send(
            guess_music_start, event,
            dedent('''\
                我将从热门乐曲中选择一首歌，每隔8秒描述它的特征，
                请输入歌曲的 id 标题 或 别名（需bot支持，无需大小写）进行猜歌（DX乐谱和标准乐谱视为两首歌）。
                猜歌时查歌等其他命令依然可用。
                积分：越早猜中越高（基础最高7分）；首条提示前猜中可叠加首阶段×2、首答×2，理论最高4倍。
            '''),
            gid,
        )
        await _guess_sleep(gid, 4)
        for cycle in range(7):
            if _guess_loop_should_stop(gid):
                break
            if cycle < 6:
                await _safe_matcher_send(
                    guess_music_start, event,
                    f'{cycle + 1}/7 这首歌{guess.Group[gid].options[cycle]}',
                    gid,
                )
                guess.Group[gid].hint_step = cycle + 1
                await _guess_sleep(gid, 8)
            else:
                await _safe_matcher_send(
                    guess_music_start, event,
                    MessageSegment.text('7/7 这首歌封面的一部分是：\n')
                    + MessageSegment.image(guess.Group[gid].img)
                    + MessageSegment.text('答案将在30秒后揭晓'),
                    gid,
                    media=True,
                )
                guess.Group[gid].hint_step = 7
                for _ in range(30):
                    await _guess_sleep(gid, 1)
                    if _guess_loop_should_stop(gid):
                        await guess_music_start.finish()
                if _guess_loop_should_stop(gid):
                    await guess_music_start.finish()
                guess.Group[gid].end = True
                await guess_score.reset_all_streaks(gid)
                answer = (
                    MessageSegment.text('答案是：\n')
                    + await draw_music_info(guess.Group[gid].music)
                )
                guess.end(gid)
                await guess_music_start.finish(answer)
    except GuessSendAborted:
        await guess_music_start.finish()


@guess_music_pic.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_pic.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if guess.is_busy(gid):
        await guess_music_pic.finish(_GUESS_BUSY_HINT, reply_message=True)
    guess.startpic(gid)
    data = guess.Group[gid]
    try:
        await _safe_matcher_send(
            guess_music_pic, event,
            dedent(f'''\
                开始猜曲绘！可以直接发送答案！
                每隔10秒会给出进一步提示。发送 重置猜歌 可结束游戏。
                当前难度：{data.difficulty}，当前干扰类型：{'、'.join(data.interference_labels)}
                积分：难度越高基础分越高（1～3分）；首次扩增前猜中可叠加首阶段×2、首答×2，理论最高4倍。
            '''),
            gid,
        )
        await _safe_matcher_send(
            guess_music_pic, event,
            MessageSegment.image(guess.render_pic_crop(data)),
            gid,
            media=True,
        )

        hint_interval = 10
        timeout_after_clear = 30
        clear_at = (data.expansion_count + 2) * hint_interval
        total_duration = clear_at + timeout_after_clear

        for elapsed in range(1, total_duration + 1):
            await _guess_sleep(gid, 1)
            if _guess_loop_should_stop(gid):
                await guess_music_pic.finish()
            if gid not in guess.Group:
                await guess_music_pic.finish()

            if elapsed % hint_interval != 0:
                continue

            data = guess.Group[gid]
            step = elapsed // hint_interval
            if step <= data.expansion_count:
                guess.expand_pic_crop(data)
                await _safe_matcher_send(
                    guess_music_pic, event,
                    MessageSegment.text('[区域扩增!]\n')
                    + MessageSegment.image(guess.render_pic_crop(data)),
                    gid,
                    media=True,
                )
                data.hint_step += 1
            elif step == data.expansion_count + 1 and not data.global_shown:
                data.global_shown = True
                await _safe_matcher_send(
                    guess_music_pic, event,
                    MessageSegment.text('[全局视野!]\n')
                    + MessageSegment.image(guess.render_pic_global(data)),
                    gid,
                    media=True,
                )
                data.hint_step += 1
            elif step == data.expansion_count + 2 and not data.interference_cleared:
                data.interference_cleared = True
                await _safe_matcher_send(
                    guess_music_pic, event,
                    MessageSegment.text('[干扰消除!]\n')
                    + MessageSegment.image(guess.render_pic_clear(data)),
                    gid,
                    media=True,
                )
                data.hint_step += 1

        if _guess_loop_should_stop(gid):
            await guess_music_pic.finish()
        data = guess.Group[gid]
        data.end = True
        await guess_score.reset_all_streaks(gid)
        guess.end(gid)
        await _send_guess_answer_bundle(
            guess_music_pic, event, data, gid, header='答案是：',
        )
        await guess_music_pic.finish()
    except GuessSendAborted:
        await guess_music_pic.finish()


@guess_music_audio.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_audio.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if not await guess.try_begin_prepare(gid):
        await guess_music_audio.finish(_GUESS_BUSY_HINT, reply_message=True)

    data = None
    try:
        try:
            await _safe_matcher_send(
                guess_music_audio, event, '正在准备猜曲音频，请稍候…', gid,
            )
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
        finally:
            guess.end_prepare(gid)

        stage_count = data.stage_count
        audio_meta = get_audio_manifest_entry(data.music.id)
        log.info(
            f'[GuessAudio] 猜曲子开始 gid={gid} music_id={data.music.id} '
            f'title={data.music.title} stages={stage_count} mode={audio_meta.get("mode", "?")}'
        )
        season_line = (
            '\n【赛季限时双倍得分】猜曲子积分 ×2（截至 6/30；'
            '第二段前猜中可叠加首阶段×2、首答×2，理论最高 8 倍）'
            if guess_score.audio_season_double_active()
            else ''
        )
        await _safe_matcher_send(
            guess_music_audio, event,
            dedent(f'''\
                猜曲子开始！共 {stage_count} 个阶段，每段约 30 秒，
                每隔 {STAGE_INTERVAL} 秒会放出更完整的混音。
                第四阶段结束后仍有 {STAGE_FINAL_GRACE} 秒作答时间。{season_line}
                请输入歌曲 id、标题或别名猜歌（DX 与标准视为不同曲目）。
                发送 重置猜歌 可结束本局。
            '''),
            gid,
        )

        for stage_idx in range(stage_count):
            if _guess_loop_should_stop(gid):
                await guess_music_audio.finish()
            cur = guess.Group[gid]

            label = STAGE_LABELS[stage_idx] if stage_idx < len(STAGE_LABELS) else '更多乐器'
            stage_path = Path(cur.stage_paths[stage_idx]).resolve()
            log.info(
                f'[GuessAudio] 发送阶段 {stage_idx + 1}/{stage_count} gid={gid} '
                f'file={stage_path.name} size={stage_path.stat().st_size}'
            )
            await _safe_matcher_send(
                guess_music_audio, event,
                MessageSegment.text(f'{stage_idx + 1}/{stage_count} [{label}]\n')
                + MessageSegment.record(str(stage_path)),
                gid,
                media=True,
            )
            cur.hint_step = stage_idx + 1

            if stage_idx == stage_count - 1:
                await _safe_matcher_send(
                    guess_music_audio, event,
                    f'第四阶段已放出，最后 {STAGE_FINAL_GRACE} 秒作答时间！',
                    gid,
                )

            if stage_idx < stage_count - 1:
                for _ in range(STAGE_INTERVAL):
                    await _guess_sleep(gid, 1)
                    if _guess_loop_should_stop(gid):
                        await guess_music_audio.finish()

        for _ in range(STAGE_FINAL_GRACE):
            await _guess_sleep(gid, 1)
            if _guess_loop_should_stop(gid):
                await guess_music_audio.finish()

        if _guess_loop_should_stop(gid):
            await guess_music_audio.finish()
        cur = guess.Group[gid]
        cur.end = True
        await guess_score.reset_all_streaks(gid)
        guess.end(gid)
        await _send_guess_answer_bundle(
            guess_music_audio, event, data, gid, header='答案是：',
        )
        await guess_music_audio.finish()
    except GuessSendAborted:
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
            first_stage=_guess_first_stage(data),
            first_guess=first_guess,
        )
        guess.end(gid)
        try:
            await _send_guess_answer_bundle(
                guess_music_solve, event, data, gid,
                header='猜对了！',
                settlement=settlement,
                reply=True,
            )
        except GuessSendAborted:
            pass
        await guess_music_solve.finish()


@guess_music_reset.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid in guess.Preparing:
        guess.end_prepare(gid)
        await guess_music_reset.finish('已取消猜曲子准备，本局未开始。', reply_message=True)
        return
    if gid not in guess.Group:
        await guess_music_reset.finish('该群未处在猜歌状态', reply_message=True)
        return
    data = guess.Group[gid]
    music = data.music
    await _force_end_guess_round(gid)
    await _guess_notify(
        guess_music_reset, event,
        f'已重置该群猜歌，本游戏已结束。\n答案是：{music.title}（ID: {music.id}）',
        reply=True,
    )
    await guess_music_reset.finish()


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
    key = args.extract_plain_text().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_daily, period='daily', period_key=key or None,
    )


@guess_score_hist_weekly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plain_text().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_weekly, period='weekly', period_key=key or None,
    )


@guess_score_hist_monthly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plain_text().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_monthly, period='monthly', period_key=key or None,
    )


@guess_score_hist_yearly.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plain_text().strip()
    await _handle_guess_history_board(
        event, guess_score_hist_yearly, period='yearly', period_key=key or None,
    )


@guess_score_hist_season.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    key = args.extract_plain_text().strip()
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
