"""
友人对战：段位（クラス）与 CP。

- 按官方式表格近似：B/A/S/SS/SSS/LEGEND 小段，胜败增减 CP、格上·同格·格下、ボスオトモダチ。
- 额外 CP：达成率碾压、DX 碾压、总 rating 以下克上（仅展示与小幅加成）。
- 连胜系数：仅统计「友人对战」连胜；胜方本局基础胜 CP（含ボス加算部分）乘以系数，败方连胜清零。
- 持久化：data/friend_battle_class.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger as log

_DATA_FILE = Path(__file__).parent.parent / "data" / "friend_battle_class.json"


@dataclass(frozen=True)
class _TierRule:
    name: str
    cp_to_next: Optional[int]  # None = LEGEND 不升段
    # 胜：格上 / 同格 / 格下 基础 CP；ボス时额外 +boss_add（与基础相加）
    win_upper: int
    win_same: int
    win_lower: int
    boss_add: int
    # 败：输给「格上/同格/格下」的对手时扣除的 CP（对手相对败者）
    loss_vs_upper: int
    loss_vs_same: int
    loss_vs_lower: int
    # B 段：胜固定、败固定，忽略格
    flat_win: Optional[int] = None
    flat_loss: Optional[int] = None
    flat_loss_no_grade: Optional[int] = None  # A 段败北统一


def _tier_table() -> List[_TierRule]:
    """从弱到强排列。"""
    rows: List[_TierRule] = []
    # B5～B1：升段 10CP，胜 +5，败 -0
    for name in ("B5", "B4", "B3", "B2", "B1"):
        rows.append(
            _TierRule(
                name=name,
                cp_to_next=10,
                win_upper=5,
                win_same=5,
                win_lower=5,
                boss_add=0,
                loss_vs_upper=0,
                loss_vs_same=0,
                loss_vs_lower=0,
                flat_win=5,
                flat_loss=0,
            )
        )
    # A5～A1
    for name in ("A5", "A4", "A3", "A2", "A1"):
        rows.append(
            _TierRule(
                name=name,
                cp_to_next=20,
                win_upper=4,
                win_same=4,
                win_lower=3,
                boss_add=10,
                loss_vs_upper=0,
                loss_vs_same=0,
                loss_vs_lower=0,
                flat_loss_no_grade=-1,
            )
        )
    # S5～S1
    for name in ("S5", "S4", "S3", "S2", "S1"):
        rows.append(
            _TierRule(
                name=name,
                cp_to_next=30,
                win_upper=4,
                win_same=3,
                win_lower=3,
                boss_add=10,
                loss_vs_upper=-1,
                loss_vs_same=-2,
                loss_vs_lower=-3,
            )
        )
    # SS5～SS1（败北表未给出，按 S 段镜像）
    for name in ("SS5", "SS4", "SS3", "SS2", "SS1"):
        rows.append(
            _TierRule(
                name=name,
                cp_to_next=50,
                win_upper=3,
                win_same=3,
                win_lower=2,
                boss_add=10,
                loss_vs_upper=-1,
                loss_vs_same=-2,
                loss_vs_lower=-3,
            )
        )
    # SSS5～SSS1：必要 CP 60→100
    sss_next = [60, 70, 80, 90, 100]
    for i, name in enumerate(("SSS5", "SSS4", "SSS3", "SSS2", "SSS1")):
        rows.append(
            _TierRule(
                name=name,
                cp_to_next=sss_next[i],
                win_upper=3,
                win_same=2,
                win_lower=2,
                boss_add=10,
                loss_vs_upper=-2,
                loss_vs_same=-2,
                loss_vs_lower=-1,
            )
        )
    rows.append(
        _TierRule(
            name="LEGEND",
            cp_to_next=None,
            win_upper=1,
            win_same=1,
            win_lower=1,
            boss_add=0,
            loss_vs_upper=-1,
            loss_vs_same=-1,
            loss_vs_lower=-1,
        )
    )
    return rows


TIER_RULES: List[_TierRule] = _tier_table()
TIER_INDEX: Dict[str, int] = {r.name: i for i, r in enumerate(TIER_RULES)}


def _ensure_file():
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _DATA_FILE.exists():
        _save_raw({"users": {}})


def _load_raw() -> Dict[str, Any]:
    _ensure_file()
    try:
        with open(_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"[friend_battle_class] load failed: {e}")
        return {"users": {}}


def _save_raw(data: Dict[str, Any]):
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[friend_battle_class] save failed: {e}")


def get_class_state(qqid: int) -> Tuple[int, int]:
    """(tier_index, cp)，新用户 B5、0。"""
    raw = _load_raw()
    u = raw.get("users", {}).get(str(qqid))
    if not u:
        return 0, 0
    name = u.get("tier", "B5")
    idx = TIER_INDEX.get(name, 0)
    cp = int(u.get("cp", 0))
    return idx, cp


def list_battle_users() -> Dict[int, Dict[str, Any]]:
    """已参与过友人对战的用户：qqid -> 存档字段（tier/cp/fb_win_streak 等）。"""
    raw = _load_raw()
    users = raw.get("users", {})
    out: Dict[int, Dict[str, Any]] = {}
    for k, v in users.items():
        try:
            out[int(k)] = dict(v)
        except (TypeError, ValueError):
            continue
    return out


def format_rank_brief(tier_idx: int, cp: int, streak: int = 0) -> str:
    """排行展示用单行段位摘要。"""
    rule = TIER_RULES[tier_idx]
    tail = f"  友対{streak}连胜" if streak else ""
    if rule.cp_to_next is None:
        return f"{rule.name}  CP {cp}{tail}"
    return f"{rule.name}  CP {cp}/{rule.cp_to_next}{tail}"


def get_win_streak(qqid: int) -> int:
    """友人对战连胜场数（仅统计该玩法）。"""
    raw = _load_raw()
    u = raw.get("users", {}).get(str(qqid))
    if not u:
        return 0
    return max(0, int(u.get("fb_win_streak", 0)))


def streak_win_multiplier(streak_before: int) -> float:
    """
    连胜系数：本局结算前已连续胜利 streak_before 场，则本局「基础胜 CP」乘以该系数。
    streak_before=0 → 1.00；之后每多 1 胜 +0.07，上限 +0.45（即最高 1.45）。
    """
    return 1.0 + min(0.45, 0.07 * max(0, int(streak_before)))


def _set_state(qqid: int, tier_idx: int, cp: int, *, win_streak: Optional[int] = None):
    raw = _load_raw()
    users = raw.setdefault("users", {})
    key = str(qqid)
    prev = dict(users.get(key, {}))
    tier_idx = max(0, min(tier_idx, len(TIER_RULES) - 1))
    cp = int(cp)
    if tier_idx == 0 and cp < 0:
        cp = 0
    prev["tier"] = TIER_RULES[tier_idx].name
    prev["cp"] = cp
    if win_streak is not None:
        prev["fb_win_streak"] = max(0, int(win_streak))
    users[key] = prev
    _save_raw(raw)


def tier_name(idx: int) -> str:
    return TIER_RULES[max(0, min(idx, len(TIER_RULES) - 1))].name


def _relation(winner_idx: int, loser_idx: int) -> str:
    """败者视角：胜者是格上 / 同格 / 格下（用于败北减 CP）。"""
    if winner_idx > loser_idx:
        return "upper"
    if winner_idx < loser_idx:
        return "lower"
    return "same"


def _win_relation(winner_idx: int, loser_idx: int) -> str:
    """胜方视角：对手是格上 / 同格 / 格下（用于胜利加 CP）。"""
    if loser_idx > winner_idx:
        return "upper"
    if loser_idx < winner_idx:
        return "lower"
    return "same"


def _is_boss(
    winner_rating: int,
    loser_rating: int,
    winner_idx: int,
    loser_idx: int,
) -> bool:
    """ボスオトモダチ：以下克上且段位或 rating 差距较大。"""
    if winner_idx >= loser_idx:
        return False
    if loser_idx - winner_idx >= 2:
        return True
    if int(loser_rating) - int(winner_rating) >= 400:
        return True
    return False


def _win_cp(rule: _TierRule, winner_idx: int, loser_idx: int, boss: bool) -> int:
    if rule.flat_win is not None:
        base = rule.flat_win
    else:
        rel = _win_relation(winner_idx, loser_idx)
        if rel == "upper":
            base = rule.win_upper
        elif rel == "same":
            base = rule.win_same
        else:
            base = rule.win_lower
    extra = rule.boss_add if boss else 0
    return base + extra


def _loss_cp(rule: _TierRule, winner_idx: int, loser_idx: int) -> int:
    if rule.flat_loss is not None:
        return rule.flat_loss
    if rule.flat_loss_no_grade is not None:
        return rule.flat_loss_no_grade
    rel = _relation(winner_idx, loser_idx)
    if rel == "upper":
        return rule.loss_vs_upper
    if rel == "same":
        return rule.loss_vs_same
    return rule.loss_vs_lower


def _apply_cp_change(tier_idx: int, cp: int, delta: int) -> Tuple[int, int, List[str]]:
    """
    应用 CP 变化，处理升段（LEGEND 不消耗 cp_to_next）。
    返回 (new_idx, new_cp, lines)。
    """
    lines: List[str] = []
    cp += delta
    tier_idx = max(0, min(tier_idx, len(TIER_RULES) - 1))

    # B5 地板：CP 不低于 0
    if tier_idx == 0 and cp < 0:
        cp = 0

    while tier_idx < len(TIER_RULES) - 1:
        rule = TIER_RULES[tier_idx]
        need = rule.cp_to_next
        if need is None:
            break
        if cp < need:
            break
        cp -= need
        tier_idx += 1
        lines.append(f"升段 → {TIER_RULES[tier_idx].name}")
        if TIER_RULES[tier_idx].cp_to_next is None:
            break

    # 掉段：CP < 0 时降一段并将 CP 置 0（可循环直到 B5）
    while cp < 0 and tier_idx > 0:
        tier_idx -= 1
        cp = 0
        lines.append(f"掉段 → {TIER_RULES[tier_idx].name}")

    if tier_idx == 0 and cp < 0:
        cp = 0

    return tier_idx, cp, lines


def extra_cp_bonus(
    winner_is_challenger: bool,
    my_achv: float,
    opp_achv: float,
    my_dx: int,
    opp_dx: int,
    my_rating: int,
    opp_rating: int,
) -> Tuple[int, List[str]]:
    """
    额外 CP（小加成，与表格分离）：最多 +2。
    - 达成碾压：胜且领先 >= 0.05% → +1
    - DX 碾压：胜且同达成、DX 领先 >= 200 → +1
    - 总 rating 以下克上：胜且我方总 rating 低于对方 >= 200 → +1
    """
    notes: List[str] = []
    if not winner_is_challenger:
        return 0, notes
    won = my_achv > opp_achv or (my_achv == opp_achv and my_dx > opp_dx)
    if not won:
        return 0, notes
    bits: List[str] = []
    if my_achv > opp_achv and (my_achv - opp_achv) >= 0.05:
        bits.append("达成碾压+1CP")
    if my_achv == opp_achv and my_dx > opp_dx and (my_dx - opp_dx) >= 200:
        bits.append("DX碾压+1CP")
    if my_rating + 200 <= opp_rating:
        bits.append("总rating以下克上+1CP")
    bonus = min(2, len(bits))
    notes = bits[:bonus]
    return bonus, notes


def settle_battle_cp_with_extras(
    challenger_qq: int,
    opponent_qq: int,
    challenger_wins: bool,
    my_rating: int,
    opp_rating: int,
    my_achv: float,
    opp_achv: float,
    my_dx: int,
    opp_dx: int,
) -> str:
    """先结算表格 CP（含连胜系数），再给挑战者加额外 CP（仅胜者）。"""
    ci, cc = get_class_state(challenger_qq)
    oi, oc = get_class_state(opponent_qq)
    sc = get_win_streak(challenger_qq)
    so = get_win_streak(opponent_qq)

    if challenger_wins:
        boss = _is_boss(my_rating, opp_rating, ci, oi)
        wgain = _win_cp(TIER_RULES[ci], ci, oi, boss)
        oloss = _loss_cp(TIER_RULES[oi], ci, oi)
        mult = streak_win_multiplier(sc)
        wgain_eff = max(1, int(round(wgain * mult)))

        nci, ncc, up_c = _apply_cp_change(ci, cc, wgain_eff)
        noi, noc, up_o = _apply_cp_change(oi, oc, oloss)

        extra, extra_notes = extra_cp_bonus(
            True, my_achv, opp_achv, my_dx, opp_dx, my_rating, opp_rating
        )
        if extra:
            nci, ncc, up_e = _apply_cp_change(nci, ncc, extra)
            up_c = up_c + up_e

        _set_state(challenger_qq, nci, ncc, win_streak=sc + 1)
        _set_state(opponent_qq, noi, noc, win_streak=0)

        streak_line = ""
        if mult > 1.0001:
            streak_line = f"连胜系数: ×{mult:.2f}（友対{sc}连胜→本局基础胜 CP {wgain}→{wgain_eff}）"

        lines = [
            "── 段位·CP ──",
            f"你: {tier_name(ci)} → {tier_name(nci)}  CP {cc} → {ncc}（胜 +{wgain_eff}" + ("·ボス" if boss else "") + "）",
        ]
        if streak_line:
            lines.append(streak_line)
        if extra_notes:
            lines.append(f"额外: {' / '.join(extra_notes)}（+{extra}CP）")
        if up_c:
            lines.append(" ".join(up_c))
        lines.append(
            f"对手: {tier_name(oi)} → {tier_name(noi)}  CP {oc} → {noc}（败 {oloss:+d}）"
        )
        if up_o:
            lines.append("对手: " + " ".join(up_o))
        return "\n".join(lines)

    boss = _is_boss(opp_rating, my_rating, oi, ci)
    ogain = _win_cp(TIER_RULES[oi], oi, ci, boss)
    closs = _loss_cp(TIER_RULES[ci], oi, ci)
    mult_o = streak_win_multiplier(so)
    ogain_eff = max(1, int(round(ogain * mult_o)))

    noi, noc, up_o = _apply_cp_change(oi, oc, ogain_eff)
    nci, ncc, up_c = _apply_cp_change(ci, cc, closs)

    _set_state(opponent_qq, noi, noc, win_streak=so + 1)
    _set_state(challenger_qq, nci, ncc, win_streak=0)

    streak_line_o = ""
    if mult_o > 1.0001:
        streak_line_o = f"对手连胜系数: ×{mult_o:.2f}（友対{so}连胜→基础胜 CP {ogain}→{ogain_eff}）"

    lines = [
        "── 段位·CP ──",
        f"你: {tier_name(ci)} → {tier_name(nci)}  CP {cc} → {ncc}（败 {closs:+d}）",
    ]
    if up_c:
        lines.append(" ".join(up_c))
    lines.append(
        f"对手: {tier_name(oi)} → {tier_name(noi)}  CP {oc} → {noc}（胜 +{ogain_eff}" + ("·ボス" if boss else "") + "）"
    )
    if streak_line_o:
        lines.append(streak_line_o)
    if up_o:
        lines.append("对手: " + " ".join(up_o))
    return "\n".join(lines)


def format_class_line(qqid: int) -> str:
    idx, cp = get_class_state(qqid)
    rule = TIER_RULES[idx]
    need = rule.cp_to_next
    streak = get_win_streak(qqid)
    tail = f" 友対{streak}连胜" if streak else ""
    if need is None:
        return f"段位 {rule.name}  CP {cp}（已达 LEGEND，胜+1/败-1）{tail}"
    return f"段位 {rule.name}  CP {cp}/{need}（距下一段）{tail}"
