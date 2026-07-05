from typing import Optional

from loguru import logger as log

from .maimaidx_sw_api import SwApiError, sw_api


class SdgbProberClient:
    """sw-api 查分器客户端：水鱼/落雪 B50 上传、倍率票获取等。"""

    @property
    def available(self) -> bool:
        return sw_api.available

    def _check_available(self):
        if not self.available:
            raise RuntimeError(
                "sw-api 未配置。请在 .env 中设置:\n"
                "  AWMCBACKEND=http://127.0.0.1:5001\n"
                "  SDGBT_CLIENT_ID=your_keychip\n"
                "  SDGBT_REGION_ID=1\n"
                "  SDGBT_PLACE_ID=1403"
            )

    async def upload_b50(self, qr_text: str, fish_token: str) -> dict:
        """拉取 Sega 成绩并上传到水鱼。"""
        self._check_available()
        try:
            return await sw_api.update_fish(qr_text, fish_token)
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

    async def get_b50_task_status(self, mai_uid: str) -> dict:
        """sw-api 同步上传，保留接口兼容；按 userId 查询无对应端点，返回已完成。"""
        self._check_available()
        return {"done": True, "mai_uid": mai_uid}

    async def get_b50_task_byid(self, task_id: str) -> dict:
        """sw-api 同步上传，保留接口兼容。"""
        self._check_available()
        return {"done": True, "task_id": task_id}

    async def upload_lx_b50(self, qr_text: str, lxns_code: str) -> dict:
        """拉取 Sega 成绩并上传到落雪。"""
        self._check_available()
        try:
            return await sw_api.update_lx(qr_text, lxns_code)
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

    async def get_lx_b50_task_status(self, mai_uid: str) -> dict:
        self._check_available()
        return {"done": True, "mai_uid": mai_uid}

    async def get_lx_b50_task_byid(self, task_id: str) -> dict:
        self._check_available()
        return {"done": True, "task_id": task_id}

    async def get_ticket(self, qr_text: str, ticket_id: int) -> dict:
        """获取倍率票。ticket_id: 2/3/4/5/6 对应 2x-6x。"""
        self._check_available()
        try:
            return await sw_api.charge_ticket(qr_text, ticket_id)
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

    async def get_charge(self, qr_text: str, user_id: Optional[str] = None) -> dict:
        """查询用户发票/票券信息。sw-api 需要 userId，若未提供则无法查询。"""
        self._check_available()
        if not user_id:
            raise RuntimeError("查询票券需要提供 userId（sw-api /user/charge 接口要求）")
        try:
            return await sw_api.get_user_charge(user_id)
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

    async def get_item(
        self, qr_text: str, item_id: int, item_kind: int, item_stock: int
    ) -> dict:
        """sw-api 当前未提供收藏品接口。"""
        raise RuntimeError("sw-api 暂不支持添加收藏品（get_item）")

    async def poll_task_until_done(
        self,
        task_id: str,
        is_lx: bool = False,
        max_wait: int = 180,
        interval: int = 3,
    ) -> dict:
        """sw-api B50 上传为同步接口，直接返回完成。"""
        self._check_available()
        get_byid = self.get_lx_b50_task_byid if is_lx else self.get_b50_task_byid
        return await get_byid(task_id)


sdgb_prober = SdgbProberClient()
