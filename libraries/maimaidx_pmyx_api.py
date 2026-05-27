# 谱面印象 API 客户端（对接 API_谱面印象使用说明.md）
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger as log


class PmyxAPI:
    """谱面印象 API：获取/上传印象、点赞、回复。"""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        if path.startswith("/"):
            return self.base_url + path
        return self.base_url + "/" + path

    async def get_impressions(self, music_id: str) -> List[Dict[str, Any]]:
        """GET /api/getpmyx?musicId=xxx 获取谱面印象列表。"""
        url = self._url("/api/getpmyx")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(url, params={"musicId": music_id})
                if r.status_code != 200:
                    log.warning(f'[maimai] 谱面印象 API 非 200 id={music_id} status={r.status_code} url={url}')
                    return []
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    if "error" in data:
                        log.debug(f'[maimai] 谱面印象 API 返回错误 id={music_id} error={data.get("error")}')
                        return []
                    # The response is a dict, let's find the list of impressions within it.
                    for key in ['data', 'impressions', 'result']:
                        if key in data and isinstance(data[key], list):
                            return data[key]
                    for value in data.values():
                        if isinstance(value, list):
                            return value
                
                log.warning(f'[maimai] 谱面印象 API 返回非列表或无法解析的字典 id={music_id} type={type(data).__name__}')
                return []
        except Exception as e:
            log.warning(f'[maimai] 谱面印象请求异常 id={music_id} url={url} err={type(e).__name__}: {e}')
            raise

    async def update_impression(
        self,
        qq_id: int,
        nickname: str,
        song_id: int,
        difficulty: int,
        impression_text: str = "",
        rating: int = 0,
        total_achievement: int = 0,
        total_play_count: int = 0,
        admiration: int = 0,
        replies: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """POST /api/updatepmyx 上传/更新谱面印象。"""
        payload = {
            "qqId": qq_id,
            "rating": rating,
            "nickname": nickname,
            "songId": song_id,
            "difficulty": difficulty,
            "totalAchievement": total_achievement,
            "totalPlayCount": total_play_count,
            "impressionText": impression_text,
            "admiration": admiration,
            "replies": replies or [],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                self._url("/api/updatepmyx"),
                json=payload,
            )
            return r.json() if r.content else {"returnCode": -1, "message": "无响应"}

    async def update_admiration(
        self,
        music_id: int,
        comment_id: int,
        new_admiration: int,
    ) -> Dict[str, Any]:
        """POST /api/updateAdmiration 更新点赞数。"""
        payload = {
            "musicId": music_id,
            "commentId": comment_id,
            "newAdmiration": new_admiration,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                self._url("/api/updateAdmiration"),
                json=payload,
            )
            return r.json() if r.content else {"returnCode": -1, "message": "无响应"}

    async def add_reply(
        self,
        music_id: int,
        comment_id: int,
        reply_content: str,
        reply_qq_id: int = 0,
        reply_nickname: str = "匿名用户",
    ) -> Dict[str, Any]:
        """POST /api/addReply 添加评论回复。"""
        payload = {
            "musicId": music_id,
            "commentId": comment_id,
            "replyQqId": reply_qq_id,
            "replyNickname": reply_nickname,
            "replyContent": reply_content,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                self._url("/api/addReply"),
                json=payload,
            )
            return r.json() if r.content else {"returnCode": -1, "message": "无响应"}
