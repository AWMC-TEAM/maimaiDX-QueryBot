"""
友人对战：从发起者 B50 随机一首，在水平接近的群友中随机匹配对手并比该谱成绩。

- 水平过滤：避免「高水平碾压 / 碾压低水平」。以双方总 rating 均值为档给出最大 |Δrating|；
  均分 ≥16000 → ±50，≥15000 → ±100，再低按阶梯放宽。可选指令数字在不超过该档前提下进一步收紧。
- 对手池：优先同群中「已开启数据存储」且符合条件者；若无人则退化为同群其它符合条件者。
- 对手成绩：优先 SQLite 玩家缓存 / 数据存储快照（默认 7 天内有效，重启不丢）；
  本地无数据时才经 datasource 拉取并写回缓存（同局内内存缓存；网络请求小并发）。
  未游玩该谱则不计入可匹配池。
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from nonebot.adapters.onebot.v11 import Bot

from ..config import log, maiconfig
from .maimaidx_datasource import get_user_b50, get_user_records, get_user_source
from .maimaidx_player_cache import get_cached_player_for_friend_battle
from .maimaidx_data_storage import ScoreRecord, data_storage
from .maimaidx_error import (
    UserDisabledQueryError,
    UserNotFoundError,
    UserNotExistsError,
)
from .maimaidx_group_rating import get_group_member_ratings, _display_name
from .maimaidx_timing import measure
from .maimaidx_model import ChartInfo, PlayInfoDev, UserInfoDev
from .maimaidx_score_formatter import get_difficulty_name
from .maimaidx_friend_battle_class import (
    format_class_line,
    get_class_state,
    settle_battle_cp_with_extras,
)

_last_friend_battle: Dict[int, float] = {}


def friend_battle_cooldown_seconds() -> int:
    return max(0, int(getattr(maiconfig, "maimaidx_friend_battle_cooldown_seconds", 180) or 0))


def check_friend_battle_cooldown(qq: int) -> Optional[str]:
    """若仍在冷却内则返回提示文案，否则返回 None。"""
    cd = friend_battle_cooldown_seconds()
    if cd <= 0:
        return None
    last = _last_friend_battle.get(qq)
    if last is None:
        return None
    remain = cd - (time.time() - last)
    if remain <= 0:
        return None
    if remain >= 60:
        m, s = int(remain // 60), int(remain % 60)
        hint = f"{m} 分 {s} 秒" if s else f"{m} 分钟"
    else:
        hint = f"{max(1, int(remain))} 秒"
    return f"友人对战冷却中，请 {hint} 后再试。"


def mark_friend_battle_used(qq: int) -> None:
    if friend_battle_cooldown_seconds() <= 0:
        return
    _last_friend_battle[qq] = time.time()


@dataclass
class FriendBattleOutcome:
    """友人对战结算数据（供图片渲染）。"""

    verdict: str
    winner_side: str  # me | opp | tie
    used_pool: str
    rating_delta: int
    rating_limit: int
    my_rating: int
    opp_rating: int
    opp_name: str
    my_qq: int
    opp_qq: int
    my_name: str
    rel_zh: str
    title: str
    level: str
    diff_name: str
    music_id: int
    level_index: int
    my_achv: float
    my_dx: int
    o_achv: float
    o_dx: int
    cp_lines: List[str] = field(default_factory=list)


def _b50_pool(user_charts) -> List[ChartInfo]:
    if not user_charts:
        return []
    b35 = list(user_charts.sd or [])
    b15 = list(user_charts.dx or [])
    return b35 + b15


def _tier_limit_for_avg(avg_rating: int) -> int:
    """
    按双方总 rating 均值分档给出本局允许的最大 |Δrating|（对称，防互相碾压）。

    高分段单独收紧：均分 ≥16000 → ±50，≥15000 → ±100；其下仍按原阶梯略宽。
    """
    a = int(avg_rating)
    if a >= 16000:
        return 50
    if a >= 15000:
        return 100
    if a < 10000:
        return 520
    if a < 11500:
        return 480
    if a < 13000:
        return 420
    if a < 14500:
        return 360
    return 300


def _pair_rating_limit(my_ra: int, opp_ra: int, user_cap: Optional[int]) -> int:
    """单对组合下的有效差限：分档上限，并可被指令数字额外收紧。"""
    tier = _tier_limit_for_avg((int(my_ra) + int(opp_ra)) // 2)
    if user_cap is None:
        return tier
    cap = max(50, min(800, int(user_cap)))
    return min(tier, cap)


def _in_rating_band(ra: int, my_ra: int, user_cap: Optional[int]) -> bool:
    lim = _pair_rating_limit(my_ra, ra, user_cap)
    return abs(int(ra) - int(my_ra)) <= lim


def _weighted_pick_opponent(
    cands: List[Tuple[int, str, PlayInfoDev]],
    my_rating: int,
    ra_by_uid: dict,
    user_cap: Optional[int],
) -> Tuple[int, str, PlayInfoDev]:
    """在已通过谱面校验的候选中，按与发起者 rating 接近度加权随机。"""
    if len(cands) == 1:
        return cands[0]
    ref = _tier_limit_for_avg(int(my_rating))
    scale = max(40.0, float(ref) / 2.5)
    weights: List[float] = []
    for uid, _name, _rec in cands:
        o_ra = int(ra_by_uid.get(uid, 0))
        d = abs(o_ra - int(my_rating))
        w = 1.0 / (1.0 + (d / scale) ** 2)
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        return random.choice(cands)
    r = random.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            return cands[i]
    return cands[-1]


def _merge_snapshot_records(records: List[ScoreRecord]) -> dict[Tuple[int, int], ScoreRecord]:
    """同一 (song_id, level_index) 保留一条（更高达成）。"""
    best: dict[Tuple[int, int], ScoreRecord] = {}
    for r in records:
        k = (int(r.song_id), int(r.level_index))
        o = best.get(k)
        if o is None or float(r.achievements) > float(o.achievements):
            best[k] = r
    return best


def _score_record_to_playinfo_dev(rec: ScoreRecord, music_id: int) -> PlayInfoDev:
    lv = rec.level or ""
    return PlayInfoDev(
        song_id=int(music_id),
        title=rec.title or "",
        level=lv,
        level_label=lv,
        level_index=int(rec.level_index),
        achievements=float(rec.achievements),
        fc=rec.fc or "",
        fs=rec.fs or "",
        type="SD",
        ds=round(float(rec.ds), 1),
        dxScore=int(rec.dxScore or 0),
        ra=int(rec.ra),
        rate=rec.rate or "",
    )


def _play_from_record_list(
    records: List[PlayInfoDev], music_id: int, level_index: int
) -> Optional[PlayInfoDev]:
    mid = int(music_id)
    li = int(level_index)
    for r in records:
        if int(getattr(r, "song_id", 0)) == mid and int(getattr(r, "level_index", -1)) == li:
            return r
    return None


def _try_local_records_play(qqid: int, music_id: int, level_index: int) -> Optional[PlayInfoDev]:
    """SQLite 玩家缓存 / 数据存储快照（长 TTL，不占网络）。"""
    bundle = get_cached_player_for_friend_battle(qqid)
    if bundle and bundle.records:
        hit = _play_from_record_list(bundle.records, music_id, level_index)
        if hit is not None:
            return hit
    mid = int(music_id)
    li = int(level_index)
    metas = data_storage.list_snapshots(qqid, limit=1)
    if not metas:
        return None
    sid = metas[0].get("snapshot_id", "")
    if not sid:
        return None
    snap = data_storage.load_snapshot_by_id(qqid, sid)
    if not snap or not snap.records:
        return None
    merged = _merge_snapshot_records(list(snap.records))
    rec = merged.get((mid, li))
    if rec is None:
        return None
    return _score_record_to_playinfo_dev(rec, mid)


def _play_from_dev_cache(
    qqid: int, music_id: int, level_index: int, dev_cache: dict[int, Optional[UserInfoDev]]
) -> Optional[PlayInfoDev]:
    """假定 dev_cache 已加载该 qq（或已标记为 None 失败）。"""
    mid = int(music_id)
    li = int(level_index)
    dev = dev_cache.get(qqid)
    if dev is None or not dev.records:
        return None
    for r in dev.records:
        if int(getattr(r, "song_id", 0)) == mid and int(getattr(r, "level_index", -1)) == li:
            return r
    return None


def _bundle_to_userinfo_dev(bundle) -> UserInfoDev:
    return UserInfoDev(
        nickname=bundle.userinfo.nickname,
        rating=bundle.userinfo.rating,
        additional_rating=bundle.userinfo.additional_rating or 0,
        username=bundle.userinfo.username,
        records=bundle.records,
    )


async def _ensure_dev_cached(qqid: int, dev_cache: dict[int, Optional[UserInfoDev]], sem: asyncio.Semaphore) -> None:
    """加载对手全量成绩：先长 TTL 本地库，没有再经 datasource 拉取并写入 SQLite。"""
    if qqid in dev_cache:
        return
    bundle = get_cached_player_for_friend_battle(qqid)
    if bundle and bundle.records:
        dev_cache[qqid] = _bundle_to_userinfo_dev(bundle)
        return
    async with sem:
        if qqid in dev_cache:
            return
        bundle = get_cached_player_for_friend_battle(qqid)
        if bundle and bundle.records:
            dev_cache[qqid] = _bundle_to_userinfo_dev(bundle)
            return
        try:
            source = get_user_source(qqid)
            _ui, records = await get_user_records(qqid=qqid, force_source=source)
            if records:
                dev_cache[qqid] = UserInfoDev(
                    nickname=_ui.nickname,
                    rating=_ui.rating,
                    additional_rating=_ui.additional_rating or 0,
                    username=_ui.username,
                    records=records,
                )
            else:
                dev_cache[qqid] = None
        except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError, ValueError, TypeError):
            dev_cache[qqid] = None
        except Exception as e:
            log.warning(f"[friend_battle] fetch records qq={qqid}: {e!r}")
            dev_cache[qqid] = None


async def _fetch_song_on_level(
    qqid: int,
    music_id: int,
    level_index: int,
    dev_cache: dict[int, Optional[UserInfoDev]],
    sem: asyncio.Semaphore,
) -> Optional[PlayInfoDev]:
    """对手该谱成绩：本地库优先（并行不占槽），否则小并发拉全量后本地筛。"""
    local = _try_local_records_play(qqid, music_id, level_index)
    if local is not None:
        return local
    await _ensure_dev_cached(qqid, dev_cache, sem)
    return _play_from_dev_cache(qqid, music_id, level_index, dev_cache)


async def run_friend_battle(
    bot: Bot,
    group_id: int,
    challenger_qq: int,
    user_rating_cap: Optional[int] = None,
) -> Union[str, FriendBattleOutcome]:
    if not getattr(maiconfig, "maimaidxtoken", None):
        return (
            "友人对战在本地无缓存时需要开发者 Token 拉取全量成绩；"
            "请 Bot 管理员配置 maimaidxtoken，或让群友先发 b50/开启存储数据以积累本地库。"
        )

    try:
        me = await get_user_b50(qqid=challenger_qq)
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        return str(e)

    if not me.charts:
        return "未找到你的 B50 数据，请先绑定查分器。"

    pool = _b50_pool(me.charts)
    if not pool:
        return "你的 B50 为空，无法随机曲目。"

    pick = random.choice(pool)
    music_id = int(pick.song_id)
    level_index = int(pick.level_index)
    title = (pick.title or "").strip() or f"ID{music_id}"
    level = pick.level or ""
    my_achv = float(pick.achievements)
    my_dx = int(getattr(pick, "dxScore", 0) or 0)
    my_rating = int(me.rating or 0)
    diff_name = get_difficulty_name(level_index)
    ref_tier = _tier_limit_for_avg(my_rating)

    try:
        with measure('fetch'):
            raw = await bot.call_api("get_group_member_list", group_id=group_id)
    except Exception as e:
        return f"获取群成员失败：{e}"
    if not raw or not isinstance(raw, list):
        return "群成员列表为空。"

    members_by_id = {int(m.get("user_id")): m for m in raw if m.get("user_id") is not None}
    member_ids = set(members_by_id.keys())

    rating_rows = await get_group_member_ratings(bot, group_id)
    ra_by_uid: dict = {uid: ra for uid, _name, ra in rating_rows}
    name_by_uid: dict = {uid: n for uid, n, _ in rating_rows}

    def name_of(uid: int) -> str:
        if uid in name_by_uid:
            return name_by_uid[uid]
        if uid in members_by_id:
            return _display_name(members_by_id[uid])
        return str(uid)

    # 候选：同群、非本人、有 rating 记录、|Δrating| 在 spread 内
    band_uids: List[int] = []
    for uid, _n, ra in rating_rows:
        if uid == challenger_qq:
            continue
        if uid not in member_ids:
            continue
        if not _in_rating_band(ra, my_rating, user_rating_cap):
            continue
        band_uids.append(uid)

    if not band_uids:
        cap_desc = (
            f"且额外收紧至 ±{user_rating_cap}"
            if user_rating_cap is not None
            else "（按分档动态差限）"
        )
        return (
            "群内没有满足「总 rating 水平接近」且已绑定查分器的群友，无法匹配。\n"
            f"你当前总 rating：{my_rating}；单人档位参考允许约 ±{ref_tier}{cap_desc}。\n"
            "可让水平接近的群友绑定查分器，或发「友人对战 300」等在分档内进一步收紧差限，或稍后重试换谱。"
        )

    storage_in_group = {u for u in data_storage.get_enabled_users() if u in member_ids and u != challenger_qq}
    prefer = [u for u in band_uids if u in storage_in_group]
    others = [u for u in band_uids if u not in storage_in_group]

    dev_cache: dict[int, Optional[UserInfoDev]] = {}
    # 仅限制「查分器 get_dev」并发；快照读盘与内存筛谱不占槽，整体更快且仍护查分器
    dev_sem = asyncio.Semaphore(5)

    async def _try_uid(uid: int) -> Optional[Tuple[int, str, PlayInfoDev]]:
        r = await _fetch_song_on_level(uid, music_id, level_index, dev_cache, dev_sem)
        if r is None:
            return None
        return (uid, name_of(uid), r)

    async def _valid_from_uids(uids: List[int]) -> List[Tuple[int, str, PlayInfoDev]]:
        if not uids:
            return []
        random.shuffle(uids)
        tasks = [asyncio.create_task(_try_uid(uid)) for uid in uids]
        return [x for x in await asyncio.gather(*tasks) if x is not None]

    used_pool = "同群同水平(优先本地库)"
    cands = await _valid_from_uids(prefer)
    if not cands and others:
        used_pool = "同群同水平"
        cands = await _valid_from_uids(others)

    if not cands:
        return (
            f"已随机到：「{title}」{diff_name}（{level}）\n"
            "在水平接近的群友中，没有人有该谱成绩。"
            "请重发「友人对战」换一首随机的歌。"
        )

    ouid, oname, orec = _weighted_pick_opponent(cands, my_rating, ra_by_uid, user_rating_cap)
    o_achv = float(orec.achievements)
    o_dx = int(getattr(orec, "dxScore", 0) or 0)
    o_rating = int(ra_by_uid.get(ouid, 0))

    ci0, _ = get_class_state(challenger_qq)
    oi0, _ = get_class_state(ouid)
    if oi0 > ci0:
        rel_zh = "对手段位更高（相对你为格上）"
    elif oi0 < ci0:
        rel_zh = "对手段位更低（相对你为格下）"
    else:
        rel_zh = "双方同段"

    if my_achv > o_achv:
        verdict = "你赢了"
    elif my_achv < o_achv:
        verdict = f"{oname} 赢了"
    else:
        if my_dx > o_dx:
            verdict = "你赢了"
        elif my_dx < o_dx:
            verdict = f"{oname} 赢了"
        else:
            verdict = "平手"

    lim_used = _pair_rating_limit(my_rating, o_rating, user_rating_cap)
    if verdict == "你赢了":
        winner_side = "me"
    elif verdict == "平手":
        winner_side = "tie"
    else:
        winner_side = "opp"

    if verdict == "平手":
        cp_lines = ["── 段位·CP ──", "本局平手，双方段位 CP 不变。"]
    else:
        cp_lines = settle_battle_cp_with_extras(
            challenger_qq,
            ouid,
            verdict == "你赢了",
            my_rating,
            o_rating,
            my_achv,
            o_achv,
            my_dx,
            o_dx,
        ).split("\n")
    cp_lines.append(format_class_line(challenger_qq))

    return FriendBattleOutcome(
        verdict=verdict,
        winner_side=winner_side,
        used_pool=used_pool,
        rating_delta=abs(o_rating - my_rating),
        rating_limit=lim_used,
        my_rating=my_rating,
        opp_rating=o_rating,
        opp_name=oname,
        my_qq=challenger_qq,
        opp_qq=ouid,
        my_name=name_of(challenger_qq),
        rel_zh=rel_zh,
        title=title,
        level=level,
        diff_name=diff_name,
        music_id=music_id,
        level_index=level_index,
        my_achv=my_achv,
        my_dx=my_dx,
        o_achv=o_achv,
        o_dx=o_dx,
        cp_lines=cp_lines,
    )
