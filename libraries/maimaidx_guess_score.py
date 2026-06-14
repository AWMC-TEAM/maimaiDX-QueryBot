import json
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..config import guess_score_file
from .maimaidx_group_rating import build_forward_node
from .tool import writefile


class GuessMemberScore(BaseModel):
    score: int = 0
    name: str = ''


class GuessGroupScores(BaseModel):
    members: Dict[str, GuessMemberScore] = Field(default_factory=dict)


class GuessScoreStore(BaseModel):
    groups: Dict[str, GuessGroupScores] = Field(default_factory=dict)


class GuessScoreManager:

    PIC_POINTS = {3: 3, 2: 2, 1: 1}
    PIC_CLEAR_POINTS = 1
    SONG_POINTS = 1

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

    async def _save(self) -> None:
        await writefile(guess_score_file, self.store.model_dump())

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

    def get_rank(self, gid: int, uid: int) -> int:
        for index, (member_uid, _, _) in enumerate(self.get_ranking(gid), start=1):
            if member_uid == uid:
                return index
        return len(self.get_ranking(gid)) + 1

    async def add_score(
        self,
        gid: int,
        uid: int,
        name: str,
        points: int,
    ) -> Tuple[int, int, int]:
        gk = self._gid_key(gid)
        uk = self._uid_key(uid)
        if gk not in self.store.groups:
            self.store.groups[gk] = GuessGroupScores()
        group = self.store.groups[gk]
        if uk not in group.members:
            group.members[uk] = GuessMemberScore()
        member = group.members[uk]
        member.score += points
        if name:
            member.name = name
        await self._save()
        return points, member.score, self.get_rank(gid, uid)

    @staticmethod
    def format_settlement_line(added: int, total: int, rank: int) -> str:
        return f'本次 +{added} 分，总分 {total} 分，群内排名第 {rank} 名'

    def build_ranking_forward(
        self,
        gid: int,
        self_id: int,
        *,
        top_n: int = 20,
    ) -> Tuple[str, List[dict]]:
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
