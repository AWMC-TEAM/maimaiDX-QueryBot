from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..maimaidx_datasource import get_user_b50, get_user_records
from ..maimaidx_model import ChartInfo, PlayInfoDev, UserInfo
from ..maimaidx_music import mai

_NEW_VERSION_POOL = {
    'maimai でらっくす PRiSM',
    'maimai でらっくす BUDDiES PLUS',
}


def _chart_to_dict(chart: ChartInfo) -> dict:
    data = chart.model_dump(by_alias=True)
    data['song_id'] = chart.song_id
    data['music_id'] = chart.song_id
    data['achievements'] = chart.achievements
    data['achievement'] = chart.achievements
    return data


def _record_to_dict(record: PlayInfoDev) -> dict:
    data = record.model_dump(by_alias=True)
    data['song_id'] = record.song_id
    data['music_id'] = record.song_id
    data['achievements'] = record.achievements
    data['achievement'] = record.achievements
    return data


def _music_lookup() -> Dict[str, dict]:
    lookup: Dict[str, dict] = {}
    for music in mai.total_list:
        version = music.basic_info.version
        lookup[str(music.id)] = {
            'basic_info': {'from': version},
            'from': version,
        }
    return lookup


def _is_new(music_id: str, lookup: Dict[str, dict]) -> bool:
    m = lookup.get(music_id)
    if not m:
        return False
    version = str(m.get('basic_info', {}).get('from', '') or m.get('from', ''))
    return version in _NEW_VERSION_POOL


def _sort_old_new(
    records: List[dict],
    lookup: Dict[str, dict],
) -> tuple[List[dict], List[dict]]:
    old, new = [], []
    for c in records:
        mid = str(c.get('song_id') or c.get('music_id') or '')
        if _is_new(mid, lookup):
            new.append(c)
        else:
            old.append(c)
    old.sort(key=lambda x: x.get('ra', 0), reverse=True)
    new.sort(key=lambda x: x.get('ra', 0), reverse=True)
    return old, new


def userinfo_to_b50_dict(userinfo: UserInfo) -> dict:
    qq = 0
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


def records_to_b50_dict(
    nickname: str,
    rating: int,
    records: List[PlayInfoDev],
) -> dict:
    lookup = _music_lookup()
    raw = [_record_to_dict(r) for r in records]
    old, new = _sort_old_new(raw, lookup)
    total_ra = sum(int(c.get('ra') or 0) for c in old + new)
    return {
        'nickname': nickname,
        'rating': rating or total_ra,
        'charts': {'sd': old, 'dx': new},
    }


async def fetch_for_analysis(qqid: int, *, assets_path: str = '') -> dict:
    """
    拉取 B50 分析所需数据：优先全量 dev 成绩（有水鱼 token），否则用 B50。
    """
    userinfo = await get_user_b50(qqid=qqid)
    try:
        info, records = await get_user_records(qqid=qqid)
        if records:
            result = records_to_b50_dict(
                str(info.nickname or userinfo.nickname or f'Player({qqid})'),
                int(info.rating or userinfo.rating or 0),
                records,
            )
            result['_assets_path'] = assets_path
            return result
    except Exception:
        pass
    result = userinfo_to_b50_dict(userinfo)
    result['_assets_path'] = assets_path
    return result
