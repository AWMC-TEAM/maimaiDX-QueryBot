import copy
import json
import os
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx

from ..config import TAG_DISPLAY_ORDER, TAG_PILL_COLORS, log


def _get_dxrating_token() -> Optional[str]:
    """优先用插件配置，否则读环境变量 MAIMAIDX_DXRATING_TOKEN。若未读到则尝试加载 .env.prod / .env。"""
    t = getattr(maiconfig, 'dxrating_token', None)
    if t:
        return t
    t = os.environ.get('MAIMAIDX_DXRATING_TOKEN')
    if t:
        return t
    try:
        from pathlib import Path
        from dotenv import load_dotenv
        cwd = Path.cwd()
        for name in ('.env.prod', '.env'):
            p = cwd / name
            if p.is_file():
                load_dotenv(p, override=False)
                t = os.environ.get('MAIMAIDX_DXRATING_TOKEN')
                if t:
                    return t
    except Exception:
        pass
    return None


from .image import rounded_corners
from .maimaidx_best_50 import *
from .maimaidx_music import Music, mai


async def get_music_by_alias(alias: str) -> Optional[Music]:
    """
    通过别名获取歌曲信息
    
    Params:
        `alias`: 歌曲别名
    Returns:
        `Music` 对象或 None
    """
    if not alias:
        return None
    
    # 使用别名列表查找歌曲
    aliases = mai.total_alias_list.by_alias(alias)
    if not aliases:
        return None
    
    # 如果找到多个别名匹配，返回第一个匹配的歌曲
    if len(aliases) > 0:
        music_id = str(aliases[0].SongID)
        return mai.total_list.by_id(music_id)
    
    return None


_music_tags_cache: Dict[str, Dict[str, str]] = {}
_tags_file_index: Optional[Dict[str, Dict[str, str]]] = None
_tags_by_difficulty_index: Optional[Dict[tuple, list]] = None
_tags_by_difficulty_and_group: Optional[Dict[tuple, Dict[str, List[str]]]] = None

DIFF_DISPLAY_ORDER = ('BASIC', 'ADVANCED', 'EXPERT', 'MASTER', 'Re:MASTER')
LEVEL_INDEX_TO_SHEET = ('basic', 'advanced', 'expert', 'master', 'remaster')
_SHEET_DIFF_TO_DISPLAY = {'basic': 'BASIC', 'advanced': 'ADVANCED', 'expert': 'EXPERT', 'master': 'MASTER', 'remaster': 'Re:MASTER'}


def _load_tags_from_json() -> Optional[Dict[str, Dict[str, str]]]:
    global _tags_file_index, _tags_by_difficulty_index, _tags_by_difficulty_and_group
    if _tags_file_index is not None:
        return _tags_file_index
    path = getattr(maiconfig, 'dxrating_tags_json_path', None)
    if not path:
        path = Path.cwd() / 'response.json'
    else:
        path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f'[maimai] 谱面标签 JSON 加载失败 path={path} err={e}')
        return None
    tags = data.get('tags') or []
    tag_groups = data.get('tagGroups') or []
    tag_songs = data.get('tagSongs') or []
    group_name_by_id = {g.get('id'): (g.get('localized_name') or {}).get('zh-Hans') or (g.get('localized_name') or {}).get('en') or '' for g in tag_groups if g.get('id') is not None}
    tag_by_id = {}
    for t in tags:
        tid = t.get('id')
        if tid is not None:
            name = (t.get('localized_name') or {}).get('zh-Hans') or (t.get('localized_name') or {}).get('en') or ''
            tag_by_id[tid] = (name, t.get('group_id'))
    from collections import defaultdict
    song_tag_ids = defaultdict(set)
    for s in tag_songs:
        title = s.get('song_id')
        if title is not None:
            song_tag_ids[str(title).strip()].add(s.get('tag_id'))
    index = {}
    for title, tag_ids in song_tag_ids.items():
        out = {}
        for tid in tag_ids:
            if tid not in tag_by_id:
                continue
            name, gid = tag_by_id[tid]
            group_name = group_name_by_id.get(gid) or ''
            if group_name and name and group_name not in out:
                out[group_name] = name
        if out:
            index[title] = out
    _tags_file_index = index
    by_diff = defaultdict(set)
    for s in tag_songs:
        title = s.get('song_id')
        diff = (s.get('sheet_difficulty') or '').strip().lower()
        tid = s.get('tag_id')
        if title is not None and diff and tid in tag_by_id:
            name = tag_by_id[tid][0]
            if name:
                by_diff[(str(title).strip(), diff)].add(name)
    _tags_by_difficulty_index = {k: sorted(v) for k, v in by_diff.items()}
    by_diff_group = {}
    for s in tag_songs:
        title = s.get('song_id')
        diff = (s.get('sheet_difficulty') or '').strip().lower()
        tid = s.get('tag_id')
        if title is None or not diff or tid not in tag_by_id:
            continue
        name, gid = tag_by_id[tid]
        group_name = group_name_by_id.get(gid) or ''
        if not name or not group_name:
            continue
        key = (str(title).strip(), diff)
        if key not in by_diff_group:
            by_diff_group[key] = defaultdict(set)
        by_diff_group[key][group_name].add(name)
    _tags_by_difficulty_and_group = {k: {gk: sorted(gv) for gk, gv in v.items()} for k, v in by_diff_group.items()}
    log.info(f'[maimai] 谱面标签已从本地 JSON 加载 共 {len(index)} 曲')
    return _tags_file_index


def _get_tags_from_file(music_title: str) -> Optional[Dict[str, str]]:
    index = _load_tags_from_json()
    if not index:
        return None
    title = (music_title or '').strip()
    return index.get(title) if title else None


def get_music_tags_by_difficulty(music_id: str) -> Dict[str, List[str]]:
    out = {}
    try:
        music = mai.total_list.by_id(music_id)
    except Exception:
        for k in DIFF_DISPLAY_ORDER:
            out[k] = ['暂无']
        return out
    title = (getattr(music, 'title', None) or '').strip()
    has_remaster = len(getattr(music, 'level', [])) >= 5
    _load_tags_from_json()
    index = _tags_by_difficulty_index
    if not index:
        for k in DIFF_DISPLAY_ORDER:
            if k == 'Re:MASTER' and not has_remaster:
                continue
            out[k] = ['暂无']
        return out
    for display_name in DIFF_DISPLAY_ORDER:
        if display_name == 'Re:MASTER' and not has_remaster:
            continue
        sheet_key = None
        for k, v in _SHEET_DIFF_TO_DISPLAY.items():
            if v == display_name:
                sheet_key = k
                break
        if sheet_key is None:
            continue
        tags = index.get((title, sheet_key))
        out[display_name] = sorted(tags) if tags else ['暂无']
    return out


def get_b50_tag_stats(userinfo) -> Dict[str, Dict[str, int]]:
    """根据 B50 用户数据统计各分组标签出现次数，用于底力分析图。"""
    _load_tags_from_json()
    index = _tags_by_difficulty_and_group
    if not index:
        return {'配置': {}, '难度': {}, '评价': {}}
    counts = defaultdict(lambda: defaultdict(int))
    # 查分器 sd=B35、dx=B15（与谱面类型 SD/DX 无关）
    for chart_list in (getattr(userinfo.charts, 'sd', None) or [], getattr(userinfo.charts, 'dx', None) or []):
        if not chart_list:
            continue
        for chart in chart_list:
            song_id = getattr(chart, 'song_id', None)
            level_index = getattr(chart, 'level_index', 0)
            if song_id is None:
                continue
            music = mai.total_list.by_id(str(song_id))
            if not music:
                continue
            title = (getattr(music, 'title', None) or '').strip()
            if not title:
                continue
            sheet = LEVEL_INDEX_TO_SHEET[min(max(0, level_index), 4)]
            by_group = index.get((title, sheet))
            diff_tags = list(by_group.get('难度', [])) if by_group else []
            if by_group:
                for group_name, tags in by_group.items():
                    for tag in tags:
                        counts[group_name][tag] += 1
            if '水' not in diff_tags and '诈称谱' not in diff_tags:
                counts['难度']['正常谱'] += 1
    return {g: dict(counts[g]) for g in ('配置', '难度', '评价')}


async def build_tags_forward_nodes(
    music_id: str, bot_user_id: int, bot_nickname: str
) -> List[Dict[str, Any]]:
    await get_music_tags(music_id)
    tags_by_diff = get_music_tags_by_difficulty(music_id)
    if not tags_by_diff:
        return []
    music = mai.total_list.by_id(music_id)
    title = getattr(music, 'title', None) or f'ID {music_id}'

    def node(user_id: int, nickname: str, text: str) -> Dict[str, Any]:
        return {
            'type': 'node',
            'data': {
                'user_id': str(user_id),
                'nickname': nickname,
                'content': text,
            },
        }

    # 过滤掉标签为 ['暂无'] 的难度
    has_tags_diffs = {}
    for diff in DIFF_DISPLAY_ORDER:
        if diff not in tags_by_diff:
            continue
        tags = tags_by_diff[diff]
        # 如果标签只有 '暂无'，则跳过
        if tags == ['暂无']:
            continue
        has_tags_diffs[diff] = tags

    if not has_tags_diffs:
        return []

    intro = f'这是 {title} 所有谱面相关的标签'
    nodes: List[Dict[str, Any]] = [node(bot_user_id, bot_nickname, intro)]
    for diff, tags in has_tags_diffs.items():
        nodes.append(node(bot_user_id, bot_nickname, f'{diff}：{" ".join(tags)}'))
    return nodes


async def get_music_tags(music_id: str) -> Optional[Dict[str, str]]:
    sid = str(music_id)
    if sid in _music_tags_cache:
        return _music_tags_cache[sid]
    tags = None
    try:
        music = mai.total_list.by_id(music_id)
        if music and getattr(music, 'title', None):
            tags = _get_tags_from_file(music.title)
    except Exception:
        pass
    if not tags:
        tags = await fetch_combined_tags(music_id)
    if tags:
        _music_tags_cache[sid] = tags
    return tags


async def fetch_combined_tags(music_id: str) -> Optional[Dict[str, str]]:
    token = _get_dxrating_token()
    url = getattr(maiconfig, 'dxrating_combined_tags_url', None) or 'https://derrakuma.dxrating.net/functions/v1/combined-tags'
    if not token:
        log.opt(lazy=True).info(
            lambda: '[maimai] 谱面标签未显示: 未配置 dxrating_token（请在 .env 设置 MAIMAIDX_DXRATING_TOKEN 或插件配置 dxrating_token）'
        )
        return None
    try:
        id_num = int(music_id) if str(music_id).strip().isdigit() else music_id
    except (ValueError, TypeError):
        id_num = music_id
    payload = {'ids': [id_num]}
    headers = {
        'Authorization': f'Bearer {token}',
        'Origin': 'https://dxrating.net',
        'Content-Type': 'application/json',
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:
        log.warning(f'[maimai] 谱面标签请求失败 id={music_id} err={type(e).__name__}: {e}')
        return None
    if resp.status_code != 200:
        log.info(f'[maimai] 谱面标签 API 非 200 id={music_id} status={resp.status_code} body={resp.text[:200]}')
        return None
    try:
        data = resp.json()
    except Exception as e:
        log.warning(f'[maimai] 谱面标签响应非 JSON id={music_id} err={e}')
        return None
    sid = str(music_id)
    raw = None
    if isinstance(data, dict):
        raw = data.get(sid) or data.get(id_num)
        if raw is None and 'data' in data:
            raw = data['data'].get(sid) or data['data'].get(id_num)
        if raw is None:
            keys_preview = list(data.keys())[:12]
            log.info(f'[maimai] 谱面标签 响应中无本曲 id={sid} 响应顶层 keys={keys_preview}')
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            mid = item.get('id') or item.get('song_id') or item.get('music_id') or item.get('sid')
            if str(mid) == sid:
                raw = item.get('tags') or item.get('combined_tags') or item
                break
        if raw is None:
            log.info(f'[maimai] 谱面标签 响应为 list 但未找到 id={sid} 的项')
    if raw is None:
        return None
    if isinstance(raw, dict) and not any(k in raw for k in ('tag_category', 'tag_name', 'category', 'tag')):
        out = {k: str(v) for k, v in raw.items() if v}
        if out:
            log.info(f'[maimai] 谱面标签 已获取 id={sid} 标签={list(out.keys())}')
        return out if out else None
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            cat = item.get('tag_category') or item.get('category')
            name = item.get('tag_name') or item.get('name') or item.get('tag')
            if cat and name:
                out[str(cat)] = str(name)
        if out:
            log.info(f'[maimai] 谱面标签 已获取 id={sid} 标签={list(out.keys())}')
        return out if out else None
    return None


def newbestscore(song_id: str, lv: int, value: int, bestlist: List[ChartInfo]) -> int:
    for v in bestlist:
        if song_id == str(v.song_id) and lv == v.level_index:
            if value >= v.ra:
                return value - v.ra
            else:
                return 0
    return value - bestlist[-1].ra


async def draw_music_info(
    music: Music, 
    qqid: Optional[int] = None, 
    user: Optional[UserInfo] = None
) -> MessageSegment:
    """
    查看谱面
    
    Params:
        `music`: 曲目模型
        `qqid`: QQID
        `user`: 用户模型
    Returns:
        `MessageSegment`
    """
    calc = True
    isfull = True
    bestlist: List[ChartInfo] = []
    try:
        if qqid:
            if user is None:
                player = await maiApi.query_user_b50(qqid=qqid)
            else:
                player = user
            # bestlist: 查分器 B15(dx) 或 B35(sd) 成绩列表
            if music.basic_info.version == list(plate_to_dx_version.values())[-1]:
                bestlist = player.charts.dx   # B15
                isfull = bool(len(bestlist) == 15)
            else:
                bestlist = player.charts.sd   # B35
                isfull = bool(len(bestlist) == 35)
        else:
            calc = False
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError):
        calc = False
    except Exception:
        calc = False

    from .maimaidx_theme import Theme as _Th, resolve_theme_path as _rtp
    _theme = _Th.get_default().value
    im = Image.open(_rtp(maimaidir, _theme, 'chart_info.png')).convert('RGBA')
    dr = ImageDraw.Draw(im)
    mr = DrawText(dr, SIYUAN)
    tb = DrawText(dr, TBFONT)

    default_color = (124, 130, 255, 255)

    im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'logo.png')).resize((249, 120)), (65, 25))
    if music.basic_info.is_new:
        im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'UI_CMN_TabTitle_NewSong.png')).resize((249, 120)), (842, 100))
    songbg = Image.open(music_picture(music.id)).resize((242, 242))
    im.alpha_composite(rounded_corners(songbg, 17, (True, False, False, True)), (133, 197))
    im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'{music.basic_info.version}.png')).resize((182, 90)), (800, 370))
    im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'{music.type}.png')).resize((80, 30)), (295, 410))

    title = music.title
    if coloumWidth(title) > 40:
        title = changeColumnWidth(title, 39) + '...'
    mr.draw(405, 220, 28, title, default_color, 'lm')
    artist = music.basic_info.artist
    if coloumWidth(artist) > 50:
        artist = changeColumnWidth(artist, 49) + '...'
    mr.draw(407, 265, 20, artist, default_color, 'lm')
    tb.draw(460, 345, 24, music.basic_info.bpm, default_color, 'lm')
    tb.draw(405, 435, 22, f'ID {music.id}', default_color, 'lm')
    mr.draw(665, 435, 24, music.basic_info.genre, default_color, 'mm')

    for num, _ in enumerate(music.level):
        if num == 4:
            color = (255, 255, 255, 255)
        else:
            color = (255, 255, 255, 255)
        spacing = 70 * num
        tb.draw(120, 590 + spacing, 22, f'{music.level[num]}({music.ds[num]:.1f})', color, 'mm')
        tb.draw(
            120, 613 + spacing, 15, 
            f'{round(music.stats[num].fit_diff, 2):.2f}' if music.stats and music.stats[num] else '-', 
            color, 'mm'
        )
        notes = list(music.charts[num].notes)
        tb.draw(480, 590 + spacing, 25, sum(notes), default_color, 'mm')
        if len(notes) == 4:
            notes.insert(3, '-')
        for n, c in enumerate(notes):
            tb.draw(480 + 122 * n, 590 + spacing, 25, c, default_color, 'mm')
        if num > 1:
            charter = music.charts[num].charter
            if coloumWidth(charter) > 19:
                charter = changeColumnWidth(charter, 18) + '...'
            mr.draw(310, 590 + spacing, 20, charter, default_color, 'mm')
            ra = sorted([computeRa(music.ds[num], r) for r in achievementList[-6:]], reverse=True)
            for _n, value in enumerate(ra):
                size = 22
                if not calc:
                    rating = value
                elif not isfull:
                    size = 17
                    rating = f'{value}(+{value})'
                elif value > bestlist[-1].ra:
                    new = newbestscore(music.id, num, value, bestlist)
                    if new == 0:
                        rating = value
                    else:
                        size = 17
                        rating = f'{value}(+{new})'
                else:
                    rating = value
                tb.draw(295 + 125 * _n, 1017 + 46 * (num - 2), size, rating, default_color, 'mm')
    mr.draw(600, 1200, 30, f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {maiconfig.botName} BOT', default_color, 'mm')
    return MessageSegment.image(image_to_base64(im))


async def draw_music_play_data(qqid: int, music_id: str) -> Union[str, MessageSegment]:
    """
    谱面游玩
    
    Params:
        `qqid`: QQID
        `music_id`: 曲目ID
    Returns:
        `Union[str, MessageSegment]`
    """
    try:
        diff: List[Union[None, PlayInfoDev, PlayInfoDefault]]
        if maiconfig.maimaidxtoken:
            data = await maiApi.query_user_post_dev(qqid=qqid, music_id=music_id)
            if not data:
                raise MusicNotPlayError

            music = mai.total_list.by_id(music_id)
            diff = [None for _ in music.ds]
            for _d in data:
                diff[_d.level_index] = _d
            dev = True
        else:
            version = list(set(_v for _v in plate_to_dx_version.values()))
            data = await maiApi.query_user_plate(qqid=qqid, version=version)

            music = mai.total_list.by_id(music_id)
            _temp = [None for _ in music.ds]
            diff = copy.deepcopy(_temp)

            for _d in data:
                if _d.song_id == int(music_id):
                    diff[_d.level_index] = _d
            if diff == _temp:
                raise MusicNotPlayError
            dev = False

        from .maimaidx_theme import Theme as _Th, resolve_theme_path as _rtp
        _theme = _Th.get_default().value
        im = Image.open(_rtp(maimaidir, _theme, 'play_info.png')).convert('RGBA')
    
        dr = ImageDraw.Draw(im)
        tb = DrawText(dr, TBFONT)
        mr = DrawText(dr, SIYUAN)

        im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'logo.png')).resize((249, 120)), (42, 34))
        cover = Image.open(music_picture(music_id))
        im.alpha_composite(cover.resize((300, 300)), (100, 260))
        im.alpha_composite(Image.open(pic(f'info_{category[music.basic_info.genre]}.png')), (100, 260))
        im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'{music.basic_info.version}.png')).resize((183, 90)), (295, 205))
        im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'{music.type}.png')).resize((55, 20)), (350, 560))
        
        color = (124, 129, 255, 255)
        artist = music.basic_info.artist
        if coloumWidth(artist) > 58:
            artist = changeColumnWidth(artist, 57) + '...'
        mr.draw(255, 595, 12, artist, color, 'mm')
        title = music.title
        if coloumWidth(title) > 38:
            title = changeColumnWidth(title, 37) + '...'
        mr.draw(255, 622, 18, title, color, 'mm')
        tb.draw(160, 720, 22, music.id, color, 'mm')
        tb.draw(380, 720, 22, music.basic_info.bpm, color, 'mm')

        y = 100
        for num, info in enumerate(diff):
            im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'd_{num}.png')), (650, 235 + y * num))
            if info:
                im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'ra_dx.png')).resize((102, 44)), (850, 272 + y * num))
                if dev:
                    dxscore = info.dxScore
                    _dxscore = sum(music.charts[num].notes) * 3
                    dxnum = dxScore(dxscore / _dxscore * 100)
                    rating, rate = info.ra, score_Rank_l[info.rate]
                    if dxnum != 0:
                        im.alpha_composite(
                            Image.open(_rtp(maimaidir, _theme, f'UI_GAM_Gauge_DXScoreIcon_0{dxnum}.png')).resize((32, 19)), 
                            (851, 296 + y * num)
                        )
                    tb.draw(916, 304 + y * num, 13, f'{dxscore}/{_dxscore}', color, 'mm')
                else:
                    rating, rate = computeRa(music.ds[num], info.achievements, israte=True)
                
                im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'fcfs.png')), (965, 265 + y * num))
                if info.fc:
                    im.alpha_composite(
                        Image.open(_rtp(maimaidir, _theme, f'UI_CHR_PlayBonus_{fcl[info.fc]}.png')).resize((65, 65)), 
                        (960, 261 + y * num)
                    )
                if info.fs:
                    im.alpha_composite(
                        Image.open(_rtp(maimaidir, _theme, f'UI_CHR_PlayBonus_{fsl[info.fs]}.png')).resize((65, 65)), 
                        (1025, 261 + y * num)
                    )
                im.alpha_composite(
                    Image.open(_rtp(maimaidir, _theme, f'UI_TTR_Rank_{rate}.png')).resize((100, 45)), 
                    (737, 272 + y * num)
                )

                tb.draw(500, 295 + y * num, 30, f'{info.achievements:.4f}%', color, 'lm')
                tb.draw(685, 248 + y * num, 20, music.ds[num], anchor='mm')
                tb.draw(915, 283 + y * num, 18, rating, color, 'mm')
            else:
                tb.draw(685, 248 + y * num, 25, music.ds[num], anchor='mm')
                mr.draw(800, 302 + y * num, 30, '未游玩', color, 'mm')
        if len(diff) == 4:
            mr.draw(800, 302 + y * 4, 30, '没有该难度', color, 'mm')

        mr.draw(600, 827, 22, f'Designed by Yuri-YuzuChaN & BlueDeer233. Generated by {maiconfig.botName} Bot', color, 'mm')
        msg = MessageSegment.image(image_to_base64(im))
        
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError, MusicNotPlayError) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


def calc_achievements_fc(scorelist: Union[List[float], List[str]], lvlist_num: int, isfc: bool = False) -> int:
    r = -1
    obj = range(4) if isfc else achievementList[-6:]
    for __f in obj:
        if len(list(filter(lambda x: x >= __f, scorelist))) == lvlist_num:
            r += 1
        else:
            break
    return r


def draw_rating(rating: str, path: Path) -> MessageSegment:
    """
    绘制指定定数表文字
    
    Params:
        `rating`: 定数
        `path`: 路径
    Returns:
        `MessageSegment`
    """
    im = Image.open(path)
    dr = ImageDraw.Draw(im)
    sy = DrawText(dr, SIYUAN)
    sy.draw(700, 100, 65, f'Level.{rating}   定数表', (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
    return MessageSegment.image(image_to_base64(im))


async def draw_rating_table(qqid: int, rating: str, isfc: bool = False) -> Union[MessageSegment, str]:
    """
    绘制定数表
    
    Params:
        `qqid`: QQID
        `rating`: 定数
        `isfc`: 是否查询fc成绩
    Returns:
        `Union[MessageSegment, str]`
    """
    try:
        version = list(set(_v for _v in plate_to_dx_version.values()))
        obj = await maiApi.query_user_plate(qqid=qqid, version=version)
        
        statistics = {
            'clear': 0,
            'sync':  0,
            's':     0,
            'sp':    0,
            'ss':    0,
            'ssp':   0,
            'sss':   0,
            'sssp':  0,
            'fc':    0,
            'fcp':   0,
            'ap':    0,
            'app':   0,
            'fs':    0,
            'fsp':   0,
            'fsd':   0,
            'fsdp':  0,
        }
        fromid = {}
        
        sp = score_Rank[-6:]
        for _d in obj:
            if _d.level != rating:
                continue
            if (id := str(_d.song_id)) not in fromid:
                fromid[id] = {}
            fromid[id][str(_d.level_index)] = {
                'achievements': _d.achievements,
                'fc': _d.fc,
                'level': _d.level
            }
            rate = computeRa(_d.ds, _d.achievements, onlyrate=True).lower()
            if _d.achievements >= 80:
                statistics['clear'] += 1
            if rate in sp:
                r_index = sp.index(rate)
                for _r in range(r_index + 1):
                    statistics[sp[_r]] += 1
            if _d.fc:
                fc_index = combo_rank.index(_d.fc)
                for _f in range(fc_index + 1):
                    statistics[combo_rank[_f]] += 1
            if _d.fs:
                if _d.fs != 'sync':
                    fs_index = sync_rank.index(_d.fs)
                    for _s in range(fs_index + 1):
                        statistics[sync_rank[_s]] += 1
                else:
                    statistics[_d.fs] += 1

        achievements_fc_list: List[Union[float, List[float]]] = []
        lvlist = mai.total_level_data[rating]
        lvnum = sum([len(v) for v in lvlist.values()])
        
        from .maimaidx_theme import Theme as _Th, resolve_theme_path as _rtp
        _theme = _Th.get_default().value
        rating_bg = Image.open(_rtp(maimaidir, _theme, 'rating_bg.png'))
        unfinished_bg = Image.open(_rtp(maimaidir, _theme, 'unfinished_bg.png'))
        complete_bg = Image.open(_rtp(maimaidir, _theme, 'complete_bg.png'))
        
        bg = ratingdir / f'{rating}.png'
        
        im = Image.open(bg).convert('RGBA')
        dr = ImageDraw.Draw(im)
        sy = DrawText(dr, SIYUAN)
        tb = DrawText(dr, TBFONT)
        
        im.alpha_composite(rating_bg, (600, 25))
        sy.draw(305, 60, 65, f'Level.{rating}', (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
        sy.draw(305, 130, 65, '定数表', (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
        tb.draw(700, 130, 45, lvnum, (124, 129, 255, 255), 'mm', 5, (255, 255, 255, 255))
        
        y = 22
        for n, v in enumerate(statistics):
            if n % 8 == 0:
                x = 824
                y += 56
            else:
                x += 64
            tb.draw(x, y, 20, statistics[v], (124, 129, 255, 255), 'mm', 2, (255, 255, 255, 255))
        
        y = 118
        for ra in lvlist:
            x = 158
            y += 20
            for num, music in enumerate(lvlist[ra]):
                if num % 14 == 0:
                    x = 158
                    y += 85
                else:
                    x += 85
                if music.id in fromid and music.lv in fromid[music.id]:
                    if not isfc:
                        score = fromid[music.id][music.lv]['achievements']
                        achievements_fc_list.append(score)
                        rate = computeRa(music.ds, score, onlyrate=True)
                        rank = Image.open(_rtp(maimaidir, _theme, f'UI_TTR_Rank_{rate}.png')).resize((78, 35))
                        if score >= 100:
                            im.alpha_composite(complete_bg, (x + 2, y - 18))
                        else:
                            im.alpha_composite(unfinished_bg, (x + 2, y - 18))
                        im.alpha_composite(rank, (x, y - 5))
                        continue
                    if _fc := fromid[music.id][music.lv]['fc']:
                        achievements_fc_list.append(combo_rank.index(_fc))
                        fc = Image.open(_rtp(maimaidir, _theme, f'UI_MSS_MBase_Icon_{fcl[_fc]}.png')).resize((50, 50))
                        im.alpha_composite(complete_bg, (x + 2, y - 18))
                        im.alpha_composite(fc, (x + 15, y - 12))

        if len(achievements_fc_list) == lvnum:
            r = calc_achievements_fc(achievements_fc_list, lvnum, isfc)
            if r != -1:
                pic_name = fcl[combo_rank[r]] if isfc else score_Rank_l[score_Rank[-6:][r]]
                im.alpha_composite(Image.open(_rtp(maimaidir, _theme, f'UI_MSS_Allclear_Icon_{pic_name}.png')), (40, 40))
        
        msg = MessageSegment.image(image_to_base64(im))
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg


async def draw_plate_table(qqid: int, version: str, plan: str) -> Union[MessageSegment, str]:
    """
    绘制完成表
    
    Params:
        `qqid`: QQID
        `version`: 版本
        `plan`: 计划
    Returns:
        `Union[MessageSegment, str]`
    """
    try:
        if version in platecn:
            version = platecn[version]
        ver, _ver = version_map.get(version, ([plate_to_dx_version[version]], version))
  
        music_id_list = mai.total_plate_id_list[_ver]
        music = mai.total_list.by_id_list(music_id_list)
        plate_total_num = len(music_id_list)
        playerdata: List[PlayInfoDefault] = []
        
        obj = await maiApi.query_user_plate(qqid=qqid, version=ver)
        # if not obj:
        #     return MessageSegment.image(Image.open(platedir / f'{version}.png'))
        for _d in obj:
            if _d.song_id not in music_id_list:
                continue
            _music = mai.total_list.by_id(_d.song_id)
            _d.table_level = _music.level
            _d.ds = round(float(_music.ds[_d.level_index]), 1)
            playerdata.append(_d)

        ra: Dict[str, Dict[str, List[Optional[PlayInfoDefault]]]] = {}
        """
        {
            "14+": {
                "365": [None, None, None, PlayInfoDefault, None],
                ...
            },
            "14": {
                ...
            }
        }
        """
        music.sort(key=lambda x: x.ds[3], reverse=True)
        number = 4 if version not in ['霸', '舞'] else 5
        for _m in music:
            if _m.level[3] not in ra:
                ra[_m.level[3]] = {}
            ra[_m.level[3]][_m.id] = [None for _ in range(number)]
        for _d in playerdata:
            if number == 4 and _d.level_index == 4:
                continue
            ra[_d.table_level[3]][str(_d.song_id)][_d.level_index] = _d
        
        from .maimaidx_theme import Theme as _Th, resolve_theme_path as _rtp
        _theme = _Th.get_default().value
        finished_bg = [Image.open(_rtp(maimaidir, _theme, f't_{_}.png')) for _ in range(4)]
        unfinished_bg = Image.open(_rtp(maimaidir, _theme, 'unfinished_bg_2.png'))
        complete_bg = Image.open(_rtp(maimaidir, _theme, 'complete_bg_2.png'))

        im = Image.open(platedir / f'plate_{version}.png')
        draw = ImageDraw.Draw(im)
        tr = DrawText(draw, TBFONT)
        mr = DrawText(draw, SIYUAN)
        
        im.alpha_composite(Image.open(_rtp(maimaidir, _theme, 'plate_num.png')), (185, 20))
        im.alpha_composite(
            Image.open(platedir / f'plate_{version}{"極" if plan == "极" else plan}.png').resize((1000, 161)), 
            (200, 35)
        )
        lv: List[set[int]] = [set() for _ in range(number)]
        y = 245
        # if plan == '者':
        #     for level in ra:
        #         x = 200
        #         y += 15
        #         for num, _id in enumerate(ra[level]):
        #             if num % 10 == 0:
        #                 x = 200
        #                 y += 115
        #             else:
        #                 x += 115
        #             f: List[int] = []
        #             for num, play in enumerate(ra[level][_id]):
        #                 if play.achievements or not play.achievements >= 80: continue
        #                 fc = Image.open(pic(f'UI_MSS_MBase_Icon_{fcl[play.fc]}.png'))
        #                 im.alpha_composite(fc, (x, y))
        #                 f.append(n)
        #             for n in f:
        #                 im.alpha_composite(finished_bg[n], (x + 5 + 25 * n, y + 67))
        if plan == '极' or plan == '極':
            for level in ra:
                x = 200
                y += 15
                for num, _id in enumerate(ra[level]):
                    if num % 10 == 0:
                        x = 200
                        y += 115
                    else:
                        x += 115
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or not play.fc: continue
                        if n == 3:
                            im.alpha_composite(complete_bg, (x, y))
                            fc = Image.open(_rtp(maimaidir, _theme, f'UI_CHR_PlayBonus_{fcl[play.fc]}.png')).resize((75, 75))
                            im.alpha_composite(fc, (x + 13, y + 3))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 5 + 25 * n, y + 67))
        if plan == '将':
            for level in ra:
                x = 200
                y += 15
                for num, _id in enumerate(ra[level]):
                    if num % 10 == 0:
                        x = 200
                        y += 115
                    else:
                        x += 115
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.achievements < 100: continue
                        if n == 3:
                            im.alpha_composite(complete_bg if play.achievements >= 100 else unfinished_bg, (x, y))
                            rate = computeRa(play.ds, play.achievements, onlyrate=True)
                            rank = Image.open(_rtp(maimaidir, _theme, f'UI_TTR_Rank_{rate}.png')).resize((102, 46))
                            im.alpha_composite(rank, (x - 1, y + 15))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 5 + 25 * n, y + 67))
        if plan == '神':
            _fc = ['ap', 'app']
            for level in ra:
                x = 200
                y += 15
                for num, _id in enumerate(ra[level]):
                    if num % 10 == 0:
                        x = 200
                        y += 115
                    else:
                        x += 115
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.fc not in _fc: continue
                        if n == 3:
                            im.alpha_composite(complete_bg, (x, y))
                            ap = Image.open(_rtp(maimaidir, _theme, f'UI_CHR_PlayBonus_{fcl[play.fc]}.png')).resize((75, 75))
                            im.alpha_composite(ap, (x + 13, y + 3))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 5 + 25 * n, y + 67))
        if plan == '舞舞':
            fs = ['fsd', 'fdx', 'fsdp', 'fdxp']
            for level in ra:
                x = 200
                y += 15
                for num, _id in enumerate(ra[level]):
                    if num % 10 == 0:
                        x = 200
                        y += 115
                    else:
                        x += 115
                    f: List[int] = []
                    for n, play in enumerate(ra[level][_id]):
                        if play is None or play.fs not in fs:
                            continue
                        if n == 3:
                            im.alpha_composite(complete_bg, (x, y))
                            fsd = Image.open(_rtp(maimaidir, _theme, f'UI_CHR_PlayBonus_{fsl[play.fs]}.png')).resize((75, 75))
                            im.alpha_composite(fsd, (x + 13, y + 3))
                        lv[n].add(play.song_id)
                        f.append(n)
                    for n in f:
                        im.alpha_composite(finished_bg[n], (x + 5 + 25 * n, y + 67))
        
        color = ScoreBaseImage.id_color.copy()
        color.insert(0, (124, 129, 255, 255))
        for num in range(len(lv) + 1):
            if num == 0:
                v = set.intersection(*lv)
                _v = f'{len(v)}/{plate_total_num}'
            else:
                _v = len(lv[num - 1])
            if _v == plate_total_num:
                mr.draw(390 + 200 * num, 270, 35, '完成', color[num], 'rm', 4, (255, 255, 255, 255))
            else:
                tr.draw(390 + 200 * num, 270, 40, _v, color[num], 'rm', 4, (255, 255, 255, 255))
        
        msg = MessageSegment.image(image_to_base64(im))
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        msg = str(e)
    except Exception as e:
        log.error(traceback.format_exc())
        msg = f'未知错误：{type(e)}\n请联系Bot管理员'
    return msg