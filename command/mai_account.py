"""由 Koishi maibot 移植的账号绑定与查分器上传命令。

账号功能现在与 QueryBot 共用配置、进程和 SQLite 数据目录；BREAK 仍由
原有 ``mai_break`` 模块管理。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg

from ..config import log, maiconfig
from ..libraries.maimaidx_account_db import AccountBinding, account_db
from ..libraries.maimaidx_admin_audit import admin_audit, redact
from ..libraries.maimaidx_break import break_db
from ..libraries.maimaidx_lxns_client import (
    LxnsApiError,
    convert_sega_music_scores,
    user_upload_scores,
)
from ..libraries.maimaidx_lxns_db import lxns_db
from ..libraries.maimaidx_machine_session import (
    MachineBusyError,
    machine_session,
    wait_between_machine_steps,
)
from ..libraries.maimaidx_platform import billing_user_id, resolve_score_qqid
from ..libraries.maimaidx_playcount_db import pc_db
from ..libraries.maimaidx_qrcode_util import extract_sgwcmaid_qrcode
from ..libraries.maimaidx_pending_session import finish_pending, session_key, track_event
from ..libraries.maimaidx_reaction import react_processing
from ..libraries.maimaidx_sw_api import sw_api
from .mai_agreement import agreement_prompt, has_user_agreed


account_help = on_command("mai账号", aliases={"账号帮助", "mai账户"})
account_bind = on_command("mai绑定", aliases={"绑定舞萌", "舞萌绑定", "maibind"})
account_unbind = on_command("mai解绑", aliases={"解绑舞萌", "舞萌解绑"})
account_status = on_command("mai状态", aliases={"mymai", "舞萌状态"})
fish_bind = on_command(
    "mai绑定水鱼", aliases={"dfbind", "绑定水鱼token", "maibindfish"}
)
fish_unbind = on_command("mai解绑水鱼", aliases={"解绑水鱼token"})
lx_upload_bind = on_command(
    "mai绑定落雪",
    aliases={"mai绑定落雪token", "绑定落雪token", "lxuploadbind", "maibindlx"},
)
lx_upload_unbind = on_command(
    "mai解绑落雪", aliases={"mai解绑落雪token", "解绑落雪token", "lxuploadunbind"}
)
upload_fish = on_command("maiu", aliases={"mai上传B50", "上传水鱼", "导"})
upload_lx = on_command("maiul", aliases={"mai上传落雪b50", "上传落雪"})
upload_all = on_command("maiua", aliases={"同时上传b50", "全部上传b50"})
account_ping = on_command("maiping", aliases={"mai连接测试"})
account_ticket = on_command("mai发票", aliases={"发票", "fp", "拿票"})
account_ticket_status = on_command("mai查票", aliases={"查票"})
account_region = on_command("mai地图", aliases={"游玩地图"})
account_opt = on_command("mai查询opt", aliases={"查询opt"})
account_queue = on_command("maiqueue", aliases={"mai队列"})

_RECALL_FAILED_NOTICE = "⚠️ Bot 无法撤回该凭据消息，请立即手动撤回。\n"
_DIVING_FISH_PROBER_URL = "https://www.diving-fish.com/maimaidx/prober/"
_FISH_TOKEN_MIN_LENGTH = 127
_FISH_TOKEN_MAX_LENGTH = 132
_ACCOUNT_SETUP_GUIDE = (
    "尚未建立账号记录，请按以下步骤完成：\n"
    "1. 发送「mai绑定」，再提交最新的 SGWCMAID 字符串；\n"
    "2. 按需发送「mai绑定水鱼 <Token>」或「mai绑定落雪 <导入Token>」；\n"
    "3. 使用 maiu / maiul / maiua 上传水鱼 / 落雪 / 两边。"
)


def _user_key(event: MessageEvent) -> str:
    return str(billing_user_id(event))


def _arg_text(args: Message) -> str:
    return args.extract_plain_text().strip()


def _mask(value: str, head: int = 5, tail: int = 4) -> str:
    if not value:
        return "未绑定"
    if len(value) <= head + tail:
        return "*" * len(value)
    return value[:head] + "…" + value[-tail:]


def _nested_preview(payload: dict) -> dict:
    for key in ("userData", "userPreview", "userPreviewData"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _merged_preview(payload: dict) -> dict:
    """保留 userData 内字段，同时保留 maibot 兼容的顶层状态字段。"""
    nested = _nested_preview(payload)
    if nested is payload:
        return dict(payload)
    merged = dict(payload)
    merged.update(nested)
    # 新版 sw-api 会把 banState / returnCode 放在最外层。
    for key in ("BanState", "banState", "ReturnCode", "returnCode"):
        if payload.get(key) is not None:
            merged[key] = payload[key]
    return merged


def _pick(data: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _normalize_preview(payload: dict) -> tuple[str, str, int, dict]:
    data = _merged_preview(payload)
    uid = _pick(payload, "userId", "UserID", "userID")
    if uid is None:
        uid = _pick(data, "userId", "UserID", "userID")
    if uid in (None, "", -1, "-1"):
        raise RuntimeError("二维码未能读取到有效舞萌账号")
    name = str(_pick(data, "userName", "UserName", default="") or "")
    rating_raw = _pick(data, "playerRating", "PlayerRating", "rating", "Rating", default=0)
    try:
        rating = int(float(rating_raw or 0))
    except (TypeError, ValueError):
        rating = 0
    return str(uid), name, rating, data


def _normalize_charge_payload(payload: dict) -> tuple[bool, list[dict], list[dict]]:
    """兼容 maibot 的新版 user/charge 顶层与 userCharge 包装格式。"""
    nested = payload.get("userCharge")
    data = nested if isinstance(nested, dict) else payload
    rows = _pick(data, "userChargeList", "UserChargeList")
    if rows is None:
        rows = _pick(payload, "userChargeList", "UserChargeList")
    free_rows = _pick(data, "userFreeChargeList", "UserFreeChargeList")
    if free_rows is None:
        free_rows = _pick(payload, "userFreeChargeList", "UserFreeChargeList")

    return_code = _pick(payload, "returnCode", "ReturnCode")
    if return_code is None:
        return_code = _pick(data, "returnCode", "ReturnCode")
    charge_status = _pick(data, "chargeStatus", "ChargeStatus")
    if charge_status is None:
        charge_status = _pick(payload, "chargeStatus", "ChargeStatus")
    user_id = _pick(payload, "userId", "UserID")
    has_new_response = user_id is not None and any(
        key in payload for key in ("userChargeList", "UserChargeList", "length", "Length")
    )
    success = charge_status in (True, 1, "1") or return_code in (1, "1") or has_new_response
    return (
        success,
        rows if isinstance(rows, list) else [],
        free_rows if isinstance(free_rows, list) else [],
    )


def _binding_or_error(event: MessageEvent) -> tuple[str, Optional[AccountBinding], Optional[str]]:
    key = _user_key(event)
    binding = account_db.get(key)
    if not binding or not binding.qrcode:
        return key, None, "尚未绑定舞萌账号，请先使用：mai绑定 SGWCMAID..."
    ttl = max(0, int(getattr(maiconfig, "awmc_qrcode_cache_seconds", 0) or 0))
    if ttl and time.time() - binding.qrcode_updated_at > ttl:
        return key, None, "已保存的二维码凭据过期，请重新使用 mai绑定 提交最新二维码。"
    return key, binding, None


def _sgid_cache_seconds() -> int:
    return max(0, int(getattr(maiconfig, "awmc_sgid_cache_seconds", 600) or 0))


def _sgid_cache_state(binding: AccountBinding) -> tuple[bool, str]:
    if not binding.qrcode:
        return False, "未保存"
    if binding.last_qrcode_success == 0:
        return False, "上次使用失败，需刷新"
    ttl = _sgid_cache_seconds()
    if ttl <= 0:
        return False, "已关闭，每次重新获取"
    age = max(0, time.time() - float(binding.qrcode_updated_at or 0))
    if not binding.qrcode_updated_at or age >= ttl:
        return False, "已过期，需刷新"
    remaining = max(1, int((ttl - age + 59) // 60))
    return True, f"有效（约剩 {remaining} 分钟）"


def _status_qrcode_prompt(reason: str) -> str:
    return (
        f"🔄 {reason}\n"
        "请打开微信中的「舞萌DX | 中二节奏」玩家二维码，\n"
        "长按二维码并选择「识别图中二维码」，复制识别出的字符或网页地址发送给 Bot。\n"
        "支持 SGWCMAID、wq.wahlap.net 的 img/req 链接；发送「取消」可查看缓存资料。"
    )


async def _read_verified_preview(
    binding: AccountBinding,
    qrcode: str,
    *,
    save_qrcode: bool,
) -> tuple[AccountBinding, dict]:
    payload = await sw_api.get_user_preview(qrcode)
    mai_uid, name, rating, data = _normalize_preview(payload)
    if binding.mai_uid and str(binding.mai_uid) != str(mai_uid):
        raise RuntimeError("二维码与当前绑定的舞萌账号不一致")
    if save_qrcode:
        account_db.save_verified_qrcode(
            binding.user_key,
            qrcode,
            mai_uid=mai_uid,
            user_name=name,
            rating=rating,
            preview=data,
        )
    else:
        account_db.refresh_preview(
            binding.user_key,
            mai_uid=mai_uid,
            user_name=name,
            rating=rating,
            preview=data,
        )
        account_db.mark_qrcode_result(binding.user_key, True)
    refreshed = account_db.get(binding.user_key)
    if refreshed is None:
        raise RuntimeError("账号状态保存失败")
    return refreshed, data


async def _bind_verified_account(
    user_key: str, qrcode: str
) -> tuple[AccountBinding, list[str]]:
    """验真并绑定/认领账号，供显式绑定和直发二维码共用。"""
    preview = await sw_api.get_user_preview(qrcode)
    mai_uid, name, rating, preview_data = _normalize_preview(preview)
    binding, claimed_keys = account_db.bind_verified(
        user_key,
        qrcode,
        mai_uid=mai_uid,
        user_name=name,
        rating=rating,
        preview=preview_data,
    )
    # 认领后令旧账号保存的 PC 登录凭据失效，避免继续访问同一舞萌账号。
    for old_key in claimed_keys:
        try:
            pc_db.delete_credential(int(old_key))
        except (TypeError, ValueError):
            continue
    return binding, claimed_keys


def _preview_line(data: dict, label: str, *keys: str) -> Optional[str]:
    value = _pick(data, *keys)
    if value in (None, ""):
        return None
    return f"{label}：{value}"


async def _render_account_status(
    event: MessageEvent,
    binding: AccountBinding,
    preview: Optional[dict] = None,
) -> str:
    data = preview or binding.preview
    _, cache_label = _sgid_cache_state(binding)
    lines = [
        "✅ 已绑定舞萌账号",
        f"绑定时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(binding.bound_at))}",
        f"二维码缓存：{cache_label}",
    ]
    try:
        lines.append(f"BREAK 余额：{break_db.get_balance(int(binding.user_key))}")
    except ValueError:
        pass
    lines.extend(["", "📊 账号信息" + ("（缓存）" if not preview else "")])
    name = _pick(data, "userName", "UserName", default=binding.user_name or "未知") or "未知"
    rating = _pick(
        data, "playerRating", "PlayerRating", "rating", "Rating",
        default=binding.rating or "未知",
    )
    old_rating = _pick(data, "PlayerOldRating", "playerOldRating")
    new_rating = _pick(data, "PlayerNewRating", "playerNewRating")
    if old_rating is not None and new_rating is not None:
        rating = f"{rating}（{old_rating}+{new_rating}）"
    lines.extend([f"用户名：{name}", f"Rating：{rating}"])

    class_rank = _pick(data, "ClassRank", "classRank")
    course_rank = _pick(data, "CourseRank", "courseRank")
    if class_rank is not None and course_rank is not None:
        lines.append(f"友人对战等级：{class_rank}[{course_rank}]")
    fields = (
        ("总游玩次数", ("PlayCount", "playCount")),
        ("当前版本游玩次数", ("CurrentPlayCount", "currentPlayCount")),
        ("机台版本", ("RomVersion", "romVersion")),
        ("数据版本", ("DataVersion", "dataVersion")),
        ("上次登录", ("LastLoginDate", "lastLoginDate")),
        ("上次游玩", ("LastPlayDate", "lastPlayDate")),
        ("上次拼机", ("LastPairLoginDate", "lastPairLoginDate")),
        ("上次游玩区域", ("LastRegionName", "lastRegionName")),
        ("总觉醒次数", ("TotalAwake", "totalAwake")),
    )
    for label, keys in fields:
        line = _preview_line(data, label, *keys)
        if line:
            lines.append(line)
    ban_state = _pick(data, "BanState", "banState")
    ban_labels = {0: "正常", 1: "警告", 2: "封禁", "0": "正常", "1": "警告", "2": "封禁"}
    lines.append(f"封禁状态：{ban_labels.get(ban_state, '未知' if ban_state is None else ban_state)}")

    lines.append("")
    lines.append(f"🐟 水鱼上传：{'已绑定' if binding.fish_token else '未绑定'}")
    if _has_lxns_oauth(event):
        lines.append("❄️ 落雪上传：OAuth 已绑定")
    elif binding.lxns_token:
        lines.append("❄️ 落雪上传：兼容 Token 已绑定")
    else:
        lines.append("❄️ 落雪上传：未绑定（发送 lxbind）")
    if binding.last_upload_at:
        lines.append(
            "最近上传："
            + time.strftime("%Y-%m-%d %H:%M", time.localtime(binding.last_upload_at))
        )

    if binding.qrcode and sw_api.api_mode == "team":
        try:
            async with machine_session():
                charge = await sw_api.get_user_charge(binding.qrcode)
            charge_ok, rows, free_rows = _normalize_charge_payload(charge)
            if not charge_ok:
                return_code = _pick(charge, "returnCode", "ReturnCode")
                suffix = f"（returnCode={return_code}）" if return_code is not None else ""
                lines.append(f"🎫 票券情况：获取失败{suffix}")
                return "\n".join(lines)
            now = time.time()
            valid = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                valid_date = row.get("validDate") or row.get("ValidDate")
                try:
                    normalized = str(valid_date).replace(" ", "T")[:19]
                    valid_ts = time.mktime(
                        time.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
                    )
                except (TypeError, ValueError):
                    valid_ts = now + 1
                if valid_ts > now and int(row.get("stock") or row.get("Stock") or 0) > 0:
                    valid.append(row)
            total = sum(int(row.get("stock") or row.get("Stock") or 0) for row in valid)
            free_total = sum(
                int(row.get("stock") or row.get("Stock") or 0)
                for row in free_rows
                if isinstance(row, dict)
            )
            if valid or free_total:
                label = f"🎫 有效票券：{total} 张（{len(valid)} 种）"
                if free_total:
                    label += f"；免费票券 {free_total} 张"
                lines.append(label)
            else:
                lines.append("🎫 票券情况：暂无有效票券")
        except Exception as exc:
            log.warning(f"[AccountStatus] 获取票券失败：{type(exc).__name__}: {exc}")
            lines.append("🎫 票券情况：暂时无法获取")
    return "\n".join(lines)


def _result_text(result: dict) -> str:
    if not result:
        return "操作已完成"
    if result.get("error"):
        return str(result["error"])
    message = result.get("msg") or result.get("message")
    if isinstance(message, dict):
        message = message.get("message") or json.dumps(message, ensure_ascii=False)
    if message:
        return str(message)
    if result.get("done") is True:
        return "异步任务已完成"
    task_id = result.get("task_id")
    if task_id:
        return f"任务已提交，任务 ID：{task_id}"
    count = result.get("count")
    if count is not None:
        return f"已处理 {count} 条成绩"
    return "操作已完成"


def _oauth_qqid(event: MessageEvent) -> Optional[int]:
    try:
        return resolve_score_qqid(event)
    except Exception:
        return None


def _has_lxns_oauth(event: MessageEvent) -> bool:
    qqid = _oauth_qqid(event)
    if qqid is None:
        return False
    row = lxns_db.get_user(qqid)
    return bool(row and row.get("access_token"))


def _lxns_oauth_missing_write_scope(event: MessageEvent) -> bool:
    qqid = _oauth_qqid(event)
    if qqid is None:
        return False
    row = lxns_db.get_user(qqid)
    if not row or not row.get("access_token"):
        return False
    scope = str(row.get("scope") or "").replace(",", " ").split()
    return bool(scope and "write_player" not in scope)


async def _lxns_oauth_access_token(
    event: MessageEvent, *, force_refresh: bool = False
) -> Optional[str]:
    """复用现有 lxbind 授权；旧授权缺少写权限时要求重新授权。"""
    qqid = _oauth_qqid(event)
    if qqid is None:
        return None
    row = lxns_db.get_user(qqid)
    if not row:
        return None
    scope = str(row.get("scope") or "").replace(",", " ").split()
    if scope and "write_player" not in scope:
        return None
    # 延迟导入，避免命令模块初始化时形成循环依赖。
    from .mai_lxns import _get_valid_access_token

    return await _get_valid_access_token(qqid, force_refresh=force_refresh)


def _oauth_token_rejected(exc: Exception) -> bool:
    if isinstance(exc, LxnsApiError):
        return exc.status_code in {401, 403}
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}


def _lxns_upload_failure_text(exc: Exception, *, stage: str) -> str:
    detail = redact(str(exc)).strip() or type(exc).__name__
    if isinstance(exc, LxnsApiError) and exc.status_code == 403:
        return (
            "落雪拒绝写入（HTTP 403）。请确认 OAuth 应用已启用 write_player，"
            "并在落雪账号隐私设置中允许第三方写入数据"
        )
    if isinstance(exc, LxnsApiError) and exc.status_code == 401:
        return "落雪 OAuth 凭据已失效，自动刷新后仍未通过验证"
    return f"{stage}失败：{detail[:200]}"


def _ensure_business_success(result: dict) -> None:
    """防止外部服务以 HTTP 200 返回业务失败时被误扣 BREAK。"""
    if not isinstance(result, dict):
        return

    def all_null(value: Any) -> bool:
        if isinstance(value, dict):
            return not value or all(all_null(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return not value or all(all_null(item) for item in value)
        return value is None

    if all_null(result):
        raise RuntimeError("外部服务返回全部 null，二维码可能已失效")
    if (
        result.get("success") is False
        or result.get("ok") is False
        or result.get("UploadStatus") is False
        or result.get("ChargeStatus") is False
    ):
        raise RuntimeError(str(result.get("error") or result.get("msg") or "外部操作失败"))
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    code = result.get("code")
    if code not in (None, 0, "0"):
        raise RuntimeError(str(result.get("msg") or f"外部操作失败（code={code}）"))


async def _await_upload_success(result: dict, *, lxns: bool) -> dict:
    """公共网关为异步任务；只有任务真正完成后才允许 BREAK 结算。"""
    _ensure_business_success(result)
    task_id = str(result.get("task_id") or "").strip()
    if not task_id or result.get("sync") is True:
        return result
    interval = max(
        1.0, float(getattr(maiconfig, "awmc_upload_poll_interval_seconds", 3.0))
    )
    timeout = max(
        interval, float(getattr(maiconfig, "awmc_upload_poll_timeout_seconds", 600.0))
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        detail = await sw_api.get_upload_task(task_id, lxns=lxns)
        error = detail.get("error")
        if error not in (None, ""):
            raise RuntimeError(str(error))
        if detail.get("done") is True:
            return {**result, **detail, "task_id": task_id}
    raise RuntimeError(f"上传任务 {task_id} 超时，未扣 BREAK")


def _log(user_key: str, operation: str, status: str, detail: str = "") -> str:
    safe_detail = str(redact(detail))[:1000]
    ref_id = admin_audit.current_ref_id()
    manual = ref_id is None
    if ref_id is None:
        ref_id = admin_audit.start_trace(
            command=operation, user_id=user_key, input_summary={"source": "account"}
        )
    admin_audit.add_step(
        f"account.{operation}", status, {"detail": safe_detail}, ref_id=ref_id
    )
    account_db.append_log(ref_id, user_key, operation, status, safe_detail)
    if manual:
        admin_audit.finish_trace(ref_id, "success" if status == "success" else "error")
    return ref_id


def _service_cost(service: str, *, multiple: int = 1) -> int:
    if service == "ticket":
        unit = int(break_db.get_config("ticket_cost_per_multiplier", "2"))
        return max(0, unit) * max(1, multiple)
    defaults = {"upload_fish": "2", "upload_lx": "2", "upload_all": "3"}
    return max(0, int(break_db.get_config(f"{service}_cost", defaults[service])))


def _allowed_ticket_multipliers() -> tuple[int, ...]:
    raw = getattr(maiconfig, "awmc_ticket_allowed_multipliers", "2,3,5")
    if isinstance(raw, (list, tuple, set)):
        parts = raw
    else:
        parts = str(raw or "").replace("，", ",").split(",")
    values: set[int] = set()
    for part in parts:
        try:
            value = int(str(part).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            values.add(value)
    return tuple(sorted(values)) or (2, 3, 5)


def _charge_text(result) -> str:
    labels = {"upload": "成绩上传", "ticket": "发票"}
    label = labels.get(result.service, result.service)
    if result.free:
        return f"💳 {label}今日首次成功，免费 · 余额 {result.balance} BREAK"
    return f"💳 {label}消耗 {result.charged} BREAK · 余额 {result.balance} BREAK"


async def _require_agreement(matcher, event: MessageEvent) -> None:
    if not bool(getattr(maiconfig, "maimaidx_user_agreement_required", True)):
        return
    if not has_user_agreed(event):
        await matcher.finish(agreement_prompt())


@account_help.handle()
async def _():
    fish_cost = break_db.get_config("upload_fish_cost", "2")
    lx_cost = break_db.get_config("upload_lx_cost", "2")
    all_cost = break_db.get_config("upload_all_cost", "3")
    ticket_unit = break_db.get_config("ticket_cost_per_multiplier", "2")
    ticket_multipliers = "/".join(map(str, _allowed_ticket_multipliers()))
    await account_help.finish(
        "AWMC 账号功能（已合并到 QueryBot）\n"
        "mai绑定 / maibind：绑定或认领舞萌账号\n"
        "mai状态 / mymai：查看详细状态，缓存失效时引导刷新二维码\n"
        "mai绑定水鱼 [Token] / maibindfish：无参数时交互引导，最多重试 3 次\n"
        "lxbind：落雪 OAuth（推荐）；maibindlx <导入Token> 为兼容方式\n"
        "maiu / maiul / maiua：上传水鱼 / 落雪 / 同时上传\n"
        f"发票 / fp <{ticket_multipliers}> / mai查票 / mai地图 / maiping\n"
        f"当前上传价格：水鱼 {fish_cost} / 落雪 {lx_cost} / 同时 {all_cost} BREAK\n"
        f"发票价格：倍率 × {ticket_unit} BREAK（例：2倍=4，3倍=6）\n"
        "成绩上传与发票各自每日首次成功免费，失败不扣费。\n"
        "发送“用户协议”阅读和确认服务条款。"
    )


@account_bind.handle()
async def _(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    await _require_agreement(account_bind, event)
    raw = _arg_text(args)
    if raw:
        matcher.set_arg("qrcode", Message(raw))
    else:
        track_event(session_key("account_bind", event), event)
        await account_bind.send(
            "请发送最新的 SGWCMAID，或舞萌二维码图片/请求链接。\n"
            "Bot 会尝试撤回凭据消息；最多可重试 3 次。\n"
            "发送“取消”可结束绑定。"
        )


@account_bind.got("qrcode")
async def _(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    qrcode_message: Message = Arg("qrcode"),
):
    pending_key = session_key("account_bind", event)
    raw = qrcode_message.extract_plain_text().strip()
    if raw.lower() in {"取消", "cancel", "q", "退出"}:
        finish_pending(pending_key)
        await account_bind.finish("已取消舞萌账号绑定。")
    qrcode = extract_sgwcmaid_qrcode(raw)
    recall_notice = ""
    if qrcode:
        try:
            await bot.delete_msg(message_id=event.message_id)
        except Exception:
            recall_notice = _RECALL_FAILED_NOTICE

    async def retry(reason: str) -> None:
        attempt = int(matcher.state.get("account_bind_retry", 0)) + 1
        matcher.state["account_bind_retry"] = attempt
        if attempt >= 3:
            finish_pending(pending_key)
            await account_bind.finish(
                recall_notice
                + f"二维码验证已连续失败 3 次：{reason}\n"
                "绑定流程已结束，请重新获取二维码后再发送 mai绑定。"
            )
        track_event(pending_key, event)
        await account_bind.reject(
            recall_notice
            + f"二维码无效或已过期：{reason}\n"
            f"请重新获取并发送 SGWCMAID 或官方二维码链接（{attempt}/3）。\n"
            "发送“取消”可退出。"
        )

    if not qrcode:
        await retry("内容不是完整 SGWCMAID 或受支持的官方二维码链接")
    key = _user_key(event)
    claimed_keys: list[str] = []
    try:
        binding, claimed_keys = await _bind_verified_account(key, qrcode)
        name = binding.user_name
        rating = binding.rating
    except Exception as exc:
        ref = _log(key, "bind", "error", str(exc))
        await retry(f"{type(exc).__name__}（Ref_ID: {ref}）")

    # PC 凭据同步失败不回滚已经验真的绑定，避免用户重复提交敏感凭据。
    pc_status = "skipped"
    pc_note = ""
    try:
        from ..libraries.maimaidx_playcount_fetcher import playcount_fetcher

        if playcount_fetcher.sdgb_available and sw_api.api_mode == "team":
            await playcount_fetcher.login_by_sdgb(qrcode, int(key))
            pc_status = "success"
    except Exception as exc:
        pc_status = f"error:{type(exc).__name__}"
        pc_note = "\nPC 凭据同步暂未完成，可稍后发送「更新pc数」。"
    operation = "claim" if claimed_keys else "bind"
    ref = _log(
        key, operation, "success",
        f"account_verified,claimed_records={len(claimed_keys)},pc={pc_status}",
    )
    label = name or "已识别玩家"
    action = "绑定认领成功" if claimed_keys else "绑定成功"
    claim_note = (
        "\n旧记录已安全转移，原记录在本 Bot 保存的舞萌/PC 凭据已失效。"
        if claimed_keys else ""
    )
    finish_pending(pending_key)
    await account_bind.finish(
        recall_notice
        + f"{action}：{label}\nRating：{rating}{claim_note}{pc_note}\nRef_ID: {ref}"
    )


@account_unbind.handle()
async def _(event: MessageEvent):
    key = _user_key(event)
    if not account_db.unbind_account(key):
        await account_unbind.finish("当前没有已绑定的舞萌账号。")
    try:
        pc_db.delete_credential(int(key))
    except (TypeError, ValueError):
        pass
    ref = _log(key, "unbind", "success")
    await account_unbind.finish(f"已解绑舞萌账号；水鱼/落雪 Token 已保留。\nRef_ID: {ref}")


@account_status.handle()
async def _(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    key = _user_key(event)
    binding = account_db.get(key)
    if not binding:
        await account_status.finish(_ACCOUNT_SETUP_GUIDE, reply_message=True)
    raw = _arg_text(args)
    if raw:
        matcher.set_arg("status_qrcode", Message(raw))
        return

    cache_valid, cache_label = _sgid_cache_state(binding)
    if cache_valid:
        try:
            binding, preview = await _read_verified_preview(
                binding, binding.qrcode, save_qrcode=False
            )
            text = await _render_account_status(event, binding, preview)
            ref = _log(key, "status", "success", "preview_source=sgid_cache")
        except Exception as exc:
            account_db.mark_qrcode_result(key, False)
            matcher.state["status_cache_error"] = type(exc).__name__
            cache_label = "缓存验证失败，需刷新"
        else:
            await account_status.finish(text + f"\nRef_ID: {ref}", reply_message=True)
    track_event(session_key("account_status", event), event)
    await account_status.send(_status_qrcode_prompt(cache_label), reply_message=True)


@account_status.got("status_qrcode")
async def _(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    qrcode_message: Message = Arg("status_qrcode"),
):
    pending_key = session_key("account_status", event)
    key = _user_key(event)
    binding = account_db.get(key)
    if not binding:
        finish_pending(pending_key)
        await account_status.finish(_ACCOUNT_SETUP_GUIDE, reply_message=True)
    raw = qrcode_message.extract_plain_text().strip()
    if raw.lower() in {"取消", "cancel", "q", "退出"}:
        text = await _render_account_status(event, binding)
        ref = _log(key, "status", "success", "preview_source=stored,cancelled_refresh")
        finish_pending(pending_key)
        await account_status.finish(text + f"\nRef_ID: {ref}", reply_message=True)

    recall_notice = ""
    try:
        await bot.delete_msg(message_id=event.message_id)
    except Exception:
        recall_notice = _RECALL_FAILED_NOTICE
    qrcode = extract_sgwcmaid_qrcode(raw)

    async def retry(reason: str) -> None:
        attempt = int(matcher.state.get("status_qrcode_retry", 0)) + 1
        matcher.state["status_qrcode_retry"] = attempt
        if attempt >= 3:
            account_db.mark_qrcode_result(key, False)
            text = await _render_account_status(event, account_db.get(key) or binding)
            ref = _log(key, "status", "error", "refresh_failed=3_attempts")
            finish_pending(pending_key)
            await account_status.finish(
                recall_notice
                + f"二维码刷新已连续失败 3 次：{redact(reason)}\n本次展示缓存资料。\n"
                + text
                + f"\nRef_ID: {ref}",
                reply_message=True,
            )
        track_event(pending_key, event)
        await account_status.reject(
            recall_notice
            + f"二维码无效或已过期：{redact(reason)}\n"
            + f"请重新识别并发送（{attempt}/3），或发送「取消」查看缓存资料。",
            reply_message=True,
        )

    if not qrcode:
        await retry("未识别到 SGWCMAID 或受支持的官方二维码链接")
    try:
        binding, preview = await _read_verified_preview(
            binding, qrcode, save_qrcode=True
        )
    except Exception as exc:
        await retry(type(exc).__name__)
    text = await _render_account_status(event, binding, preview)
    ref = _log(key, "status", "success", "preview_source=user_refresh")
    finish_pending(pending_key)
    await account_status.finish(
        recall_notice + text + f"\nRef_ID: {ref}", reply_message=True
    )


def _save_upload_token(event: MessageEvent, token: str, kind: str) -> str:
    key = _user_key(event)
    account_db.set_token(key, kind, token)
    try:
        if kind == "fish":
            pc_db.save_prober_token(int(key), fish_token=token)
        else:
            pc_db.save_prober_token(int(key), lxns_code=token)
    except (TypeError, ValueError):
        pass
    return _log(key, f"bind_{kind}", "success")


@fish_bind.handle()
async def _(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    await _require_agreement(fish_bind, event)
    token = _arg_text(args)
    if token:
        matcher.set_arg("fish_token", Message(token))
        return
    track_event(session_key("fish_bind", event), event)
    await fish_bind.send(
        "🐟 水鱼 Import-Token 获取方式：\n"
        f"1. 打开水鱼查分器：{_DIVING_FISH_PROBER_URL}\n"
        "2. 登录后进入「编辑个人资料」；\n"
        "3. 找到 Import-Token，生成后复制完整 Token 发给我。\n\n"
        "我会等待你的输入；格式不正确时可以重试，本轮最多 3 次。\n"
        "发送「取消」可结束绑定。",
        reply_message=True,
    )


@fish_bind.got("fish_token")
async def _(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    token_message: Message = Arg("fish_token"),
):
    pending_key = session_key("fish_bind", event)
    token = token_message.extract_plain_text().strip()
    if token.lower() in {"取消", "cancel", "q", "退出"}:
        finish_pending(pending_key)
        await fish_bind.finish("已取消水鱼 Token 绑定。", reply_message=True)

    recall_notice = ""
    if token:
        try:
            await bot.delete_msg(message_id=event.message_id)
        except Exception:
            recall_notice = _RECALL_FAILED_NOTICE

    if not (_FISH_TOKEN_MIN_LENGTH <= len(token) <= _FISH_TOKEN_MAX_LENGTH):
        attempt = int(matcher.state.get("fish_token_retry", 0)) + 1
        matcher.state["fish_token_retry"] = attempt
        reason = (
            f"Token 长度为 {len(token)}，应为 "
            f"{_FISH_TOKEN_MIN_LENGTH}–{_FISH_TOKEN_MAX_LENGTH} 个字符。"
        )
        if attempt >= 3:
            finish_pending(pending_key)
            await fish_bind.finish(
                recall_notice
                + f"❌ {reason}\n已连续输入失败 3 次，本轮绑定已结束。\n"
                "请重新生成完整 Import-Token 后，再发送「maibindfish」。",
                reply_message=True,
            )
        track_event(pending_key, event)
        await fish_bind.reject(
            recall_notice
            + f"❌ {reason}\n"
            f"请重新复制完整 Import-Token 发给我（{attempt}/3）。\n"
            "发送「取消」可退出。",
            reply_message=True,
        )

    ref = _save_upload_token(event, token, "fish")
    finish_pending(pending_key)
    await fish_bind.finish(
        f"✅ 水鱼 Token 已绑定。\nToken：{_mask(token, 8, 4)}\nRef_ID: {ref}",
        reply_message=True,
    )


@lx_upload_bind.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await _require_agreement(lx_upload_bind, event)
    token = _arg_text(args)
    if not token:
        await lx_upload_bind.finish(
            "推荐发送「lxbind」完成落雪 OAuth，无需提供导入 Token。\n"
            "兼容用法：mai绑定落雪 <导入Token>"
        )
    if len(token) < 8:
        await lx_upload_bind.finish("落雪导入 Token 格式过短，请检查后重试。")
    ref = _save_upload_token(event, token, "lxns")
    await lx_upload_bind.finish(f"落雪 Token 已绑定。\nRef_ID: {ref}")


async def _clear_token(matcher, event: MessageEvent, kind: str):
    key = _user_key(event)
    account_db.set_token(key, kind, "")
    try:
        if kind == "fish":
            pc_db.save_prober_token(int(key), fish_token="")
        else:
            pc_db.save_prober_token(int(key), lxns_code="")
    except (TypeError, ValueError):
        pass
    await matcher.finish(f"已解绑{'水鱼' if kind == 'fish' else '落雪'} Token。")


@fish_unbind.handle()
async def _(event: MessageEvent):
    await _clear_token(fish_unbind, event, "fish")


@lx_upload_unbind.handle()
async def _(event: MessageEvent):
    await _clear_token(lx_upload_unbind, event, "lxns")


async def _upload(
    event: MessageEvent,
    *,
    fish: bool,
    lxns: bool,
    qrcode_arg: str = "",
    _machine_locked: bool = False,
    _qrcode_verified: bool = False,
) -> str:
    if not _machine_locked:
        try:
            async with machine_session():
                return await _upload(
                    event,
                    fish=fish,
                    lxns=lxns,
                    qrcode_arg=qrcode_arg,
                    _machine_locked=True,
                    _qrcode_verified=_qrcode_verified,
                )
        except MachineBusyError as exc:
            return f"上传失败：{exc}"
    if bool(getattr(maiconfig, "maimaidx_user_agreement_required", True)):
        key = _user_key(event)
        if not has_user_agreed(event):
            return agreement_prompt()
    key = _user_key(event)
    binding = account_db.get(key)
    direct_qrcode = extract_sgwcmaid_qrcode(qrcode_arg)
    if not binding:
        return _ACCOUNT_SETUP_GUIDE
    qrcode = direct_qrcode or binding.qrcode
    if not qrcode:
        return "尚未绑定舞萌账号，请使用 mai绑定，或在上传命令后附带 SGWCMAID。"
    oauth_token = await _lxns_oauth_access_token(event) if lxns else None
    has_lxns_upload = bool(oauth_token or binding.lxns_token)
    if lxns and not oauth_token and _lxns_oauth_missing_write_scope(event):
        return (
            "落雪 OAuth 授权缺少 write_player 写入权限。"
            "请让管理员在落雪 OAuth 应用中启用该权限，然后重新发送 lxbind 授权。"
        )
    if fish and lxns and not binding.fish_token and not has_lxns_upload:
        return (
            "水鱼和落雪上传均未绑定。\n"
            "请使用「mai绑定水鱼 <Token>」，并发送「lxbind」完成落雪 OAuth。"
        )
    if fish and not binding.fish_token:
        return "未绑定水鱼 Token，请使用「mai绑定水鱼 <Token>」。"
    if lxns and not has_lxns_upload:
        return "未绑定落雪上传，请先发送「lxbind」完成 OAuth。"

    try:
        if _qrcode_verified:
            qrcode = direct_qrcode or binding.qrcode
        elif direct_qrcode:
            binding, _ = await _read_verified_preview(
                binding, direct_qrcode, save_qrcode=True
            )
            qrcode = direct_qrcode
        else:
            cache_valid, cache_label = _sgid_cache_state(binding)
            if not cache_valid:
                return f"上传失败：二维码缓存{cache_label}"
            binding, _ = await _read_verified_preview(
                binding, binding.qrcode, save_qrcode=False
            )
            qrcode = binding.qrcode
    except Exception as exc:
        account_db.mark_qrcode_result(key, False)
        ref = _log(key, "upload", "error", f"sgid_preview={type(exc).__name__}")
        return f"上传失败：二维码验证失败（{type(exc).__name__}）\nRef_ID: {ref}"

    operation = "upload_all" if fish and lxns else "upload_fish" if fish else "upload_lx"
    # 三种上传共用一个每日免费额度，避免通过轮流调用水鱼/落雪/同时上传获得三次免费。
    billing_service = "upload"
    cost = _service_cost(operation)
    results: list[str] = []
    try:
        break_db.ensure_service_affordable(int(key), billing_service, cost)
        # 显式 maiu/maiul 会先验二维码；给下一次机台登录留出间隔。
        if not _qrcode_verified:
            await wait_between_machine_steps()
        if fish:
            result = await sw_api.update_fish(qrcode, binding.fish_token)
            result = await _await_upload_success(result, lxns=False)
            results.append("水鱼：" + _result_text(result))
        if lxns:
            if fish:
                await wait_between_machine_steps()
            if oauth_token:
                lxns_stage = "读取玩家 PC 数据"
                try:
                    raw_scores = await sw_api.get_user_music(qrcode)
                    scores = convert_sega_music_scores(raw_scores)
                    if not scores:
                        raise RuntimeError("机台返回的成绩无法转换为落雪 Score")
                    lxns_stage = "向落雪写入成绩"
                    try:
                        result = await user_upload_scores(oauth_token, scores)
                    except Exception as exc:
                        if not _oauth_token_rejected(exc):
                            raise
                        refreshed_token = await _lxns_oauth_access_token(
                            event, force_refresh=True
                        )
                        if not refreshed_token:
                            raise RuntimeError("落雪 OAuth Token 刷新失败") from exc
                        result = await user_upload_scores(refreshed_token, scores)
                    results.append("落雪（OAuth）：" + _result_text(result))
                except Exception as exc:
                    if not binding.lxns_token:
                        raise RuntimeError(
                            _lxns_upload_failure_text(exc, stage=lxns_stage)
                            + "。请修正后重新发送 lxbind 授权并重试"
                        ) from exc
                    await wait_between_machine_steps()
                    result = await sw_api.update_lx(qrcode, binding.lxns_token)
                    result = await _await_upload_success(result, lxns=True)
                    results.append("落雪（兼容 Token）：" + _result_text(result))
            else:
                result = await sw_api.update_lx(qrcode, binding.lxns_token)
                result = await _await_upload_success(result, lxns=True)
                results.append("落雪（兼容 Token）：" + _result_text(result))
        account_db.mark_uploaded(key)
        from ..libraries.maimaidx_player_cache import invalidate_player_cache

        try:
            invalidate_player_cache(int(key))
        except ValueError:
            pass
        charge = break_db.settle_service_success(
            int(key), billing_service, cost,
            meta={"operation": operation, "fish": fish, "lxns": lxns},
        )
        ref = _log(key, operation, "success", f"charged={charge.charged},free={charge.free}")
        return "上传完成\n" + "\n".join(results) + f"\n{_charge_text(charge)}\nRef_ID: {ref}"
    except Exception as exc:
        failure_message = f"上传失败：{exc}"
        if _upload_retryable(failure_message):
            account_db.mark_qrcode_result(key, False)
        ref = _log(key, "upload", "error", str(exc))
        return failure_message + f"\nRef_ID: {ref}"


def _upload_mode(matcher: Matcher) -> tuple[bool, bool]:
    if type(matcher) is upload_fish:
        return True, False
    if type(matcher) is upload_lx:
        return False, True
    if type(matcher) is upload_all:
        return True, True
    raise ValueError("未知上传指令")


def _upload_retryable(message: str) -> bool:
    if not message.startswith("上传失败："):
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in (
        "http 500", "500 internal", "null", "none", "qrcode", "sgwcmaid",
        "二维码", "过期", "失效", "无效", "登录失败",
    ))


def _upload_retry_prompt(message: str, attempt: int) -> str:
    reason = message.split("\nRef_ID:", 1)[0].removeprefix("上传失败：")
    retry_label = f"已尝试 {attempt}/3" if attempt else "尚未重试，最多可尝试 3 次"
    return (
        f"上传未完成：{redact(reason)}\n"
        f"请重新获取并发送最新 SGWCMAID 或官方二维码链接（{retry_label}）。\n"
        "Bot 会尝试撤回凭据消息；发送“取消”可退出。"
    )


@upload_fish.handle()
@upload_lx.handle()
@upload_all.handle()
async def _(
    matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    fish, lxns = _upload_mode(matcher)
    raw = _arg_text(args)
    # 先贴表情再撤回凭据，让用户立刻看到「已开始处理」。
    await react_processing(bot, event)
    recall_notice = ""
    if extract_sgwcmaid_qrcode(raw):
        try:
            await bot.delete_msg(message_id=event.message_id)
        except Exception:
            recall_notice = _RECALL_FAILED_NOTICE
    if raw and not extract_sgwcmaid_qrcode(raw):
        result = "上传失败：二维码格式无效"
    else:
        result = await _upload(event, fish=fish, lxns=lxns, qrcode_arg=raw)
    if not _upload_retryable(result):
        await matcher.finish(recall_notice + result, reply_message=True)
    attempt = 1 if raw else 0
    matcher.state["upload_qrcode_retry"] = attempt
    track_event(session_key("upload_qrcode", event), event)
    await matcher.send(
        recall_notice + _upload_retry_prompt(result, attempt), reply_message=True
    )


@upload_fish.got("upload_qrcode")
@upload_lx.got("upload_qrcode")
@upload_all.got("upload_qrcode")
async def _(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    qrcode_message: Message = Arg("upload_qrcode"),
):
    pending_key = session_key("upload_qrcode", event)
    raw = qrcode_message.extract_plain_text().strip()
    if raw.lower() in {"取消", "cancel", "q", "退出"}:
        finish_pending(pending_key)
        await matcher.finish("已取消成绩上传。", reply_message=True)
    await react_processing(bot, event)
    qrcode = extract_sgwcmaid_qrcode(raw)
    recall_notice = ""
    if qrcode:
        try:
            await bot.delete_msg(message_id=event.message_id)
        except Exception:
            recall_notice = _RECALL_FAILED_NOTICE
    fish, lxns = _upload_mode(matcher)
    result = (
        await _upload(event, fish=fish, lxns=lxns, qrcode_arg=qrcode or "")
        if qrcode else "上传失败：二维码格式无效"
    )
    if not _upload_retryable(result):
        finish_pending(pending_key)
        await matcher.finish(recall_notice + result, reply_message=True)
    attempt = int(matcher.state.get("upload_qrcode_retry", 0)) + 1
    matcher.state["upload_qrcode_retry"] = attempt
    if attempt >= 3:
        finish_pending(pending_key)
        await matcher.finish(
            recall_notice
            + _upload_retry_prompt(result, 3)
            + "\n已连续失败 3 次，本次上传流程结束，且不扣 BREAK。",
            reply_message=True,
        )
    track_event(pending_key, event)
    await matcher.reject(
        recall_notice + _upload_retry_prompt(result, attempt), reply_message=True
    )


@account_ping.handle()
async def _():
    try:
        result = await sw_api.health()
    except Exception as exc:
        await account_ping.finish(f"AWMC API 连接失败：{exc}")
    await account_ping.finish("AWMC API 连接正常\n" + _result_text(result))


@account_ticket.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await _require_agreement(account_ticket, event)
    key, binding, error = _binding_or_error(event)
    if error or binding is None:
        await account_ticket.finish(error or "账号未绑定")
    raw = _arg_text(args) or "2"
    try:
        multiple = int(raw)
    except ValueError:
        await account_ticket.finish("倍率格式错误，用法：发票 2（或 fp 2）")
    allowed = _allowed_ticket_multipliers()
    if multiple not in allowed:
        allowed_text = " / ".join(map(str, allowed))
        await account_ticket.finish(f"票券倍率仅支持：{allowed_text}。")
    try:
        cost = _service_cost("ticket", multiple=multiple)
        break_db.ensure_service_affordable(int(key), "ticket", cost)
        async with machine_session():
            result = await sw_api.charge_ticket(binding.qrcode, multiple)
        _ensure_business_success(result)
        charge = break_db.settle_service_success(
            int(key), "ticket", cost, meta={"multiple": multiple}
        )
        ref = _log(
            key, "ticket", "success",
            f"multiple={multiple},charged={charge.charged},free={charge.free}",
        )
    except Exception as exc:
        ref = _log(key, "ticket", "error", str(exc))
        await account_ticket.finish(
            f"发票失败：{exc}\nRef_ID: {ref}", reply_message=True
        )
    await account_ticket.finish(
        f"{multiple} 倍票请求完成：{_result_text(result)}\n"
        f"{_charge_text(charge)}\nRef_ID: {ref}",
        reply_message=True,
    )


@account_ticket_status.handle()
async def _(event: MessageEvent):
    _, binding, error = _binding_or_error(event)
    if error or binding is None:
        await account_ticket_status.finish(error or "账号未绑定")
    try:
        async with machine_session():
            result = await sw_api.get_user_charge(binding.qrcode)
    except Exception as exc:
        await account_ticket_status.finish(f"查询失败：{exc}")
    await account_ticket_status.finish(
        "票券状态：\n" + json.dumps(result, ensure_ascii=False, indent=2)[:3000]
    )


@account_region.handle()
async def _(event: MessageEvent):
    _, binding, error = _binding_or_error(event)
    if error or binding is None:
        await account_region.finish(error or "账号未绑定")
    try:
        result = await sw_api.get_user_region(binding.qrcode)
    except Exception as exc:
        await account_region.finish(f"查询失败：{exc}")
    rows = result.get("userRegionList") or result.get("UserRegionList") or []
    if not rows:
        await account_region.finish("暂无游玩地区记录。")
    lines = ["游玩地区记录："]
    for row in rows[:50]:
        region = row.get("regionName") or row.get("RegionName") or row.get("regionId") or row.get("RegionId")
        count = row.get("playCount") or row.get("PlayCount") or 0
        lines.append(f"{region}：{count} PC")
    await account_region.finish("\n".join(lines))


@account_opt.handle()
async def _(args: Message = CommandArg()):
    title_ver = _arg_text(args)
    if not title_ver:
        await account_opt.finish("用法：mai查询opt <titleVer>")
    try:
        result = await sw_api.get_opt(title_ver)
    except Exception as exc:
        await account_opt.finish(f"查询失败：{exc}")
    await account_opt.finish(json.dumps(result, ensure_ascii=False, indent=2)[:3000])


@account_queue.handle()
async def _():
    try:
        result = await sw_api.get_charge_queue()
    except Exception as exc:
        await account_queue.finish(f"查询失败：{exc}")
    await account_queue.finish(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
