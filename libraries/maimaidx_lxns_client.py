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

from ..config import log, maiconfig

_BASE_URL = 'https://maimai.lxns.net'
# 落雪「无回调模式」标准 OOB 地址（授权后直接在页面显示授权码）
_DEFAULT_REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'


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
        resp.raise_for_status()
        result = resp.json()
        if not result.get('success'):
            raise ValueError(result.get('message', 'OAuth token exchange failed'))
        return result['data']


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
        resp.raise_for_status()
        result = resp.json()
        if not result.get('success'):
            raise ValueError(result.get('message', 'OAuth token refresh failed'))
        return result['data']


# ─────────────────────────── 开发者 API ───────────────────────────


async def _billable_lxns_fetch(coro):
    """落雪成绩/玩家 API：在 break_billing 上下文中扣费。"""
    from .maimaidx_break import ensure_query_affordable, get_billing_qqid, settle_prober_fetch

    qqid = get_billing_qqid()
    if qqid:
        ensure_query_affordable(qqid)
    result = await coro
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
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                raise ValueError(result.get('message', '获取 b50 失败'))
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
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                raise ValueError(result.get('message', '获取用户信息失败'))
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
            resp.raise_for_status()
            result = resp.json()
            if not result.get('success'):
                raise ValueError(result.get('message', '获取成绩失败'))
            return result.get('data')

    return await _billable_lxns_fetch(_fetch())


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
