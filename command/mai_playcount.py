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
from ..libraries.maimaidx_sdgb_prober import sdgb_prober
from ..config import log, maiconfig

update_pc = on_command('更新pc数', aliases={'更新PC数', '同步pc数', '同步PC数', '绑定机台', '登录机台'})
my_pc = on_command('我的pc数', aliases={'我的PC数', '我的pc', '我的PC'})
pc_rank = on_command('pc排行', aliases={'PC排行', 'pc数排行', 'PC数排行'})
pc_detail = on_command('pc数', aliases={'PC数'})
pc50 = on_command('pc50', aliases={'PC50', '嫖娼50'})
pca50 = on_command('pca50', aliases={'PCA50', '嫖娼a50'})

# 查分器绑定命令
df_bind = on_command('dfbind', aliases={'DFBIND', 'Dfbind', '水鱼绑定'})
lx_bind = on_command('lxbind', aliases={'LXBIND', 'Lxbind', '落雪绑定'})

# 查分器上传命令
upload_b50 = on_command('上传水鱼', aliases={'上传水鱼b50', '更新水鱼', '水鱼b50', '水鱼B50'})
upload_lx_b50 = on_command('上传落雪', aliases={'上传落雪b50', '更新落雪', '落雪b50', '落雪B50', 'lx_b50', 'LX_B50'})
get_ticket = on_command('拿票', aliases={'获取倍率票', '倍率票', 'ticket', 'Ticket'})
get_ticket_regex = on_regex(r'^/tk[2-6](?:\s|$)', block=True)
query_charge = on_command('查票', aliases={'查询票券', '票券', 'charge', 'Charge'})
add_item = on_command('add', aliases={'添道具', '送道具', '加道具', '添加收藏品'})

# 全局二维码接收（用于拿票）
qr_receiver = on_regex(r'^SGWCMAID', block=True)

_waiting_qrcode: dict[int, bool] = {}

# 等待二维码的拿票任务状态
_waiting_ticket_qr: dict[int, dict] = {}

# 倍率票 ID 映射
TICKET_MAP = {
    '2': 2, '2x': 2, '双倍': 2,
    '3': 3, '3x': 3, '三倍': 3,
    '4': 4, '4x': 4, '四倍': 4,
    '5': 5, '5x': 5, '五倍': 5,
    '6': 6, '6x': 6, '六倍': 6,
}


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


async def _try_recall_qr(bot: Bot, event: GroupMessageEvent, qr_text: str):
    """尝试撤回二维码消息，如果 bot 不是管理员则提醒用户撤回。"""
    try:
        member_info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.self_id
        )
        role = member_info.get('role', '')
        if role in ('owner', 'admin'):
            try:
                await bot.delete_msg(message_id=event.message_id)
            except Exception:
                pass
        else:
            await bot.send(
                event,
                MessageSegment.reply(event.message_id)
                + MessageSegment.text('⚠️ 请尽快撤回你发送的二维码消息，避免泄露个人信息。')
            )
    except Exception:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('⚠️ 请尽快撤回你发送的二维码消息，避免泄露个人信息。')
        )


# ============================================================================
# 水鱼 Token 绑定
# ============================================================================

@df_bind.handle()
async def handle_df_bind(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    """绑定水鱼查分器 Token。"""
    await check_feature(bot, event)
    qqid = event.user_id
    fish_token = arg.extract_plain_text().strip()

    if not fish_token:
        saved_fish, _ = pc_db.get_prober_token(qqid)
        if saved_fish:
            await df_bind.finish(
                MessageSegment.reply(event.message_id)
                + MessageSegment.text(
                    f'\n你已绑定水鱼 Token。\n'
                    f'Token: {saved_fish[:20]}...\n'
                    f'如需重新绑定，请发送: dfbind <token>'
                )
            )
        else:
            await df_bind.finish(
                MessageSegment.reply(event.message_id)
                + MessageSegment.text(
                    '\n请提供水鱼 Token，格式：\n'
                    '  dfbind <token>\n\n'
                    'Token 可在水鱼查分器个人设置中获取。'
                )
            )

    if len(fish_token) < 127:
        await df_bind.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('水鱼 Token 长度不正确，应为 127-132 字符。')
        )

    pc_db.save_prober_token(qqid, fish_token=fish_token)
    await df_bind.finish(
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            '\n✅ 水鱼 Token 绑定成功！\n'
            '使用「上传水鱼」命令即可更新水鱼查分器数据。'
        )
    )


# ============================================================================
# 落雪好友码绑定
# ============================================================================

@lx_bind.handle()
async def handle_lx_bind(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    """绑定落雪查分器好友码。"""
    await check_feature(bot, event)
    qqid = event.user_id
    lxns_code = arg.extract_plain_text().strip()

    if not lxns_code:
        _, saved_lxns = pc_db.get_prober_token(qqid)
        if saved_lxns:
            await lx_bind.finish(
                MessageSegment.reply(event.message_id)
                + MessageSegment.text(
                    f'\n你已绑定落雪好友码。\n'
                    f'好友码: {saved_lxns}\n'
                    f'如需重新绑定，请发送: lxbind <15位好友码>'
                )
            )
        else:
            await lx_bind.finish(
                MessageSegment.reply(event.message_id)
                + MessageSegment.text(
                    '\n请提供落雪好友码，格式：\n'
                    '  lxbind <15位好友码>\n\n'
                    '好友码可在落雪查分器个人设置中获取。'
                )
            )

    if len(lxns_code) != 15:
        await lx_bind.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('落雪好友码长度不正确，应为 15 位。')
        )

    pc_db.save_prober_token(qqid, lxns_code=lxns_code)
    await lx_bind.finish(
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            '\n✅ 落雪好友码绑定成功！\n'
            '使用「上传落雪」命令即可更新落雪查分器数据。'
        )
    )


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
                '请在 .env 中配置：\n'
                '  SDGBTECHAPI=http://...\n'
                '  SDGBT_CLIENT_ID=A63E01E1459\n'
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
    """通过 SDGBTECHAPI 更新 PC 数据"""
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
            + MessageSegment.text(f'数据拉取失败: {e}。请检查 SDGBTECHAPI 服务或稍后重试。')
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
    await pc50.finish(await generate_pc50(qqid), reply_message=True)


@pca50.handle()
async def handle_pca50(
    event: MessageEvent,
    user_id: Optional[int] = Depends(get_at_qq)
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    await pca50.finish(await generate_pca50(qqid), reply_message=True)


# ============================================================================
# 水鱼 B50 上传
# ============================================================================

@upload_b50.handle()
async def handle_upload_b50(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    await check_feature(bot, event)
    qqid = event.user_id

    if not sdgb_prober.available:
        await upload_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\nSDGBTECHAPI 未配置。\n'
                '请在 .env 中配置：\n'
                '  SDGBTECHAPI=http://...\n'
                '  SDGBT_CLIENT_ID=A63E01C2562\n'
                '  SDGBT_REGION_ID=24\n'
                '  SDGBT_PLACE_ID=1320'
            )
        )

    # 获取已保存的 token
    saved_fish, _ = pc_db.get_prober_token(qqid)
    if not saved_fish:
        await upload_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n你还没有绑定水鱼 Token，请先使用以下命令绑定：\n'
                '  dfbind <token>\n\n'
                'Token 可在水鱼查分器个人设置中获取。'
            )
        )

    # 检查是否有二维码（命令中附带）
    inline_qr = arg.extract_plain_text().strip()

    if inline_qr.startswith('SGWCMAID'):
        qr_text = inline_qr
        await _try_recall_qr(bot, event, qr_text)
        await _do_upload_fish(bot, event, qqid, qr_text)
    else:
        await upload_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '请在命令后面附带二维码，例如：上传水鱼 SGWCMAID...'
            )
        )


async def _do_upload_fish(bot: Bot, event: GroupMessageEvent, qqid: int, qr_text: str):
    """执行水鱼 B50 上传"""
    saved_fish, _ = pc_db.get_prober_token(qqid)
    if not saved_fish:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('未找到已绑定的水鱼 Token，请先使用 dfbind 绑定。')
        )
        return

    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text('正在提交水鱼 B50 更新任务...')
    )

    try:
        result = await sdgb_prober.upload_b50(qr_text, saved_fish)
    except Exception as e:
        log.error(f'[UploadB50] 用户 {qqid} 提交失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'提交失败: {e}')
        )
        return

    if not result.get('UploadStatus'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'提交失败: {result.get("msg", "未知错误")}')
        )
        return

    task_id = result.get('task_id', '')
    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(f'✅ 任务已提交，正在处理中...\n任务ID: {task_id[:8]}...')
    )

    # 轮询任务状态
    try:
        task = await sdgb_prober.poll_task_until_done(task_id, is_lx=False, max_wait=180)
    except Exception as e:
        log.error(f'[UploadB50] 用户 {qqid} 轮询失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'任务状态查询失败: {e}')
        )
        return

    if task.get('timeout'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n⏱ 任务处理超时（超过3分钟），请稍后通过水鱼查分器查看是否更新成功。\n'
                f'任务ID: {task_id}'
            )
        )
        return

    if task.get('success'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n✅ 水鱼 B50 更新成功！\n'
                f'登出状态: {"成功" if task.get("logout_status") else "失败"}'
            )
        )
    else:
        error = task.get('error') or '未知错误'
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'❌ 水鱼 B50 更新失败: {error}')
        )


# ============================================================================
# 落雪 B50 上传
# ============================================================================

@upload_lx_b50.handle()
async def handle_upload_lx_b50(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    await check_feature(bot, event)
    qqid = event.user_id

    if not sdgb_prober.available:
        await upload_lx_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\nSDGBTECHAPI 未配置。\n'
                '请在 .env 中配置：\n'
                '  SDGBTECHAPI=http://...\n'
                '  SDGBT_CLIENT_ID=A63E01C2562\n'
                '  SDGBT_REGION_ID=24\n'
                '  SDGBT_PLACE_ID=1320'
            )
        )

    # 获取已保存的 code
    _, saved_lxns = pc_db.get_prober_token(qqid)
    if not saved_lxns:
        await upload_lx_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n你还没有绑定落雪好友码，请先使用以下命令绑定：\n'
                '  lxbind <15位好友码>\n\n'
                '好友码可在落雪查分器个人设置中获取。'
            )
        )

    # 检查是否有二维码（内联或已绑定）
    inline_qr = arg.extract_plain_text().strip()

    if inline_qr.startswith('SGWCMAID'):
        qr_text = inline_qr
        await _try_recall_qr(bot, event, qr_text)
        await _do_upload_lx(bot, event, qqid, qr_text)
    else:
        await upload_lx_b50.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '请在命令后面附带二维码，例如：上传落雪 SGWCMAID...'
            )
        )


async def _do_upload_lx(bot: Bot, event: GroupMessageEvent, qqid: int, qr_text: str):
    """执行落雪 B50 上传"""
    _, saved_lxns = pc_db.get_prober_token(qqid)
    if not saved_lxns:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('未找到已绑定的落雪好友码，请先使用 lxbind 绑定。')
        )
        return

    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text('正在提交落雪 B50 更新任务...')
    )

    try:
        result = await sdgb_prober.upload_lx_b50(qr_text, saved_lxns)
    except Exception as e:
        log.error(f'[UploadLxB50] 用户 {qqid} 提交失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'提交失败: {e}')
        )
        return

    if not result.get('UploadStatus'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'提交失败: {result.get("msg", "未知错误")}')
        )
        return

    task_id = result.get('task_id', '')
    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(f'✅ 任务已提交，正在处理中...\n任务ID: {task_id[:8]}...')
    )

    # 轮询任务状态
    try:
        task = await sdgb_prober.poll_task_until_done(task_id, is_lx=True, max_wait=180)
    except Exception as e:
        log.error(f'[UploadLxB50] 用户 {qqid} 轮询失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'任务状态查询失败: {e}')
        )
        return

    if task.get('timeout'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n⏱ 任务处理超时（超过3分钟），请稍后通过落雪查分器查看是否更新成功。\n'
                f'任务ID: {task_id}'
            )
        )
        return

    if task.get('success'):
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n✅ 落雪 B50 更新成功！\n'
                f'登出状态: {"成功" if task.get("logout_status") else "失败"}'
            )
        )
    else:
        error = task.get('error') or '未知错误'
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'❌ 落雪 B50 更新失败: {error}')
        )


# ============================================================================
# 接收查分器二维码
# ============================================================================

# ============================================================================
# 获取倍率票
# ============================================================================

def _parse_ticket_cmd(raw_cmd: str) -> tuple[Optional[int], Optional[str]]:
    """解析拿票命令，返回 (ticket_id, qr_text_or_none)。
    支持格式:
      - 拿票 2 [二维码]
      - /tk6 [二维码]
      - /tk6 SGWCMAID...
    """
    raw_cmd = raw_cmd.strip()
    parts = raw_cmd.split(None, 1)
    cmd_part = parts[0].lower() if parts else ''

    # 从命令前缀解析倍数，如 /tk6 -> 6
    ticket_id: Optional[int] = None
    if cmd_part.startswith('/tk'):
        try:
            ticket_id = int(cmd_part[3:])
        except ValueError:
            pass

    # 从参数解析倍数和二维码
    arg_part = parts[1] if len(parts) > 1 else ''
    arg_ticket_id: Optional[int] = None
    qr_text: Optional[str] = None

    if arg_part:
        arg_parts = arg_part.split(None, 1)
        first = arg_parts[0].lower()
        # 检查第一部分是否是倍数
        if first in TICKET_MAP:
            arg_ticket_id = TICKET_MAP[first]
            # 剩余部分可能是二维码
            if len(arg_parts) > 1:
                maybe_qr = arg_parts[1].strip()
                if maybe_qr.startswith('SGWCMAID'):
                    qr_text = maybe_qr
        elif first.startswith('sgwcmaid'):
            # 整个参数就是二维码
            qr_text = arg_part.strip()
        else:
            # 尝试解析为倍数
            arg_ticket_id = TICKET_MAP.get(first)
            if arg_ticket_id is None and len(arg_parts) > 1:
                maybe_qr = arg_parts[1].strip()
                if maybe_qr.startswith('SGWCMAID'):
                    qr_text = maybe_qr

    # 优先使用命令前缀中的倍数
    final_ticket_id = ticket_id if ticket_id is not None else arg_ticket_id
    return final_ticket_id, qr_text


@get_ticket.handle()
async def handle_get_ticket(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    await check_feature(bot, event)
    qqid = event.user_id
    raw_text = event.get_plaintext().strip()

    if not sdgb_prober.available:
        await get_ticket.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\nSDGBTECHAPI 未配置。\n'
                '请在 .env 中配置：\n'
                '  SDGBTECHAPI=http://...\n'
                '  SDGBT_CLIENT_ID=A63E01C2562\n'
                '  SDGBT_REGION_ID=24\n'
                '  SDGBT_PLACE_ID=1320'
            )
        )

    # 解析命令
    ticket_id, inline_qr = _parse_ticket_cmd(raw_text)

    if not ticket_id:
        await get_ticket.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n请指定倍率票类型，例如：\n'
                '  拿票 2  /  拿票 2x  /  拿票 双倍\n'
                '  /tk2  /  /tk3  /  /tk6 SGWCMAID...\n'
                '支持的倍率: 2x, 3x, 4x, 5x, 6x'
            )
        )

    if ticket_id not in (2, 3, 4, 5, 6):
        await get_ticket.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('不支持的倍率票类型，仅支持 2x-6x。')
        )

    # 确定二维码来源：仅命令中附带的二维码
    qr_text: Optional[str] = None
    if inline_qr and inline_qr.startswith('SGWCMAID'):
        qr_text = inline_qr
        # 尝试撤回二维码消息
        await _try_recall_qr(bot, event, qr_text)

    if not qr_text:
        # 没有二维码，进入等待状态
        _waiting_ticket_qr[qqid] = {'ticket_id': ticket_id}
        await get_ticket.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n请发送你的机台二维码数据（以 SGWCMAID 开头的字符串），\n'
                f'Bot 收到后会立即获取 {ticket_id}x 倍率票。\n\n'
                '⚠️ 发送后请尽快撤回，避免泄露个人信息。'
            )
        )

    # 有二维码，直接执行
    await _do_get_ticket(bot, event, qqid, qr_text, ticket_id)


async def _do_get_ticket(bot: Bot, event: GroupMessageEvent, qqid: int, qr_text: str, ticket_id: int):
    """执行获取倍率票"""
    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(f'正在获取 {ticket_id}x 倍率票，请坐和放宽一分钟。')
    )

    try:
        result = await sdgb_prober.get_ticket(qr_text, ticket_id)
    except Exception as e:
        log.error(f'[GetTicket] 用户 {qqid} 获取倍率票失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'获取倍率票失败: {e}')
        )
        return

    log.info(f'[GetTicket] 用户 {qqid} 获取 {ticket_id}x 倍率票结果: {result}')

    qr_status = result.get('QrStatus', False)
    login_status = result.get('LoginStatus', False)
    logout_status = result.get('LogoutStatus', False)
    ticket_status = result.get('TicketStatus', False)

    if ticket_status:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n✅ {ticket_id}x 倍率票获取成功！\n'
                f'扫码: {"成功" if qr_status else "失败"} | '
                f'登录: {"成功" if login_status else "失败"} | '
                f'登出: {"成功" if logout_status else "失败"}'
            )
        )
    else:
        # 静默处理失败，不发送失败提示
        log.warning(f'[GetTicket] 用户 {qqid} 获取 {ticket_id}x 倍率票失败，静默处理')
        return


@get_ticket_regex.handle()
async def handle_get_ticket_regex(bot: Bot, event: GroupMessageEvent, regex_str: str = RegexStr()):
    """处理 /tk2-/tk6 快捷命令。"""
    await check_feature(bot, event)
    qqid = event.user_id

    if not sdgb_prober.available:
        await get_ticket_regex.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\nSDGBTECHAPI 未配置。\n'
                '请在 .env 中配置：\n'
                '  SDGBTECHAPI=http://...\n'
                '  SDGBT_CLIENT_ID=A63E01C2562\n'
                '  SDGBT_REGION_ID=24\n'
                '  SDGBT_PLACE_ID=1320'
            )
        )

    # 解析命令
    ticket_id, inline_qr = _parse_ticket_cmd(regex_str)

    if not ticket_id or ticket_id not in (2, 3, 4, 5, 6):
        await get_ticket_regex.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('不支持的倍率票类型，仅支持 /tk2 - /tk6。')
        )

    # 确定二维码来源：仅命令中附带的二维码
    qr_text: Optional[str] = None
    if inline_qr and inline_qr.startswith('SGWCMAID'):
        qr_text = inline_qr
        # 尝试撤回二维码消息
        await _try_recall_qr(bot, event, qr_text)

    if not qr_text:
        # 没有二维码，进入等待状态
        _waiting_ticket_qr[qqid] = {'ticket_id': ticket_id}
        await get_ticket_regex.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n请发送你的机台二维码数据（以 SGWCMAID 开头的字符串），\n'
                f'Bot 收到后会立即获取 {ticket_id}x 倍率票。\n\n'
                '⚠️ 发送后请尽快撤回，避免泄露个人信息。'
            )
        )

    # 有二维码，直接执行
    await _do_get_ticket(bot, event, qqid, qr_text, ticket_id)


@get_ticket.receive()
@get_ticket_regex.receive()
async def receive_ticket_qrcode(bot: Bot, event: GroupMessageEvent):
    """接收拿票时用户发送的二维码数据。"""
    qqid = event.user_id

    if qqid not in _waiting_ticket_qr:
        return

    state = _waiting_ticket_qr.pop(qqid)
    raw_text = event.get_plaintext().strip()

    if not raw_text.startswith('SGWCMAID'):
        await get_ticket.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('二维码数据格式错误，请重新发送以 SGWCMAID 开头的完整字符串。')
        )

    # 尝试撤回二维码消息
    await _try_recall_qr(bot, event, raw_text)

    await _do_get_ticket(bot, event, qqid, raw_text, state['ticket_id'])


# ============================================================================
# 全局二维码接收（用于拿票）
# ============================================================================

@qr_receiver.handle()
async def handle_qr_receiver(bot: Bot, event: GroupMessageEvent, regex_str: str = RegexStr()):
    """全局接收 SGWCMAID 二维码，用于处理等待中的拿票任务。"""
    qqid = event.user_id
    raw_text = regex_str.strip()

    # 检查是否有等待中的拿票任务
    if qqid in _waiting_ticket_qr:
        state = _waiting_ticket_qr.pop(qqid)

        # 尝试撤回二维码消息
        await _try_recall_qr(bot, event, raw_text)

        await _do_get_ticket(bot, event, qqid, raw_text, state['ticket_id'])
        return


@query_charge.handle()
async def handle_query_charge(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    qqid = event.user_id
    raw_arg = arg.extract_plain_text().strip()

    if not sdgb_prober:
        await query_charge.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('SDGBTECHAPI 未配置，请联系管理员。')
        )

    if raw_arg and raw_arg.upper().startswith('SGWCMAID'):
        qr_text = raw_arg
        await _try_recall_qr(bot, event, qr_text)
        await _do_query_charge(bot, event, qqid, qr_text)
    else:
        await query_charge.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n请在命令后面附带二维码，例如：查票 SGWCMAID...\n'
                '⚠️ 发送后请尽快撤回，避免泄露个人信息。'
            )
        )


async def _do_query_charge(bot: Bot, event: GroupMessageEvent, qqid: int, qr_text: str):
    try:
        result = await sdgb_prober.get_charge(qr_text)
    except Exception as e:
        log.warning(f'[QueryCharge] 查询票券异常: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('查询票券失败，请稍后重试。')
        )
        return

    charge_status = result.get('ChargeStatus', False)
    user_charge_list = result.get('userChargeList', [])
    user_free_charge_list = result.get('userFreeChargeList', [])

    if not charge_status:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('查询票券失败，二维码可能已过期。')
        )
        return

    lines = ['票券状态查询结果：']

    if user_charge_list:
        lines.append('\n🎫 功能票：')
        for c in user_charge_list:
            cid = c.get('chargeId', '?')
            stock = c.get('stock', 0)
            valid_date = c.get('validDate', '')
            lines.append(f'  ID:{cid} | 库存:{stock} | 有效期:{valid_date}')

    if user_free_charge_list:
        lines.append('\n🆓 免费票：')
        for c in user_free_charge_list:
            cid = c.get('chargeId', '?')
            stock = c.get('stock', 0)
            lines.append(f'  ID:{cid} | 数量:{stock}')

    if not user_charge_list and not user_free_charge_list:
        lines.append('\n暂未持有任何票券。')

    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text('\n'.join(lines))
    )


# ============================================================================
# 添加收藏品（道具）
# ============================================================================

@add_item.handle()
async def handle_add_item(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    qqid = event.user_id
    raw_arg = arg.extract_plain_text().strip()

    if not sdgb_prober.available:
        await add_item.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('SDGBTECHAPI 未配置，请联系管理员。')
        )

    if not raw_arg:
        await add_item.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n格式：添道具 <item_id> <item_kind> <stock> SGWCMAID...\n'
                '示例：添道具 12345 5 1 SGWCMAID...\n'
                'item_kind: 道具类型编号\n'
                'stock: 数量'
            )
        )

    qr_text = None
    item_id = None
    item_kind = None
    item_stock = None

    parts = raw_arg.split()
    for i, part in enumerate(parts):
        if part.upper().startswith('SGWCMAID'):
            qr_text = part
        elif item_id is None:
            try:
                item_id = int(part)
            except ValueError:
                pass
        elif item_kind is None:
            try:
                item_kind = int(part)
            except ValueError:
                pass
        elif item_stock is None:
            try:
                item_stock = int(part)
            except ValueError:
                pass

    if None in (item_id, item_kind, item_stock):
        await add_item.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n参数不完整，格式：添道具 <item_id> <item_kind> <stock> SGWCMAID...\n'
                '示例：添道具 12345 5 1 SGWCMAID...'
            )
        )

    if not qr_text:
        await add_item.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                '\n请在命令后面附带二维码，例如：添道具 <item_id> <item_kind> <stock> SGWCMAID...\n'
                '⚠️ 发送后请尽快撤回，避免泄露个人信息。'
            )
        )

    await _try_recall_qr(bot, event, qr_text)
    await _do_add_item(bot, event, qqid, qr_text, item_id, item_kind, item_stock)


async def _do_add_item(bot: Bot, event: GroupMessageEvent, qqid: int, qr_text: str,
                       item_id: int, item_kind: int, item_stock: int):
    await bot.send(
        event,
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            f'正在添加收藏品（ID:{item_id} 类型:{item_kind} 数量:{item_stock}），\n'
            f'服务器处理中，请坐和放宽一分钟。'
        )
    )

    try:
        result = await sdgb_prober.get_item(qr_text, item_id, item_kind, item_stock)
    except Exception as e:
        log.error(f'[AddItem] 用户 {qqid} 添加道具失败: {e}')
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'添加道具失败: {e}')
        )
        return

    log.info(f'[AddItem] 用户 {qqid} item_id={item_id} kind={item_kind} stock={item_stock} 结果: {result}')

    qr_status = result.get('QrStatus', False)
    login_status = result.get('LoginStatus', False)
    logout_status = result.get('LogoutStatus', False)
    userall_status = result.get('UserAllStatus', False)

    if userall_status:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'\n✅ 收藏品添加成功！\n'
                f'ID:{item_id} | 类型:{item_kind} | 数量:{item_stock}\n'
                f'扫码: {"成功" if qr_status else "失败"} | '
                f'登录: {"成功" if login_status else "失败"} | '
                f'登出: {"成功" if logout_status else "失败"}'
            )
        )
    else:
        await bot.send(
            event,
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(
                f'❌ 收藏品添加失败\n'
                f'扫码: {"成功" if qr_status else "失败"} | '
                f'登录: {"成功" if login_status else "失败"} | '
                f'登出: {"成功" if logout_status else "失败"} | '
                f'上传: {"成功" if userall_status else "失败"}'
            )
        )
