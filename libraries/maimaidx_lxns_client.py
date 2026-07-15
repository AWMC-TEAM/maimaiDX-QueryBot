"""
落雪查分器（Lxns / maimai.lxns.net）API 客户端。

支持两种查询方式：
  - 开发者 Token（查曲库 / 别名 / 按 QQ 或好友码查别人）
  - OAuth2 用户授权（查自己的 b50 / recent / scores 等私有数据）
"""

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from ..config import maiconfig

_BASE_URL = 'https://maimai.lxns.net'
# 落雪「无回调模式」标准 OOB 地址（授权后直接在页面显示授权码）
_DEFAULT_REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'


class LxnsApiError(RuntimeError):
    """落雪 API 错误；仅保留可安全展示的状态码与服务端说明。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _error_message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    return str(
        payload.get('error_description')
        or payload.get('message')
        or payload.get('error')
        or fallback
    )


def _parse_oauth_token_response(
    response: httpx.Response, *, operation: str
) -> Dict[str, Any]:
    """兼容 OAuth 标准顶层响应与旧版 ``success/data`` 包装。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise LxnsApiError(
            f'{operation}响应不是有效 JSON', status_code=response.status_code
        ) from exc

    if response.is_error:
        raise LxnsApiError(
            _error_message(payload, f'{operation}失败'),
            status_code=response.status_code,
        )

    token_data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(token_data, dict) or not token_data.get('access_token'):
        token_data = payload
    if not isinstance(token_data, dict) or not token_data.get('access_token'):
        raise LxnsApiError(
            _error_message(payload, f'{operation}未返回 access_token'),
            status_code=response.status_code,
        )
    return token_data


def _parse_user_api_response(
    response: httpx.Response, *, operation: str
) -> Dict[str, Any]:
    """解析落雪 OAuth 用户 API 的统一响应，并保留明确失败原因。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise LxnsApiError(
            f'{operation}响应不是有效 JSON', status_code=response.status_code
        ) from exc
    if response.is_error or not isinstance(payload, dict) or payload.get('success') is False:
        raise LxnsApiError(
            _error_message(payload, f'{operation}失败'),
            status_code=response.status_code,
        )
    return payload


def _dev_headers() -> Dict[str, str]:
    """开发者 Token 请求头。"""
    return {'Authorization': maiconfig.lxns_dev_token or ''}


def _oauth_headers(access_token: str) -> Dict[str, str]:
    """OAuth 用户请求头。"""
    return {'Authorization': f'Bearer {access_token}'}


# ─────────────────────────── OAuth ───────────────────────────


def get_authorize_url(client_id: str, scope: str = 'read_player read_user_profile write_player') -> str:
    """生成 OAuth 授权链接。无回调模式使用 OOB 地址，授权后页面直接显示授权码。"""
    redirect_uri = maiconfig.lx_redirect_uri or _DEFAULT_REDIRECT_URI
    query = urlencode({
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': scope,
    })
    return f'{_BASE_URL}/oauth/authorize?{query}'


async def fetch_token(code: str) -> Dict[str, Any]:
    """
    用授权码换取 access_token / refresh_token。
    返回 OAuth2Token 字典：access_token, token_type, expires_in, refresh_token, scope
    """
    redirect_uri = maiconfig.lx_redirect_uri or _DEFAULT_REDIRECT_URI
    payload = {
        'client_id': maiconfig.lx_client_id,
        'client_secret': maiconfig.lx_client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f'{_BASE_URL}/api/v0/oauth/token', json=payload)
        return _parse_oauth_token_response(resp, operation='OAuth 授权码兑换')


async def refresh_token(refresh_token: str) -> Dict[str, Any]:
    """
    用 refresh_token 刷新 access_token。
    返回新的 OAuth2Token 字典。
    """
    payload = {
        'client_id': maiconfig.lx_client_id,
        'client_secret': maiconfig.lx_client_secret,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f'{_BASE_URL}/api/v0/oauth/token', json=payload)
        return _parse_oauth_token_response(resp, operation='OAuth Token 刷新')


# ─────────────────────────── 开发者 API ───────────────────────────


async def _billable_lxns_fetch(coro):
    """落雪成绩/玩家 API：在 break_billing 上下文中扣费。"""
    from .maimaidx_break import ensure_query_affordable, get_billing_qqid, settle_prober_fetch
    from .maimaidx_admin_audit import admin_audit

    qqid = get_billing_qqid()
    if qqid:
        ensure_query_affordable(qqid)
    started = time.time()
    try:
        result = await coro
    except Exception as exc:
        admin_audit.add_step(
            'http.lxns', 'error', {'error': str(exc)}, started_at=started,
        )
        raise
    admin_audit.add_step('http.lxns', 'success', started_at=started)
    if qqid and result is not None:
        settle_prober_fetch(qqid)
    return result


async def dev_get_player_by_qq(qq: int) -> Optional[Dict[str, Any]]:
    """通过 QQ 号获取玩家信息（开发者 Token）。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/maimai/player/qq/{qq}',
                headers=_dev_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                return None
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


async def dev_get_player_by_friend_code(friend_code: int) -> Optional[Dict[str, Any]]:
    """通过好友码获取玩家信息（开发者 Token）。"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f'{_BASE_URL}/api/v0/maimai/player/{friend_code}',
            headers=_dev_headers(),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        result = resp.json()
        if not result.get('success'):
            return None
        return result.get('data')


async def dev_get_bests(friend_code: int) -> Optional[Dict[str, Any]]:
    """通过好友码获取玩家 Best50（开发者 Token）。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/maimai/player/{friend_code}/bests',
                headers=_dev_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                return None
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


# ─────────────────────────── 用户 API（OAuth） ───────────────────────────


async def user_get_bests(access_token: str) -> Optional[Dict[str, Any]]:
    """获取当前用户的 Best50（OAuth token）。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/user/maimai/player/bests',
                headers=_oauth_headers(access_token),
            )
            result = _parse_user_api_response(resp, operation='获取落雪 B50')
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


async def user_get_player(access_token: str) -> Optional[Dict[str, Any]]:
    """获取当前用户信息（OAuth token）。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/user/maimai/player',
                headers=_oauth_headers(access_token),
            )
            result = _parse_user_api_response(resp, operation='获取落雪用户信息')
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


async def user_get_scores(access_token: str) -> Optional[list]:
    """获取当前用户所有成绩（OAuth token）。返回 Score 列表。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/user/maimai/player/scores',
                headers=_oauth_headers(access_token),
            )
            result = _parse_user_api_response(resp, operation='获取落雪成绩')
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


def convert_sega_music_scores(detail_list: List[dict]) -> List[dict]:
    """把 ``userMusicDetail`` 转成落雪个人 API 接受的 Score 列表。"""
    combo_map = {0: None, 1: 'fc', 2: 'fcp', 3: 'ap', 4: 'app'}
    sync_map = {0: None, 1: 'fs', 2: 'fsp', 3: 'fsd', 4: 'fsdp', 5: 'sync'}
    best: Dict[tuple[int, str, int], dict] = {}

    for item in detail_list:
        try:
            raw_id = int(item.get('musicId', 0))
            level_index = int(item.get('level', 0))
            achievement = float(item.get('achievement', 0)) / 10000.0
            dx_score = int(item.get('deluxscoreMax', 0) or 0)
        except (TypeError, ValueError):
            continue
        if raw_id <= 0 or not 0 <= level_index <= 4 or achievement < 0:
            continue

        if raw_id > 100000:
            song_id, song_type = raw_id, 'utage'
        elif raw_id >= 10000:
            song_id, song_type = raw_id % 10000, 'dx'
        else:
            song_id, song_type = raw_id, 'standard'
        if song_id <= 0:
            continue

        score = {
            'id': song_id,
            'type': song_type,
            'level_index': level_index,
            'achievements': achievement,
            'fc': combo_map.get(item.get('comboStatus')),
            'fs': sync_map.get(item.get('syncStatus')),
            'dx_score': dx_score,
        }
        key = (song_id, song_type, level_index)
        previous = best.get(key)
        if previous is None or (achievement, dx_score) > (
            previous['achievements'], previous['dx_score']
        ):
            best[key] = score
    return list(best.values())


async def user_upload_scores(access_token: str, scores: List[dict]) -> Dict[str, Any]:
    """使用 OAuth ``write_player`` 权限上传当前用户成绩。"""
    if not scores:
        raise ValueError('没有可上传到落雪的有效成绩')

    from .maimaidx_admin_audit import admin_audit

    uploaded = 0
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # 控制单次请求体大小；成绩接口按谱面覆盖，分批重试是幂等的。
            for offset in range(0, len(scores), 500):
                batch = scores[offset:offset + 500]
                resp = await client.post(
                    f'{_BASE_URL}/api/v0/user/maimai/player/scores',
                    headers=_oauth_headers(access_token),
                    json={'scores': batch},
                )
                _parse_user_api_response(resp, operation='OAuth 成绩上传')
                uploaded += len(batch)
    except Exception as exc:
        admin_audit.add_step(
            'http.lxns.upload', 'error',
            {'error': str(exc), 'uploaded': uploaded}, started_at=started,
        )
        raise
    admin_audit.add_step(
        'http.lxns.upload', 'success', {'count': uploaded}, started_at=started,
    )
    return {'success': True, 'count': uploaded, 'oauth': True}


async def dev_get_scores(friend_code: int) -> Optional[list]:
    """通过好友码获取玩家所有成绩（开发者 Token）。返回 Score 列表。"""
    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'{_BASE_URL}/api/v0/maimai/player/{friend_code}/scores',
                headers=_dev_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                return None
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())
