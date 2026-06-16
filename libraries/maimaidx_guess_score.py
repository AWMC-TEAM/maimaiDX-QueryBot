import json
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import guess_score_file
from .maimaidx_group_rating import build_forward_node
from .tool import writefile


class PeriodSpec(NamedTuple):
    score_attr: str
    key_attr: str
    label: str
    rank_label: str
    board_title: str


class GuessMemberScore(BaseModel):
    score: int = 0
    name: str = ''
    streak: int = 0
    daily_score: int = 0
    daily_key: str = ''
    weekly_score: int = 0
    weekly_week: str = ''
    monthly_score: int = 0
    monthly_key: str = ''
    yearly_score: int = 0
    yearly_key: str = ''
    season_score: int = 0
    season_key: str = ''


class GuessGroupScores(BaseModel):
    members: Dict[str, GuessMemberScore] = Field(default_factory=dict)


class GuessScoreStore(BaseModel):
    groups: Dict[str, GuessGroupScores] = Field(default_factory=dict)


class GuessScoreManager:

    PIC_POINTS = {3: 3, 2: 2, 1: 1}
    PIC_CLEAR_POINTS = 1
    SONG_POINTS = 1
    STREAK_BONUS_MAX = 3

    PERIODS: Dict[str, PeriodSpec] = {
        'daily': PeriodSpec('daily_score', 'daily_key', '今日', '日榜', '日榜'),
        'weekly': PeriodSpec('weekly_score', 'weekly_week', '本周', '周榜', '周榜'),
        'monthly': PeriodSpec('monthly_score', 'monthly_key', '本月', '月榜', '月榜'),
        'yearly': PeriodSpec('yearly_score', 'yearly_key', '今年', '年榜', '年榜'),
        'season': PeriodSpec('season_score', 'season_key', '赛季', '赛季榜', '赛季榜'),
    }

    def pic_points_for(self, data) -> int:
        if data.interference_cleared:
            return self.PIC_CLEAR_POINTS
        return self.PIC_POINTS.get(data.difficulty, 1)

    def __init__(self) -> None:
        if guess_score_file.exists():
            with open(guess_score_file, 'r', encoding='utf-8') as f:
                self.store = GuessScoreStore.model_validate(json.load(f))
        else:
            self.store = GuessScoreStore()

    @staticmethod
    def _gid_key(gid: int) -> str:
        return str(gid)

    @staticmethod
    def _uid_key(uid: int) -> str:
        return str(uid)

    @staticmethod
    def current_day_key() -> str:
        return datetime.now().strftime('%Y-%m-%d')

    @staticmethod
    def current_week_key() -> str:
        year, week, _ = datetime.now().isocalendar()
        return f'{year}-W{week:02d}'

    @staticmethod
    def current_month_key() -> str:
        return datetime.now().strftime('%Y-%m')

    @staticmethod
    def current_year_key() -> str:
        return datetime.now().strftime('%Y')

    @staticmethod
    def current_season_key() -> str:
        now = datetime.now()
        season = (now.month - 1) // 3 + 1
        return f'{now.year}-S{season}'

    @classmethod
    def period_key(cls, period: str) -> str:
        return {
            'daily': cls.current_day_key(),
            'weekly': cls.current_week_key(),
            'monthly': cls.current_month_key(),
            'yearly': cls.current_year_key(),
            'season': cls.current_season_key(),
        }[period]

    async def _save(self) -> None:
        await writefile(guess_score_file, self.store.model_dump())

    def _ensure_period(self, member: GuessMemberScore, period: str) -> None:
        spec = self.PERIODS[period]
        current_key = self.period_key(period)
        if getattr(member, spec.key_attr) != current_key:
            setattr(member, spec.key_attr, current_key)
            setattr(member, spec.score_attr, 0)

    def _ensure_all_periods(self, member: GuessMemberScore) -> None:
        for period in self.PERIODS:
            self._ensure_period(member, period)

    def get_ranking(
        self,
        gid: int,
        top_n: Optional[int] = None,
    ) -> List[Tuple[int, str, int]]:
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return []
        items = [
            (int(uid), member.name or uid, member.score)
            for uid, member in group.members.items()
            if member.score > 0
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_period_ranking(
        self,
        gid: int,
        period: str,
        top_n: Optional[int] = None,
    ) -> List[Tuple[int, str, int]]:
        spec = self.PERIODS[period]
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return []
        current_key = self.period_key(period)
        items = [
            (int(uid), member.name or uid, getattr(member, spec.score_attr))
            for uid, member in group.members.items()
            if getattr(member, spec.key_attr) == current_key
            and getattr(member, spec.score_attr) > 0
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_rank(self, gid: int, uid: int) -> int:
        ranking = self.get_ranking(gid)
        for index, (member_uid, _, _) in enumerate(ranking, start=1):
            if member_uid == uid:
                return index
        return len(ranking) + 1

    def get_period_rank(self, gid: int, uid: int, period: str) -> int:
        ranking = self.get_period_ranking(gid, period)
        for index, (member_uid, _, _) in enumerate(ranking, start=1):
            if member_uid == uid:
                return index
        return len(ranking) + 1

    def get_period_snapshot(self, gid: int, uid: int) -> Dict[str, Tuple[int, int]]:
        member = self.store.groups.get(self._gid_key(gid), GuessGroupScores()).members.get(self._uid_key(uid))
        snapshot: Dict[str, Tuple[int, int]] = {}
        for period, spec in self.PERIODS.items():
            score = getattr(member, spec.score_attr, 0) if member else 0
            snapshot[period] = (score, self.get_period_rank(gid, uid, period))
        return snapshot

    def streak_bonus(self, streak: int) -> int:
        if streak < 2:
            return 0
        return min(streak - 1, self.STREAK_BONUS_MAX)

    def get_score_multiplier(
        self,
        *,
        first_stage: bool,
        first_guess: bool,
    ) -> Tuple[int, List[str]]:
        multiplier = 1
        tags: List[str] = []
        if first_stage:
            multiplier *= 2
            tags.append('首阶段×2')
        if first_guess:
            multiplier *= 2
            tags.append('首答×2')
        return multiplier, tags

    def _get_group(self, gid: int) -> GuessGroupScores:
        gk = self._gid_key(gid)
        if gk not in self.store.groups:
            self.store.groups[gk] = GuessGroupScores()
        return self.store.groups[gk]

    def _get_member(self, gid: int, uid: int) -> GuessMemberScore:
        group = self._get_group(gid)
        uk = self._uid_key(uid)
        if uk not in group.members:
            group.members[uk] = GuessMemberScore()
        return group.members[uk]

    async def reset_all_streaks(self, gid: int) -> None:
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return
        for member in group.members.values():
            member.streak = 0
        await self._save()

    async def award_correct_guess(
        self,
        gid: int,
        uid: int,
        name: str,
        raw_base: int,
        multiplier: int,
    ) -> Tuple[int, int, int, int, int, int, Dict[str, Tuple[int, int]]]:
        group = self._get_group(gid)
        uk = self._uid_key(uid)
        for member_uid, member in group.members.items():
            if member_uid != uk:
                member.streak = 0
        winner = self._get_member(gid, uid)
        self._ensure_all_periods(winner)
        winner.streak += 1
        combo = self.streak_bonus(winner.streak)
        total_added = (raw_base + combo) * multiplier
        winner.score += total_added
        for period, spec in self.PERIODS.items():
            setattr(winner, spec.score_attr, getattr(winner, spec.score_attr) + total_added)
        if name:
            winner.name = name
        await self._save()
        period_snapshot = self.get_period_snapshot(gid, uid)
        return (
            total_added,
            raw_base,
            combo,
            winner.streak,
            winner.score,
            self.get_rank(gid, uid),
            period_snapshot,
        )

    @staticmethod
    def format_settlement_lines(
        added: int,
        raw_base: int,
        combo: int,
        multiplier: int,
        streak: int,
        total: int,
        total_rank: int,
        period_snapshot: Dict[str, Tuple[int, int]],
        multiplier_tags: Optional[List[str]] = None,
    ) -> str:
        detail_parts: List[str] = []
        if multiplier_tags:
            detail_parts.extend(multiplier_tags)
        if combo > 0:
            detail_parts.append(f'连击 +{combo}')
        if multiplier > 1:
            detail_parts.append(f'({raw_base}+{combo})×{multiplier}')
        bonus_part = f'（{"，".join(detail_parts)}）' if detail_parts else ''
        streak_part = f'\n{streak} 连击！' if streak >= 2 else ''

        period_lines: List[str] = []
        for period in ('daily', 'weekly', 'monthly', 'season', 'yearly'):
            spec = GuessScoreManager.PERIODS[period]
            score, rank = period_snapshot[period]
            extra = ''
            if period == 'season':
                extra = f'（{GuessScoreManager.current_season_key()}）'
            period_lines.append(
                f'{spec.label} {score} 分{extra}，群内{spec.rank_label}第 {rank} 名'
            )

        return (
            f'本次 +{added} 分{bonus_part}，总分 {total} 分，总榜第 {total_rank} 名'
            f'{streak_part}\n'
            + '\n'.join(period_lines)
        )

    def build_ranking_forward(
        self,
        gid: int,
        self_id: int,
        *,
        period: str = 'total',
        top_n: int = 20,
    ) -> Tuple[str, List[dict]]:
        if period == 'total':
            ranking = self.get_ranking(gid, top_n=top_n)
            if not ranking:
                return '本群暂无猜歌积分记录。', []
            title = f'猜歌积分总榜（前 {min(top_n, len(ranking))} 名）'
        else:
            spec = self.PERIODS[period]
            ranking = self.get_period_ranking(gid, period, top_n=top_n)
            if not ranking:
                return f'本群当前{spec.board_title}暂无积分记录。', []
            key = self.period_key(period)
            title = f'猜歌积分{spec.board_title}（{key}，前 {min(top_n, len(ranking))} 名）'
        nodes = [
            build_forward_node(
                str(self_id),
                name,
                f'{index}. {name} — {score} 分',
            )
            for index, (_, name, score) in enumerate(ranking, start=1)
        ]
        return title, nodes


guess_score = GuessScoreManager()
