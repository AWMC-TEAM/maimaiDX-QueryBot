import asyncio
import json

from loguru import logger as log
from nonebot import get_bot, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GROUP_ADMIN, GROUP_OWNER, GroupMessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from ..libraries.maimaidx_group_rating import build_forward_node
from ..libraries.maimaidx_guess_score import guess_score
from ..libraries.maimaidx_music import guess
from ..libraries.maimaidx_model import GuessData, GuessPicData
from ..libraries.maimaidx_music_info import *
from ..libraries.maimaidx_update_plate import *


def is_now_playing_guess_music(event: GroupMessageEvent) -> bool:
    return event.group_id in guess.Group

guess_music_start   = on_command('猜歌')
guess_music_pic     = on_command('猜曲绘')
guess_music_solve   = on_message(rule=is_now_playing_guess_music)
guess_music_reset   = on_command('重置猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_music_enable  = on_command('开启mai猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_music_disable = on_command('关闭mai猜歌', permission=SUPERUSER | GROUP_OWNER | GROUP_ADMIN)
guess_score_rank    = on_command('猜歌积分排行')


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


async def _award_guess_points(
    event: GroupMessageEvent,
    data: GuessData,
) -> str:
    if isinstance(data, GuessPicData):
        points = guess_score.pic_points_for(data)
    else:
        points = guess_score.SONG_POINTS
    added, total, rank = await guess_score.add_score(
        event.group_id,
        event.user_id,
        _sender_name(event),
        points,
    )
    return guess_score.format_settlement_line(added, total, rank)


async def _send_guess_score_forward(
    bot: Bot,
    event: GroupMessageEvent,
    title: str,
    nodes: list,
) -> None:
    if not nodes:
        await guess_score_rank.finish(title, reply_message=True)
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
        await guess_score_rank.finish('合并转发序列化失败，请稍后再试。', reply_message=True)
    except Exception as e:
        log.warning(f'[maimai] 猜歌积分排行 合并转发发送失败: {type(e).__name__}: {e}')
        await guess_score_rank.finish('合并转发发送失败，请稍后再试。', reply_message=True)
    await guess_score_rank.finish(reply_message=True)


@guess_music_start.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_start.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌')
    if gid in guess.Group:
        await guess_music_start.finish('该群已有正在进行的猜歌或猜曲绘')
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
            await asyncio.sleep(8)
        else:
            await guess_music_start.send(
                MessageSegment.text('7/7 这首歌封面的一部分是：\n') + 
                MessageSegment.image(guess.Group[gid].img) + 
                MessageSegment.text('答案将在30秒后揭晓')
            )
            for _ in range(30):
                await asyncio.sleep(1)
                if gid in guess.Group:
                    if event.group_id not in guess.switch.enable or guess.Group[gid].end:
                        await guess_music_start.finish()
                else:
                    await guess_music_start.finish()
            guess.Group[gid].end = True
            answer = MessageSegment.text('答案是：\n') + await draw_music_info(guess.Group[gid].music)
            guess.end(gid)
            await guess_music_start.finish(answer)


@guess_music_pic.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.switch.enable:
        await guess_music_pic.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    if gid in guess.Group:
        await guess_music_pic.finish('该群已有正在进行的猜歌或猜曲绘', reply_message=True)
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
        elif step == data.expansion_count + 1 and not data.global_shown:
            data.global_shown = True
            await guess_music_pic.send(
                MessageSegment.text('[全局视野!]\n') +
                MessageSegment.image(guess.render_pic_global(data))
            )
        elif step == data.expansion_count + 2 and not data.interference_cleared:
            data.interference_cleared = True
            await guess_music_pic.send(
                MessageSegment.text('[干扰消除!]\n') +
                MessageSegment.image(guess.render_pic_clear(data))
            )

    data.end = True
    answer = (
        MessageSegment.text('答案是：\n') +
        await draw_music_info(data.music) +
        MessageSegment.text('\n') +
        MessageSegment.image(guess.render_pic_reveal(data))
    )
    guess.end(gid)
    await guess_music_pic.finish(answer)


@guess_music_solve.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid not in guess.Group:
        await guess_music_solve.finish()
    data = guess.Group[gid]
    ans = event.get_plaintext().strip()
    if ans.lower() in data.answer:
        data.end = True
        settlement = await _award_guess_points(event, data)
        answer = (
            MessageSegment.text('猜对了，答案是：\n') +
            await draw_music_info(data.music) +
            MessageSegment.text(f'\n{settlement}')
        )
        if isinstance(data, GuessPicData):
            answer += (
                MessageSegment.text('\n') +
                MessageSegment.image(guess.render_pic_reveal(data))
            )
        guess.end(gid)
        await guess_music_solve.finish(answer, reply_message=True)


@guess_music_reset.handle()
async def _(event: GroupMessageEvent):
    gid = event.group_id
    if gid in guess.Group:
        data = guess.Group[gid]
        data.end = True
        msg = (
            MessageSegment.text('已重置该群猜歌，答案是：\n') +
            await draw_music_info(data.music)
        )
        if isinstance(data, GuessPicData):
            msg += (
                MessageSegment.text('\n') +
                MessageSegment.image(guess.render_pic_reveal(data))
            )
        guess.end(gid)
    else:
        msg = '该群未处在猜歌状态'
    await guess_music_reset.finish(msg, reply_message=True)


@guess_score_rank.handle()
async def _(event: GroupMessageEvent):
    if event.group_id not in guess.switch.enable:
        await guess_score_rank.finish('该群已关闭猜歌功能，开启请输入 开启mai猜歌', reply_message=True)
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    title, nodes = guess_score.build_ranking_forward(
        event.group_id,
        int(event.self_id),
    )
    await _send_guess_score_forward(bot, event, title, nodes)


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
