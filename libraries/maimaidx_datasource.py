"""
统一数据源层：根据用户的「数据源」设置，从水鱼或落雪获取成绩数据，
对外返回相同的数据结构（UserInfo / UserInfoDev），使各指令可透明切换。

关键限制：
  - 落雪按用户名查询不支持（lxns 无 username 接口），username 查询强制走水鱼。
  - 落雪全量成绩需 OAuth 授权（dev 接口按好友码仅返回简化成绩，无达成率）。
  - 拟合难度（fit_diff）、全服 rating 排行为水鱼独有，相关功能不切换。
"""

from typing import List, Optional, Tuple

from ..config import log
from . import maimaidx_timing as _timing
from .maimaidx_api_data import maiApi
from .maimaidx_error import LxnsDataError
from .maimaidx_lxns_client import (
    dev_get_bests,
    dev_get_player_by_qq,
    user_get_bests,
    user_get_player,
    user_get_scores,
)
from .maimaidx_lxns_db import lxns_db
from .maimaidx_model import ChartInfo, Data, PlayInfoDev, UserInfo
from .maimaidx_music import mai

_LEVEL_LABELS = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:Master']


def get_user_source(qqid: int) -> str:
    """获取用户的数据源偏好：'divingfish' 或 'lxns'。"""
    try:
        return lxns_db.get_source(qqid)
    except Exception:
        return 'divingfish'


# 不支持落雪的功能名 -> 用于提示文案
def lxns_unsupported_notice(qqid: Optional[int], feature: str = '该功能') -> str:
    """
    若用户数据源为落雪，返回提示文案（说明该功能仅支持水鱼，已用水鱼数据）；
    否则返回空字符串。
    """
    if qqid and get_user_source(qqid) == 'lxns':
        return f'\n[提示] {feature}依赖水鱼独有数据，落雪暂不支持，已使用水鱼数据生成。'
    return ''


def _import_compute_ra():
    """延迟导入 computeRa，避免循环导入。"""
    from .maimaidx_best_50 import computeRa
    return computeRa


def _resolve_local_music(lxns_id: int, lxns_type: str):
    """
    把落雪的 (id, type) 映射到本地（水鱼）曲目。

    落雪约定：标准/DX 谱面共用同一基础 ID（不带 +10000 偏移）。
    水鱼约定：DX 谱面 ID = 基础 ID + 10000。

    返回 (music, local_id_str)；找不到返回 (None, None)。
    """
    is_dx = lxns_type == 'dx'
    candidates = []
    if is_dx:
        # 水鱼 DX 谱面 ID 通常为 基础ID + 10000
        candidates.append(str(lxns_id + 10000))
        candidates.append(str(lxns_id))
    else:
        candidates.append(str(lxns_id))
        candidates.append(str(lxns_id + 10000))

    want_type = 'DX' if is_dx else 'SD'
    # 优先匹配 id 且 type 一致
    for cid in candidates:
        m = mai.total_list.by_id(cid)
        if m and getattr(m, 'type', '').upper() == want_type:
            return m, cid
    # 退而求其次：id 命中即可（type 可能缺失）
    for cid in candidates:
        m = mai.total_list.by_id(cid)
        if m:
            return m, cid
    return None, None


def _lxns_score_to_chartinfo(score: dict):
    """将 lxns score dict 转换为 ChartInfo（也兼容 PlayInfoDev，字段一致）。"""
    computeRa = _import_compute_ra()
    lxns_id = int(score['id'])
    level_index = score.get('level_index', 0)
    achievements = score.get('achievements', 0.0)
    lxns_type = score.get('type', 'standard')
    song_type = 'DX' if lxns_type == 'dx' else 'SD'

    music, local_id = _resolve_local_music(lxns_id, lxns_type)
    if music is None:
        return None

    song_id = int(local_id)

    if level_index < len(music.ds):
        ds = round(float(music.ds[level_index]), 1)
        level_str = music.level[level_index] if level_index < len(music.level) else score.get('level', '')
        title = music.title
        level_label = _LEVEL_LABELS[min(level_index, 4)]
    else:
        ds = float(str(score.get('level', '0')).replace('+', '.5'))
        level_str = score.get('level', '')
        title = score.get('song_name', '')
        level_label = score.get('level', '')

    fc = (score.get('fc') or '').lower()
    fs = (score.get('fs') or '').lower()

    # rate 统一由 computeRa 计算，保证与渲染所需格式一致（如 Sp/SSp/SSSp）
    ra, rate = computeRa(ds, achievements, israte=True)

    return dict(
        song_id=song_id,
        level=level_str,
        level_index=level_index,
        ds=ds,
        ra=ra,
        rate=rate,
        achievements=achievements,
        fc=fc,
        fs=fs,
        dxScore=score.get('dx_score', 0),
        title=title,
        type=song_type,
        level_label=level_label,
    )


def lxns_bests_to_userinfo(bests: dict, nickname: str = '', rating: int = 0) -> UserInfo:
    """将 lxns Best50 响应转换为本地 UserInfo。"""
    sd_raw = [_lxns_score_to_chartinfo(s) for s in (bests.get('standard') or [])]
    dx_raw = [_lxns_score_to_chartinfo(s) for s in (bests.get('dx') or [])]
    sd_list = [ChartInfo(**c) for c in sd_raw if c is not None]
    dx_list = [ChartInfo(**c) for c in dx_raw if c is not None]

    sd_list.sort(key=lambda x: -x.ra)
    dx_list.sort(key=lambda x: -x.ra)

    return UserInfo(
        nickname=nickname or '落雪用户',
        rating=rating,
        additional_rating=0,
        username=nickname or '',
        charts=Data(sd=sd_list, dx=dx_list),
    )


def lxns_scores_to_records(scores: list) -> List[PlayInfoDev]:
    """将 lxns 全量成绩列表转换为 PlayInfoDev 列表。"""
    out: List[PlayInfoDev] = []
    for s in (scores or []):
        c = _lxns_score_to_chartinfo(s)
        if c is not None:
            out.append(PlayInfoDev(**c))
    return out


# ─────────────────────────── 落雪取数（OAuth 优先，dev 兜底） ───────────────────────────


async def _lxns_get_bests_and_player(qqid: int) -> Tuple[Optional[dict], str, int, bool]:
    """
    返回 (bests, nickname, rating, via_oauth)。
    via_oauth 标记是否走了 OAuth（决定能否拿全量成绩）。
    """
    from ..command.mai_lxns import _get_valid_access_token  # 延迟导入避免循环

    nickname, rating = '', 0
    access_token = await _get_valid_access_token(qqid)
    if access_token:
        try:
            with _timing.measure('fetch'):
                bests = await user_get_bests(access_token)
                player = await user_get_player(access_token)
            if player:
                nickname = player.get('name', '')
                rating = player.get('rating', 0)
            return bests, nickname, rating, True
        except Exception as e:
            log.warning(f'[datasource] lxns OAuth bests failed qq={qqid}: {e}')

    # dev 兜底（按 QQ）
    from ..config import maiconfig
    if not maiconfig.lxns_dev_token:
        return None, nickname, rating, False
    with _timing.measure('fetch'):
        player_info = await dev_get_player_by_qq(qqid)
        if not player_info:
            return None, nickname, rating, False
        fc = player_info.get('friend_code')
        nickname = player_info.get('name', '')
        rating = player_info.get('rating', 0)
        bests = await dev_get_bests(fc) if fc else None
    return bests, nickname, rating, False


# ─────────────────────────── 统一对外接口 ───────────────────────────


async def get_user_b50(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
    *,
    force_source: Optional[str] = None,
) -> UserInfo:
    """
    获取用户 b50（UserInfo）。根据数据源偏好选择水鱼/落雪。
    username 查询强制走水鱼（落雪无此接口）。

    Raises:
        LxnsDataError: 落雪数据获取失败
        以及水鱼的 UserNotFoundError 等
    """
    source = force_source or (get_user_source(qqid) if qqid and not username else 'divingfish')

    if source == 'lxns' and qqid and not username:
        bests, nickname, rating, _ = await _lxns_get_bests_and_player(qqid)
        if not bests:
            raise LxnsDataError(
                '落雪数据获取失败，请先绑定落雪查分器：发送 lxbind\n'
                '或切换回水鱼数据源：数据源 水鱼'
            )
        return lxns_bests_to_userinfo(bests, nickname=nickname, rating=rating)

    return await maiApi.query_user_b50(qqid=qqid, username=username)


async def get_user_b50_or_fallback(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
) -> UserInfo:
    """
    获取用户 b50，落雪失败时自动降级到水鱼（不抛异常）。
    用于合作 b50 等需要"尽量不要因为一方失败而整体失败"的场景。
    """
    try:
        return await get_user_b50(qqid=qqid, username=username)
    except LxnsDataError:
        log.warning(f'[datasource] lxns fallback to divingfish for qq={qqid}')
        return await maiApi.query_user_b50(qqid=qqid, username=username)


async def get_user_records(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
    *,
    force_source: Optional[str] = None,
) -> Tuple[UserInfo, List[PlayInfoDev]]:
    """
    获取用户基础信息 + 全量成绩。根据数据源偏好选择水鱼/落雪。
    落雪全量成绩需 OAuth 授权。username 查询强制走水鱼。

    Returns:
        (userinfo, records)
    Raises:
        LxnsDataError: 落雪数据获取失败 / 未授权
        以及水鱼的 UserNotFoundError 等
    """
    source = force_source or (get_user_source(qqid) if qqid and not username else 'divingfish')

    if source == 'lxns' and qqid and not username:
        from ..command.mai_lxns import _get_valid_access_token
        access_token = await _get_valid_access_token(qqid)
        if not access_token:
            raise LxnsDataError(
                '落雪全量成绩需要 OAuth 授权，请先发送 lxbind 绑定落雪查分器\n'
                '或切换回水鱼数据源：数据源 水鱼'
            )
        try:
            with _timing.measure('fetch'):
                scores = await user_get_scores(access_token)
                player = await user_get_player(access_token)
        except Exception as e:
            log.warning(f'[datasource] lxns OAuth scores failed qq={qqid}: {e}')
            raise LxnsDataError(f'落雪成绩获取失败：{e}')
        nickname = player.get('name', '') if player else ''
        rating = player.get('rating', 0) if player else 0
        userinfo = UserInfo(
            nickname=nickname or '落雪用户',
            rating=rating,
            additional_rating=0,
            username=nickname or '',
            charts=None,
        )
        records = lxns_scores_to_records(scores)
        return userinfo, records

    # 水鱼
    if username:
        qqid = None
    userinfo = await maiApi.query_user_b50(qqid=qqid, username=username)
    dev = await maiApi.query_user_get_dev(qqid=qqid, username=username)
    records = list(dev.records or [])
    return userinfo, records
