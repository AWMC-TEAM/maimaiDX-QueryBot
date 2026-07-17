import json
from datetime import date, datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

from loguru import logger as log
from pydantic import BaseModel, Field

from ..config import guess_score_file, guess_score_history_file
from .maimaidx_group_rating import build_forward_node
from .maimaidx_platform import GroupId, UserId, format_forward_nodes_as_text, is_likely_qq_group_id, send_group_plain_text
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
    archived_periods: Dict[str, str] = Field(default_factory=dict)


class GuessScoreStore(BaseModel):
    groups: Dict[str, GuessGroupScores] = Field(default_factory=dict)


class GuessHistoryEntry(BaseModel):
    uid: str
    name: str
    score: int


class GuessHistoryRecord(BaseModel):
    period: str
    period_key: str
    archived_at: str
    ranking: List[GuessHistoryEntry] = Field(default_factory=list)


class GuessHistoryGroup(BaseModel):
    records: List[GuessHistoryRecord] = Field(default_factory=list)


class GuessScoreHistoryStore(BaseModel):
    groups: Dict[str, GuessHistoryGroup] = Field(default_factory=dict)


class GuessScoreManager:

    PIC_POINTS = {4: 4, 3: 3, 2: 2, 1: 1}
    PIC_CLEAR_POINTS = 1
    SONG_MAX_POINTS = 7
    AUDIO_MIN_POINTS = 5
    AUDIO_MAX_POINTS = 10
    AUDIO_STAGE_POINTS = (10, 9, 7, 5)
    # 猜曲子赛季限时双倍（含 2026-06-30 当天）
    AUDIO_SEASON_DOUBLE_END = date(2026, 6, 30)
    MAX_HISTORY_PER_PERIOD = 30

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

    def song_points_for(self, hint_step: int) -> int:
        # 开局与 1/7 提示均为最高分，随后每多一条提示减 1 分，封面 7/7 为 1 分
        return max(1, self.SONG_MAX_POINTS + 1 - max(hint_step, 1))

    def audio_points_for(self, hint_step: int) -> int:
        if hint_step <= 0:
            return self.AUDIO_MAX_POINTS
        idx = min(int(hint_step), len(self.AUDIO_STAGE_POINTS)) - 1
        return self.AUDIO_STAGE_POINTS[idx]

    @classmethod
    def audio_season_double_active(cls) -> bool:
        return date.today() <= cls.AUDIO_SEASON_DOUBLE_END

    def __init__(self) -> None:
        if guess_score_file.exists():
            with open(guess_score_file, 'r', encoding='utf-8') as f:
                self.store = GuessScoreStore.model_validate(json.load(f))
        else:
            self.store = GuessScoreStore()
        if guess_score_history_file.exists():
            with open(guess_score_history_file, 'r', encoding='utf-8') as f:
                self.history_store = GuessScoreHistoryStore.model_validate(json.load(f))
        else:
            self.history_store = GuessScoreHistoryStore()

    @staticmethod
    def _gid_key(gid: GroupId) -> str:
        return str(gid)

    @staticmethod
    def _uid_key(uid: UserId) -> str:
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

    async def _save_history(self) -> None:
        await writefile(guess_score_history_file, self.history_store.model_dump())

    @classmethod
    def previous_period_key(cls, period: str) -> str:
        now = datetime.now()
        if period == 'daily':
            return (now - timedelta(days=1)).strftime('%Y-%m-%d')
        if period == 'weekly':
            prev = now - timedelta(days=7)
            year, week, _ = prev.isocalendar()
            return f'{year}-W{week:02d}'
        if period == 'monthly':
            first = now.replace(day=1)
            prev = first - timedelta(days=1)
            return prev.strftime('%Y-%m')
        if period == 'yearly':
            return str(now.year - 1)
        if period == 'season':
            month = now.month
            year = now.year
            season = (month - 1) // 3 + 1
            if season == 1:
                return f'{year - 1}-S4'
            return f'{year}-S{season - 1}'
        raise ValueError(f'unknown period: {period}')

    def periods_to_archive_today(self) -> List[Tuple[str, str]]:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        result: List[Tuple[str, str]] = [('daily', self.current_day_key())]
        if tomorrow.isocalendar()[:2] != now.isocalendar()[:2]:
            result.append(('weekly', self.current_week_key()))
        if tomorrow.month != now.month:
            result.append(('monthly', self.current_month_key()))
            if now.month in (3, 6, 9, 12):
                result.append(('season', self.current_season_key()))
        if now.month == 12 and now.day == 31:
            result.append(('yearly', self.current_year_key()))
        return result

    def _is_period_archived(self, gid: GroupId, period: str, period_key: str) -> bool:
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return False
        return group.archived_periods.get(period) == period_key

    def _mark_period_archived(self, gid: GroupId, period: str, period_key: str) -> None:
        group = self._get_group(gid)
        group.archived_periods[period] = period_key

    def _append_history(
        self,
        gid: GroupId,
        period: str,
        period_key: str,
        ranking: List[Tuple[str, str, int]],
    ) -> None:
        gk = self._gid_key(gid)
        if gk not in self.history_store.groups:
            self.history_store.groups[gk] = GuessHistoryGroup()
        record = GuessHistoryRecord(
            period=period,
            period_key=period_key,
            archived_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ranking=[
                GuessHistoryEntry(uid=str(uid), name=name, score=score)
                for uid, name, score in ranking
            ],
        )
        group_hist = self.history_store.groups[gk]
        group_hist.records.append(record)
        same_period = [r for r in group_hist.records if r.period == period]
        if len(same_period) > self.MAX_HISTORY_PER_PERIOD:
            drop_keys = {
                r.period_key
                for r in sorted(same_period, key=lambda item: item.archived_at)[:-self.MAX_HISTORY_PER_PERIOD]
            }
            group_hist.records = [
                r for r in group_hist.records
                if not (r.period == period and r.period_key in drop_keys)
            ]

    def get_history_ranking(
        self,
        gid: GroupId,
        period: str,
        period_key: str,
        top_n: Optional[int] = None,
    ) -> List[Tuple[str, str, int]]:
        group = self.history_store.groups.get(self._gid_key(gid))
        if not group:
            return []
        for record in reversed(group.records):
            if record.period == period and record.period_key == period_key:
                items = [
                    (entry.uid, entry.name or entry.uid, entry.score)
                    for entry in record.ranking
                    if entry.score > 0
                ]
                items.sort(key=lambda item: (-item[2], item[0]))
                if top_n is not None:
                    return items[:top_n]
                return items
        return []

    def list_history_keys(self, gid: GroupId, period: str, limit: int = 8) -> List[str]:
        group = self.history_store.groups.get(self._gid_key(gid))
        if not group:
            return []
        keys = sorted(
            {record.period_key for record in group.records if record.period == period},
            reverse=True,
        )
        return keys[:limit]

    def _build_forward_from_ranking(
        self,
        ranking: List[Tuple[str, str, int]],
        self_id: int,
        title: str,
        top_n: int = 20,
    ) -> Tuple[str, List[dict]]:
        if not ranking:
            return title, []
        shown = ranking[:top_n]
        nodes = [
            build_forward_node(
                str(self_id),
                name,
                f'{index}. {name} — {score} 分',
            )
            for index, (_, name, score) in enumerate(shown, start=1)
        ]
        return title, nodes

    async def archive_and_broadcast_period(
        self,
        bot,
        group_ids: List[GroupId],
        period: str,
        period_key: str,
        top_n: int = 20,
    ) -> None:
        from nonebot import get_bots

        spec = self.PERIODS[period]
        score_changed = False
        history_changed = False
        bots = get_bots()
        qq_bot = None
        onebot_bot = None
        for candidate in bots.values():
            mod = type(candidate.adapter).__module__
            if 'adapters.qq' in mod:
                qq_bot = candidate
            elif 'adapters.onebot' in mod:
                onebot_bot = candidate
        if bot is not None and onebot_bot is None and qq_bot is None:
            mod = type(getattr(bot, 'adapter', bot)).__module__
            if 'adapters.qq' in mod:
                qq_bot = bot
            else:
                onebot_bot = bot

        for gid in group_ids:
            if self._is_period_archived(gid, period, period_key):
                continue
            ranking = self.get_period_ranking(gid, period)
            self._append_history(gid, period, period_key, ranking)
            history_changed = True
            self._mark_period_archived(gid, period, period_key)
            score_changed = True
            if ranking:
                title = (
                    f'猜歌积分{spec.board_title}结算'
                    f'（{period_key}，前 {min(top_n, len(ranking))} 名）'
                )
                self_id = int((onebot_bot or qq_bot or bot).self_id)
                _, nodes = self._build_forward_from_ranking(
                    ranking, self_id, title, top_n=top_n,
                )
                try:
                    if is_likely_qq_group_id(gid):
                        if qq_bot is None:
                            raise RuntimeError('未找到官方 QQ Bot')
                        await send_group_plain_text(
                            qq_bot,
                            gid,
                            format_forward_nodes_as_text(title, nodes),
                        )
                    else:
                        target_bot = onebot_bot or bot
                        title_node = build_forward_node(str(self_id), '猜歌榜结算', title)
                        messages = json.loads(
                            json.dumps([title_node] + nodes, ensure_ascii=False),
                        )
                        await target_bot.call_api(
                            'send_group_forward_msg',
                            group_id=int(gid),
                            messages=messages,
                        )
                except Exception as e:
                    log.warning(
                        f'[maimai] 猜歌{spec.board_title}结算推送失败 '
                        f'({gid}, {period_key}): {type(e).__name__}: {e}'
                    )
            else:
                empty_msg = (
                    f'猜歌积分{spec.board_title}结算（{period_key}）\n'
                    f'本群该周期无人上榜。'
                )
                try:
                    if is_likely_qq_group_id(gid):
                        if qq_bot is None:
                            raise RuntimeError('未找到官方 QQ Bot')
                        await send_group_plain_text(qq_bot, gid, empty_msg)
                    else:
                        target_bot = onebot_bot or bot
                        await target_bot.send_group_msg(group_id=int(gid), message=empty_msg)
                except Exception as e:
                    log.warning(
                        f'[maimai] 猜歌{spec.board_title}空榜推送失败 '
                        f'({gid}, {period_key}): {type(e).__name__}: {e}'
                    )
        if score_changed:
            await self._save()
        if history_changed:
            await self._save_history()

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
        gid: GroupId,
        top_n: Optional[int] = None,
    ) -> List[Tuple[str, str, int]]:
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return []
        items = [
            (uid, member.name or uid, member.score)
            for uid, member in group.members.items()
            if member.score > 0
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_period_ranking(
        self,
        gid: GroupId,
        period: str,
        top_n: Optional[int] = None,
    ) -> List[Tuple[str, str, int]]:
        spec = self.PERIODS[period]
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return []
        current_key = self.period_key(period)
        items = [
            (uid, member.name or uid, getattr(member, spec.score_attr))
            for uid, member in group.members.items()
            if getattr(member, spec.key_attr) == current_key
            and getattr(member, spec.score_attr) > 0
        ]
        items.sort(key=lambda item: (-item[2], item[0]))
        if top_n is not None:
            return items[:top_n]
        return items

    def get_rank(self, gid: GroupId, uid: UserId) -> int:
        ranking = self.get_ranking(gid)
        uk = self._uid_key(uid)
        for index, (member_uid, _, _) in enumerate(ranking, start=1):
            if member_uid == uk:
                return index
        return len(ranking) + 1

    def get_period_rank(self, gid: GroupId, uid: UserId, period: str) -> int:
        ranking = self.get_period_ranking(gid, period)
        uk = self._uid_key(uid)
        for index, (member_uid, _, _) in enumerate(ranking, start=1):
            if member_uid == uk:
                return index
        return len(ranking) + 1

    def get_period_snapshot(self, gid: GroupId, uid: UserId) -> Dict[str, Tuple[int, int]]:
        member = self.store.groups.get(self._gid_key(gid), GuessGroupScores()).members.get(self._uid_key(uid))
        snapshot: Dict[str, Tuple[int, int]] = {}
        for period, spec in self.PERIODS.items():
            score = getattr(member, spec.score_attr, 0) if member else 0
            snapshot[period] = (score, self.get_period_rank(gid, uid, period))
        return snapshot

    def streak_bonus(self, streak: int) -> int:
        if streak < 2:
            return 0
        return streak - 1

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

    def _get_group(self, gid: GroupId) -> GuessGroupScores:
        gk = self._gid_key(gid)
        if gk not in self.store.groups:
            self.store.groups[gk] = GuessGroupScores()
        return self.store.groups[gk]

    def _get_member(self, gid: GroupId, uid: UserId) -> GuessMemberScore:
        group = self._get_group(gid)
        uk = self._uid_key(uid)
        if uk not in group.members:
            group.members[uk] = GuessMemberScore()
        return group.members[uk]

    async def reset_all_streaks(self, gid: GroupId) -> None:
        group = self.store.groups.get(self._gid_key(gid))
        if not group:
            return
        for member in group.members.values():
            member.streak = 0
        await self._save()

    async def award_correct_guess(
        self,
        gid: GroupId,
        uid: UserId,
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
            detail_parts.append(f'连击 +{combo}（{streak} 连击）')
        if multiplier > 1:
            detail_parts.append(f'({raw_base}+{combo})×{multiplier}')
        bonus_part = f'（{"，".join(detail_parts)}）' if detail_parts else ''
        streak_part = ''

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
        gid: GroupId,
        self_id: int,
        *,
        period: str = 'total',
        period_key: Optional[str] = None,
        top_n: int = 20,
    ) -> Tuple[str, List[dict]]:
        if period == 'total':
            ranking = self.get_ranking(gid, top_n=top_n)
            if not ranking:
                return '本群暂无猜歌积分记录。', []
            title = f'猜歌积分总榜（前 {min(top_n, len(ranking))} 名）'
        elif period_key is not None:
            spec = self.PERIODS[period]
            ranking = self.get_history_ranking(gid, period, period_key, top_n=top_n)
            if not ranking:
                keys = self.list_history_keys(gid, period)
                hint = f'可查询：{", ".join(keys)}' if keys else '暂无历史记录'
                return f'未找到 {period_key} 的猜歌积分{spec.board_title}。{hint}', []
            title = (
                f'猜歌积分历史{spec.board_title}'
                f'（{period_key}，前 {min(top_n, len(ranking))} 名）'
            )
        else:
            spec = self.PERIODS[period]
            ranking = self.get_period_ranking(gid, period, top_n=top_n)
            if not ranking:
                return f'本群当前{spec.board_title}暂无积分记录。', []
            key = self.period_key(period)
            title = f'猜歌积分{spec.board_title}（{key}，前 {min(top_n, len(ranking))} 名）'
        _, nodes = self._build_forward_from_ranking(ranking, self_id, title, top_n=top_n)
        return title, nodes


guess_score = GuessScoreManager()
