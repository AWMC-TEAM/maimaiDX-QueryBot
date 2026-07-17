"""QueryBot 运行时审计、封禁拦截与管理员 REF_ID 指令。"""

from __future__ import annotations

import time
from typing import Optional

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor, run_preprocessor
from nonebot.params import CommandArg, Depends
from nonebot.typing import T_State

from ..config import log, maiconfig
from ..libraries.maimaidx_admin_audit import admin_audit, redact
from ..libraries.maimaidx_bot_admin import PLUGIN_ADMIN_ONLY, is_plugin_admin
from ..libraries.maimaidx_platform import get_event_group_id
from ..libraries.maimaidx_platform import billing_user_id
from ..libraries.maimaidx_break import (
    break_db,
    format_break_insufficient_message,
    is_superuser_exempt,
)
from ..libraries.maimaidx_request_rate import request_meter


audit_query = on_command("查询REF", aliases={"查询Ref_ID", "ref查询"}, permission=PLUGIN_ADMIN_ONLY)
ban_user_cmd = on_command("封禁用户", aliases={"封禁"}, permission=PLUGIN_ADMIN_ONLY)
unban_user_cmd = on_command("解封用户", aliases={"解封"}, permission=PLUGIN_ADMIN_ONLY)
ban_list_cmd = on_command("封禁列表", permission=PLUGIN_ADMIN_ONLY)
admin_web_cmd = on_command("管理面板", aliases={"WebUI"}, permission=PLUGIN_ADMIN_ONLY)

_message_recorder = on_message(priority=99, block=False)
_ban_notified: dict[str, float] = {}


@get_driver().on_startup
async def _cleanup_admin_audit() -> None:
    retention_days = int(getattr(maiconfig, "maimaidx_audit_retention_days", 90))
    result = admin_audit.cleanup(retention_days)
    if any(result.values()):
        log.info(f'管理审计过期数据已清理：{result}')


def _plugin_matcher(matcher: Matcher) -> bool:
    module = str(getattr(matcher, "module", "") or "")
    plugin_name = str(getattr(matcher, "plugin_name", "") or "")
    return "maimaidx" in module.lower() or "maimaidx" in plugin_name.lower()


def _busy_surcharge_exempt(matcher: Matcher) -> bool:
    """猜歌模块是免费奖励玩法，整模块不参与高负载 BREAK 附加费。"""
    if bool(getattr(type(matcher), "_maimaidx_busy_surcharge_exempt", False)):
        return True
    module = str(getattr(matcher, "module", "") or "")
    return module.endswith(".mai_guess")


def _command_name(matcher: Matcher, event: Event, state: T_State) -> str:
    prefix = state.get("_prefix") or {}
    command = prefix.get("command") if isinstance(prefix, dict) else None
    if isinstance(command, (tuple, list)):
        command = "".join(str(item) for item in command)
    if command:
        return str(command)[:200]
    module = str(getattr(matcher, "module", "") or "")
    try:
        text = event.get_plaintext().strip()
    except Exception:
        text = ""
    label = text.split(maxsplit=1)[0][:40] if text else "message"
    return f"{module.rsplit('.', 1)[-1]}:{label}"


def _event_summary(event: Event) -> dict:
    try:
        message = event.get_message()
        segment_types = [str(getattr(seg, "type", "unknown")) for seg in message]
    except Exception:
        segment_types = []
    try:
        text_len = len(event.get_plaintext())
    except Exception:
        text_len = 0
    return {"message_length": text_len, "segment_types": segment_types[:30]}


def _event_request_key(bot: Bot, event: Event) -> str:
    event_id = getattr(event, "message_id", None) or getattr(event, "id", None)
    if event_id is None:
        event_id = f"object-{id(event)}"
    return f"{getattr(bot, 'self_id', '')}:{event_id}"


@_message_recorder.handle()
async def _(event: Event):
    if not bool(getattr(maiconfig, "maimaidx_message_stats_enabled", True)):
        return
    gid = get_event_group_id(event)
    if gid is None:
        return
    admin_audit.record_message(str(gid), str(event.get_user_id()))


@run_preprocessor
async def _audit_and_ban_preprocessor(
    matcher: Matcher, bot: Bot, event: Event, state: T_State
):
    if not _plugin_matcher(matcher):
        return
    # 全量消息统计只计数，不为每条普通聊天创建 REF 链路，也不触发封禁提示。
    if type(matcher) is _message_recorder or bool(
        getattr(type(matcher), "_maimaidx_passive_recorder", False)
    ):
        return
    uid = str(event.get_user_id())
    ban_keys = [uid]
    try:
        billing_key = str(billing_user_id(event))
        if billing_key not in ban_keys:
            ban_keys.append(billing_key)
    except Exception:
        pass
    ban = next((row for key in ban_keys if (row := admin_audit.get_active_ban(key))), None)
    if ban and not is_plugin_admin(uid):
        event_key = str(getattr(event, "message_id", "") or getattr(event, "id", "") or f"{uid}:{int(time.time())}")
        now = time.time()
        if now - _ban_notified.get(event_key, 0) > 10:
            _ban_notified[event_key] = now
            reason = str(ban.get("reason") or "违反使用规则")
            expires = ban.get("expires_at")
            expire_text = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(float(expires)))
                if expires else "永久"
            )
            try:
                await bot.send(event, f"你已被禁止使用 maimai 功能。\n原因：{reason}\n到期：{expire_text}")
            except Exception:
                pass
        raise IgnoredException("maimaidx user banned")

    # 图片扫码监听会匹配所有图片；普通图片既不计真实请求，也不触发附加费。
    # 识别到舞萌二维码后的业务调用仍会由账号流水记录。
    if bool(getattr(type(matcher), "_maimaidx_deferred_audit", False)):
        return

    busy_surcharge_exempt = _busy_surcharge_exempt(matcher)
    if (
        bool(getattr(maiconfig, "maimaidx_busy_surcharge_enabled", True))
        and not busy_surcharge_exempt
    ):
        window = max(
            1.0, float(getattr(maiconfig, "maimaidx_busy_window_seconds", 60.0))
        )
        free_requests = max(
            0, int(getattr(maiconfig, "maimaidx_busy_free_requests", 30))
        )
        surcharge = max(
            0, int(getattr(maiconfig, "maimaidx_busy_surcharge_break", 1))
        )
        request_count = request_meter.record(
            _event_request_key(bot, event), window_seconds=window
        )
        if request_count is not None and request_count > free_requests and surcharge:
            payer = int(billing_user_id(event))
            if not is_superuser_exempt(payer):
                meta = {
                    "window_seconds": window,
                    "free_requests": free_requests,
                    "request_count": request_count,
                }
                if not break_db.try_consume(
                    payer, surcharge, "busy_request_surcharge", meta=meta
                ):
                    balance = break_db.get_balance(payer)
                    await bot.send(
                        event,
                        "当前使用人数较多，本次请求需额外支付 "
                        f"{surcharge} BREAK。\n"
                        + format_break_insufficient_message(payer, surcharge, balance),
                    )
                    raise IgnoredException("maimaidx busy surcharge insufficient")
                state["__maimaidx_busy_charge"] = {
                    **meta, "charged": surcharge, "balance": break_db.get_balance(payer)
                }

    ref_id = admin_audit.start_trace(
        command=_command_name(matcher, event, state),
        user_id=uid,
        group_id=str(get_event_group_id(event) or ""),
        matcher=str(getattr(matcher, "module", "") or ""),
        input_summary=_event_summary(event),
    )
    state["__maimaidx_ref_id"] = ref_id
    state["__maimaidx_ref_token"] = admin_audit.set_current_ref(ref_id)
    busy_charge = state.get("__maimaidx_busy_charge")
    if busy_charge:
        admin_audit.add_step(
            "break.busy_surcharge", "success", busy_charge, ref_id=ref_id
        )


@run_postprocessor
async def _audit_postprocessor(
    matcher: Matcher,
    exception: Optional[Exception],
    event: Event,
    state: T_State,
):
    ref_id = state.get("__maimaidx_ref_id")
    if not ref_id:
        return
    trace = admin_audit.get_trace(str(ref_id))
    if trace and trace.get("status") != "running":
        token = state.get("__maimaidx_ref_token")
        if token is not None:
            try:
                admin_audit.reset_current_ref(token)
            except Exception:
                pass
        return
    normal_control = {
        "FinishedException", "PausedException", "RejectedException", "SkippedException",
        "StopPropagation",
    }
    if exception is None or type(exception).__name__ in normal_control:
        admin_audit.finish_trace(str(ref_id), "success")
    elif isinstance(exception, IgnoredException):
        admin_audit.finish_trace(str(ref_id), "ignored")
    else:
        admin_audit.finish_trace(str(ref_id), "error", error=exception)
    token = state.get("__maimaidx_ref_token")
    if token is not None:
        try:
            admin_audit.reset_current_ref(token)
        except Exception:
            pass


def _at_user(event: MessageEvent) -> Optional[str]:
    for segment in event.message:
        if isinstance(segment, MessageSegment) and segment.type == "at":
            value = str(segment.data.get("qq") or "")
            if value and value != "all":
                return value
    return None


@audit_query.handle()
async def _(args: Message = CommandArg()):
    ref_id = args.extract_plain_text().strip().upper()
    if not ref_id:
        await audit_query.finish("用法：查询REF REF-XXXXXXXXXXXXXXXX")
    if not ref_id.startswith("REF-"):
        ref_id = "REF-" + ref_id
    trace = admin_audit.get_trace(ref_id)
    if not trace:
        await audit_query.finish("没有找到该 REF_ID。")
    lines = [
        f"REF_ID：{trace['ref_id']}",
        f"状态：{trace['status']}  耗时：{trace.get('duration_ms') or 0}ms",
        f"用户：{trace.get('user_id') or '-'}  群：{trace.get('group_id') or '私聊'}",
        f"命令：{trace.get('command')}",
    ]
    if trace.get("error_type"):
        lines.append(f"错误：{trace['error_type']} · {trace.get('error_message') or ''}")
    lines.append("处理链路：")
    for index, step in enumerate(trace.get("steps") or [], 1):
        lines.append(
            f"{index}. {step['step_name']} [{step['status']}] {step.get('duration_ms') or 0}ms"
        )
        if step.get("detail"):
            lines.append("   " + str(step["detail"])[:500])
    if not trace.get("steps"):
        lines.append("（该请求没有外部调用步骤）")
    await audit_query.finish("\n".join(lines))


@ban_user_cmd.handle()
async def _(
    event: MessageEvent,
    args: Message = CommandArg(),
    at_user: Optional[str] = Depends(_at_user),
):
    parts = args.extract_plain_text().strip().split()
    target = at_user
    if target is None and parts:
        target = parts.pop(0)
    if not target:
        await ban_user_cmd.finish("用法：封禁用户 @用户 [小时，0=永久] [原因]")
    hours = 0.0
    if parts:
        try:
            hours = max(0.0, float(parts[0]))
            parts.pop(0)
        except ValueError:
            pass
    reason = " ".join(parts).strip() or "管理员封禁"
    expires_at = time.time() + hours * 3600 if hours > 0 else None
    admin_audit.ban_user(target, reason, str(event.get_user_id()), expires_at=expires_at)
    await ban_user_cmd.finish(
        f"已封禁 {target}\n期限：{'永久' if expires_at is None else f'{hours:g} 小时'}\n原因：{redact(reason)}"
    )


@unban_user_cmd.handle()
async def _(
    args: Message = CommandArg(), at_user: Optional[str] = Depends(_at_user)
):
    target = at_user or args.extract_plain_text().strip().split(maxsplit=1)[0]
    if not target:
        await unban_user_cmd.finish("用法：解封用户 @用户")
    changed = admin_audit.unban_user(target)
    await unban_user_cmd.finish("已解除封禁。" if changed else "该用户当前没有生效中的封禁。")


@ban_list_cmd.handle()
async def _():
    rows = admin_audit.list_bans(active_only=True)
    if not rows:
        await ban_list_cmd.finish("当前没有被封禁的用户。")
    lines = [f"当前封禁 {len(rows)} 人："]
    for row in rows[:50]:
        expires = row.get("expires_at")
        end = time.strftime("%m-%d %H:%M", time.localtime(expires)) if expires else "永久"
        lines.append(f"{row['user_id']} · {end} · {row['reason']}")
    await ban_list_cmd.finish("\n".join(lines))


@admin_web_cmd.handle()
async def _():
    if not bool(getattr(maiconfig, "maimaidx_admin_web_enabled", False)):
        await admin_web_cmd.finish("管理 WebUI 尚未启用，请设置 MAIMAIDX_ADMIN_WEB_ENABLED=true。")
    public_url = str(getattr(maiconfig, "maimaidx_admin_web_public_url", "") or "").rstrip("/")
    path = str(getattr(maiconfig, "maimaidx_admin_web_path", "/maimaidx/admin") or "/maimaidx/admin")
    if public_url:
        await admin_web_cmd.finish(public_url + path)
    port = int(getattr(maiconfig, "maimaidx_admin_web_port", 8099) or 0)
    if port > 0:
        host = str(getattr(maiconfig, "maimaidx_admin_web_host", "127.0.0.1") or "127.0.0.1")
        await admin_web_cmd.finish(f"http://{host}:{port}{path}")
    await admin_web_cmd.finish(f"管理面板路径：{path}（WebUI 使用 NoneBot 共享端口）")
