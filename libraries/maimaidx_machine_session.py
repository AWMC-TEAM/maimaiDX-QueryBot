"""Serialize workflows that share the configured arcade machine identity."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..config import maiconfig


# One configured keychip represents one arcade machine session.  Concurrent
# logins can invalidate one another even when they belong to different users,
# so this lock is intentionally global rather than keyed by QQ/user ID.
_machine_lock = asyncio.Lock()


@asynccontextmanager
async def machine_session() -> AsyncIterator[None]:
    """Hold the shared machine session for one complete business workflow."""
    async with _machine_lock:
        yield


async def wait_between_machine_steps() -> None:
    """Leave a short gap between consecutive machine-side login operations."""
    delay = max(
        0.0,
        float(getattr(maiconfig, "awmc_machine_step_delay_seconds", 3.0) or 0.0),
    )
    if delay:
        await asyncio.sleep(delay)
