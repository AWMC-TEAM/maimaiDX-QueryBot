from typing import Optional

from nonebot import get_bots, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg, Depends
from nonebot.permission import SUPERUSER

from ..libraries.maimaidx_break import (
    DEFAULT_CONFIG,
    LOTTERY_PRIZES,
    LOTTERY_WEIGHTS,
    break_db,
    format_account_profile,
    format_account_profile_sections,
    format_analysis_pricing_help,
    format_checkin_result,
    get_account_profile,
)
from ..libraries.maimaidx_platform import billing_user_id
from ..libraries.maimaidx_group_rating import build_forward_node
from ..libraries.maimaidx_pending_session import finish_pending, session_key, track_event
from ..config import log, maiconfig
from .mai_agreement import agreement_prompt, has_user_agreed

require('nonebot_plugin_apscheduler')
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

awmc_checkin = on_command('AWMC签到', aliases={'签到', 'awmc签到'})
my_awmc = on_command(
    '我的AWMC', aliases={'我的awmc', 'AWMC状态', 'awmc状态', '我的账号'}
)
awmc_admin_set = on_command('设置BREAK', permission=SUPERUSER)
awmc_admin_add = on_command('增减BREAK', permission=SUPERUSER)
awmc_admin_config = on_command('BREAK配置', permission=SUPERUSER)
awmc_admin_view = on_command('查看AWMC', permission=SUPERUSER)
ticket_stats_admin = on_command(
    '发票统计', aliases={'ticket统计', 'returnCode统计'}, permission=SUPERUSER
)
awmc_help = on_command('AWMC帮助', aliases={'BREAK帮助'})
break_transfer = on_command('转账BREAK', aliases={'BREAK转账'})
break_lottery = on_command('BREAK抽奖', aliases={'抽奖BREAK'})
break_red_packet_send = on_command(
    '发红包', aliases={'发BREAK红包', 'BREAK红包', '发break红包'}
)
break_red_packet_claim = on_command(
    '抢红包', aliases={'领红包', '抢BREAK红包', '抢break红包'}
)
break_red_packet_status = on_command(
    '红包状态', aliases={'红包记录', '查看红包'}
)

for _debt_exempt_matcher in (
    awmc_checkin,
    my_awmc,
    awmc_help,
    break_red_packet_claim,
    break_red_packet_status,
):
    setattr(_debt_exempt_matcher, "_maimaidx_debt_exempt", True)

_LOTTERY_PRIZE_LINES = ''.join(
    f'· {prize} BREAK：{weight}%\n'
    for prize, weight in zip(LOTTERY_PRIZES, LOTTERY_WEIGHTS)
)
LOTTERY_HELP = (
    '【BREAK 抽奖】\n'
    '这是 Bot 内部的群互动消耗玩法，BREAK 不具有现金价值。\n'
    '默认每次消耗 2 BREAK，奖池为：\n'
    + _LOTTERY_PRIZE_LINES
    + '用法：BREAK抽奖 [次数]\n'
    '例：“BREAK抽奖”抽 1 次，“BREAK抽奖 5”连抽 5 次。\n'
    '非空奖概率 65%，单抽期望返还 1.6 BREAK；最多 10 连抽，长期仍为净消耗。'
)

def get_at_qq(message: MessageEvent) -> Optional[int]:
    for item in message.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data['qq'] != 'all':
            return int(item.data['qq'])
    return None


async def _require_break_agreement(matcher, event: MessageEvent) -> None:
    if not has_user_agreed(event):
        await matcher.finish(agreement_prompt(), reply_message=True)


@awmc_help.handle()
async def _():
    text = (
        '【AWMC BREAK 系统】\n'
        '· AWMC签到 — 每日签到获取 BREAK（基础 1~2，连续签到奖励不封顶）\n'
        '· 今日舞萌 — 人品值四舍五入后 ÷10，每日领取一次 BREAK\n'
        '· 猜歌 — 每次猜对奖励 1 BREAK，无每日上限\n'
        '· 转账BREAK @用户 数量 — 转给其他用户\n'
        '· BREAK抽奖 [1-10] — 每次默认消耗 2 BREAK，发送“BREAK抽奖 帮助”看奖池\n'
        '· 发红包 [总额] [份数] — 群内发送 BREAK 手气红包，也可按提示逐步输入\n'
        '· 抢红包 — 领取本群当前红包；红包状态 — 查看领取明细\n'
        '· 我的AWMC — 查看账号状态与使用统计\n'
        '· 查分指令 — 每日首次实际请求查分器 API 免费，之后每次扣 1 BREAK（缓存命中不扣）\n'
        + format_analysis_pricing_help()
        + '· BREAK 不足时请先签到\n\n'
        '【签到加成（加算）】\n'
        '· 指定群 1072033605、993795066 +25%\n'
        '· 指定群 669800745 签到总奖励 ×2\n'
        '· 周四 +50%\n'
        '· 群内当日首签 +50%\n'
        '· 连续签到额外奖励'
    )
    await awmc_help.finish(text, reply_message=True)


@break_transfer.handle()
async def _(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    await _require_break_agreement(break_transfer, event)
    parts = message.extract_plain_text().strip().split()
    target = user_id
    if target is None and parts and parts[0].isdigit():
        target = int(parts.pop(0))
    amount = int(parts[-1]) if parts and parts[-1].isdigit() else 0
    if target is None or amount <= 0:
        await break_transfer.finish('用法：转账BREAK @用户 数量', reply_message=True)
    try:
        result = break_db.transfer(int(billing_user_id(event)), target, amount)
    except Exception as exc:
        await break_transfer.finish(f'转账失败：{exc}', reply_message=True)
    fee_text = f'（手续费 {result.fee}）' if result.fee else ''
    await break_transfer.finish(
        f'转账成功：{result.amount} BREAK {fee_text}\n当前余额：{result.sender_balance}',
        reply_message=True,
    )


@break_lottery.handle()
async def _(event: MessageEvent, message: Message = CommandArg()):
    raw = message.extract_plain_text().strip() or '1'
    if raw.lower() in {'帮助', '说明', 'help', '?'}:
        await break_lottery.finish(LOTTERY_HELP, reply_message=True)
    await _require_break_agreement(break_lottery, event)
    if not raw.isdigit() or not 1 <= int(raw) <= 10:
        await break_lottery.finish('用法：BREAK抽奖 [1-10]', reply_message=True)
    try:
        result = break_db.lottery(int(billing_user_id(event)), int(raw))
    except Exception as exc:
        await break_lottery.finish(f'抽奖失败：{exc}', reply_message=True)
    await break_lottery.finish(
        f'抽奖 {result.count} 次\n消耗：{result.cost} BREAK\n'
        f'获得：{result.prize} BREAK\n当前余额：{result.balance}',
        reply_message=True,
    )


def _red_packet_group_id(event: MessageEvent) -> Optional[int]:
    if isinstance(event, GroupMessageEvent):
        return int(event.group_id)
    return None


@break_red_packet_send.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    message: Message = CommandArg(),
):
    await _require_break_agreement(break_red_packet_send, event)
    if _red_packet_group_id(event) is None:
        await break_red_packet_send.finish('BREAK 红包只能在群聊中发送。')
    raw = message.extract_plain_text().strip()
    if raw.lower() in {'帮助', '说明', 'help', '?'}:
        await break_red_packet_send.finish(
            '【BREAK 手气红包】\n'
            '用法：发红包 总额 份数\n'
            '例：发红包 20 5\n'
            '也可以只发送“发红包”，Bot 会依次询问总额和份数。\n'
            '群友发送“抢红包”领取，每人限领一次；10 分钟后未领金额自动退回。\n'
            'BREAK 是 Bot 内部积分，不具有现金价值。',
            reply_message=True,
        )
    parts = raw.split()
    if len(parts) > 2:
        await break_red_packet_send.finish(
            '参数太多啦。用法：发红包 总额 份数', reply_message=True
        )
    pending_key = session_key('break_red_packet', event)
    track_event(pending_key, event)
    if parts:
        matcher.set_arg('red_packet_total', Message(parts[0]))
    if len(parts) == 2:
        matcher.set_arg('red_packet_count', Message(parts[1]))


@break_red_packet_send.got(
    'red_packet_total', prompt='请输入红包总额（正整数 BREAK），发送“取消”可退出。'
)
async def _(
    matcher: Matcher,
    event: MessageEvent,
    total_message: Message = Arg('red_packet_total'),
):
    pending_key = session_key('break_red_packet', event)
    raw = total_message.extract_plain_text().strip()
    if raw.lower() in {'取消', 'cancel', 'q', '退出'}:
        finish_pending(pending_key)
        await break_red_packet_send.finish('已取消发送红包。')
    if not raw.isdigit() or int(raw) <= 0:
        track_event(pending_key, event)
        await break_red_packet_send.reject(
            '红包总额必须是正整数，请重新输入；发送“取消”可退出。'
        )
    matcher.state['red_packet_total_value'] = int(raw)


@break_red_packet_send.got(
    'red_packet_count', prompt='请输入红包份数（正整数），发送“取消”可退出。'
)
async def _(
    matcher: Matcher,
    event: MessageEvent,
    count_message: Message = Arg('red_packet_count'),
):
    pending_key = session_key('break_red_packet', event)
    raw = count_message.extract_plain_text().strip()
    if raw.lower() in {'取消', 'cancel', 'q', '退出'}:
        finish_pending(pending_key)
        await break_red_packet_send.finish('已取消发送红包。')
    if not raw.isdigit() or int(raw) <= 0:
        track_event(pending_key, event)
        await break_red_packet_send.reject(
            '红包份数必须是正整数，请重新输入；发送“取消”可退出。'
        )
    group_id = _red_packet_group_id(event)
    if group_id is None:
        finish_pending(pending_key)
        await break_red_packet_send.finish('BREAK 红包只能在群聊中发送。')
    try:
        result = break_db.create_red_packet(
            int(billing_user_id(event)),
            group_id,
            int(matcher.state['red_packet_total_value']),
            int(raw),
        )
    except Exception as exc:
        finish_pending(pending_key)
        await break_red_packet_send.finish(f'红包发送失败：{exc}', reply_message=True)
    finish_pending(pending_key)
    expire_minutes = max(
        1,
        int(float(break_db.get_config('red_packet_expire_minutes', '10'))),
    )
    await break_red_packet_send.finish(
        '🧧 BREAK 手气红包来啦！\n'
        f'总额：{result.total_amount} BREAK · 共 {result.total_count} 份\n'
        f'红包编号：{result.packet_id}\n'
        '发送“抢红包”即可领取，每人限领一次。\n'
        f'{expire_minutes} 分钟后未领取的余额会自动退回。',
        reply_message=True,
    )


@break_red_packet_claim.handle()
async def _(event: MessageEvent):
    await _require_break_agreement(break_red_packet_claim, event)
    group_id = _red_packet_group_id(event)
    if group_id is None:
        await break_red_packet_claim.finish('BREAK 红包只能在群聊中领取。')
    try:
        result = break_db.claim_red_packet(int(billing_user_id(event)), group_id)
    except Exception as exc:
        await break_red_packet_claim.finish(f'领取失败：{exc}', reply_message=True)
    tail = '\n🎉 红包已经被领完啦！' if result.completed else (
        f'\n剩余 {result.remaining_count} 份，共 {result.remaining_amount} BREAK。'
    )
    await break_red_packet_claim.finish(
        f'🧧 领取成功：{result.amount} BREAK\n'
        f'当前余额：{result.recipient_balance} BREAK{tail}',
        reply_message=True,
    )


@break_red_packet_status.handle()
async def _(event: MessageEvent):
    group_id = _red_packet_group_id(event)
    if group_id is None:
        await break_red_packet_status.finish('红包状态只能在群聊中查看。')
    status = break_db.get_red_packet_status(group_id)
    if status is None:
        await break_red_packet_status.finish('本群还没有红包记录。', reply_message=True)
    labels = {'active': '领取中', 'completed': '已领完', 'expired': '已过期'}
    claim_lines = [f'· {qqid}：{amount} BREAK' for qqid, amount in status.claims]
    detail = '\n'.join(claim_lines) if claim_lines else '· 暂无人领取'
    await break_red_packet_status.finish(
        f'【红包 {status.packet_id} · {labels.get(status.status, status.status)}】\n'
        f'发送者：{status.sender_qqid}\n'
        f'总额 {status.total_amount} BREAK · {status.total_count} 份\n'
        f'剩余 {status.remaining_amount} BREAK · {status.remaining_count} 份\n'
        f'领取明细：\n{detail}',
        reply_message=True,
    )


@scheduler.scheduled_job(
    'interval', minutes=1, id='break_red_packet_expiry'
)
async def _expire_break_red_packets() -> None:
    refunds = break_db.expire_red_packets()
    if not refunds:
        return
    bots = get_bots()
    bot = next(iter(bots.values()), None)
    for item in refunds:
        log.info(
            f'[BREAK红包] {item.packet_id} 已过期，退回 {item.refund} BREAK '
            f'to={item.sender_qqid}'
        )
        if bot is None:
            continue
        try:
            await bot.send_group_msg(
                group_id=item.group_id,
                message=(
                    f'🧧 红包 {item.packet_id} 已过期，'
                    f'剩余 {item.refund} BREAK 已退回发送者。'
                ),
            )
        except Exception as exc:
            log.warning(
                f'[BREAK红包] 过期通知发送失败：{type(exc).__name__}: {exc}'
            )


@awmc_checkin.handle()
async def _(event: MessageEvent):
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    qqid = int(event.get_user_id())
    result = break_db.checkin(qqid, group_id)
    await awmc_checkin.finish(format_checkin_result(result), reply_message=True)


@my_awmc.handle()
async def _(bot: Bot, event: MessageEvent):
    qqid = int(billing_user_id(event))
    profile = get_account_profile(qqid)
    sections = format_account_profile_sections(profile)
    nickname = str(getattr(maiconfig, 'botName', None) or 'AWMC Bot')
    nodes = [build_forward_node(str(event.self_id), nickname, section) for section in sections]
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                'send_group_forward_msg', group_id=event.group_id, messages=nodes
            )
        else:
            await bot.call_api(
                'send_private_forward_msg', user_id=event.user_id, messages=nodes
            )
    except Exception as exc:
        log.warning(f'[BREAK] 我的AWMC合并转发失败，回退文本：{type(exc).__name__}: {exc}')
        await my_awmc.finish(format_account_profile(profile), reply_message=True)
    await my_awmc.finish()


@awmc_admin_view.handle()
async def _(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    target = user_id
    if target is None:
        text = message.extract_plain_text().strip()
        if text.isdigit():
            target = int(text)
    if target is None:
        await awmc_admin_view.finish('请 @用户 或提供 QQ 号', reply_message=True)
        return
    profile = get_account_profile(target)
    await awmc_admin_view.finish(
        format_account_profile(profile, title=f'AWMC 账号 {target}'),
        reply_message=True,
    )


@ticket_stats_admin.handle()
async def _(message: Message = CommandArg()):
    from ..libraries.maimaidx_account_db import account_db

    raw = message.extract_plain_text().strip()
    days: Optional[int] = None
    if raw:
        try:
            days = max(1, int(raw))
        except ValueError:
            await ticket_stats_admin.finish(
                '用法：发票统计 [天数]，例如「发票统计 7」；不填为全部历史',
                reply_message=True,
            )
            return
    stats = account_db.get_ticket_stats(days=days)
    title = f'全局发票统计（近 {days} 天）' if days else '全局发票统计（全部）'
    await ticket_stats_admin.finish(
        account_db.format_ticket_stats(stats, title=title),
        reply_message=True,
    )


@awmc_admin_set.handle()
async def _(
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    parts = message.extract_plain_text().strip().split()
    target = user_id
    amount: Optional[int] = None
    if target is None and parts and parts[0].isdigit():
        target = int(parts[0])
        parts = parts[1:]
    if parts and parts[-1].lstrip('-').isdigit():
        amount = int(parts[-1])
    if target is None or amount is None:
        await awmc_admin_set.finish(
            '用法：设置BREAK @用户 数量\n示例：设置BREAK @某人 100',
            reply_message=True,
        )
        return
    balance = break_db.admin_set_balance(target, amount)
    await awmc_admin_set.finish(f'已将 {target} 的 BREAK 设为 {balance}', reply_message=True)


@awmc_admin_add.handle()
async def _(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    parts = message.extract_plain_text().strip().split()
    target = user_id
    delta: Optional[int] = None
    if target is None and parts and parts[0].lstrip('-').isdigit():
        target = int(parts[0])
        parts = parts[1:]
    if parts:
        raw = parts[-1]
        if raw.lstrip('-+').isdigit() or (raw.startswith(('+', '-')) and raw[1:].isdigit()):
            delta = int(raw)
    if target is None or delta is None:
        await awmc_admin_add.finish(
            '用法：增减BREAK @用户 ±N\n示例：增减BREAK @某人 +10',
            reply_message=True,
        )
        return
    balance = break_db.add_balance(target, delta, 'admin_add', meta={'by': event.get_user_id()})
    await awmc_admin_add.finish(
        f'已为 {target} {"增加" if delta >= 0 else "减少"} {abs(delta)} BREAK，当前 {balance}',
        reply_message=True,
    )


@awmc_admin_config.handle()
async def _(message: Message = CommandArg()):
    parts = message.extract_plain_text().strip().split(maxsplit=1)
    if len(parts) < 2:
        lines = ['当前 BREAK 配置：'] + [
            f'  · {k} = {break_db.get_config(k, v)}' for k, v in DEFAULT_CONFIG.items()
        ]
        await awmc_admin_config.finish('\n'.join(lines), reply_message=True)
        return
    key, value = parts[0].strip(), parts[1].strip()
    break_db.set_config(key, value)
    await awmc_admin_config.finish(f'已设置 {key} = {value}', reply_message=True)
