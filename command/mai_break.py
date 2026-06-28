from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg, Depends
from nonebot.permission import SUPERUSER

from ..libraries.maimaidx_break import (
    DEFAULT_CONFIG,
    break_db,
    format_account_profile,
    format_checkin_result,
    get_account_profile,
)

awmc_checkin = on_command('AWMC签到', aliases={'签到', 'awmc签到'})
my_awmc = on_command('我的AWMC', aliases={'AWMC状态', '我的账号'})
awmc_admin_set = on_command('设置BREAK', permission=SUPERUSER)
awmc_admin_add = on_command('增减BREAK', permission=SUPERUSER)
awmc_admin_config = on_command('BREAK配置', permission=SUPERUSER)
awmc_admin_view = on_command('查看AWMC', permission=SUPERUSER)
awmc_help = on_command('AWMC帮助', aliases={'BREAK帮助'})


def get_at_qq(message: MessageEvent) -> Optional[int]:
    for item in message.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data['qq'] != 'all':
            return int(item.data['qq'])
    return None


@awmc_help.handle()
async def _():
    text = (
        '【AWMC BREAK 系统】\n'
        '· AWMC签到 — 每日签到获取 BREAK\n'
        '· 我的AWMC — 查看账号状态与使用统计\n'
        '· 查分指令 — 每日首次实际请求查分器 API 免费，之后每次扣 1 BREAK（缓存命中不扣）\n'
        '· 分析b50 — 每次成功消耗 3 BREAK（AI 锐评 + 分析长图）\n'
        '· BREAK 不足时请先签到\n\n'
        '【签到加成（加算）】\n'
        '· 指定群 1072033605 +50%\n'
        '· 周四 +100%\n'
        '· 群内当日首签 +100%\n'
        '· 连续签到额外奖励'
    )
    await awmc_help.finish(text, reply_message=True)


@awmc_checkin.handle()
async def _(event: MessageEvent):
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    qqid = int(event.get_user_id())
    result = break_db.checkin(qqid, group_id)
    await awmc_checkin.finish(format_checkin_result(result), reply_message=True)


@my_awmc.handle()
async def _(event: MessageEvent):
    qqid = int(event.get_user_id())
    profile = get_account_profile(qqid)
    await my_awmc.finish(format_account_profile(profile), reply_message=True)


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
