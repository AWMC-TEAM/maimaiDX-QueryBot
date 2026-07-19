"""舞萌开字母：按群持久化通关用时、自适应星级阈值与排行榜。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from ..config import letter_stats_file, log
from .tool import writefile

GroupId = Union[int, str]

# 默认阈值（秒），比例 30:45:60:90:180 = 1:1.5:2:3:6
DEFAULT_FIVE_STAR = 30.0
MIN_FIVE_STAR = 15.0
MAX_FIVE_STAR = 30.0
STAR_RATIO: Dict[int, float] = {5: 1.0, 4: 1.5, 3: 2.0, 2: 3.0, 1: 6.0}
HISTORY_WINDOW = 40
MIN_SAMPLES_FOR_ADAPTIVE = 8


@dataclass(frozen=True)
class StarThresholds:
    """五档上限：elapsed <= limits[stars] 得对应星；超出一星上限为 0 星。"""

    limits: Dict[int, float]  # keys 5..1
    adaptive: bool = False
    sample_count: int = 0

    @property
    def five_star(self) -> float:
        return float(self.limits[5])

    def star_for(self, elapsed: float) -> int:
        t = max(0.0, float(elapsed))
        for stars in (5, 4, 3, 2, 1):
            if t <= self.limits[stars]:
                return stars
        return 0

    def format_lines(self) -> str:
        mode = "自适应" if self.adaptive else "默认"
        parts = [
            f"⭐️×{s}≤{self.limits[s]:.3f}秒" for s in (5, 4, 3, 2, 1)
        ]
        return f"本局阈值（{mode}，样本 {self.sample_count}）：" + " / ".join(parts)


def default_thresholds(*, sample_count: int = 0) -> StarThresholds:
    five = DEFAULT_FIVE_STAR
    limits = {s: five * STAR_RATIO[s] for s in STAR_RATIO}
    return StarThresholds(limits=limits, adaptive=False, sample_count=sample_count)


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return DEFAULT_FIVE_STAR
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    p = min(1.0, max(0.0, float(p)))
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def compute_thresholds(history_elapsed: List[float]) -> StarThresholds:
    """
    自适应五星阈值：
    - 样本不足时用默认 30/45/60/90/180
    - 否则取最近 HISTORY_WINDOW 局的 P35，夹紧到 [15, 30]
    - 其余星级按 1:1.5:2:3:6 相对五星缩放
    群打得越快，P35 越低，阈值越严（但五星不低于 15s）。
    """
    samples = [max(0.0, float(x)) for x in history_elapsed if x is not None]
    n = len(samples)
    if n < MIN_SAMPLES_FOR_ADAPTIVE:
        return default_thresholds(sample_count=n)
    window = sorted(samples[-HISTORY_WINDOW:])
    five = min(MAX_FIVE_STAR, max(MIN_FIVE_STAR, _percentile(window, 0.35)))
    limits = {s: five * STAR_RATIO[s] for s in STAR_RATIO}
    return StarThresholds(limits=limits, adaptive=True, sample_count=n)


class LetterClearRecord(BaseModel):
    elapsed: float
    stars: int = 0
    at: float = 0.0
    score_pool: int = 0
    break_pool: int = 0
    players: List[str] = Field(default_factory=list)


class LetterMemberStats(BaseModel):
    uid: str = ""
    name: str = ""
    billing_id: int = 0
    score: int = 0
    weight: int = 0
    games: int = 0
    best_elapsed: Optional[float] = None


class LetterGroupStats(BaseModel):
    clears: List[LetterClearRecord] = Field(default_factory=list)
    members: Dict[str, LetterMemberStats] = Field(default_factory=dict)


class LetterStatsStore(BaseModel):
    groups: Dict[str, LetterGroupStats] = Field(default_factory=dict)


class LetterStatsManager:
    MAX_CLEARS_KEPT = 200

    def __init__(self) -> None:
        if letter_stats_file.exists():
            try:
                with open(letter_stats_file, "r", encoding="utf-8") as f:
                    self.store = LetterStatsStore.model_validate(json.load(f))
            except Exception as exc:
                log.warning(f"[LetterStats] 读取失败，使用空库：{type(exc).__name__}: {exc}")
                self.store = LetterStatsStore()
        else:
            self.store = LetterStatsStore()

    def _gid(self, gid: GroupId) -> str:
        return str(gid)

    def _group(self, gid: GroupId) -> LetterGroupStats:
        key = self._gid(gid)
        if key not in self.store.groups:
            self.store.groups[key] = LetterGroupStats()
        return self.store.groups[key]

    async def _save(self) -> None:
        await writefile(letter_stats_file, self.store.model_dump())

    def history_elapsed(self, gid: GroupId) -> List[float]:
        group = self.store.groups.get(self._gid(gid))
        if not group:
            return []
        return [float(c.elapsed) for c in group.clears]

    def thresholds_for(self, gid: GroupId) -> StarThresholds:
        return compute_thresholds(self.history_elapsed(gid))

    async def record_clear(
        self,
        gid: GroupId,
        *,
        elapsed: float,
        stars: int,
        score_pool: int,
        break_pool: int,
        rewards: List[Tuple[str, int, str, int, int]],
    ) -> None:
        """
        rewards: (uid, billing_id, name, score, weight)
        通关后写入；不影响本局已算好的阈值（阈值基于历史）。
        """
        group = self._group(gid)
        players: List[str] = []
        for uid, billing_id, name, score, weight in rewards:
            players.append(str(uid))
            member = group.members.get(str(uid))
            if member is None:
                member = LetterMemberStats(uid=str(uid))
                group.members[str(uid)] = member
            member.uid = str(uid)
            member.name = name or member.name or str(uid)
            member.billing_id = int(billing_id)
            member.score += max(0, int(score))
            member.weight += max(0, int(weight))
            member.games += 1
            el = float(elapsed)
            if member.best_elapsed is None or el < member.best_elapsed:
                member.best_elapsed = el
        group.clears.append(
            LetterClearRecord(
                elapsed=float(elapsed),
                stars=int(stars),
                at=time.time(),
                score_pool=int(score_pool),
                break_pool=int(break_pool),
                players=players,
            )
        )
        if len(group.clears) > self.MAX_CLEARS_KEPT:
            group.clears = group.clears[-self.MAX_CLEARS_KEPT :]
        await self._save()

    def _members_with_uid(self, gid: GroupId) -> List[LetterMemberStats]:
        group = self.store.groups.get(self._gid(gid))
        if not group:
            return []
        rows: List[LetterMemberStats] = []
        for uid, m in group.members.items():
            if not m.uid:
                m.uid = str(uid)
            rows.append(m)
        return rows

    def score_ranking(self, gid: GroupId, *, top_n: int = 20) -> List[LetterMemberStats]:
        rows = [m for m in self._members_with_uid(gid) if m.score > 0]
        rows.sort(key=lambda m: (-m.score, -m.weight, m.name))
        return rows[:top_n]

    def contrib_ranking(self, gid: GroupId, *, top_n: int = 20) -> List[LetterMemberStats]:
        rows = [m for m in self._members_with_uid(gid) if m.weight > 0]
        rows.sort(key=lambda m: (-m.weight, -m.score, m.name))
        return rows[:top_n]

    def time_ranking(self, gid: GroupId, *, top_n: int = 20) -> List[LetterMemberStats]:
        rows = [m for m in self._members_with_uid(gid) if m.best_elapsed is not None]
        rows.sort(key=lambda m: (float(m.best_elapsed), -m.games, m.name))  # type: ignore[arg-type]
        return rows[:top_n]


letter_stats = LetterStatsManager()
