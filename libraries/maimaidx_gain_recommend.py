from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import footer_generated, log
from .maimaidx_best_50 import _is_latest_version, computeRa
from .maimaidx_data_storage import DailySnapshot, ScoreRecord, data_storage
from .maimaidx_music import mai


@dataclass
class _Ability:
    avg_gain: float
    improve_rate: float


@dataclass
class _Recommend:
    title: str
    level: str
    ds: float
    fit_diff: float
    achv_now: float
    achv_target: float
    need: float
    ra_now: int
    ra_target: int
    net_gain: int
    probability: float
    score: float
    zone: str


def _song_key(song_id: int, level_index: int) -> Tuple[int, int]:
    return int(song_id), int(level_index)


def _level_bucket(ds: float) -> str:
    if ds < 12:
        return "<12"
    if ds < 13:
        return "12.x"
    if ds < 14:
        return "13.x"
    if ds < 14.7:
        return "14.x"
    return "14.7+"


def _build_b50(records: List[ScoreRecord]) -> tuple[list[ScoreRecord], list[ScoreRecord], dict[tuple[int, int], ScoreRecord]]:
    records_sorted = sorted(records, key=lambda x: int(x.ra), reverse=True)
    b15 = sorted([r for r in records_sorted if _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:15]
    b35 = sorted([r for r in records_sorted if not _is_latest_version(r)], key=lambda x: int(x.ra), reverse=True)[:35]
    b50_map = {_song_key(r.song_id, r.level_index): r for r in (b35 + b15)}
    return b35, b15, b50_map


def _load_recent_snapshots(qqid: int, limit: int = 20) -> List[DailySnapshot]:
    metas = data_storage.list_snapshots(qqid, limit=limit)
    out: List[DailySnapshot] = []
    for m in reversed(metas):  # 时间正序
        sid = m.get("snapshot_id", "")
        snap = data_storage.load_snapshot_by_id(qqid, sid)
        if snap:
            out.append(snap)
    return out


def _calc_user_ability(snaps: List[DailySnapshot]) -> Dict[str, _Ability]:
    attempts: Dict[str, int] = {}
    improved_cnt: Dict[str, int] = {}
    gain_sum: Dict[str, float] = {}

    for i in range(1, len(snaps)):
        prev = {_song_key(r.song_id, r.level_index): r for r in snaps[i - 1].records}
        curr = {_song_key(r.song_id, r.level_index): r for r in snaps[i].records}
        for key, old_r in prev.items():
            new_r = curr.get(key)
            if not new_r:
                continue
            ds = float(new_r.ds or old_r.ds or 0.0)
            b = _level_bucket(ds)
            attempts[b] = attempts.get(b, 0) + 1
            d = float(new_r.achievements) - float(old_r.achievements)
            if d > 0.0001:
                improved_cnt[b] = improved_cnt.get(b, 0) + 1
                gain_sum[b] = gain_sum.get(b, 0.0) + d

    abilities: Dict[str, _Ability] = {}
    for b in ["<12", "12.x", "13.x", "14.x", "14.7+"]:
        total = attempts.get(b, 0)
        inc_n = improved_cnt.get(b, 0)
        avg_gain = (gain_sum.get(b, 0.0) / inc_n) if inc_n > 0 else 0.03
        improve_rate = (inc_n / total) if total > 0 else 0.35
        # 稳定下限，避免样本过小时全零
        avg_gain = max(0.02, min(0.2, avg_gain))
        improve_rate = max(0.2, min(0.85, improve_rate))
        abilities[b] = _Ability(avg_gain=avg_gain, improve_rate=improve_rate)
    return abilities


def _fit_diff(song_id: int, level_index: int, fallback_ds: float) -> float:
    try:
        music = mai.total_list.by_id(str(song_id))
        if music and music.stats and level_index < len(music.stats) and music.stats[level_index]:
            f = music.stats[level_index].fit_diff
            if f is not None:
                return float(f)
    except Exception:
        pass
    return float(fallback_ds)


def _pick_zone(prob: float, net_gain: int) -> str:
    if prob >= 0.65 and net_gain >= 4:
        return "稳赚"
    if prob >= 0.45:
        return "均衡"
    return "冲刺"


async def generate_today_gain_recommendation(qqid: int, top_n: int = 12) -> str:
    snaps = _load_recent_snapshots(qqid, limit=20)
    if len(snaps) < 2:
        return "历史快照不足（至少需要2次存档）\n请先使用「立即存储数据」积累历史后再试。"

    abilities = _calc_user_ability(snaps)
    latest = snaps[-1]
    b35, b15, b50_map = _build_b50(latest.records)
    b35_tail = int(b35[-1].ra) if b35 else 0
    b15_tail = int(b15[-1].ra) if b15 else 0

    from .maimaidx_datasource import get_user_records

    _ui, dev_records = await get_user_records(qqid=qqid)
    records = list(dev_records or [])
    from .maimaidx_best_50 import filter_utage_records
    records = filter_utage_records(records)
    if not records:
        return "未读取到全量成绩，无法推荐。"

    targets = [97.0, 98.0, 99.0, 99.5, 100.0, 100.5]
    picks: List[_Recommend] = []

    for r in records:
        achv_now = float(r.achievements)
        if achv_now >= 100.5:
            continue
        ds = float(r.ds)
        fit = _fit_diff(int(r.song_id), int(r.level_index), ds)
        bucket = _level_bucket(ds)
        abi = abilities.get(bucket, _Ability(avg_gain=0.05, improve_rate=0.35))

        ease = 1.0 + max(-0.4, min(0.4, (ds - fit) * 0.4))
        expected_gain = abi.avg_gain * ease

        best: Optional[_Recommend] = None
        key = _song_key(int(r.song_id), int(r.level_index))
        in_b50 = key in b50_map
        ra_now = int(r.ra)

        for t in targets:
            if t <= achv_now + 1e-9:
                continue
            need = t - achv_now
            if need > 0.45:
                continue
            ra_target = int(computeRa(ds, t))
            base = ra_now if in_b50 else (b15_tail if _is_latest_version(r) else b35_tail)
            net = max(0, ra_target - base)
            if net <= 0:
                continue
            ratio = expected_gain / max(need, 1e-6)
            prob = max(0.1, min(0.95, abi.improve_rate * ratio))
            score = net * prob
            cand = _Recommend(
                title=r.title,
                level=r.level,
                ds=ds,
                fit_diff=fit,
                achv_now=achv_now,
                achv_target=t,
                need=need,
                ra_now=ra_now,
                ra_target=ra_target,
                net_gain=net,
                probability=prob,
                score=score,
                zone=_pick_zone(prob, net),
            )
            if best is None or cand.score > best.score:
                best = cand

        if best:
            picks.append(best)

    if not picks:
        return "今天没有明显吃分候选（可能是当前 B50 已很满，或可提升空间较小）。"

    picks.sort(key=lambda x: x.score, reverse=True)
    top = picks[: max(1, min(20, top_n))]

    groups: Dict[str, List[_Recommend]] = {"稳赚": [], "均衡": [], "冲刺": []}
    for p in top:
        groups[p.zone].append(p)

    lines = [
        "今日吃分推荐（基于历史提分能力 + 拟合难度 + B35/B15门槛净收益）",
        f"历史样本：{len(snaps)} 次快照",
    ]
    for zone in ["稳赚", "均衡", "冲刺"]:
        arr = groups[zone]
        if not arr:
            continue
        lines.append(f"\n【{zone}】")
        for i, p in enumerate(arr[:4], 1):
            lines.append(
                f"{i}. {p.title} [{p.level}] "
                f"{p.achv_now:.4f}%->{p.achv_target:.1f}% "
                f"净增{p.net_gain:+d}ra "
                f"成功率{p.probability*100:.0f}% "
                f"(拟合{p.fit_diff:.2f}/定数{p.ds:.2f})"
            )

    log.debug(f"[today_gain] qq={qqid} picks={len(picks)} top={len(top)}")
    return "\n".join(lines) + f"\n\n{footer_generated()}"

