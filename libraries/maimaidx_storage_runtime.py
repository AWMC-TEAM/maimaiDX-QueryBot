"""统一持久化后端的定时同步生命周期。"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..config import driver, log, maiconfig
from .maimaidx_storage import (
    backend_name,
    local_change_token,
    sync_configured_backend,
)


_sync_task: Optional[asyncio.Task] = None
_startup_sync_task: Optional[asyncio.Task] = None
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
                if snapshot.get("skipped"):
                    log.debug("统一存储：工作集未变化，跳过同步")
                else:
                    log.debug(
                        f"统一存储已同步：{snapshot['file_count']} 文件 / "
                        f"{snapshot['total_bytes']} bytes"
                        + (
                            f"（上传 {snapshot.get('uploaded', 0)} / "
                            f"复用 {snapshot.get('reused', 0)}）"
                            if "uploaded" in snapshot
                            else ""
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"统一存储定时同步失败：{type(exc).__name__}: {exc}")


async def _run_startup_sync() -> None:
    started = time.monotonic()
    try:
        log.info("统一存储：后台启动同步开始")
        # force=False：若内存中已有 token 可跳过；真正跳过依赖 change_token 标记。
        result = await sync_storage_now(force=True)
        elapsed = time.monotonic() - started
        if not result:
            log.info(f"统一存储：后台启动同步结束（无变更，{elapsed:.1f}s）")
        elif result.get("skipped"):
            log.info(
                f"统一存储：后台启动同步跳过（与远端一致，{elapsed:.1f}s，"
                f"{result.get('file_count', 0)} 文件）"
            )
        else:
            log.info(
                f"统一存储：后台启动同步完成（{elapsed:.1f}s，"
                f"{result.get('file_count', 0)} 文件 / {result.get('total_bytes', 0)} bytes"
                f"，上传 {result.get('uploaded', 0)} / 复用 {result.get('reused', 0)}）"
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error(f"统一存储首次同步失败：{type(exc).__name__}: {exc}")


@driver.on_startup
async def _start_storage_sync() -> None:
    global _sync_task, _startup_sync_task
    if backend_name(maiconfig) == "sqlite":
        return
    # 不阻塞 NoneBot 完成 startup；曲目加载与接消息可并行进行。
    _startup_sync_task = asyncio.create_task(
        _run_startup_sync(), name="maimaidx-storage-startup-sync"
    )
    _sync_task = asyncio.create_task(_sync_loop(), name="maimaidx-storage-sync")


@driver.on_shutdown
async def _stop_storage_sync() -> None:
    global _sync_task, _startup_sync_task
    for task in (_startup_sync_task, _sync_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _startup_sync_task = None
    _sync_task = None
    if backend_name(maiconfig) != "sqlite":
        try:
            await sync_storage_now(force=False)
        except Exception as exc:
            log.error(f"统一存储关机同步失败：{type(exc).__name__}: {exc}")
