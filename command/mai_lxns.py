"""
落雪查分器绑定与查询指令。

指令：
  lxbind          — OAuth 授权绑定落雪账号（无回调模式）
  lxunbind        — 解绑落雪账号
  lxb50           — 用落雪数据源查询 B50
"""

import re
import time
from textwrap import dedent
from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent
from nonebot.params import CommandArg

from ..config import log, maiconfig
from ..libraries.image import image_to_base64
from ..libraries.maimaidx_best_50 import DrawBest, computeRa
from ..libraries.maimaidx_lxns_client import (
    fetch_token,
    get_authorize_url,
    refresh_token,
    user_get_bests,
    user_get_player,
    dev_get_bests,
    dev_get_player_by_qq,
)
from ..libraries.maimaidx_lxns_db import lxns_db
from ..libraries.maimaidx_model import ChartInfo, Data, UserInfo
from ..libraries.maimaidx_music import mai

# ─────────────────────────── helpers ───────────────────────────

_RATE_MAP = {
    'sssp': 'SSS+', 'sss': 'SSS', 'ssp': 'SS+', 'ss': 'SS',
    'sp': 'S+', 's': 'S', 'aaa': 'AAA', 'aa': 'AA', 'a': 'A',
    'bbb': 'BBB', 'bb': 'BB', 'b': 'B', 'c': 'C', 'd': 'D',
}


def _lxns_score_to_chartinfo(score: dict) -> Optional[ChartInfo]:
    """
    将 lxns API 返回的单个 score dict 转换为本地 ChartInfo。
    跳过曲目表中查不到的曲目（返回 None）。
    """
    song_id = score['id']
    level_index = score.get('level_index', 0)
    achievements = score.get('achievements', 0.0)
    lxns_type = score.get('type', 'standard')

    music = mai.total_list.by_id(str(song_id))
    if music is None:
        return None

    if level_index < len(music.ds):
        ds = round(float(music.ds[level_index]), 1)
        level_str = music.level[level_index] if level_index < len(music.level) else score.get('level', '')
        title = music.title
        level_label = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:Master'][min(level_index, 4)]
    else:
        ds = float(score.get('level', '0').replace('+', '.5'))
        level_str = score.get('level', '')
        title = score.get('song_name', '')
        level_label = score.get('level', '')

    song_type = 'DX' if lxns_type == 'dx' else 'SD'
    rate_raw = (score.get('rate') or '').lower()
    rate = _RATE_MAP.get(rate_raw, rate_raw.upper()) if rate_raw else ''
    fc = (score.get('fc') or '').lower()
    fs = (score.get('fs') or '').lower()

    ra, computed_rate = computeRa(ds, achievements, israte=True)
    if not rate:
        rate = computed_rate

    return ChartInfo(
        song_id=song_id,
        level=level_str,
        level_index=level_index,
        ds=ds,
        ra=ra,
        rate=rate,
        achievements=achievements,
        fc=fc,
        fs=fs,
        dxScore=score.get('dx_score', 0),
        title=title,
        type=song_type,
        level_label=level_label,
    )


def _lxns_bests_to_userinfo(bests: dict, nickname: str = '', rating: int = 0) -> UserInfo:
    """将 lxns Best50 响应转换为本地 UserInfo。"""
    sd_list = [_lxns_score_to_chartinfo(s) for s in (bests.get('standard') or [])]
    dx_list = [_lxns_score_to_chartinfo(s) for s in (bests.get('dx') or [])]
    sd_list = [c for c in sd_list if c is not None]
    dx_list = [c for c in dx_list if c is not None]

    sd_list.sort(key=lambda x: -x.ra)
    dx_list.sort(key=lambda x: -x.ra)

    return UserInfo(
        nickname=nickname or '落雪用户',
        rating=rating,
        additional_rating=0,
        username=nickname or '',
        charts=Data(sd=sd_list, dx=dx_list),
    )


async def _do_token_refresh(qqid: int, db_row: dict) -> Optional[str]:
    """尝试刷新 token，成功返回新 access_token，失败返回 None。"""
    rt = db_row.get('refresh_token')
    if not rt:
        return None
    try:
        token_data = await refresh_token(rt)
        lxns_db.update_tokens(
            qqid,
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data.get('expires_in', 900),
            scope=token_data.get('scope', ''),
            token_type=token_data.get('token_type', 'Bearer'),
        )
        return token_data['access_token']
    except Exception as e:
        log.warning(f'[lxns] refresh_token failed for qq={qqid}: {e}')
        return None


async def _get_valid_access_token(qqid: int) -> Optional[str]:
    """获取有效的 OAuth access_token（自动刷新过期 token）。"""
    db_row = lxns_db.get_user(qqid)
    if not db_row:
        return None

    access_token = db_row.get('access_token')
    expires_at = db_row.get('expires_at', 0)

    if access_token and expires_at > time.time() + 60:
        return access_token

    return await _do_token_refresh(qqid, db_row)


# ─────────────────────────── lxbind ───────────────────────────

lxbind = on_command('lxbind', aliases={'绑定落雪', '绑定lx'})


@lxbind.handle()
async def _lxbind(event: MessageEvent, message: Message = CommandArg()):
    if not maiconfig.lx_client_id or not maiconfig.lx_client_secret:
        await lxbind.finish('Bot 管理员尚未配置落雪 OAuth 信息，无法绑定。', reply_message=True)

    qqid = event.user_id
    args = message.extract_plain_text().strip()

    if not args:
        url = get_authorize_url(maiconfig.lx_client_id)
        msg = dedent(f"""\
            请点击以下链接进行落雪查分器授权
            =======================
            {url}
            =======================
            授权后你将看到一个授权码（形如 XXXX-XXXX-XXXX）
            请复制该授权码，发送给 Bot 完成绑定
            =======================
            请注意！你必须在落雪查分器的
            「账号设置 → 常规设置」中的
            「隐私设置」开启允许读取成绩，
            否则 Bot 将无法查询你的成绩
        """).strip()
        await lxbind.finish(msg, reply_message=True)

    code = args.strip()
    if not re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', code, re.IGNORECASE):
        await lxbind.finish('授权码格式错误，应为 XXXX-XXXX-XXXX', reply_message=True)

    try:
        token_data = await fetch_token(code)
    except Exception as e:
        log.error(f'[lxbind] token exchange failed: {e}')
        await lxbind.finish(f'授权码兑换失败：{e}', reply_message=True)

    access_token = token_data['access_token']
    refresh_token_val = token_data['refresh_token']
    expires_in = token_data.get('expires_in', 900)
    scope = token_data.get('scope', '')

    friend_code = None
    nickname = ''
    rating = 0
    try:
        player = await user_get_player(access_token)
        if player:
            friend_code = player.get('friend_code')
            nickname = player.get('name', '')
            rating = player.get('rating', 0)
    except Exception as e:
        log.warning(f'[lxbind] get player info failed: {e}')

    lxns_db.upsert_user(
        qqid,
        friend_code=friend_code,
        access_token=access_token,
        refresh_token=refresh_token_val,
        token_type=token_data.get('token_type', 'Bearer'),
        expires_at=time.time() + expires_in,
        scope=scope,
    )

    fc_msg = f'好友码：{friend_code}' if friend_code else '（未获取到好友码）'
    await lxbind.finish(
        f'落雪绑定成功！\n'
        f'昵称：{nickname}\n'
        f'{fc_msg}\n'
        f'Rating：{rating}',
        reply_message=True,
    )


# ─────────────────────────── lxunbind ───────────────────────────

lxunbind = on_command('lxunbind', aliases={'解绑落雪', '解绑lx'})


@lxunbind.handle()
async def _lxunbind(event: MessageEvent):
    qqid = event.user_id
    user = lxns_db.get_user(qqid)
    if not user:
        await lxunbind.finish('你尚未绑定落雪查分器。', reply_message=True)
    lxns_db.clear_user(qqid)
    await lxunbind.finish('已解绑落雪查分器。', reply_message=True)


# ─────────────────────────── lxb50 ───────────────────────────

lxb50 = on_command('lxb50', aliases={'落雪b50', '落雪B50', 'lx50'})


@lxb50.handle()
async def _lxb50(event: MessageEvent, message: Message = CommandArg()):
    qqid = event.user_id

    try:
        bests = None
        nickname = ''
        rating = 0

        # 方式 1：OAuth 用户数据
        access_token = await _get_valid_access_token(qqid)
        if access_token:
            try:
                bests = await user_get_bests(access_token)
                player = await user_get_player(access_token)
                if player:
                    nickname = player.get('name', '')
                    rating = player.get('rating', 0)
            except Exception as e:
                log.warning(f'[lxb50] OAuth query failed for qq={qqid}: {e}')
                bests = None

        # 方式 2：开发者 Token 按 QQ 查
        if bests is None:
            if not maiconfig.lxns_dev_token:
                await lxb50.finish(
                    '你尚未绑定落雪查分器（发送 lxbind 绑定），'
                    '且 Bot 未配置开发者 Token，无法查询。',
                    reply_message=True,
                )
            player_info = await dev_get_player_by_qq(qqid)
            if not player_info:
                await lxb50.finish(
                    '未在落雪查分器找到你的数据。\n'
                    '请先在落雪查分器绑定 QQ，或发送 lxbind 进行 OAuth 授权绑定。',
                    reply_message=True,
                )
            fc = player_info.get('friend_code')
            nickname = player_info.get('name', '')
            rating = player_info.get('rating', 0)
            if fc:
                bests = await dev_get_bests(fc)
            if not bests:
                await lxb50.finish('获取落雪 B50 数据失败。', reply_message=True)

        userinfo = _lxns_bests_to_userinfo(bests, nickname=nickname, rating=rating)

        if not userinfo.charts or (not userinfo.charts.sd and not userinfo.charts.dx):
            await lxb50.finish('落雪查分器中没有你的 B50 数据。', reply_message=True)

        draw_best = DrawBest(userinfo, qqid)
        msg = MessageSegment.image(image_to_base64(await draw_best.draw()))
        await lxb50.finish(msg, reply_message=True)

    except Exception as e:
        log.error(f'[lxb50] error: {e}', exc_info=True)
        await lxb50.finish(f'查询失败：{type(e).__name__}: {e}', reply_message=True)
