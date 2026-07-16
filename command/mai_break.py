from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg, Depends
from nonebot.permission import SUPERUSER

from ..libraries.maimaidx_break import (
    DEFAULT_CONFIG,
    break_db,
    format_account_profile,
    format_account_profile_sections,
    format_analysis_pricing_help,
    format_checkin_result,
    get_account_profile,
)
from ..libraries.maimaidx_platform import billing_user_id
from ..libraries.maimaidx_group_rating import build_forward_node
from ..config import log, maiconfig
from .mai_agreement import agreement_prompt, has_user_agreed

awmc_checkin = on_command('AWMC签到', aliases={'签到', 'awmc签到'})
my_awmc = on_command('我的AWMC', aliases={'AWMC状态', '我的账号'})
awmc_admin_set = on_command('设置BREAK', permission=SUPERUSER)
awmc_admin_add = on_command('增减BREAK', permission=SUPERUSER)
awmc_admin_config = on_command('BREAK配置', permission=SUPERUSER)
awmc_admin_view = on_command('查看AWMC', permission=SUPERUSER)
awmc_help = on_command('AWMC帮助', aliases={'BREAK帮助'})
break_transfer = on_command('转账BREAK', aliases={'BREAK转账'})
break_lottery = on_command('BREAK抽奖', aliases={'抽奖BREAK'})

LOTTERY_HELP = (
    '【BREAK 抽奖】\n'
    '这是 Bot 内部的群互动消耗玩法，BREAK 不具有现金价值。\n'
    '默认每次消耗 2 BREAK，奖池为：\n'
    '· 0 BREAK：55%\n'
    '· 1 BREAK：25%\n'
    '· 2 BREAK：15%\n'
    '· 5 BREAK：5%\n'
    '用法：BREAK抽奖 [次数]\n'
    '例：“BREAK抽奖”抽 1 次，“BREAK抽奖 5”连抽 5 次。\n'
    '单次最多 10 连抽；长期期望为净消耗，用于控制 BREAK 通胀。'
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
        '· 我的AWMC — 查看账号状态与使用统计\n'
        '· 查分指令 — 每日首次实际请求查分器 API 免费，之后每次扣 1 BREAK（缓存命中不扣）\n'
        + format_analysis_pricing_help()
        + '· BREAK 不足时请先签到\n\n'
        '【签到加成（加算）】\n'
        '· 指定群 1072033605 +25%\n'
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
