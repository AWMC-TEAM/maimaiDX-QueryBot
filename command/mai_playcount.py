import asyncio
import base64
import json
import time
from typing import Optional

from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import IgnoredException
from nonebot.params import CommandArg, Depends, RegexStr

from ..config import log, maiconfig
from ..libraries.maimaidx_best_50 import generate_pc50, generate_pca50
from ..libraries.maimaidx_music import feature_manager
from ..libraries.maimaidx_playcount_db import pc_db
from ..libraries.maimaidx_playcount_fetcher import playcount_fetcher

update_pc = on_command('更新pc数', aliases={'更新PC数', '同步pc数', '同步PC数', '绑定机台', '登录机台'})
my_pc = on_command('我的pc数', aliases={'我的PC数', '我的pc', '我的PC'})
pc_rank = on_command('pc排行', aliases={'PC排行', 'pc数排行', 'PC数排行'})
pc_detail = on_command('pc数', aliases={'PC数'})
pc50 = on_command('pc50', aliases={'PC50', '嫖娼50'})
pca50 = on_command('pca50', aliases={'PCA50', '嫖娼a50'})

_waiting_qrcode: dict[int, bool] = {}


async def get_at_qq(message: MessageEvent) -> Optional[int]:
    for item in message.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data['qq'] != 'all':
            return int(item.data['qq'])
    return None


async def check_feature(bot: Bot, event: GroupMessageEvent):
    if not feature_manager.is_enabled(event.group_id, 'query'):
        await bot.send(event, message=MessageSegment.reply(event.message_id) + MessageSegment.text('本群查询功能已关闭'))
        raise IgnoredException('功能未开启')


async def _send_progress(bot: Bot, event: MessageEvent, text: str):
    try:
        await bot.send(event, message=MessageSegment.text(text))
    except Exception:
        pass


# ============================================================================
# 更新PC数（绑定二维码）
# ============================================================================

@update_pc.handle()
async def handle_update_pc(bot: Bot, event: GroupMessageEvent):
    """处理「更新pc数」命令，引导用户发送机台二维码。"""
    await check_feature(bot, event)

    qqid = event.user_id

    if not playcount_fetcher.sdgb_available:
        await update_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\nPC数功能未配置。\n'
                '请在 .env 中配置 sw-api：\n'
                '  SDGBTECHAPI=http://127.0.0.1:5001\n'
                '  SDGBT_CLIENT_ID=your_keychip\n'
                '  SDGBT_REGION_ID=1\n'
                '  SDGBT_PLACE_ID=1403\n'
                '配置后重启 Bot 即可使用。'
            )
        )

    await update_pc.send(
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            '\n请发送你的机台二维码数据。\n\n'
            '获取方式：打开舞萌中二获取一个最新二维码，\n'
            '进入后长按识别，\n'
            '复制以 SGWCMAID 开头的完整字符串，\n'
            '直接发送给 Bot 即可。\n\n'
            '⚠️ 请注意保护好你的二维码数据，不要发给他人。'
        )
    )
    _waiting_qrcode[qqid] = True


@update_pc.receive()
async def receive_qrcode(bot: Bot, event: GroupMessageEvent):
    """接收用户发送的二维码数据。"""
    qqid = event.user_id

    if qqid not in _waiting_qrcode:
        return

    del _waiting_qrcode[qqid]

    raw_text = event.get_plaintext().strip()

    if not raw_text.startswith('SGWCMAID'):
        await update_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('二维码数据格式错误，请重新发送以 SGWCMAID 开头的完整字符串。')
        )

    await _handle_sdgb_update(bot, event, qqid, raw_text)


async def _handle_sdgb_update(bot: Bot, event: GroupMessageEvent, qqid: int, qrcode_data: str):
    """通过 sw-api 更新 PC 数据"""
    try:
        success = await playcount_fetcher.login_by_sdgb(qrcode_data, qqid)
        if not success:
            await update_pc.finish(
                MessageSegment.reply(event.message_id)
                + MessageSegment.text('凭据保存失败。')
            )
    except RuntimeError as e:
        await update_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'配置错误: {e}')
        )

    try:
        count = await playcount_fetcher.fetch_via_sdgb(qqid)
    except Exception as e:
        log.error(f'[SDGBPC] 用户 {qqid} 拉取PC数据异常: {e}')
        await update_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'数据拉取失败: {e}。请检查 sw-api 服务或稍后重试。')
        )

    total_plays = pc_db.get_user_total_plays(qqid)

    await update_pc.finish(
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            f'\n✅ PC数据更新完成！\n'
            f'- 收录谱面: {count} 个\n'
            f'- 总游玩次数: {total_plays} 次\n\n'
            f'使用「pc50」查看按次数排序的 B50\n'
            f'使用「pca50」查看全局 PC 排行的 B50\n'
            f'使用「我的pc数」查看详细数据\n'
            f'使用「pc排行」查看群内排行'
        )
    )


@my_pc.handle()
async def handle_my_pc(bot: Bot, event: GroupMessageEvent):
    """处理「我的pc数」命令，展示用户PC数统计。"""
    await check_feature(bot, event)

    qqid = event.user_id

    records = pc_db.get_user_play_counts(qqid)
    if not records:
        await my_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('你还没有PC数据，请先使用「更新pc数」命令登录机台并同步数据。')
        )

    total_plays = pc_db.get_user_total_plays(qqid)
    top_15 = sorted(records, key=lambda r: r.play_count, reverse=True)[:15]

    lines = [
        f'总谱面数: {len(records)} 个',
        f'总游玩次数: {total_plays} 次',
        '',
        '游玩最多的15个谱面:',
    ]
    for i, r in enumerate(top_15, 1):
        title = r.title if r.title else f'#{r.song_id}'
        lines.append(f'{i:2}. {title} [{r.level}] - {r.play_count} 次')

    msg = '\n'.join(lines)
    await my_pc.finish(MessageSegment.reply(event.message_id) + MessageSegment.text(msg))


@pc_rank.handle()
async def handle_pc_rank(bot: Bot, event: GroupMessageEvent):
    """处理「pc排行」命令，展示群内用户PC数排行。"""
    await check_feature(bot, event)

    all_users = pc_db.get_all_users_with_data()
    if not all_users:
        await pc_rank.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('暂无PC排行数据，请群成员先使用「更新pc数」同步数据。')
        )

    user_stats = []
    for uid in all_users:
        total = pc_db.get_user_total_plays(uid)
        records = pc_db.get_user_play_counts(uid)
        user_stats.append((uid, total, len(records)))

    user_stats.sort(key=lambda x: x[1], reverse=True)

    lines = ['群内PC数排行:']
    for i, (uid, total, count) in enumerate(user_stats[:15], 1):
        lines.append(f'{i:2}. QQ:{uid} - PC: {total} 次 ({count} 谱面)')

    msg = '\n'.join(lines)
    await pc_rank.finish(MessageSegment.reply(event.message_id) + MessageSegment.text(msg))


@pc_detail.handle()
async def handle_pc_detail(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    """处理「pc数 歌曲名/ID」命令，展示指定歌曲的PC数。"""
    await check_feature(bot, event)

    keyword = arg.extract_plain_text().strip()
    if not keyword:
        await pc_detail.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('请输入歌曲名或ID，例如: pc数 1231')
        )

    target_qqid = event.user_id
    at_qq = await get_at_qq(event)
    if at_qq:
        target_qqid = at_qq

    records = pc_db.get_user_play_counts(target_qqid)
    if not records:
        await pc_detail.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'QQ:{target_qqid} 还没有PC数据。')
        )

    matched = []
    for r in records:
        title_lower = (r.title or '').lower()
        keyword_lower = keyword.lower()
        try:
            if int(keyword) == r.song_id:
                matched.append(r)
                continue
        except ValueError:
            pass
        if keyword_lower in title_lower:
            matched.append(r)

    if not matched:
        await pc_detail.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'未找到与「{keyword}」匹配的PC记录。')
        )

    matched.sort(key=lambda r: r.level_index)

    title_display = matched[0].title if matched[0].title else f'#{matched[0].song_id}'
    lines = [f'{title_display} 的PC数:']
    for r in matched:
        lines.append(f'  {r.level} - {r.play_count} 次')

    msg = '\n'.join(lines)
    await pc_detail.finish(MessageSegment.reply(event.message_id) + MessageSegment.text(msg))


@pc50.handle()
async def handle_pc50(
    event: MessageEvent,
    user_id: Optional[int] = Depends(get_at_qq)
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    from ..libraries.maimaidx_break import break_billing, take_break_charge_footer
    from ..libraries.maimaidx_error import BreakInsufficientError
    try:
        async with break_billing(event.user_id):
            result = await generate_pc50(qqid)
    except BreakInsufficientError as e:
        await pc50.finish(str(e), reply_message=True)
        return
    charge = take_break_charge_footer()
    if charge and not isinstance(result, str):
        result = result + MessageSegment.text('\n' + '\n'.join(charge))
    await pc50.finish(result, reply_message=True)


@pca50.handle()
async def handle_pca50(
    event: MessageEvent,
    user_id: Optional[int] = Depends(get_at_qq)
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    from ..libraries.maimaidx_break import break_billing, take_break_charge_footer
    from ..libraries.maimaidx_error import BreakInsufficientError
    try:
        async with break_billing(event.user_id):
            result = await generate_pca50(qqid)
    except BreakInsufficientError as e:
        await pca50.finish(str(e), reply_message=True)
        return
    charge = take_break_charge_footer()
    if charge and not isinstance(result, str):
        result = result + MessageSegment.text('\n' + '\n'.join(charge))
    await pca50.finish(result, reply_message=True)
