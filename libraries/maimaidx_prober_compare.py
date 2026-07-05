"""AWMC 机台成绩与查分器（水鱼/落雪）dev 全量成绩对比。"""

from __future__ import annotations

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
    '⚠️ 数据当前存储在AWMC，如果您想要同步到水鱼，请使用maiu指令。'
)
SYNC_WARN_LXNS = (
    '⚠️ 数据当前存储在AWMC，如果您想要同步到落雪，请使用maiul指令。'
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

    storage_qqid: playcount.db 中的 qqid（默认与 score_qqid 相同）
    source: 'divingfish' 或 'lxns'，默认读取用户数据源偏好

    Returns:
        True — 不一致或无法从查分器拉取绑定数据（应提示 maiu/maiul）
        False — 一致，或发生临时网络错误（不提示）
    """
    storage_qqid = storage_qqid if storage_qqid is not None else score_qqid
    source = source or get_user_source(score_qqid)

    awmc_records = pc_db.get_user_play_counts(storage_qqid)
    if not awmc_records:
        return True

    try:
        _, prober_records = await get_user_records(
            qqid=score_qqid,
            force_source=source,
            force_refresh=True,
        )
    except (UserNotFoundError, LxnsDataError) as e:
        log.info(f'[ProberCompare] qq={score_qqid} source={source} 查分器不可用: {e}')
        return True
    except Exception as e:
        log.warning(f'[ProberCompare] qq={score_qqid} source={source} 对比失败: {e}')
        return False

    prober_map = _build_prober_map(prober_records)
    return _records_differ(awmc_records, prober_map)
