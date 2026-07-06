"""AWMC 机台成绩与查分器（水鱼/落雪）dev 全量成绩对比。"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from loguru import logger as log

from .maimaidx_datasource import get_user_records, get_user_source
from .maimaidx_error import LxnsDataError, UserNotFoundError
from .maimaidx_model import PlayInfoDev, Source
from .maimaidx_music import mai
from .maimaidx_playcount_db import PlayCountRecord, pc_db

LINK_TITLE = 'Link'
ACH_EPSILON = 1e-4

SYNC_WARN_FISH = (
    '⚠️ 机台成绩与水鱼查分器不一致（b50 以查分器数据为准），'
    '请使用 maiu 指令将最新成绩上传到水鱼。'
)
SYNC_WARN_LXNS = (
    '⚠️ 机台成绩与落雪查分器不一致（b50 以查分器数据为准），'
    '请使用 maiul 指令将最新成绩上传到落雪。'
)


def sync_warning_for_source(source: str) -> str:
    return SYNC_WARN_LXNS if source == Source.LXNS else SYNC_WARN_FISH


def _record_title(record: PlayCountRecord) -> str:
    if record.title:
        return record.title
    music = mai.total_list.by_id(str(record.song_id))
    return music.title if music else ''


def _is_link_record(record: PlayCountRecord) -> bool:
    return _record_title(record) == LINK_TITLE


def _build_prober_map(records: list[PlayInfoDev]) -> Dict[Tuple[int, int], float]:
    out: Dict[Tuple[int, int], float] = {}
    for item in records:
        out[(item.song_id, item.level_index)] = float(item.achievements)
    return out


def _records_differ(
    awmc_records: list[PlayCountRecord],
    prober_map: Dict[Tuple[int, int], float],
) -> bool:
    for record in awmc_records:
        if _is_link_record(record):
            continue
        key = (record.song_id, record.level_index)
        prober_ach = prober_map.get(key)
        if prober_ach is None:
            return True
        if abs(float(record.achievements) - prober_ach) > ACH_EPSILON:
            return True
    return False


async def awmc_differs_from_prober(
    score_qqid: int,
    *,
    storage_qqid: Optional[int] = None,
    source: Optional[str] = None,
) -> bool:
    """
    对比 AWMC 本地成绩与用户查分器 dev 全量成绩是否一致。

    Returns:
        True — 不一致或查分器不可用（应提示 maiu/maiul）
        False — 一致，或临时网络错误（不提示）
    """
    storage_qqid = storage_qqid if storage_qqid is not None else score_qqid
    source = source or get_user_source(score_qqid)

    awmc_records = pc_db.get_user_play_counts(storage_qqid)
    if not awmc_records:
        log.info(f'[ProberCompare] qq={score_qqid} 无 AWMC 本地记录')
        return True

    t0 = time.perf_counter()
    try:
        _, prober_records = await get_user_records(
            qqid=score_qqid,
            force_source=source,
            force_refresh=True,
        )
    except (UserNotFoundError, LxnsDataError) as e:
        log.info(
            f'[ProberCompare] qq={score_qqid} source={source} 查分器不可用 ({time.perf_counter() - t0:.2f}s): {e}'
        )
        return True
    except Exception as e:
        log.warning(
            f'[ProberCompare] qq={score_qqid} source={source} 对比失败 ({time.perf_counter() - t0:.2f}s): {e}'
        )
        return False

    differs = _records_differ(awmc_records, _build_prober_map(prober_records))
    log.info(
        f'[ProberCompare] qq={score_qqid} source={source} differs={differs} '
        f'awmc={len(awmc_records)} prober={len(prober_records)} ({time.perf_counter() - t0:.2f}s)'
    )
    if differs:
        # 查分器落后于机台，用户随后大概率会执行 maiu/maiul（由其他机器人上传）。
        # 上面 force_refresh 拉取的「上传前」数据不能留在缓存里，否则上传完成后
        # 15 分钟内 b50 仍显示旧成绩。清除缓存，让之后的 b50 直接拉查分器最新数据。
        from .maimaidx_player_cache import invalidate_player_cache
        invalidate_player_cache(score_qqid)
    return differs
