"""将 AWMC 机台成绩（playcount.db）同步到玩家缓存，供 b50 等指令使用。"""

from __future__ import annotations

import time
from typing import List

from ..config import diffs, log
from .maimaidx_best_50 import computeRa, filter_utage_records, regroup_b50_userinfo
from .maimaidx_b50_pipeline import _build_userinfo, _group_records, _recalculate_rating
from .maimaidx_datasource import get_user_source
from .maimaidx_model import PlayInfoDev, UserInfo
from .maimaidx_music import mai
from .maimaidx_playcount_db import PlayCountRecord, pc_db
from .maimaidx_player_cache import player_cache_db, save_cached_player


def pc_record_to_playinfo_dev(record: PlayCountRecord) -> PlayInfoDev:
    """将机台成绩记录转换为 PlayInfoDev。"""
    song_id = record.song_id
    level_index = max(0, min(4, record.level_index))
    achievements = float(record.achievements or 0)
    rate = record.rate or ''
    fc = record.fc or ''
    fs = record.fs or ''
    title = record.title or ''
    type_ = 'SD'
    level = record.level or ''
    ds = float(record.dx_rating or 0)
    dx_score = int(record.dx_score or 0)
    ra = 0

    try:
        music = mai.total_list.by_id(str(song_id))
        if music and level_index < len(music.ds):
            title = music.title
            type_ = music.type
            level = music.level[level_index] if level_index < len(music.level) else level
            ds = round(float(music.ds[level_index]), 1)
    except Exception:
        pass

    if ds > 0 and achievements >= 0:
        computed = computeRa(ds, achievements, israte=True)
        if isinstance(computed, tuple):
            ra, rate_calc = computed
            if not rate:
                rate = rate_calc

    level_label = diffs[level_index] if level_index < len(diffs) else level
    return PlayInfoDev(
        song_id=song_id,
        title=title,
        level=level,
        level_label=level_label,
        level_index=level_index,
        achievements=achievements,
        fc=fc,
        fs=fs,
        type=type_,
        ds=ds,
        dxScore=dx_score,
        ra=ra,
        rate=rate,
    )


def sync_awmc_scores_to_player_cache(qqid: int) -> int:
    """
    将 AWMC 本地机台成绩写入玩家缓存（含全量 records + B50 charts）。

    Returns:
        写入的有效成绩条数；无成绩数据时返回 0。
    """
    pc_records = pc_db.get_user_play_counts(qqid)
    if not pc_records:
        return 0

    playinfo = [pc_record_to_playinfo_dev(r) for r in pc_records if float(r.achievements or 0) > 0]
    if not playinfo:
        log.info(f'[AwmcCache] qq={qqid} 无有效达成率成绩，跳过玩家缓存同步')
        return 0

    playinfo = filter_utage_records(playinfo)
    if not playinfo:
        return 0

    playinfo = _recalculate_rating(playinfo)
    playinfo.sort(key=lambda x: -x.ra)
    b35, b15 = _group_records(playinfo, by_group=True)

    source = get_user_source(qqid)
    prev = player_cache_db.get(qqid, None, source, 10**9)
    nickname = str(qqid)
    plate = ''
    additional_rating = 0
    if prev is not None:
        nickname = prev.userinfo.nickname or prev.userinfo.username or nickname
        plate = prev.userinfo.plate or ''
        additional_rating = (
            prev.userinfo.additional_rating
            if prev.userinfo.additional_rating is not None
            else 0
        )

    base_userinfo = UserInfo(
        nickname=nickname,
        plate=plate,
        additional_rating=additional_rating,
        rating=0,
        username=nickname,
        charts=None,
    )
    userinfo = regroup_b50_userinfo(_build_userinfo(base_userinfo, b35, b15))
    save_cached_player(qqid, None, source, userinfo, playinfo)

    fetched_at = max(float(r.updated_at or 0) for r in pc_records) or time.time()
    from .maimaidx_player_cache import _set_fetch_meta

    _set_fetch_meta(fetched_at, 'awmc_local')
    log.info(
        f'[AwmcCache] qq={qqid} 已同步 {len(playinfo)} 条成绩到玩家缓存 '
        f'(B35={len(b35)} B15={len(b15)})'
    )
    return len(playinfo)
