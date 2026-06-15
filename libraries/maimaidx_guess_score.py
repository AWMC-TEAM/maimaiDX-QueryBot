import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import guess_score_file
from .maimaidx_group_rating import build_forward_node
from .tool import writefile


class GuessMemberScore(BaseModel):
    score: int = 0
    name: str = ''
    weekly_score: int = 0
    weekly_week: str = ''
    streak: int = 0


class GuessGroupScores(BaseModel):
    members: Dict[str, GuessMemberScore] = Field(default_factory=dict)


class GuessScoreStore(BaseModel):
    groups: Dict[str, GuessGroupScores] = Field(default_factory=dict)


class GuessScoreManager:

    PIC_POINTS = {3: 3, 2: 2, 1: 1}
    PIC_CLEAR_POINTS = 1
    SONG_POINTS = 1
    STREAK_BONUS_MAX = 3

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
    def current_week_key() -> str:
        year, week, _ = datetime.now().isocalendar()
        return f'{year}-W{week:02d}'

    async def _save(self) -> None:
        await writefile(guess_score_file, self.store.model_dump())

    def _ensure_weekly(self, member: GuessMemberScore) -> None:
        week_key = self.current_week_key()
        if member.weekly_week != week_key:
            member.weekly_week = week_key
            member.weekly_score = 0

    def get_ranking(
        self,
        gid: int,
        top_n: Optional[int] = None,
    ) -> List[Tuple[int, str, int]]:
        gk = self._gid_key(gid)
        group = self.store.groups.get(gk)
        if not group:
            return []
        items = [
            (int(uid), member.name or uid, member.score)
            for uid, member in group.members.items()
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_weekly_ranking(
        self,
        gid: int,
        top_n: Optional[int] = None,
    ) -> List[Tuple[int, str, int]]:
        gk = self._gid_key(gid)
        group = self.store.groups.get(gk)
        if not group:
            return []
        week_key = self.current_week_key()
        items = [
            (int(uid), member.name or uid, member.weekly_score)
            for uid, member in group.members.items()
            if member.weekly_week == week_key and member.weekly_score > 0
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_rank(self, gid: int, uid: int) -> int:
        for index, (member_uid, _, _) in enumerate(self.get_ranking(gid), start=1):
            if member_uid == uid:
                return index
        return len(self.get_ranking(gid)) + 1

    def get_weekly_rank(self, gid: int, uid: int) -> int:
        for index, (member_uid, _, _) in enumerate(self.get_weekly_ranking(gid), start=1):
            if member_uid == uid:
                return index
        return len(self.get_weekly_ranking(gid)) + 1

    def streak_bonus(self, streak: int) -> int:
        if streak < 2:
            return 0
        return min(streak - 1, self.STREAK_BONUS_MAX)

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
        base_points: int,
    ) -> Tuple[int, int, int, int, int, int, int, int]:
        group = self._get_group(gid)
        uk = self._uid_key(uid)
        for member_uid, member in group.members.items():
            if member_uid != uk:
                member.streak = 0
        winner = self._get_member(gid, uid)
        self._ensure_weekly(winner)
        winner.streak += 1
        bonus = self.streak_bonus(winner.streak)
        total_added = base_points + bonus
        winner.score += total_added
        winner.weekly_score += total_added
        if name:
            winner.name = name
        await self._save()
        return (
            total_added,
            base_points,
            bonus,
            winner.streak,
            winner.score,
            self.get_rank(gid, uid),
            winner.weekly_score,
            self.get_weekly_rank(gid, uid),
        )

    async def add_score(
        self,
        gid: int,
        uid: int,
        name: str,
        points: int,
    ) -> Tuple[int, int, int, int, int]:
        gk = self._gid_key(gid)
        uk = self._uid_key(uid)
        if gk not in self.store.groups:
            self.store.groups[gk] = GuessGroupScores()
        group = self.store.groups[gk]
        if uk not in group.members:
            group.members[uk] = GuessMemberScore()
        member = group.members[uk]
        self._ensure_weekly(member)
        member.score += points
        member.weekly_score += points
        if name:
            member.name = name
        await self._save()
        return (
            points,
            member.score,
            self.get_rank(gid, uid),
            member.weekly_score,
            self.get_weekly_rank(gid, uid),
        )

    @staticmethod
    def format_settlement_lines(
        added: int,
        base: int,
        bonus: int,
        streak: int,
        total: int,
        rank: int,
        weekly_total: int,
        weekly_rank: int,
    ) -> str:
        bonus_part = f'（含连击 +{bonus}）' if bonus > 0 else ''
        streak_part = f'\n{streak} 连击！' if streak >= 2 else ''
        return (
            f'本次 +{added} 分{bonus_part}，总分 {total} 分，群内排名第 {rank} 名'
            f'{streak_part}\n'
            f'本周 {weekly_total} 分，群内周排名第 {weekly_rank} 名'
        )

    def build_ranking_forward(
        self,
        gid: int,
        self_id: int,
        *,
        top_n: int = 20,
        weekly: bool = False,
    ) -> Tuple[str, List[dict]]:
        if weekly:
            ranking = self.get_weekly_ranking(gid, top_n=top_n)
            if not ranking:
                return '本群本周暂无猜歌积分记录。', []
            title = f'猜歌积分周榜（{self.current_week_key()}，前 {min(top_n, len(ranking))} 名）'
        else:
            ranking = self.get_ranking(gid, top_n=top_n)
            if not ranking:
                return '本群暂无猜歌积分记录。', []
            title = f'猜歌积分排行榜（前 {min(top_n, len(ranking))} 名）'
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
