import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger as log

from ..config import maiconfig

# 与 maibot WAHLAP_REGIONS 对齐：API 只返回 regionId，需本地映射省份名。
WAHLAP_REGIONS: Dict[int, str] = {
    1: "北京",
    2: "重庆",
    3: "上海",
    4: "天津",
    5: "安徽",
    6: "福建",
    7: "甘肃",
    8: "广东",
    9: "贵州",
    10: "海南",
    11: "河北",
    12: "黑龙江",
    13: "河南",
    14: "湖北",
    15: "湖南",
    16: "江苏",
    17: "江西",
    18: "吉林",
    19: "辽宁",
    20: "青海",
    21: "陕西",
    22: "山东",
    23: "山西",
    24: "四川",
    25: "未知25",
    26: "云南",
    27: "浙江",
    28: "广西",
    29: "内蒙古",
    30: "宁夏",
    31: "新疆",
    32: "西藏",
}


def format_wahlap_region_name(region_id: int) -> str:
    return WAHLAP_REGIONS.get(region_id, f"未知({region_id})")


def format_user_region_block(result: dict) -> str:
    """将 user/region 响应格式化为与 maibot 一致的游玩地图文本。"""
    rows = result.get("userRegionList") or result.get("UserRegionList") or []
    entries: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        region_id = row.get("regionId", row.get("RegionId"))
        play_count = row.get("playCount", row.get("PlayCount"))
        created = row.get("created") or row.get("Created") or ""
        try:
            region_id_int = int(region_id)
        except (TypeError, ValueError):
            continue
        try:
            play_count_int = int(play_count or 0)
        except (TypeError, ValueError):
            play_count_int = 0
        entries.append(
            {
                "regionId": region_id_int,
                "playCount": play_count_int,
                "created": str(created).strip(),
            }
        )

    if not entries:
        return "暂无游玩地区记录。"

    entries.sort(key=lambda item: item["playCount"], reverse=True)
    length = result.get("length", result.get("Length"))
    try:
        length_int = int(length) if length is not None else len(entries)
    except (TypeError, ValueError):
        length_int = len(entries)
    total_play_count = sum(item["playCount"] for item in entries)

    lines = [
        f"记录地区数: {length_int}",
        f"总游玩次数: {total_play_count}",
        "",
        "🗺️ 游玩地区：",
    ]
    for item in entries:
        created = item["created"]
        created_part = f" · 首次 {created}" if created else ""
        lines.append(
            f"  {format_wahlap_region_name(item['regionId'])} · "
            f"{item['playCount']} 次{created_part}"
        )
    return "\n".join(lines)


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
        retry_count: Optional[int] = None,
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
        if retry_count is None:
            retry_count = int(getattr(maiconfig, "awmc_api_retry_count", 3))
        retry_count = max(0, int(retry_count))
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

    def _b50_upload_timeout(self) -> float:
        return max(
            1.0,
            float(getattr(maiconfig, "awmc_b50_upload_timeout_seconds", 120.0)),
        )

    async def get_user_music(
        self,
        qrcode: str,
        *,
        timeout: Optional[float] = None,
        retry_count: Optional[int] = None,
    ) -> List[dict]:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不提供 PC 全量数据接口")
        # 全量成绩默认 15s 硬超时，与 B50 上传对齐；禁止长重试把 OAuth 上传拖成「一直卡住」。
        # 有新鲜 PC 缓存时上传路径会跳过本接口。
        music_timeout = float(
            timeout
            if timeout is not None
            else getattr(maiconfig, "awmc_user_music_timeout_seconds", 15.0)
        )
        music_retries = (
            retry_count
            if retry_count is not None
            else int(getattr(maiconfig, "awmc_user_music_retry_count", 0))
        )
        log.info(
            f"[SwApi] 开始拉取谱面成绩 timeout={music_timeout:.0f}s "
            f"retry={music_retries}"
        )
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/music",
            json_body=self._machine_body(qrcode),
            timeout=music_timeout,
            retry_count=music_retries,
        )
        payload = self._parse_envelope(data)
        detail_list = self.flatten_user_music(payload)
        log.info(f"[SwApi] 拉取谱面成绩完成，共 {len(detail_list)} 条")
        return detail_list

    async def update_fish(self, qrcode: str, token: str) -> dict:
        # B50 生成偶尔较慢，允许 120s，但仍禁止自动重试造成重复提交。
        upload_timeout = self._b50_upload_timeout()
        if self.api_mode == "public":
            return await self._request(
                "POST",
                "/v1/upload_b50",
                params={"qr_text": qrcode, "fish_token": token},
                timeout=upload_timeout,
                retry_count=0,
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-fish",
            json_body=self._machine_body(qrcode, token=token),
            timeout=upload_timeout,
            retry_count=0,
        )
        return self._parse_envelope(data)

    async def update_lx(self, qrcode: str, import_token: str) -> dict:
        # 兼容 Token 备选路径；允许 120s，但保持零重试避免重复提交。
        upload_timeout = self._b50_upload_timeout()
        if self.api_mode == "public":
            return await self._request(
                "POST",
                "/v1/upload_lx_b50",
                params={"qr_text": qrcode, "lxns_code": import_token},
                timeout=upload_timeout,
                retry_count=0,
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/update-lx",
            json_body=self._machine_body(qrcode, key=import_token, type="maimai"),
            timeout=upload_timeout,
            retry_count=0,
        )
        return self._parse_envelope(data)

    async def get_upload_task(self, task_id: str, *, lxns: bool = False) -> dict:
        """查询公共网关异步上传任务；team 模式上传为同步，无需调用。"""
        if self.api_mode != "public":
            raise SwApiError("team 模式没有异步上传任务")
        path = "/v1/get_lx_b50_task_byid" if lxns else "/v1/get_b50_task_byid"
        # 单次查询超时需远小于轮询总预算，避免一次卡住耗尽 15s。
        return await self._request(
            "GET", path, params={"task_id": task_id}, timeout=5, retry_count=0
        )

    async def charge_ticket(self, qrcode: str, charge_id: int) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持发放票券")
        # /charge 是异步入队接口。保留 code/msg 原始信封，不能把 code=0 的
        # 文本 msg 解析成 {"raw": ...}，否则调用方无法区分入队成功与最终到账。
        return await self._request(
            "POST",
            "/awmc/api/v1/charge",
            json_body=self._machine_body(qrcode, charge=charge_id),
            timeout=60,
            retry_count=0,
        )

    async def get_user_charge(self, qrcode: str) -> dict:
        if self.api_mode == "public":
            raise SwApiError("AWMC 公共网关暂不支持查询票券")
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/charge",
            # 与 maibot 新版 swQrBody 保持一致；只读查询不传 region/place。
            json_body={"qrcode": qrcode, "keychip": maiconfig.sdgbt_client_id},
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
        # 上传前验码也会走这里；显式短超时，避免沿用默认 120s×重试。
        if self.api_mode == "public":
            return await self._request(
                "GET",
                "/v1/get_preview",
                params={"qr_text": qrcode},
                timeout=15,
                retry_count=0,
            )
        data = await self._request(
            "POST",
            "/awmc/api/v1/user/data",
            json_body={"qrcode": qrcode, "keychip": maiconfig.sdgbt_client_id},
            timeout=15,
            retry_count=0,
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
