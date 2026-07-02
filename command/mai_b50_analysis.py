from __future__ import annotations

import io
import json

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..config import maiconfig
from ..libraries.b50_analysis import (
    build_context,
    check_llm_output,
    check_user_input,
    generate_analysis,
    load_peer_stats,
    prepare_render_cache,
    render_image,
)
from ..libraries.b50_analysis.adapter import fetch_for_analysis
from ..libraries.maimaidx_break import (
    analysis_cost,
    break_billing,
    break_db,
    ensure_analysis_affordable,
    format_analysis_cost_line,
    is_analysis_peak_hour,
    settle_analysis_charge,
    take_break_charge_footer,
)
from ..libraries.maimaidx_error import BreakInsufficientError

_peer_stats = None


def get_peer_stats():
    global _peer_stats
    if _peer_stats is None and maiconfig.b50_assets_path:
        _peer_stats = load_peer_stats(maiconfig.b50_assets_path)
    return _peer_stats


def set_peer_stats(stats):
    global _peer_stats
    _peer_stats = stats


b50_analysis_cmd = on_command(
    '锐评一下',
    aliases={'分析b50', '分析B50', 'B50分析'},
    priority=4,
    block=True,
)


@b50_analysis_cmd.handle()
async def _handle(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    style = args.extract_plain_text().strip()
    qq = int(event.get_user_id())

    try:
        ensure_analysis_affordable(qq)
    except BreakInsufficientError as e:
        await matcher.finish(str(e), reply_message=True)
        return

    if not maiconfig.b50_llm_key:
        await matcher.finish('未配置 b50_llm_key，请在 .env 中填写 API Key', reply_message=True)
        return
    if not maiconfig.b50_assets_path:
        await matcher.finish('未配置 b50_assets_path，请在 .env 中填写分析素材目录', reply_message=True)
        return

    pending = '正在查询 B50，请稍候…'
    if is_analysis_peak_hour():
        pending += f'（峰时双倍计费，本次消耗 {analysis_cost()} BREAK）'
    await matcher.send(pending, reply_message=True)

    if style:
        mod_result = check_user_input(style)
        if not mod_result.get('allowed', True):
            await matcher.finish(
                mod_result.get('reason', '请求包含不适合处理的内容，本次分析已驳回'),
                reply_message=True,
            )
            return

    try:
        async with break_billing(qq):
            b50_data = await fetch_for_analysis(qq, assets_path=maiconfig.b50_assets_path)
    except BreakInsufficientError as e:
        await matcher.finish(str(e), reply_message=True)
        return
    except ValueError as e:
        await matcher.finish(str(e), reply_message=True)
        return
    except Exception:
        await matcher.finish('查询失败，请稍后重试', reply_message=True)
        return

    peer_stats = get_peer_stats()
    context = build_context(b50_data, peer_stats)
    context['player']['qq'] = str(qq)

    try:
        analysis_text = await generate_analysis(context, maiconfig, style)
    except Exception as e:
        await matcher.finish(f'分析生成失败：{e}', reply_message=True)
        return

    try:
        _parsed = json.loads(analysis_text)
        for field in ('overall_roast', 'impression_roast', 'title'):
            original = str(_parsed.get(field) or '')
            if not original:
                continue
            checked = check_llm_output(original)
            if checked.get('safe', True):
                continue
            _parsed[field] = checked.get('redacted', original)
        if isinstance(_parsed.get('push_recommendations'), list):
            context.setdefault('evidence', {})['push_recommendations'] = (
                _parsed.get('push_recommendations') or []
            )
        analysis_text = json.dumps(_parsed, ensure_ascii=False)
    except Exception:
        pass

    try:
        await prepare_render_cache(context, maiconfig.b50_assets_path)
        img = render_image(context, analysis_text, maiconfig.b50_assets_path)
    except Exception as e:
        await matcher.finish(f'制图失败：{e}', reply_message=True)
        return

    settle_analysis_charge(qq)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    cost = analysis_cost()
    balance = break_db.get_balance(qq)
    query_footer = take_break_charge_footer()
    footer_parts = []
    if query_footer:
        footer_parts.extend(query_footer)
    footer_parts.append(format_analysis_cost_line(charged=cost, balance=balance))
    footer = '\n' + '\n'.join(footer_parts)
    await matcher.finish(
        MessageSegment.image(buf) + MessageSegment.text(footer),
        reply_message=True,
    )
