"""
统计「牌子」完成数量（规则与完成表 / 牌子进度一致，不调用 plate 接口）。

数据来源：优先使用本地最近一次快照；若无快照则在本指令内拉取 query_user_get_dev 全量一次再统计。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from ..config import plate_to_dx_version, version_map
from .maimaidx_data_storage import DailySnapshot, ScoreRecord
from .maimaidx_music import mai

# 与 mai_table 中 plate_table_pfm 一致：暂不统计舞系、霸者
_SKIP_VERSIONS = frozenset({"舞", "霸"})

# 牌子统计：仅统计这几类（不含「者」）
_PLANS: Tuple[str, ...] = ("極", "将", "神", "舞舞")


def _fc_rank(fc: Optional[str]) -> int:
    if not fc:
        return 0
    return {"fc": 1, "fcp": 2, "ap": 3, "app": 4}.get(fc.lower(), 0)


def _merge_records(records: List[ScoreRecord]) -> Dict[Tuple[int, int], ScoreRecord]:
    """同一 (song_id, level_index) 合并为一条（优先更高达成与更高 FC）。"""
    best: Dict[Tuple[int, int], ScoreRecord] = {}
    for r in records:
        k = (int(r.song_id), int(r.level_index))
        o = best.get(k)
        if o is None:
            best[k] = r
            continue
        if r.achievements > o.achievements:
            best[k] = r
        elif r.achievements == o.achievements and _fc_rank(r.fc) > _fc_rank(o.fc):
            best[k] = r
    return best


def _slot_ok(plan: str, rec: Optional[ScoreRecord]) -> bool:
    if rec is None:
        ach, fc, fs = 0.0, None, None
    else:
        ach, fc, fs = float(rec.achievements), rec.fc, rec.fs
    pl = plan if plan != "极" else "極"
    if pl == "極":
        return bool(fc)
    if pl == "将":
        return ach >= 100.0
    if pl == "者":
        return ach >= 80.0
    if pl == "神":
        return (fc or "").lower() in ("ap", "app")
    if pl == "舞舞":
        return (fs or "").lower() in ("fsd", "fdx", "fsdp", "fdxp")
    return False


def _iter_slots(
    version_key: str,
    _ver: str,
    plate_ids: List[int],
    remaster: Set[int],
) -> List[Tuple[int, int]]:
    """需判定的 (song_id, level_index) 列表。"""
    slots: List[Tuple[int, int]] = []
    is_mai2 = version_key in ("舞", "霸")
    for sid in plate_ids:
        if is_mai2 and sid in remaster:
            idx_range = range(5)
        else:
            idx_range = range(4)
        for idx in idx_range:
            slots.append((int(sid), idx))
    return slots


def _plate_satisfied(
    plan: str,
    merged: Dict[Tuple[int, int], ScoreRecord],
    slots: List[Tuple[int, int]],
) -> bool:
    for k in slots:
        if not _slot_ok(plan, merged.get(k)):
            return False
    return True


def count_completed_plates_from_records(
    records: List[ScoreRecord], source_note: str
) -> Tuple[int, int, List[str], str]:
    """
    根据全量成绩记录统计已完成的牌子数。

    Returns:
        (完成数, 总组合数, 已完成名称列表, 数据说明行)
    """
    if not mai.total_plate_id_list:
        return 0, 0, [], "曲库牌子列表未加载，请确认 Bot 已正常启动并拉取 plate 数据。"

    merged = _merge_records(list(records or []))

    versions = [k for k in list(plate_to_dx_version.keys())[1:] if k not in _SKIP_VERSIONS]
    completed: List[str] = []
    total = 0

    remaster_ids: Set[int] = set()
    try:
        raw_rm = mai.total_plate_id_list.get("舞ReMASTER") or []
        remaster_ids = {int(x) for x in raw_rm}
    except Exception:
        remaster_ids = set()

    for vk in versions:
        _, _ver = version_map.get(vk, ([plate_to_dx_version.get(vk, "")], vk))
        plate_ids = mai.total_plate_id_list.get(_ver)
        if not plate_ids:
            continue
        ids_int = [int(x) for x in plate_ids]
        slots = _iter_slots(vk, _ver, ids_int, remaster_ids)
        if not slots:
            continue

        for plan in _PLANS:
            if vk == "真" and plan == "将":
                continue
            total += 1
            if _plate_satisfied(plan, merged, slots):
                completed.append(f"{vk}{plan}")

    return len(completed), total, sorted(completed), source_note


def count_completed_plates_from_snapshot(snap: DailySnapshot) -> Tuple[int, int, List[str], str]:
    note = f"数据来源：本地快照 {snap.snapshot_id or snap.date}（{snap.stored_at or snap.date}）"
    return count_completed_plates_from_records(list(snap.records or []), note)


def format_plate_count_lines(n_done: int, n_all: int, names: List[str], note: str) -> str:
    lines = [
        "── 牌子统计 ──",
        note,
        f"已完成：{n_done} / {n_all} 种（按代×極/将/神/舞舞，不含舞·霸；真将不计）",
    ]
    if names:
        lines.append("已完成：" + "、".join(names))
    else:
        lines.append("暂无已完成的牌子，可对照各代「〇〇进度」查漏补缺。")
    return "\n".join(lines)


def format_plate_count_message(snap: DailySnapshot) -> str:
    n_done, n_all, names, note = count_completed_plates_from_snapshot(snap)
    return format_plate_count_lines(n_done, n_all, names, note)


def format_plate_count_message_from_records(records: List[ScoreRecord], source_note: str) -> str:
    n_done, n_all, names, note = count_completed_plates_from_records(records, source_note)
    return format_plate_count_lines(n_done, n_all, names, note)


async def fetch_dev_records_as_score_records(qqid: int) -> List[ScoreRecord]:
    """拉取查分器全量成绩（优先玩家缓存），转为与快照一致的 ScoreRecord。"""
    from .maimaidx_best_50 import filter_utage_records
    from .maimaidx_datasource import get_user_records

    _ui, dev_records = await get_user_records(qqid=qqid)
    records = list(dev_records or [])
    records = filter_utage_records(records)
    out: List[ScoreRecord] = []
    for r in records:
        out.append(
            ScoreRecord(
                song_id=r.song_id,
                title=r.title,
                level=r.level,
                level_index=r.level_index,
                ds=r.ds,
                achievements=r.achievements,
                rate=r.rate,
                ra=r.ra,
                fc=r.fc,
                fs=r.fs,
                dxScore=getattr(r, "dxScore", 0),
            )
        )
    return out
