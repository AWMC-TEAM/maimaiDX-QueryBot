import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger as log

from ..config import maiconfig


class SwApiError(RuntimeError):
    pass


class SwApiClient:
    """sw-api (AWMC) HTTP 客户端。"""

    def __init__(self):
        self.base_url = (maiconfig.awmcbackend or "").rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.base_url) and bool(maiconfig.sdgbt_client_id)

    def _check_available(self):
        if not self.available:
            raise SwApiError(
                "sw-api 未配置。请在 .env 中设置:\n"
                "  AWMCBACKEND=http://127.0.0.1:5001\n"
                "  SDGBT_CLIENT_ID=your_keychip"
            )

    def _machine_body(self, qrcode: str, **extra: Any) -> dict:
        body: dict = {
            "qrcode": qrcode,
            "keychip": maiconfig.sdgbt_client_id,
        }
        if maiconfig.sdgbt_region_id is not None:
            body["regionId"] = maiconfig.sdgbt_region_id
        if maiconfig.sdgbt_place_id is not None:
            body["placeId"] = maiconfig.sdgbt_place_id
        body.update(extra)
        return body

    @staticmethod
    def _parse_msg_payload(msg: Any) -> Any:
        if isinstance(msg, dict):
            return msg
        if isinstance(msg, str):
            if not msg:
                return {}
            try:
                return json.loads(msg)
            except json.JSONDecodeError:
                return {"raw": msg}
        return msg

    @staticmethod
    def _parse_envelope(data: dict) -> Any:
        if "error" in data:
            raise SwApiError(str(data["error"]))

        code = data.get("code")
        if code == -1:
            raise SwApiError(str(data.get("msg", "未知错误")))

        # user/music 等接口：成功时 code=0，msg 为 JSON 字符串
        if code in (0, 1) and "msg" in data:
            return SwApiClient._parse_msg_payload(data.get("msg"))

        if "userId" in data and "count" in data:
            return data

        if data.get("Status"):
            return data

        if code == 0:
            return data

        raise SwApiError(str(data.get("msg") or data.get("error") or "未知错误"))

    @staticmethod
    def flatten_user_music(payload: Any) -> List[dict]:
        if not isinstance(payload, dict):
            return []

        direct = payload.get("userMusicDetailList")
        if isinstance(direct, list):
            if direct and isinstance(direct[0], dict) and "musicId" in direct[0]:
                return direct

        detail_list: List[dict] = []
        for music in payload.get("userMusicList") or []:
            if not isinstance(music, dict):
                continue
            for detail in music.get("userMusicDetailList") or []:
                if isinstance(detail, dict):
                    detail_list.append(detail)
        return detail_list

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: float = 120,
    ) -> dict:
        self._check_available()
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            res = await client.request(method, url, json=json_body, params=params)
        if res.status_code != 200:
            text = res.text[:200]
            try:
                err_data = res.json()
                if "error" in err_data:
                    raise SwApiError(str(err_data["error"]))
                if err_data.get("code") == -1:
                    raise SwApiError(str(err_data.get("msg", text)))
            except json.JSONDecodeError:
                pass
            raise SwApiError(f"HTTP {res.status_code}: {text}")
        return res.json()

    async def get_user_music(self, qrcode: str) -> List[dict]:
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/music",
            json_body=self._machine_body(qrcode),
        )
        payload = self._parse_envelope(data)
        detail_list = self.flatten_user_music(payload)
        log.info(f"[SwApi] 拉取谱面成绩完成，共 {len(detail_list)} 条")
        return detail_list

    async def update_fish(self, qrcode: str, token: str) -> dict:
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-fish",
            json_body=self._machine_body(qrcode, token=token),
            timeout=120,
        )
        return self._parse_envelope(data)

    async def update_lx(self, qrcode: str, import_token: str) -> dict:
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-lx",
            json_body=self._machine_body(qrcode, key=import_token, type="maimai"),
            timeout=120,
        )
        return self._parse_envelope(data)

    async def charge_ticket(self, qrcode: str, charge_id: int) -> dict:
        data = await self._request(
            "POST",
            "/awmc/api/v1/charge",
            json_body=self._machine_body(qrcode, charge=charge_id),
            timeout=60,
        )
        return self._parse_envelope(data)

    async def get_user_charge(self, user_id: str) -> dict:
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/charge",
            json_body={
                "userId": user_id,
                "keychip": maiconfig.sdgbt_client_id,
            },
            timeout=30,
        )
        return self._parse_envelope(data)

    async def get_charge_queue(self) -> dict:
        return await self._request("GET", "/awmc/api/v1/charge/queue", timeout=15)

    async def health(self) -> dict:
        return await self._request("GET", "/awmc/api/v1/health", timeout=10)


sw_api = SwApiClient()
