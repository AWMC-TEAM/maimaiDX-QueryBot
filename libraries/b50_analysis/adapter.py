from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from ..maimaidx_best_50 import _music_is_new
from ..maimaidx_datasource import get_user_b50, get_user_records
from ..maimaidx_model import ChartInfo, PlayInfoDev, UserInfo
from ..maimaidx_music import mai


def _chart_to_dict(chart: ChartInfo) -> dict:
    return {
        'song_id': chart.song_id,
        'music_id': chart.song_id,
        'title': chart.title or '',
        'type': chart.type or 'SD',
        'level': chart.level or '',
        'level_index': chart.level_index,
        'level_label': getattr(chart, 'level_label', None) or chart.level or '',
        'ds': chart.ds,
        'achievements': chart.achievements,
        'achievement': chart.achievements,
        'ra': int(chart.ra or 0),
        'fc': chart.fc or '',
        'fs': chart.fs or '',
        'rate': chart.rate or '',
    }


def _record_to_dict(record: PlayInfoDev) -> dict:
    return {
        'song_id': record.song_id,
        'music_id': record.song_id,
        'title': record.title or '',
        'type': record.type or 'SD',
        'level': record.level or '',
        'level_index': record.level_index,
        'level_label': getattr(record, 'level_label', None) or record.level or '',
        'ds': record.ds,
        'achievements': record.achievements,
        'achievement': record.achievements,
        'ra': int(record.ra or 0),
        'fc': record.fc or '',
        'fs': record.fs or '',
        'rate': record.rate or '',
    }


def _chart_key(chart: dict) -> Tuple[str, int]:
    return (
        str(chart.get('song_id') or chart.get('music_id') or ''),
        int(chart.get('level_index') or 0),
    )


def _is_new_song(music_id: str) -> bool:
    music = mai.total_list.by_id(str(music_id))
    if not music:
        return False
    return _music_is_new(music)


def _split_records(records: List[PlayInfoDev]) -> tuple[List[dict], List[dict]]:
    old, new = [], []
    for record in records:
        item = _record_to_dict(record)
        if _is_new_song(str(record.song_id)):
            new.append(item)
        else:
            old.append(item)
    old.sort(key=lambda x: int(x.get('ra') or 0), reverse=True)
    new.sort(key=lambda x: int(x.get('ra') or 0), reverse=True)
    return old, new


def userinfo_to_b50_dict(userinfo: UserInfo) -> dict:
    sd: List[dict] = []
    dx: List[dict] = []
    if userinfo.charts:
        if userinfo.charts.sd:
            sd = [_chart_to_dict(c) for c in userinfo.charts.sd]
        if userinfo.charts.dx:
            dx = [_chart_to_dict(c) for c in userinfo.charts.dx]
    return {
        'nickname': str(userinfo.nickname or 'Player'),
        'rating': int(userinfo.rating or 0),
        'charts': {'sd': sd, 'dx': dx},
    }


def _merge_push_pool(
    b50_sd: List[dict],
    b50_dx: List[dict],
    records: List[PlayInfoDev],
) -> Dict[str, List[dict]]:
    """B50 区用查分器分组结果，其余全量成绩供推分候选。"""
    b50_keys: Set[Tuple[str, int]] = {_chart_key(c) for c in b50_sd + b50_dx}
    pool_sd = list(b50_sd)
    pool_dx = list(b50_dx)
    old, new = _split_records(records)
    for item in old:
        if _chart_key(item) not in b50_keys:
            pool_sd.append(item)
    for item in new:
        if _chart_key(item) not in b50_keys:
            pool_dx.append(item)
    return {'sd': pool_sd, 'dx': pool_dx}


async def fetch_for_analysis(qqid: int, *, assets_path: str = '') -> dict:
    """
    拉取 B50 分析数据：B35/B15 以 get_user_b50（含 PRiSM PLUS 重分组）为准；
    有水鱼 dev 成绩时追加非 B50 谱面供推分候选。
    """
    userinfo = await get_user_b50(qqid=qqid)
    result = userinfo_to_b50_dict(userinfo)
    result['_assets_path'] = assets_path

    try:
        _, records = await get_user_records(qqid=qqid)
        if records:
            b50_sd = result['charts']['sd']
            b50_dx = result['charts']['dx']
            result['charts'] = _merge_push_pool(b50_sd, b50_dx, records)
    except Exception:
        pass
    return result
