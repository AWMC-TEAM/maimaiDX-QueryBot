import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger as log

from ..config import maiconfig


class SwApiError(RuntimeError):
    pass


class SwApiClient:
    """sw-api (AWMC) HTTP 客户端。"""

    def __init__(self):
        self.base_url = (
            getattr(maiconfig, "awmc_api_base_url", None)
            or maiconfig.sdgbtechapi
            or ""
        ).rstrip("/")
        self.api_mode = str(
            getattr(maiconfig, "awmc_api_mode", "team") or "team"
        ).lower()

    @property
    def available(self) -> bool:
        if not bool(getattr(maiconfig, "awmc_account_enabled", True)):
            return False
        if self.api_mode == "public":
            return bool(self.base_url) and bool(
                getattr(maiconfig, "awmc_public_gateway_token", None)
            )
        return bool(self.base_url) and bool(maiconfig.sdgbt_client_id)

    def _check_available(self):
        if not self.available:
            raise SwApiError(
                "sw-api 未配置。请在 .env 中设置:\n"
                "  SDGBTECHAPI=http://127.0.0.1:5001\n"
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

        if "userId" in data or "userData" in data or "userPreview" in data:
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
        timeout: Optional[float] = None,
    ) -> dict:
        self._check_available()
        url = f"{self.base_url}{path}"
        from .maimaidx_admin_audit import admin_audit

        audit_started = time.time()
        actual_timeout = float(
            timeout
            if timeout is not None
            else getattr(maiconfig, "awmc_api_timeout_seconds", 120.0)
        )
        headers: Dict[str, str] = {}
        if self.api_mode == "public":
            token = str(getattr(maiconfig, "awmc_public_gateway_token", "") or "")
            headers["Authorization"] = f"Bearer {token}"
        retry_count = max(0, int(getattr(maiconfig, "awmc_api_retry_count", 3)))
        retry_delay = max(
            0.0, float(getattr(maiconfig, "awmc_api_retry_delay_seconds", 1.0))
        )
        res: Optional[httpx.Response] = None
        last_error: Optional[Exception] = None
        for attempt in range(retry_count + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(actual_timeout), headers=headers
                ) as client:
                    res = await client.request(
                        method, url, json=json_body, params=params
                    )
                if res.status_code not in (408, 429) and res.status_code < 500:
                    break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            if attempt < retry_count:
                await asyncio.sleep(retry_delay * (attempt + 1))
        if res is None:
            admin_audit.add_step(
                "http.awmc",
                "error",
                {"method": method, "path": path, "error": str(last_error or "request failed")},
                started_at=audit_started,
            )
            raise SwApiError(str(last_error or "AWMC API 请求失败"))
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
            admin_audit.add_step(
                "http.awmc",
                "error",
                {"method": method, "path": path, "status_code": res.status_code},
                started_at=audit_started,
            )
            raise SwApiError(f"HTTP {res.status_code}: {text}")
        admin_audit.add_step(
            "http.awmc",
            "success",
            {"method": method, "path": path, "status_code": res.status_code},
            started_at=audit_started,
        )
        return res.json()

    async def get_user_music(self, qrcode: str) -> List[dict]:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不提供 PC 全量数据接口")
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
        if self.api_mode == "public":
            return await self._request(
                "POST",
                "/v1/upload_b50",
                params={"qr_text": qrcode, "fish_token": token},
                timeout=600,
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-fish",
            json_body=self._machine_body(qrcode, token=token),
            timeout=120,
        )
        return self._parse_envelope(data)

    async def update_lx(self, qrcode: str, import_token: str) -> dict:
        if self.api_mode == "public":
            return await self._request(
                "POST",
                "/v1/upload_lx_b50",
                params={"qr_text": qrcode, "lxns_code": import_token},
                timeout=600,
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-lx",
            json_body=self._machine_body(qrcode, key=import_token, type="maimai"),
            timeout=120,
        )
        return self._parse_envelope(data)

    async def get_upload_task(self, task_id: str, *, lxns: bool = False) -> dict:
        """查询公共网关异步上传任务；team 模式上传为同步，无需调用。"""
        if self.api_mode != "public":
            raise SwApiError("team 模式没有异步上传任务")
        path = "/v1/get_lx_b50_task_byid" if lxns else "/v1/get_b50_task_byid"
        return await self._request("GET", path, params={"task_id": task_id}, timeout=30)

    async def charge_ticket(self, qrcode: str, charge_id: int) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持发放票券")
        data = await self._request(
            "POST",
            "/awmc/api/v1/charge",
            json_body=self._machine_body(qrcode, charge=charge_id),
            timeout=60,
        )
        return self._parse_envelope(data)

    async def get_user_charge(self, user_id: str) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持查询票券")
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
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关没有发票队列")
        return await self._request("GET", "/awmc/api/v1/charge/queue", timeout=15)

    async def health(self) -> dict:
        path = "/v1/mai_ping" if self.api_mode == "public" else "/awmc/api/v1/health"
        return await self._request("GET", path, timeout=10)

    async def get_user_preview(self, qrcode: str) -> dict:
        """读取绑定账号摘要；兼容公共网关与自建 sw-api。"""
        if self.api_mode == "public":
            return await self._request(
                "GET", "/v1/get_preview", params={"qr_text": qrcode}
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/data",
            json_body={"qrcode": qrcode, "keychip": maiconfig.sdgbt_client_id},
        )
        return self._parse_envelope(data)

    async def get_user_region(self, qrcode: str) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持游玩地图")
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/region",
            json_body={"qrcode": qrcode, "keychip": maiconfig.sdgbt_client_id},
        )
        return self._parse_envelope(data)

    async def get_opt(self, title_ver: str) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持查询 opt")
        return await self._request(
            "GET",
            "/api/private/get_opt",
            params={"title_ver": title_ver, "client_id": maiconfig.sdgbt_client_id},
            timeout=30,
        )


sw_api = SwApiClient()
