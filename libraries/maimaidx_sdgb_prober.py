import asyncio
import time
from typing import Optional

import httpx
from loguru import logger as log

from ..config import maiconfig


class SdgbProberClient:
    """SDGBTECHAPI 查分器客户端：水鱼/落雪 B50 上传、倍率票获取等。"""

    def __init__(self):
        self.base_url = (maiconfig.sdgbtechapi or "").rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.base_url) and bool(maiconfig.sdgbt_client_id)

    def _check_available(self):
        if not self.available:
            raise RuntimeError(
                "SDGBTECHAPI 未配置。请在 .env 中设置:\n"
                "  SDGBTECHAPI=http://127.0.0.1:12346\n"
                "  SDGBT_CLIENT_ID=A63E01C2562\n"
                "  SDGBT_REGION_ID=24\n"
                "  SDGBT_PLACE_ID=1320"
            )

    def _build_params(self, qr_text: str) -> dict:
        return {
            "client_id": maiconfig.sdgbt_client_id,
            "region_id": maiconfig.sdgbt_region_id,
            "place_id": maiconfig.sdgbt_place_id,
            "qr_text": qr_text,
        }

    def _build_private_params(self, qr_text: str) -> dict:
        return {
            "client_id": maiconfig.sdgbt_client_id,
            "region_id": maiconfig.sdgbt_region_id,
            "place_id": maiconfig.sdgbt_place_id,
            "region_name": maiconfig.sdgbt_region_name or "",
            "place_name": maiconfig.sdgbt_place_name or "",
            "qr_text": qr_text,
        }

    async def upload_b50(
        self, qr_text: str, fish_token: str
    ) -> dict:
        """提交水鱼 B50 更新任务，返回包含 task_id 的字典。"""
        self._check_available()
        params = self._build_params(qr_text)
        params["fish_token"] = fish_token

        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            r = await client.post(
                f"{self.base_url}/api/public/upload_b50",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def get_b50_task_status(self, mai_uid: str) -> dict:
        """查询水鱼 B50 任务状态。"""
        self._check_available()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            r = await client.get(
                f"{self.base_url}/api/public/get_b50_task_status",
                params={"mai_uid": mai_uid},
            )
            r.raise_for_status()
            return r.json()

    async def get_b50_task_byid(self, task_id: str) -> dict:
        """根据任务 ID 查询水鱼 B50 任务详情。"""
        self._check_available()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            r = await client.get(
                f"{self.base_url}/api/public/get_b50_task_byid",
                params={"task_id": task_id},
            )
            r.raise_for_status()
            return r.json()

    async def upload_lx_b50(
        self, qr_text: str, lxns_code: str
    ) -> dict:
        """提交落雪 B50 更新任务，返回包含 task_id 的字典。"""
        self._check_available()
        params = self._build_params(qr_text)
        params["lxns_code"] = lxns_code

        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            r = await client.post(
                f"{self.base_url}/api/public/upload_lx_b50",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def get_lx_b50_task_status(self, mai_uid: str) -> dict:
        """查询落雪 B50 任务状态。"""
        self._check_available()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            r = await client.get(
                f"{self.base_url}/api/public/get_lx_b50_task_status",
                params={"mai_uid": mai_uid},
            )
            r.raise_for_status()
            return r.json()

    async def get_lx_b50_task_byid(self, task_id: str) -> dict:
        """根据任务 ID 查询落雪 B50 任务详情。"""
        self._check_available()
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
            r = await client.get(
                f"{self.base_url}/api/public/get_lx_b50_task_byid",
                params={"task_id": task_id},
            )
            r.raise_for_status()
            return r.json()

    async def get_ticket(
        self, qr_text: str, ticket_id: int
    ) -> dict:
        """获取倍率票。ticket_id: 2/3/4/5/6 对应 2x-6x。"""
        self._check_available()
        params = self._build_params(qr_text)
        params["ticket_id"] = ticket_id

        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
            r = await client.post(
                f"{self.base_url}/api/private/get_ticket",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def get_charge(self, qr_text: str) -> dict:
        """查询用户功能票信息。"""
        self._check_available()
        params = self._build_params(qr_text)

        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            r = await client.get(
                f"{self.base_url}/api/public/get_charge",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def get_item(
        self, qr_text: str, item_id: int, item_kind: int, item_stock: int
    ) -> dict:
        """为用户添加收藏品（道具）。item_kind: 道具类型, item_id: 道具ID, item_stock: 数量。"""
        self._check_available()
        params = self._build_private_params(qr_text)
        params["item_id"] = item_id
        params["item_kind"] = item_kind
        params["item_stock"] = item_stock

        async with httpx.AsyncClient(timeout=httpx.Timeout(90)) as client:
            r = await client.post(
                f"{self.base_url}/api/private/get_item",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def poll_task_until_done(
        self,
        task_id: str,
        is_lx: bool = False,
        max_wait: int = 180,
        interval: int = 3,
    ) -> dict:
        """轮询任务直到完成或超时，返回最终任务详情。"""
        self._check_available()
        get_byid = self.get_lx_b50_task_byid if is_lx else self.get_b50_task_byid

        start = time.time()
        while time.time() - start < max_wait:
            task = await get_byid(task_id)
            if task.get("done"):
                return task
            await asyncio.sleep(interval)

        return {"done": False, "timeout": True, "task_id": task_id}


sdgb_prober = SdgbProberClient()
