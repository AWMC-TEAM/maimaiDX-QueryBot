import asyncio
import json
import time
from typing import List, Optional

from loguru import logger as log

from ..config import achievementList, maiconfig
from .maimaidx_playcount_db import ArcadeCredential, PlayCountRecord, pc_db
from .maimaidx_sw_api import SwApiError, sw_api

_maimai_available = False
try:
    from maimai_py import ArcadeProvider, MaimaiClient, PlayerIdentifier  # type: ignore
    _maimai_available = True
except ImportError:
    log.warning("[PlayCountFetcher] maimai-py 未安装，PC 数功能将受限。请执行 pip install maimai-py")


def _compute_rate(achievement: float) -> str:
    if achievement >= 100.5:
        return 'SSSp'
    elif achievement >= 100.0:
        return 'SSS'
    elif achievement >= 99.5:
        return 'SSp'
    elif achievement >= 99.0:
        return 'SS'
    elif achievement >= 98.0:
        return 'Sp'
    elif achievement >= 97.0:
        return 'S'
    elif achievement >= 94.0:
        return 'AAA'
    elif achievement >= 90.0:
        return 'AA'
    elif achievement >= 80.0:
        return 'A'
    elif achievement >= 75.0:
        return 'BBB'
    elif achievement >= 70.0:
        return 'BB'
    elif achievement >= 60.0:
        return 'B'
    elif achievement >= 50.0:
        return 'C'
    else:
        return 'D'


class PlayCountFetcher:
    def __init__(self):
        self._client: Optional["MaimaiClient"] = None
        self._arcade: Optional["ArcadeProvider"] = None

    @property
    def available(self) -> bool:
        return _maimai_available or self.sdgb_available

    @property
    def sdgb_available(self) -> bool:
        return sw_api.available

    def _ensure_client(self):
        if not _maimai_available:
            raise RuntimeError("maimai-py 未安装，无法使用此功能")
        if self._client is None:
            self._client = MaimaiClient()
        if self._arcade is None:
            self._arcade = ArcadeProvider()

    def _build_identifier_from_credential(self, cred: ArcadeCredential) -> "PlayerIdentifier":
        data = json.loads(cred.credential_data)
        return PlayerIdentifier(credentials=data)

    async def login_by_qrcode(self, qrcode_data: str, qqid: int) -> bool:
        """
        通过机台二维码登录。

        qrcode_data: 机台上的二维码数据，以 SGWCMAID 开头
        qqid: 用户 QQ 号，用于关联本地数据库
        """
        if not _maimai_available:
            raise RuntimeError("maimai-py 未安装，无法使用此功能")

        self._ensure_client()

        if not qrcode_data.startswith("SGWCMAID"):
            raise ValueError("二维码数据必须以 SGWCMAID 开头")

        try:
            identifier = await self._client.qrcode(qrcode_data)

            cred = ArcadeCredential(
                qqid=qqid,
                credential_type="arcade_qrcode",
                credential_data=json.dumps(identifier.credentials),
                created_at=time.time(),
                expires_at=time.time() + 365 * 24 * 3600,
            )
            pc_db.save_credential(cred)
            log.info(f"[PlayCountFetcher] 用户 {qqid} 机台登录成功")
            return True

        except Exception as e:
            log.error(f"[PlayCountFetcher] 用户 {qqid} 机台登录失败: {e}")
            raise

    async def fetch_and_store_play_counts(self, qqid: int) -> int:
        """
        从机台拉取全量 PC 数据并存储到本地数据库。

        返回: 拉取到的记录数
        """
        if not _maimai_available:
            raise RuntimeError("maimai-py 未安装，无法使用此功能")

        self._ensure_client()

        cred = pc_db.get_credential(qqid)
        if cred is None:
            raise RuntimeError(f"用户 {qqid} 尚未登录机台，请先使用「更新pc数」命令登录")

        identifier = self._build_identifier_from_credential(cred)

        try:
            scores = await self._client.scores(identifier, provider=self._arcade)
        except Exception as e:
            log.error(f"[PlayCountFetcher] 用户 {qqid} 获取成绩失败: {e}")
            raise

        now = time.time()
        records: List[PlayCountRecord] = []

        for score in scores.scores:
            record = PlayCountRecord(
                song_id=score.id % 10000 if score.id else 0,
                title=getattr(score, 'title', ''),
                level=score.level or '',
                level_index=getattr(score, 'level_index', 0) or 0,
                play_count=getattr(score, 'play_count', 0) or 0,
                achievements=getattr(score, 'achievements', 0) or 0,
                rate=getattr(score, 'rate', '') or '',
                dx_score=getattr(score, 'dx_score', 0) or 0,
                dx_rating=getattr(score, 'dx_rating', 0) or 0,
                fc=getattr(score, 'fc', '') or '',
                fs=getattr(score, 'fs', '') or '',
                updated_at=now,
            )
            records.append(record)

        if records:
            pc_db.save_play_count_records(qqid, records)

        log.info(f"[PlayCountFetcher] 用户 {qqid} 拉取完成，共 {len(records)} 条记录")
        return len(records)

    async def fetch_and_store_play_counts_with_fallback(
        self, qqid: int, songs_cache=None
    ) -> int:
        """
        从机台拉取 PC 数据，在保存时尝试用本地曲库填充 title 信息。
        songs_cache: MaimaiSongs 对象（可选），用于根据 song_id 查询曲名
        """
        count = await self.fetch_and_store_play_counts(qqid)

        if songs_cache is not None:
            records = pc_db.get_user_play_counts(qqid)
            updated_records = []
            for r in records:
                if not r.title and r.song_id:
                    try:
                        song = await songs_cache.by_id(r.song_id)
                        if song:
                            r.title = song.title
                            updated_records.append(r)
                    except Exception:
                        pass
            if updated_records:
                pc_db.save_play_count_records(qqid, updated_records)

        return count

    async def login_by_sdgb(self, qrcode_data: str, qqid: int) -> bool:
        """
        保存机台二维码凭据（供 sw-api 拉取成绩使用）。

        qrcode_data: 机台上的二维码数据
        qqid: 用户 QQ 号，用于关联本地数据库
        """
        if not sw_api.available:
            raise RuntimeError("sw-api 未配置")

        if not qrcode_data.startswith("SGWCMAID"):
            log.warning(f"[SwApi] 二维码数据不以 SGWCMAID 开头，仍尝试保存")

        cred = ArcadeCredential(
            qqid=qqid,
            credential_type="sdgb_qrcode",
            credential_data=qrcode_data,
            created_at=time.time(),
            expires_at=time.time() + 90 * 24 * 3600,
        )
        pc_db.save_credential(cred)
        log.info(f"[SwApi] 用户 {qqid} 二维码凭据已保存")
        return True

    @staticmethod
    async def _get_user_music_with_retry(
        qr_text: str,
        *,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> List[dict]:
        last_error: Optional[SwApiError] = None
        for attempt in range(max_retries + 1):
            try:
                return await sw_api.get_user_music(qr_text)
            except SwApiError as e:
                last_error = e
                if 'HTTP 500' not in str(e) or attempt >= max_retries:
                    raise
                log.warning(
                    f'[SwApi] HTTP 500，{retry_delay}s 后重试 ({attempt + 1}/{max_retries})'
                )
                await asyncio.sleep(retry_delay)
        if last_error is not None:
            raise last_error
        return []

    def _store_user_music_details(self, qqid: int, detail_list: List[dict]) -> int:
        if not detail_list:
            log.warning(f'[SwApi] 用户 {qqid} 没有成绩数据')
            return 0

        now = time.time()
        records: List[PlayCountRecord] = []
        level_labels = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:Master']
        combo_map = {0: '', 1: 'fc', 2: 'fcp', 3: 'ap', 4: 'app'}
        sync_map = {0: '', 1: 'fs', 2: 'fsp', 3: 'fsd', 4: 'fsdp', 5: 'sync'}

        for item in detail_list:
            music_id = int(item.get('musicId', 0))
            level_index = int(item.get('level', 0))
            achievement_raw = int(item.get('achievement', 0))
            achievement_val = achievement_raw / 10000.0

            level = level_labels[level_index] if level_index < len(level_labels) else str(level_index)
            fc = combo_map.get(item.get('comboStatus', 0), '')
            fs = sync_map.get(item.get('syncStatus', 0), '')

            try:
                from .maimaidx_music import mai as _mai
                music = _mai.total_list.by_id(str(music_id))
                ds = round(float(music.ds[level_index]), 1) if music and level_index < len(music.ds) else 0.0
            except Exception:
                ds = 0.0

            rate = _compute_rate(achievement_val)

            record = PlayCountRecord(
                song_id=music_id,
                title='',
                level=level,
                level_index=level_index,
                play_count=int(item.get('playCount', 0)),
                achievements=achievement_val,
                rate=rate,
                dx_score=int(item.get('deluxscoreMax', 0)),
                dx_rating=ds,
                fc=fc,
                fs=fs,
                updated_at=now,
            )
            records.append(record)

        if records:
            pc_db.save_play_count_records(qqid, records)
            self._fill_titles(qqid)

        log.info(f'[SwApi] 用户 {qqid} 拉取完成，共 {len(records)} 条记录')
        return len(records)

    async def fetch_via_sdgb(self, qqid: int) -> int:
        """
        通过 sw-api 拉取全量 PC 数据并存储到本地数据库。

        返回: 拉取到的记录数
        """
        return await self.fetch_via_sdgb_with_retry(qqid, max_retries=0)

    async def fetch_via_sdgb_with_retry(
        self,
        qqid: int,
        *,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> int:
        """
        通过 sw-api 拉取全量 PC 数据并存储到本地数据库；HTTP 500 时等待后重试。

        返回: 拉取到的记录数
        """
        cred = pc_db.get_credential(qqid)
        if cred is None:
            raise RuntimeError(f'用户 {qqid} 尚未登录，请先使用「更新pc数」命令')

        qr_text = cred.credential_data

        log.info(f'[SwApi] 用户 {qqid} 正在拉取全量成绩...')
        try:
            detail_list = await self._get_user_music_with_retry(
                qr_text,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
        except SwApiError as e:
            raise RuntimeError(str(e)) from e

        return self._store_user_music_details(qqid, detail_list)

    def _fill_titles(self, qqid: int):
        try:
            from .maimaidx_music import mai as _mai
            current_records = pc_db.get_user_play_counts(qqid)
            updated = []
            for r in current_records:
                if not r.title and r.song_id:
                    try:
                        music = _mai.total_list.by_id(str(r.song_id))
                        if music:
                            r.title = music.title
                            updated.append(r)
                    except Exception:
                        pass
            if updated:
                pc_db.save_play_count_records(qqid, updated)
        except Exception as e:
            log.warning(f"[SwApi] 曲目标题填充失败: {e}")


playcount_fetcher = PlayCountFetcher()