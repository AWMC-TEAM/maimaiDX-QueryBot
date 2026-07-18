from typing import Optional

from .maimaidx_sw_api import SwApiError, sw_api


class SdgbProberClient:
    """sw-api 查分器客户端：水鱼/落雪 B50 上传、倍率票获取等。"""

    @property
    def available(self) -> bool:
        return sw_api.available

    def _check_available(self):
        if not self.available:
            raise RuntimeError(
                "AWMC API 未配置。team 模式：\n"
                "  AWMC_API_MODE=team\n"
                "  AWMC_API_BASE_URL=http://127.0.0.1:5001\n"
                "  SDGBT_CLIENT_ID=your_keychip\n"
                "或 public 模式：\n"
                "  AWMC_API_MODE=public\n"
                "  AWMC_PUBLIC_GATEWAY_TOKEN=gw_xxx"
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
        """获取倍率票；允许值由 AWMC_TICKET_ALLOWED_MULTIPLIERS 控制。"""
        self._check_available()
        try:
            return await sw_api.charge_ticket(qr_text, ticket_id)
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

    async def get_charge(self, qr_text: str, user_id: Optional[str] = None) -> dict:
        """查询用户发票/票券信息；新版 sw-api 使用二维码只读查询。"""
        self._check_available()
        try:
            return await sw_api.get_user_charge(qr_text)
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
