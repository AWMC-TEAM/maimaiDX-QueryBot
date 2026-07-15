"""Koishi maiBot 数据迁移管理员指令。"""

from __future__ import annotations

import asyncio
import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

from nonebot import on_command
from nonebot.params import CommandArg

from ..config import maiconfig
from ..libraries.maimaidx_account_db import account_db
from ..libraries.maimaidx_admin_audit import admin_audit, redact
from ..libraries.maimaidx_bot_admin import PLUGIN_ADMIN_ONLY
from ..libraries.maimaidx_storage import (
    StorageError,
    backend_name,
    check_target,
    collect_local_snapshot,
    load_snapshot,
    save_snapshot,
)


koishi_migrate = on_command(
    "迁移Koishi",
    aliases={"迁移koishi", "迁移maibot", "迁移maiBot"},
    permission=PLUGIN_ADMIN_ONLY,
)
storage_status = on_command("存储状态", aliases={"数据存储状态"}, permission=PLUGIN_ADMIN_ONLY)
storage_migrate = on_command("存储迁移", aliases={"数据迁移"}, permission=PLUGIN_ADMIN_ONLY)
storage_sync = on_command("存储同步", aliases={"数据同步"}, permission=PLUGIN_ADMIN_ONLY)

_MIGRATOR: Optional[ModuleType] = None
_ALLOWED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".json"}


def _migration_root() -> Path:
    configured = str(
        getattr(maiconfig, "maimaidx_koishi_migration_dir", "data/migration")
        or "data/migration"
    )
    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root.resolve()


def _resolve_allowed_file(value: str, *, identity_map: bool = False) -> Path:
    root = _migration_root()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("只允许读取 MAIMAIDX_KOISHI_MIGRATION_DIR 目录内的文件")
    if not candidate.is_file():
        raise ValueError(f"文件不存在：{candidate.name}")
    allowed = {".json"} if identity_map else _ALLOWED_SUFFIXES
    if candidate.suffix.lower() not in allowed:
        raise ValueError("源文件仅支持 SQLite/JSON；identity map 仅支持 JSON")
    return candidate


def _load_migrator() -> ModuleType:
    global _MIGRATOR
    if _MIGRATOR is not None:
        return _MIGRATOR
    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_maibot_accounts.py"
    if not script.is_file():
        raise RuntimeError("安装包缺少 scripts/migrate_maibot_accounts.py")
    name = "nonebot_plugin_maimaidx._koishi_migrator"
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 Koishi 迁移器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _MIGRATOR = module
    return module


def _help_text() -> str:
    root = _migration_root()
    return (
        "Koishi maiBot 数据迁移（仅超级管理员）\n"
        f"1. 把 Koishi SQLite 数据库复制到：{root}\n"
        "2. 先预检：迁移Koishi 检查 koishi.db\n"
        "3. 确认结果后执行：迁移Koishi 确认 koishi.db\n"
        "若自动身份映射仍有跳过项，可把映射 JSON 放在同目录并使用：\n"
        "迁移Koishi 检查 koishi.db identity-map.json\n"
        "映射格式：{\"koishi:12\": \"123456789\"}\n"
        "源数据库始终只读；只导入账号、二维码、上传 Token 和协议记录。"
    )


def _format_result(result: dict, *, source: Path, confirmed: bool, map_name: str) -> str:
    mode = "迁移完成" if confirmed else "预检完成（尚未写入）"
    lines = [
        f"Koishi {mode}",
        f"源文件：{source.name}",
        f"账号绑定：{result['bindings']} 条",
        f"旧协议记录：{result['agreements']} 条",
        f"自动 QQ 身份映射：{result['identity_mappings']} 个",
        f"跳过：{len(result['skipped'])} 条",
        "源 Koishi 数据库及其它插件表未被修改。",
    ]
    notices = list(result["identity_warnings"]) + list(result["skipped"])
    if notices:
        lines.append("需处理的问题（最多显示 5 条）：")
        lines.extend(f"- {redact(item)}" for item in notices[:5])
    if not confirmed:
        suffix = f" {map_name}" if map_name else ""
        lines.append(f"确认导入请发送：迁移Koishi 确认 {source.name}{suffix}")
    lines.append("注意：旧版协议记录不会代替 v4 确认，用户仍需重新阅读并同意。")
    return "\n".join(lines)


@koishi_migrate.handle()
async def _(args=CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw or raw.lower() in {"帮助", "help", "?"}:
        await koishi_migrate.finish(_help_text())
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        await koishi_migrate.finish(f"参数格式错误：{exc}\n\n{_help_text()}")
    action = parts[0].lower()
    if action not in {"检查", "预检", "确认", "执行"} or len(parts) not in {2, 3}:
        await koishi_migrate.finish(_help_text())
    confirmed = action in {"确认", "执行"}
    try:
        source = _resolve_allowed_file(parts[1])
        identity_path = (
            _resolve_allowed_file(parts[2], identity_map=True)
            if len(parts) == 3 else None
        )
        migrator = _load_migrator()
        result = await asyncio.to_thread(
            migrator.run_migration,
            source,
            account_db.path,
            admin_audit.path,
            identity_map_path=identity_path,
            dry_run=not confirmed,
        )
    except Exception as exc:
        admin_audit.add_step(
            "migration.koishi", "error", {"error": redact(str(exc))}
        )
        await koishi_migrate.finish(f"迁移失败：{redact(str(exc))}")
    admin_audit.add_step(
        "migration.koishi",
        "success",
        {
            "source": source.name,
            "confirmed": confirmed,
            "bindings": result["bindings"],
            "agreements": result["agreements"],
            "skipped": len(result["skipped"]),
        },
    )
    await koishi_migrate.finish(
        _format_result(
            result,
            source=source,
            confirmed=confirmed,
            map_name=identity_path.name if identity_path else "",
        )
    )


def _storage_help() -> str:
    return (
        "统一存储迁移（仅插件管理员）\n"
        "后端：sqlite / yaml / mysql\n"
        "预检：存储迁移 检查 sqlite mysql\n"
        "确认：存储迁移 确认 sqlite mysql\n"
        "其它：存储状态 / 存储同步\n"
        "确认迁移后修改 MAIMAIDX_STORAGE_BACKEND 并重启 Bot。"
    )


def _snapshot_line(snapshot: dict) -> str:
    mib = snapshot["total_bytes"] / 1024 / 1024
    return (
        f"{snapshot['file_count']} 个文件 / {mib:.2f} MiB / "
        f"SHA-256 {snapshot['manifest'][:16]}…"
    )


@storage_status.handle()
async def _():
    try:
        selected = backend_name(maiconfig)
        local = await asyncio.to_thread(collect_local_snapshot, maiconfig)
        lines = [f"当前配置后端：{selected}", "本地工作数据：" + _snapshot_line(local)]
        if selected != "sqlite":
            remote = await asyncio.to_thread(load_snapshot, maiconfig, selected)
            lines.append(f"{selected} 持久化快照：" + _snapshot_line(remote))
            lines.append("同步状态：" + ("一致" if local["manifest"] == remote["manifest"] else "有待同步变更"))
    except Exception as exc:
        await storage_status.finish(
            f"存储状态检查失败：{redact(str(exc))}"
        )
    await storage_status.finish("\n".join(lines))


@storage_sync.handle()
async def _():
    selected = backend_name(maiconfig)
    if selected == "sqlite":
        await storage_sync.finish("当前使用原生 SQLite/JSON，本地写入已即时生效，无需同步。")
    try:
        from ..libraries.maimaidx_storage_runtime import sync_storage_now

        snapshot = await sync_storage_now()
    except Exception as exc:
        await storage_sync.finish(f"存储同步失败：{redact(str(exc))}")
    await storage_sync.finish(f"已同步到 {selected}：{_snapshot_line(snapshot)}")


@storage_migrate.handle()
async def _(args=CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw or raw.lower() in {"帮助", "help", "?"}:
        await storage_migrate.finish(_storage_help())
    parts = raw.lower().split()
    if len(parts) != 3 or parts[0] not in {"检查", "预检", "确认", "执行"}:
        await storage_migrate.finish(_storage_help())
    action, source_backend, target_backend = parts
    allowed = {"sqlite", "yaml", "mysql"}
    if source_backend not in allowed or target_backend not in allowed:
        await storage_migrate.finish("后端只能是 sqlite、yaml 或 mysql。")
    if source_backend == target_backend:
        await storage_migrate.finish("源后端和目标后端不能相同。")
    confirmed = action in {"确认", "执行"}
    try:
        snapshot = await asyncio.to_thread(load_snapshot, maiconfig, source_backend)
        await asyncio.to_thread(check_target, maiconfig, target_backend)
    except Exception as exc:
        reason = redact(str(exc))
        admin_audit.add_step("migration.storage", "error", {"error": reason})
        await storage_migrate.finish(f"存储迁移失败：{reason}")
    if not confirmed:
        await storage_migrate.finish(
            "存储迁移预检通过（尚未写入）\n"
            f"{source_backend} → {target_backend}\n"
            f"数据：{_snapshot_line(snapshot)}\n"
            f"确认执行：存储迁移 确认 {source_backend} {target_backend}"
        )
    try:
        detail = await asyncio.to_thread(save_snapshot, maiconfig, target_backend, snapshot)
        if target_backend != "sqlite":
            verified = await asyncio.to_thread(load_snapshot, maiconfig, target_backend)
            if verified["manifest"] != snapshot["manifest"]:
                raise StorageError("目标后端写入后总校验不一致")
        admin_audit.add_step(
            "migration.storage",
            "success",
            {"source": source_backend, "target": target_backend, "manifest": snapshot["manifest"]},
        )
    except Exception as exc:
        reason = redact(str(exc))
        admin_audit.add_step("migration.storage", "error", {"error": reason})
        await storage_migrate.finish(f"存储迁移失败：{reason}")
    await storage_migrate.finish(
        "存储迁移成功\n"
        f"{source_backend} → {target_backend}\n"
        f"数据：{_snapshot_line(snapshot)}\n{detail}\n"
        f"下一步：设置 MAIMAIDX_STORAGE_BACKEND={target_backend} 并重启 Bot。"
    )
