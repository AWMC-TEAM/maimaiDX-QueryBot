"""统一持久化后端的定时同步生命周期。"""

from __future__ import annotations

import asyncio
from typing import Optional

from ..config import driver, log, maiconfig
from .maimaidx_storage import (
    backend_name,
    local_change_token,
    sync_configured_backend,
)


_sync_task: Optional[asyncio.Task] = None
_sync_lock = asyncio.Lock()
_last_synced_change_token: Optional[str] = None


async def sync_storage_now(*, force: bool = True) -> Optional[dict]:
    global _last_synced_change_token
    async with _sync_lock:
        before = await asyncio.to_thread(local_change_token, maiconfig)
        if not force and before == _last_synced_change_token:
            return None
        result = await asyncio.to_thread(sync_configured_backend, maiconfig)
        after = await asyncio.to_thread(local_change_token, maiconfig)
        # 制作快照期间仍有写入时不记为干净，下个周期会再补一次。
        _last_synced_change_token = after if before == after else None
        return result


async def _sync_loop() -> None:
    interval = int(getattr(maiconfig, "maimaidx_storage_sync_interval_seconds", 900))
    if interval <= 0:
        return
    interval = max(10, interval)
    while True:
        await asyncio.sleep(interval)
        try:
            snapshot = await sync_storage_now(force=False)
            if snapshot:
                log.debug(
                    f"统一存储已同步：{snapshot['file_count']} 文件 / "
                    f"{snapshot['total_bytes']} bytes"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"统一存储定时同步失败：{type(exc).__name__}: {exc}")


@driver.on_startup
async def _start_storage_sync() -> None:
    global _sync_task
    if backend_name(maiconfig) == "sqlite":
        return
    try:
        await sync_storage_now(force=True)
    except Exception as exc:
        log.error(f"统一存储首次同步失败：{type(exc).__name__}: {exc}")
    _sync_task = asyncio.create_task(_sync_loop(), name="maimaidx-storage-sync")


@driver.on_shutdown
async def _stop_storage_sync() -> None:
    global _sync_task
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
    if backend_name(maiconfig) != "sqlite":
        try:
            await sync_storage_now(force=False)
        except Exception as exc:
            log.error(f"统一存储关机同步失败：{type(exc).__name__}: {exc}")
