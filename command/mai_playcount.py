import asyncio
import re
import time
from typing import Callable, Optional

from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Depends

from ..config import log
from ..libraries.maimaidx_best_50 import generate_pc50, generate_pca50, generate_pc_rank50
from ..libraries.maimaidx_datasource import get_user_source
from ..libraries.maimaidx_error import QBindRequiredError
from ..libraries.maimaidx_music import feature_manager
from ..libraries.maimaidx_platform import billing_user_id, resolve_score_qqid
from ..libraries.maimaidx_playcount_db import pc_db
from ..libraries.maimaidx_playcount_fetcher import playcount_fetcher
from ..libraries.maimaidx_prober_compare import (
    SYNC_WARN_FISH,
    awmc_differs_from_prober,
    sync_warning_for_source,
)
from ..libraries.maimaidx_qrcode_util import (
    DIRECT_QRCODE_PREFIX_PATTERN,
    extract_sgwcmaid_qrcode,
    qrcode_log_preview,
)

update_pc = on_command('更新pc数', aliases={'更新PC数', '同步pc数', '同步PC数', '绑定机台', '登录机台'})
my_pc = on_command('我的pc数', aliases={'我的PC数', '我的pc', '我的PC'})
pc_rank = on_command('pc排行', aliases={'PC排行', 'pc数排行', 'PC数排行', 'pc全部排行', 'PC全部排行'})
pc_detail = on_command('pc数', aliases={'PC数'})
pc50 = on_command('pc50', aliases={'PC50', '嫖娼50'})
pca50 = on_command('pca50', aliases={'PCA50', '嫖娼a50'})
pc_rank50 = on_command('游玩排行50', aliases={'游玩PC50', 'PC游玩50', 'pc游玩50'})

_waiting_qrcode: dict[int, bool] = {}
_qrcode_auto_dedupe: dict[tuple[int, str], float] = {}
_qrcode_auto_processing: set[int] = set()
_qrcode_retry_state: dict[int, tuple[int, float]] = {}
_QRCODE_AUTO_DEDUPE_SECONDS = 60
_QRCODE_RETRY_WINDOW_SECONDS = 180


# 仅拦截“直接发送”的 SGWCMAID/官方二维码链接；显式命令仍交给命令处理器。
# 优先级 1 + block=True 确保先撤回敏感凭据，再做任何外部请求。
qrcode_auto_listener = on_regex(
    DIRECT_QRCODE_PREFIX_PATTERN,
    flags=re.IGNORECASE,
    priority=1,
    block=True,
)

# maiu/maiul 由其他机器人负责上传查分器；本 bot 监听到后清除玩家缓存，
# 保证上传完成后 b50 拉取的是查分器最新数据（而不是 15 分钟内的旧缓存）。
maiu_cache_listener = on_regex(
    r'^\s*maiul?\s*$',
    flags=re.IGNORECASE,
    priority=98,
    block=False,
)
_MAIU_UPLOAD_GRACE_SECONDS = 120


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

    # 账号功能合并后，已执行「mai绑定」的用户无需重复发送二维码。
    from ..libraries.maimaidx_account_db import account_db
    from ..libraries.maimaidx_platform import billing_user_id

    account_qqid = billing_user_id(event)
    binding = account_db.get(str(account_qqid))
    if binding and binding.qrcode:
        await _handle_sdgb_update(
            bot,
            event,
            account_qqid,
            binding.qrcode,
            matcher=update_pc,
            success_builder=lambda count, total: (
                f'\n✅ 已使用绑定账号同步 PC 数据！\n'
                f'- 收录谱面: {count} 个\n- 总游玩次数: {total} 次'
            ),
        )
        return

    await update_pc.send(
        MessageSegment.reply(event.message_id)
        + MessageSegment.text(
            '\n请发送你的机台二维码数据。\n\n'
            '获取方式：打开舞萌中二获取一个最新二维码，\n'
            '进入后长按识别，\n'
            '复制 SGWCMAID 完整字符串，或二维码图片/请求链接，\n'
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

    raw_text = event.get_plaintext()
    qrcode_data = extract_sgwcmaid_qrcode(raw_text)
    if not qrcode_data:
        await update_pc.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('二维码格式错误，请发送 SGWCMAID 或官方二维码链接。')
        )

    log.info(
        f'[SDGBPC] 用户 {qqid} 手动提交二维码 qrcode={qrcode_log_preview(qrcode_data)}'
    )
    await _handle_sdgb_update(bot, event, qqid, qrcode_data, matcher=update_pc)


def _default_pc_success_message(count: int, total_plays: int) -> str:
    return (
        f'\n✅ 机台成绩已同步到 AWMC！\n'
        f'- 收录谱面: {count} 个\n'
        f'- 总游玩次数: {total_plays} 次\n\n'
        f'使用「pc50」查看按次数排序的 B50\n'
        f'使用「pca50」查看 B50 内按 PC 排序\n'
        f'使用「游玩排行50」查看游玩最多的 50 首谱面\n'
        f'使用「我的pc数」查看详细数据\n'
        f'使用「pc排行」查看全部用户 PC 排行\n\n'
        f'⚠️ b50 等成绩指令以查分器数据为准，'
        f'如需更新请使用 maiu（水鱼）/ maiul（落雪）上传成绩。'
    )


async def _build_auto_qrcode_success_message(event: MessageEvent, storage_qqid: int) -> str:
    """扫码自动同步成功后的提示。

    b50 以查分器数据为准：与查分器比对——
    一致：查分器最新数据已刷入玩家缓存，告知可直接使用 b50；
    不一致：提示用 maiu/maiul 上传，并清除玩家缓存（见 awmc_differs_from_prober），
    保证上传完成后 b50 拉取查分器最新数据。
    """
    base_msg = '🤔 AWMC已根据您提供的二维码同步机台成绩（可使用 pc50 / 我的pc数 等指令）。'
    try:
        score_qqid = resolve_score_qqid(event)
        source = get_user_source(score_qqid)
        if await awmc_differs_from_prober(
            score_qqid,
            storage_qqid=storage_qqid,
            source=source,
        ):
            base_msg += '\n' + sync_warning_for_source(source)
        else:
            base_msg += '\n✅ 机台成绩与查分器一致，可直接使用 b50 等指令。'
    except QBindRequiredError:
        base_msg += '\n' + SYNC_WARN_FISH
    return base_msg


async def _sync_sdgb_qrcode(qqid: int, qrcode_data: str) -> int:
    """扫码同步机台成绩到 AWMC 本地库（供 pc 类指令使用）。

    b50 等成绩指令始终以查分器（水鱼/落雪）数据为准，机台数据不写入玩家缓存。
    同步完成后清除该用户的玩家缓存，保证之后的 b50 拉取查分器最新数据。
    """
    success = await playcount_fetcher.login_by_sdgb(qrcode_data, qqid)
    if not success:
        raise RuntimeError('凭据保存失败')
    count = await playcount_fetcher.fetch_via_sdgb_with_retry(qqid)
    from ..libraries.maimaidx_player_cache import invalidate_player_cache
    invalidate_player_cache(qqid)
    return count


def _qrcode_dedupe_hit(qqid: int, qrcode_data: str) -> bool:
    key = (qqid, qrcode_data[:48])
    now = time.time()
    last = _qrcode_auto_dedupe.get(key, 0)
    if now - last < _QRCODE_AUTO_DEDUPE_SECONDS:
        return True
    _qrcode_auto_dedupe[key] = now
    if len(_qrcode_auto_dedupe) > 500:
        cutoff = now - _QRCODE_AUTO_DEDUPE_SECONDS
        stale = [k for k, ts in _qrcode_auto_dedupe.items() if ts < cutoff]
        for k in stale:
            del _qrcode_auto_dedupe[k]
    return False


def _qrcode_retry_failed(qqid: int) -> tuple[int, bool]:
    now = time.time()
    previous, deadline = _qrcode_retry_state.get(qqid, (0, 0.0))
    attempt = 1 if now > deadline else previous + 1
    exhausted = attempt >= 3
    if exhausted:
        _qrcode_retry_state.pop(qqid, None)
    else:
        _qrcode_retry_state[qqid] = (attempt, now + _QRCODE_RETRY_WINDOW_SECONDS)
    return attempt, exhausted


def _qrcode_retry_succeeded(qqid: int, qrcode_data: str) -> None:
    _qrcode_retry_state.pop(qqid, None)
    # 失败时去掉去重标记，成功后才保留 60 秒防重复请求。
    _qrcode_auto_dedupe[(qqid, qrcode_data[:48])] = time.time()


def _qrcode_retry_release_dedupe(qqid: int, qrcode_data: str) -> None:
    _qrcode_auto_dedupe.pop((qqid, qrcode_data[:48]), None)


async def _handle_sdgb_update(
    bot: Bot,
    event: GroupMessageEvent,
    qqid: int,
    qrcode_data: str,
    *,
    matcher: Matcher = update_pc,
    success_builder: Optional[Callable[[int, int], str]] = None,
):
    """通过 sw-api 更新 PC 数据"""
    try:
        count = await _sync_sdgb_qrcode(qqid, qrcode_data)
    except RuntimeError as e:
        log.error(
            f'[SDGBPC] 用户 {qqid} 拉取失败 qrcode={qrcode_log_preview(qrcode_data)}: {e}'
        )
        await matcher.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text(f'数据拉取失败: {e}。请检查 sw-api 服务或稍后重试。')
        )
        return

    total_plays = pc_db.get_user_total_plays(qqid)
    log.info(
        f'[SDGBPC] 用户 {qqid} 更新成功 records={count} total_plays={total_plays} '
        f'qrcode={qrcode_log_preview(qrcode_data)}'
    )
    if success_builder is None:
        msg = _default_pc_success_message(count, total_plays)
    else:
        msg = success_builder(count, total_plays)

    await matcher.finish(
        MessageSegment.reply(event.message_id) + MessageSegment.text(msg)
    )


@qrcode_auto_listener.handle()
async def _auto_qrcode_update(bot: Bot, event: MessageEvent):
    """直发 SGWCMAID/官方链接：先撤回，再更新 PC，最后按绑定配置上传。"""
    recalled = True
    try:
        await bot.delete_msg(message_id=event.message_id)
    except Exception as exc:
        recalled = False
        log.warning(f'[QrcodeAuto] 敏感消息撤回失败：{type(exc).__name__}')

    from .mai_agreement import agreement_prompt, has_user_agreed

    recall_warning = (
        '⚠️ Bot 无法撤回原凭据消息，请立即手动撤回。\n'
        if not recalled else ''
    )
    if not has_user_agreed(event):
        await bot.send(event, recall_warning + agreement_prompt())
        return
    if not playcount_fetcher.sdgb_available:
        await bot.send(event, recall_warning + 'AWMC PC 服务尚未配置，未处理该二维码。')
        return
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'query'):
        if recall_warning:
            await bot.send(event, recall_warning.strip())
        return

    qqid = int(billing_user_id(event))
    if qqid in _waiting_qrcode:
        _waiting_qrcode.pop(qqid, None)

    qrcode_data = extract_sgwcmaid_qrcode(event.get_plaintext())
    if not qrcode_data:
        log.debug(
            f'[QrcodeAuto] group={getattr(event, "group_id", "private")} '
            f'用户 {qqid} 的二维码消息提取失败'
        )
        return

    if _qrcode_dedupe_hit(qqid, qrcode_data):
        log.info(
            f'[QrcodeAuto] 跳过重复请求 group={getattr(event, "group_id", "private")} qq={qqid} '
            f'qrcode={qrcode_log_preview(qrcode_data)}'
        )
        return

    if qqid in _qrcode_auto_processing:
        log.info(f'[QrcodeAuto] 用户 {qqid} 已有进行中的同步，跳过')
        return

    _qrcode_auto_processing.add(qqid)
    t0 = time.perf_counter()
    log.info(
        f'[QrcodeAuto] 开始同步 group={getattr(event, "group_id", "private")} qq={qqid} '
        f'qrcode={qrcode_log_preview(qrcode_data)}'
    )
    try:
        count = await _sync_sdgb_qrcode(qqid, qrcode_data)
    except Exception as e:
        _qrcode_retry_release_dedupe(qqid, qrcode_data)
        attempt, exhausted = _qrcode_retry_failed(qqid)
        log.error(
            f'[QrcodeAuto] 同步失败 group={getattr(event, "group_id", "private")} qq={qqid} '
            f'qrcode={qrcode_log_preview(qrcode_data)} ({time.perf_counter() - t0:.2f}s): {e}'
        )
        if exhausted:
            retry_text = (
                f'二维码验证已连续失败 3 次（{type(e).__name__}），流程已结束。\n'
                '请返回舞萌页面重新获取最新二维码，稍后再直接发送。'
            )
        else:
            retry_text = (
                f'二维码无效、已过期或服务暂时不可用（{type(e).__name__}）。\n'
                f'请在 3 分钟内重新获取并发送 SGWCMAID 或官方链接（{attempt}/3）。'
            )
        await bot.send(event, recall_warning + retry_text)
        return
    finally:
        _qrcode_auto_processing.discard(qqid)

    _qrcode_retry_succeeded(qqid, qrcode_data)

    from ..libraries.maimaidx_account_db import account_db
    from .mai_account import _has_lxns_oauth, _upload

    binding = account_db.get(str(qqid))
    fish = bool(binding and binding.fish_token)
    lxns = bool(binding and binding.lxns_token) or _has_lxns_oauth(event)
    total_plays = pc_db.get_user_total_plays(qqid)
    lines = [
        '✅ 已同步 PC 数据',
        f'收录谱面：{count} 个 · 总游玩：{total_plays} PC',
    ]
    if recall_warning:
        lines.insert(0, recall_warning.strip())
    if fish or lxns:
        lines.append(await _upload(
            event, fish=fish, lxns=lxns, qrcode_arg=qrcode_data,
        ))
    else:
        lines.append('未绑定水鱼 Token 或落雪 OAuth，本次仅同步 PC。')
    msg = '\n'.join(lines)
    log.info(
        f'[QrcodeAuto] 同步成功 group={getattr(event, "group_id", "private")} qq={qqid} records={count} '
        f'({time.perf_counter() - t0:.2f}s) qrcode={qrcode_log_preview(qrcode_data)}'
    )
    prefix = MessageSegment.at(event.user_id) + MessageSegment.text('\n') if isinstance(event, GroupMessageEvent) else Message()
    await bot.send(event, message=prefix + MessageSegment.text(msg))


@maiu_cache_listener.handle()
async def _maiu_invalidate_cache(event: MessageEvent):
    """用户发送 maiu/maiul（由其他机器人上传查分器）时清除本 bot 的玩家缓存。

    上传耗时不定：先立即清一次；再在宽限期后清一次，
    防止用户在上传完成前查询 b50 又把旧数据缓存 15 分钟。
    """
    from ..libraries.maimaidx_player_cache import invalidate_player_cache
    try:
        qqid = resolve_score_qqid(event)
    except QBindRequiredError:
        return
    invalidate_player_cache(qqid)
    log.info(f'[MaiuCache] 检测到 maiu/maiul，已清除 qq={qqid} 玩家缓存')

    async def _delayed_invalidate():
        await asyncio.sleep(_MAIU_UPLOAD_GRACE_SECONDS)
        invalidate_player_cache(qqid)
        log.info(f'[MaiuCache] 上传宽限期结束，再次清除 qq={qqid} 玩家缓存')

    asyncio.create_task(_delayed_invalidate())


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
    """处理「pc排行」命令，展示全部已同步 PC 数据的用户排行。"""
    await check_feature(bot, event)

    all_users = pc_db.get_all_users_with_data()
    if not all_users:
        await pc_rank.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.text('暂无PC排行数据，请先使用「更新pc数」同步数据。')
        )

    user_stats = []
    for uid in all_users:
        total = pc_db.get_user_total_plays(uid)
        records = pc_db.get_user_play_counts(uid)
        user_stats.append((uid, total, len(records)))

    user_stats.sort(key=lambda x: x[1], reverse=True)

    lines = [f'PC全部排行（共 {len(user_stats)} 人）:']
    for i, (uid, total, count) in enumerate(user_stats, 1):
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


@pc_rank50.handle()
async def handle_pc_rank50(
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
            result = await generate_pc_rank50(qqid)
    except BreakInsufficientError as e:
        await pc_rank50.finish(str(e), reply_message=True)
        return
    charge = take_break_charge_footer()
    if charge and not isinstance(result, str):
        result = result + MessageSegment.text('\n' + '\n'.join(charge))
    await pc_rank50.finish(result, reply_message=True)
