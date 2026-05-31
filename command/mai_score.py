import json
import re

from nonebot import get_bot, on_command, on_regex
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import IgnoredException
from nonebot.params import CommandArg, Depends, RegexMatched

from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_error import UserDisabledQueryError, UserNotFoundError, UserNotExistsError
from ..libraries.maimaidx_group_rating import (
    group_weak_rank,
    group_rating_ranking,
    group_gain_ranking,
    group_sun_lock_ranking,
    build_forward_node,
    get_group_member_ratings,
    group_song_my_rank,
    group_song_leaderboard,
)
from ..libraries.maimaidx_score_formatter import (
    format_leaderboard_text,
    format_score_line_from_dict,
    get_difficulty_name,
)
from ..libraries.maimaidx_song_resolver import SongResolver
from ..libraries.maimaidx_music import feature_manager
from ..config import log
from ..libraries.maimaidx_music_info import get_b50_tag_stats
from ..libraries.maimaidx_music_info import *
from ..libraries.maimaidx_player_score import *
from ..libraries.maimaidx_best_50 import (
    generate,
    generate_all,
    generate_coop_b50,
    generate_coop_all_b50,
    generate_lock_b50,
    generate_lock_all_b50,
    generate_yueji_b50,
    generate_yueji_all_b50,
    generate_version_b50,
    generate_difficulty_b50,
    generate_difficulty_all_b50,
    generate_ideal_b50,
    generate_ideal_all_b50,
)
from ..libraries.maimaidx_data_storage import data_storage
from ..libraries.maimaidx_data_scheduler import fetch_and_store_user_scores
from ..libraries.maimaidx_plate_count import (
    fetch_dev_records_as_score_records,
    format_plate_count_message,
    format_plate_count_message_from_records,
)
from ..libraries.maimaidx_progress_report import generate_progress_report, generate_progress_report_between
from ..libraries.maimaidx_gain_recommend import generate_today_gain_recommendation
from ..libraries.maimaidx_friend_battle import run_friend_battle
from ..libraries.maimaidx_gold_water import generate_gold_content, generate_water_content
from ..libraries.maimaidx_rating_compare import generate_how_weak
from ..libraries.maimaidx_tag_analysis import draw_analysis, image_to_message_segment
from ..libraries.maimaidx_update_plate import *

best50       = on_command('b50', aliases={'B50'})
best_all50   = on_command('ab50', aliases={'a50', 'allb50'})
coop_b50     = on_command('合作b50', aliases={'合作B50'})
coop_ab50    = on_command('合作a50', aliases={'合作A50'})
how_weak     = on_command('我有多菜', aliases={'我有多菜'})
gold_content = on_command('含金量', aliases={'含金量'})
water_content = on_command('含水量', aliases={'含水量'})
fcb50        = on_command('fcb50', aliases={'fc50'})
fcallb50     = on_command('fcallb50', aliases={'fcallb50', 'fca50'})
apb50        = on_command('apb50', aliases={'ap50'})
apallb50     = on_command('apallb50', aliases={'apallb50', 'apa50'})
fit_b50      = on_command('拟合b50', aliases={'拟合50'})
fit_all_b50  = on_command('拟合b50全部', aliases={'拟合b50全部', '拟合allb50', '拟合a50'})
sun_b50      = on_command('寸b50', aliases={'寸50'})
sun_all_b50  = on_command('寸ab50', aliases={'寸a50', '寸allb50'})
lock_b50     = on_command('锁血b50', aliases={'锁血50', '名刀50', '名刀b50'})
lock_ab50    = on_command('锁血ab50', aliases={'锁血a50'})
yueji_b50    = on_command('越级b50', aliases={'越级50'})
yueji_ab50   = on_command('越级ab50', aliases={'越级a50'})
# 允许首尾及中间空格/换行，避免 QQ 换行导致「双\n代b50」无法匹配
version_b50  = on_regex(r'^\s*([初真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉霸舞熊华華爽煌宙星祭祝双宴镜彩])\s*代\s*b50\s*$')
legacy_b50   = on_regex(r'^\s*l\s*(.+代)\s*b50\s*$')
legacy_b35   = on_regex(r'^\s*l\s*(.+代)\s*b35\s*$')
dx2026_b35   = on_command('dx2026b35', aliases={'DX2026b35'})
# 难度 B50：交给 DifficultyFilter 解析（支持：紫14+、13-14、master14.0 等）
# 注意：排除纯 "b50/ab50"，避免与 on_command('b50/ab50') 冲突
# 以及排除某些 b50 别名（如 名刀b50），避免被当作「任意筛选+b50」误解析
difficulty_b50 = on_regex(r'^\s*(?!b50\s*$)(?!名刀\s*b50\s*$)(?!l\s*.+?代\s*b50\s*$)(.+?)\s*b50\s*$', flags=re.IGNORECASE)
difficulty_ab50 = on_regex(r'^\s*(?!ab50\s*$)(.+?)\s*ab50\s*$', flags=re.IGNORECASE)
# 理想 B50
ideal_b50 = on_command('理想b50', aliases={'理想B50'})
ideal_ab50 = on_command('理想ab50', aliases={'理想a50', '理想allb50'})
# 数据存储
enable_data_storage = on_command('开启存储数据', aliases={'开启数据存储'})
disable_data_storage = on_command('关闭存储数据', aliases={'关闭数据存储'})
store_data_now = on_command('立即存储数据', aliases={'存储数据'})
storage_history = on_command('存储历史', aliases={'查询存储历史', '存储记录'})
storage_snapshot = on_command('查看存档', aliases={'查看存储快照', '存档详情'})
weekly_report = on_command('周报', aliases={'成绩周报', 'maimai周报'})
monthly_report = on_command('月报', aliases={'成绩月报', 'maimai月报'})
daily_report = on_command('日报', aliases={'成绩日报', 'maimai日报'})
today_gain_recommend = on_command('今日吃分推荐', aliases={'吃分推荐', '今日推分推荐'})
plate_count_stats = on_command('牌子统计', aliases={'统计牌子'})
compare_report = on_command('对比存档', aliases={'存档对比', '报告对比'})
tag_analysis = on_command('底力分析', aliases={'底力分析'})
minfo   = on_command('minfo', aliases={'minfo', 'Minfo', 'MINFO', 'info', 'Info', 'INFO'})
ginfo   = on_command('ginfo', aliases={'ginfo', 'Ginfo', 'GINFO'})
score   = on_command('分数线')

# 群内 rating：我在群里有多菜、群聊 rating 排行榜
group_weak = on_command('我在群里有多菜', aliases={'我在群里有多菜'})
group_rating_leaderboard = on_command('群聊rating排行榜', aliases={'群聊rating排行榜'})
group_gain_board = on_command('群吃分榜', aliases={'群内吃分榜', '吃分榜'})
group_sun_board = on_command('群寸止榜', aliases={'群内寸止榜', '寸止榜'})
group_lock_board = on_command('群锁血榜', aliases={'群内锁血榜', '锁血榜'})
friend_battle = on_command('友人对战', aliases={'好友对战'})

def get_at_qq(message: MessageEvent) -> Optional[int]:
    for item in message.message:
        if isinstance(item, MessageSegment) and item.type == 'at' and item.data['qq'] != 'all':
            return int(item.data['qq'])
    return None


def _source_label(qqid: Optional[int]) -> str:
    """返回当前用户数据源中文名。username/@他人 查询传 None，视为水鱼。"""
    if qqid is None:
        return '水鱼'
    from ..libraries.maimaidx_datasource import get_user_source
    return '落雪' if get_user_source(qqid) == 'lxns' else '水鱼'


def _build_footer(
    qqid: Optional[int],
    total: float,
    *,
    forced_source: Optional[str] = None,
    unsupported_feature: Optional[str] = None,
) -> str:
    """构建成绩图下方文案：数据源 + 主题 + 耗时（+ 落雪不支持提示）。"""
    from ..libraries.maimaidx_timing import get_fetch, format_summary
    source = forced_source or _source_label(qqid)
    lines = [f'📊 数据源：{source} | 可使用 数据源 水鱼/落雪 修改']
    # 当前用户主题提示（仅自己查自己时显示）
    if qqid is not None:
        try:
            from ..libraries.maimaidx_theme import get_user_theme, get_theme_display_name, Theme
            _t = get_user_theme(qqid)
            _name = get_theme_display_name(_t)
            _all = ' / '.join(get_theme_display_name(x.value) for x in Theme)
            lines.append(f'🎨 主题：{_name} | 可使用 主题 {_all} 切换')
        except Exception:
            pass
    if unsupported_feature and qqid is not None and source == '落雪':
        lines.append(f'⚠️ {unsupported_feature}依赖水鱼独有数据，落雪暂不支持，已用水鱼生成')
    from ..libraries.maimaidx_b50_warnings import pop_b50_warning_footer
    warning = pop_b50_warning_footer()
    if warning:
        lines.append('')
        lines.append(warning)
    lines.append(format_summary(total, get_fetch()))
    return '\n' + '\n'.join(lines)


async def _finish_score(
    matcher,
    coro,
    qqid: Optional[int],
    *,
    forced_source: Optional[str] = None,
    unsupported_feature: Optional[str] = None,
):
    """统一成绩图收尾：计时执行 coro，成功追加「数据源 + 耗时」文案，错误原样发送。"""
    from ..libraries.maimaidx_timing import run_timed
    result, total = await run_timed(coro)
    if isinstance(result, str):
        await matcher.finish(result, reply_message=True)
        return
    footer = _build_footer(
        qqid, total,
        forced_source=forced_source,
        unsupported_feature=unsupported_feature,
    )
    await matcher.finish(result + MessageSegment.text(footer), reply_message=True)


@best50.handle()
async def _(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq)
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    # 数据源路由统一由 libraries.maimaidx_datasource.get_user_b50 处理
    # username 查询强制水鱼；qqid 查询按用户偏好（自己/@的人均生效）
    await _finish_score(best50, generate(qqid, username), None if username else qqid)


@best_all50.handle()
async def _best_all50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq)
):
    """常规 ab50：无视 B35/B15 分组，直接取 rating 最高的 50 首。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(best_all50, generate_all(qqid, username), None if username else qqid)


def _display_name_from_sender(sender) -> str:
    """优先群名片，其次昵称。"""
    card = (getattr(sender, 'card', None) or '').strip()
    if card:
        return card
    return (getattr(sender, 'nickname', None) or '').strip() or '未知'


async def _coop_resolve_nicks_and_finish(event, at_qq, cmd, generator):
    """合作 b50/ab50 共用：解析两人昵称并调用对应生成函数。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    if not at_qq:
        await cmd.finish('请使用「合作b50@某人」或「合作ab50@某人」并@一位好友。', reply_message=True)
    if at_qq == event.user_id:
        await cmd.finish('请@除自己以外的另一位好友。', reply_message=True)
    nick_a = _display_name_from_sender(event.sender) or str(event.user_id)
    nick_b = nick_a
    if isinstance(event, GroupMessageEvent):
        try:
            try:
                bot = get_bot()
            except Exception:
                bot = get_bot(str(event.self_id))
            member = await bot.call_api('get_group_member_info', group_id=event.group_id, user_id=at_qq)
            card = (member.get('card') or '').strip()
            nick_b = card or (member.get('nickname') or str(at_qq)).strip() or '未知'
        except Exception:
            nick_b = str(at_qq)
    else:
        nick_b = str(at_qq)
    from ..libraries.maimaidx_timing import finish_timed
    await finish_timed(cmd, generator(event.user_id, at_qq, nick_a, nick_b))


@coop_b50.handle()
async def _coop_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    at_qq: Optional[int] = Depends(get_at_qq),
):
    await _coop_resolve_nicks_and_finish(event, at_qq, coop_b50, generate_coop_b50)


@coop_ab50.handle()
async def _coop_ab50(
    event: MessageEvent,
    message: Message = CommandArg(),
    at_qq: Optional[int] = Depends(get_at_qq),
):
    await _coop_resolve_nicks_and_finish(event, at_qq, coop_ab50, generate_coop_all_b50)


@how_weak.handle()
async def _how_weak(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip() or None
    await _finish_score(how_weak, generate_how_weak(qqid=qqid, username=username), None if username else qqid, unsupported_feature='我有多菜')


@group_weak.handle()
async def _group_weak(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await group_weak.finish('该功能仅在群聊中可用。', reply_message=True)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, 'self_id', event.self_id))
    nickname = str(getattr(bot, 'nickname', None) or 'Bot')
    text, nodes = await group_weak_rank(
        bot, event.group_id, self_id, nickname, event.user_id
    )
    await group_weak.send(text, reply_message=True)
    if nodes:
        try:
            # 做一次 JSON 往返，确保仅传递可序列化数据，避免 adapter 层 partial 等导致 TypeError
            messages = json.loads(json.dumps(nodes, ensure_ascii=False))
            await bot.call_api(
                'send_group_forward_msg',
                group_id=event.group_id,
                messages=messages,
            )
        except TypeError as e:
            log.warning(f'[maimai] 我在群里有多菜 合并转发序列化失败: {e}')
        except Exception as e:
            log.warning(f'[maimai] 我在群里有多菜 合并转发发送失败: {type(e).__name__}: {e}')
    await group_weak.finish()


@group_rating_leaderboard.handle()
async def _group_rating_leaderboard(event: MessageEvent, message: Message = CommandArg()):
    """群聊 rating 排行榜：获取群内成员 rating 倒序前 N 名，合并转发展示，不引用用户消息。默认前 10 名，可传参如 群聊rating排行榜 20。"""
    if not isinstance(event, GroupMessageEvent):
        await group_rating_leaderboard.finish('该功能仅在群聊中可用。', reply_message=False)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    args = message.extract_plain_text().strip().split()
    top_n = 10
    if args:
        try:
            n = int(args[0])
            if n < 1:
                n = 1
            elif n > 50:
                n = 50
            top_n = n
        except (ValueError, TypeError):
            top_n = 10
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, 'self_id', event.self_id))
    raw_nick = getattr(bot, 'nickname', None)
    nickname = raw_nick if isinstance(raw_nick, str) else 'Bot'
    if not nickname:
        nickname = 'Bot'
    text, nodes = await group_rating_ranking(
        bot, event.group_id, self_id, nickname, top_n=top_n
    )
    if not nodes:
        await group_rating_leaderboard.finish(text or '群内暂无已绑定查分器的成员。', reply_message=False)
    # 计算当前用户在群内排名（使用同一缓存）
    rows = await get_group_member_ratings(bot, event.group_id)
    user_rank = None
    for i, (uid, _, _) in enumerate(rows):
        if uid == event.user_id:
            user_rank = i + 1
            break
    if user_rank is not None:
        title_content = f"{text}\n您在群里排名为第{user_rank}名"
    else:
        title_content = f"{text}\n您尚未绑定查分器，无法显示排名"
    # 标题作为合并转发第一条，不引用用户；保证 name/content 均为纯字符串，避免 partial 等泄露
    title_node = build_forward_node(str(self_id), nickname, title_content)
    all_nodes = [title_node] + nodes
    try:
        messages = json.loads(json.dumps(all_nodes, ensure_ascii=False))
        await bot.call_api(
            'send_group_forward_msg',
            group_id=event.group_id,
            messages=messages,
        )
    except TypeError as e:
        log.warning(f'[maimai] 群聊rating排行榜 合并转发序列化失败: {e}')
        await group_rating_leaderboard.finish('合并转发序列化失败，请稍后再试。', reply_message=False)
    except Exception as e:
        log.warning(f'[maimai] 群聊rating排行榜 合并转发发送失败: {type(e).__name__}: {e}')
        await group_rating_leaderboard.finish('合并转发发送失败，请稍后再试。', reply_message=False)
    await group_rating_leaderboard.finish(reply_message=False)


async def _send_group_forward_or_finish(
    matcher,
    bot: Bot,
    group_id: int,
    self_id: int,
    nickname: str,
    text: str,
    nodes: list,
    *,
    empty_fallback: str = '暂无数据。',
):
    """合并转发：首条为标题 text，后续为 nodes；失败则文本收尾。"""
    if not nodes:
        await matcher.finish(text or empty_fallback, reply_message=False)
        return
    title_node = build_forward_node(str(self_id), nickname, text)
    all_nodes = [title_node] + nodes
    try:
        messages = json.loads(json.dumps(all_nodes, ensure_ascii=False))
        await bot.call_api(
            'send_group_forward_msg',
            group_id=group_id,
            messages=messages,
        )
    except TypeError as e:
        log.warning(f'[maimai] 群榜合并转发序列化失败: {e}')
        await matcher.finish('合并转发序列化失败，请稍后再试。', reply_message=False)
    except Exception as e:
        log.warning(f'[maimai] 群榜合并转发发送失败: {type(e).__name__}: {e}')
        await matcher.finish('合并转发发送失败，请稍后再试。', reply_message=False)
    await matcher.finish(reply_message=False)


@group_gain_board.handle()
async def _group_gain_board(event: MessageEvent, message: Message = CommandArg()):
    """群吃分榜 [近N天] [前M名]，默认 7 天、前 15 名。"""
    if not isinstance(event, GroupMessageEvent):
        await group_gain_board.finish('该功能仅在群聊中可用。', reply_message=False)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    parts = message.extract_plain_text().strip().split()
    days, top_n = 7, 15
    if len(parts) >= 1:
        try:
            days = int(parts[0])
        except (ValueError, TypeError):
            pass
    if len(parts) >= 2:
        try:
            top_n = int(parts[1])
        except (ValueError, TypeError):
            pass
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, 'self_id', event.self_id))
    nickname = str(getattr(bot, 'nickname', None) or 'Bot')
    text, nodes = await group_gain_ranking(
        bot, event.group_id, self_id, nickname, days=days, top_n=top_n
    )
    await _send_group_forward_or_finish(
        group_gain_board, bot, event.group_id, self_id, nickname, text, nodes
    )


@group_sun_board.handle()
async def _group_sun_board(event: MessageEvent, message: Message = CommandArg()):
    """群寸止榜 [前N名]，默认 15。"""
    if not isinstance(event, GroupMessageEvent):
        await group_sun_board.finish('该功能仅在群聊中可用。', reply_message=False)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    parts = message.extract_plain_text().strip().split()
    top_n = 15
    if parts:
        try:
            top_n = int(parts[0])
        except (ValueError, TypeError):
            pass
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, 'self_id', event.self_id))
    nickname = str(getattr(bot, 'nickname', None) or 'Bot')
    text, nodes = await group_sun_lock_ranking(
        bot, event.group_id, self_id, nickname, mode='sun', top_n=top_n
    )
    await _send_group_forward_or_finish(
        group_sun_board, bot, event.group_id, self_id, nickname, text, nodes
    )


@group_lock_board.handle()
async def _group_lock_board(event: MessageEvent, message: Message = CommandArg()):
    """群锁血榜 [前N名]，默认 15。"""
    if not isinstance(event, GroupMessageEvent):
        await group_lock_board.finish('该功能仅在群聊中可用。', reply_message=False)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    parts = message.extract_plain_text().strip().split()
    top_n = 15
    if parts:
        try:
            top_n = int(parts[0])
        except (ValueError, TypeError):
            pass
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, 'self_id', event.self_id))
    nickname = str(getattr(bot, 'nickname', None) or 'Bot')
    text, nodes = await group_sun_lock_ranking(
        bot, event.group_id, self_id, nickname, mode='lock', top_n=top_n
    )
    await _send_group_forward_or_finish(
        group_lock_board, bot, event.group_id, self_id, nickname, text, nodes
    )


@friend_battle.handle()
async def _friend_battle(event: MessageEvent, message: Message = CommandArg()):
    """
    友人对战 [可选数字]：从本人 B50 随机一首，与同群友比该谱成绩。
    按双方总 rating 分档动态限制 |Δrating|，避免高水平碾压或碾压低水平；可选数字 50～800 在不超过分档上限前提下进一步收紧。
    """
    if not isinstance(event, GroupMessageEvent):
        await friend_battle.finish('该功能仅在群聊中可用。', reply_message=True)
    if not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    arg = message.extract_plain_text().strip()
    user_rating_cap = None
    if arg:
        try:
            user_rating_cap = max(50, min(800, int(arg.split()[0])))
        except (ValueError, TypeError):
            pass
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    text = await run_friend_battle(
        bot, event.group_id, event.user_id, user_rating_cap=user_rating_cap
    )
    await friend_battle.finish(text, reply_message=True)


async def _get_bot_info(event: MessageEvent) -> tuple[Bot, int, str]:
    """获取 bot 实例和基本信息。"""
    try:
        bot = get_bot()
    except Exception:
        bot = get_bot(str(event.self_id))
    self_id = int(getattr(bot, "self_id", event.self_id))
    nickname = str(getattr(bot, "nickname", None) or "Bot")
    return bot, self_id, nickname




@fcb50.handle()
async def _fcb50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(fcb50, generate_fc_b50(qqid, username), None if username else qqid)


@fcallb50.handle()
async def _fcallb50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(fcallb50, generate_fc_all_b50(qqid, username), None if username else qqid)


@apb50.handle()
async def _apb50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(apb50, generate_ap_b50(qqid, username), None if username else qqid)


@apallb50.handle()
async def _apallb50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(apallb50, generate_ap_all_b50(qqid, username), None if username else qqid)


@fit_b50.handle()
async def _fit_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(fit_b50, generate_fit_b50(qqid, username), None if username else qqid, unsupported_feature='拟合b50')


@fit_all_b50.handle()
async def _fit_all_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(fit_all_b50, generate_fit_all_b50(qqid, username), None if username else qqid, unsupported_feature='拟合ab50')


def _parse_threshold_and_username(args: str) -> tuple:
    """解析可选档位门槛（数字）+ 可选用户名。返回 (threshold 或 None, username)。用于寸b50/锁血b50。"""
    args = args.strip()
    if not args:
        return None, ''
    parts = args.split(maxsplit=1)
    try:
        t = float(parts[0])
        if 50 <= t <= 100.5:
            return t, (parts[1].strip() if len(parts) > 1 else '')
    except (ValueError, TypeError):
        pass
    return None, args


@sun_b50.handle()
async def _sun_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    threshold, username = _parse_threshold_and_username(message.extract_plain_text())
    await _finish_score(sun_b50, generate_sun_b50(qqid, username, threshold), None if username else qqid)


@sun_all_b50.handle()
async def _sun_all_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    threshold, username = _parse_threshold_and_username(message.extract_plain_text())
    await _finish_score(sun_all_b50, generate_sun_all_b50(qqid, username, threshold), None if username else qqid)




@lock_b50.handle()
async def _lock_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    _, username = _parse_threshold_and_username(message.extract_plain_text())
    await _finish_score(lock_b50, generate_lock_b50(qqid, username), None if username else qqid)


@lock_ab50.handle()
async def _lock_ab50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    _, username = _parse_threshold_and_username(message.extract_plain_text())
    await _finish_score(lock_ab50, generate_lock_all_b50(qqid, username), None if username else qqid)


@yueji_b50.handle()
async def _yueji_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    th, username = _parse_threshold_and_username(message.extract_plain_text())
    threshold = th if th is not None else 97.0
    await _finish_score(yueji_b50, generate_yueji_b50(qqid, username, threshold), None if username else qqid)


@yueji_ab50.handle()
async def _yueji_ab50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    th, username = _parse_threshold_and_username(message.extract_plain_text())
    threshold = th if th is not None else 97.0
    await _finish_score(yueji_ab50, generate_yueji_all_b50(qqid, username, threshold), None if username else qqid)


@difficulty_b50.handle()
async def _difficulty_b50(event: MessageEvent, matched = RegexMatched()):
    """<难度>b50：筛选指定难度的成绩，新版本取前15，旧版本取前35，按 B35/B15 分组显示。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')

    difficulty = matched.group(1).strip() if matched and matched.group(1) else ''
    log.debug(f"[difficulty_b50] raw='{event.get_plaintext().strip()}', difficulty='{difficulty}'")

    if not difficulty:
        await difficulty_b50.finish('请提供难度，如：紫b50、13b50、Master b50', reply_message=True)

    qqid = event.user_id
    import time as _t
    from ..libraries.maimaidx_timing import reset
    reset()
    t0 = _t.perf_counter()
    result = await generate_difficulty_b50(qqid=qqid, difficulty=difficulty)
    total = _t.perf_counter() - t0
    if result is not None:
        if isinstance(result, str):
            await difficulty_b50.finish(result, reply_message=True)
        else:
            await difficulty_b50.finish(result + MessageSegment.text(_build_footer(qqid, total)), reply_message=True)


@difficulty_ab50.handle()
async def _difficulty_ab50(event: MessageEvent, matched = RegexMatched()):
    """<难度>ab50：筛选指定难度的成绩，无视分组直接取前50首。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')

    difficulty = matched.group(1).strip() if matched and matched.group(1) else ''
    log.debug(f"[difficulty_ab50] raw='{event.get_plaintext().strip()}', difficulty='{difficulty}'")

    if not difficulty:
        await difficulty_ab50.finish('请提供难度，如：紫ab50、13ab50、Master ab50', reply_message=True)

    qqid = event.user_id
    import time as _t
    from ..libraries.maimaidx_timing import reset
    reset()
    t0 = _t.perf_counter()
    result = await generate_difficulty_all_b50(qqid=qqid, difficulty=difficulty)
    total = _t.perf_counter() - t0
    if result is not None:
        if isinstance(result, str):
            await difficulty_ab50.finish(result, reply_message=True)
        else:
            await difficulty_ab50.finish(result + MessageSegment.text(_build_footer(qqid, total)), reply_message=True)


@ideal_b50.handle()
async def _ideal_b50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    """理想b50：将每个成绩的评级提高一个档次，新版本取前15，旧版本取前35，按 B35/B15 分组显示。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(ideal_b50, generate_ideal_b50(qqid, username), None if username else qqid)


@ideal_ab50.handle()
async def _ideal_ab50(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    """理想ab50：将每个成绩的评级提高一个档次，无视分组直接取前50首。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(ideal_ab50, generate_ideal_all_b50(qqid, username), None if username else qqid)


@enable_data_storage.handle()
async def _enable_data_storage(event: MessageEvent):
    """开启数据存储：每天自动存储成绩；首次开启立即拉取一次全量存档"""
    qqid = event.user_id
    success = data_storage.enable_user(qqid)
    if success:
        await enable_data_storage.send('正在首次同步全量成绩到本地，请稍候…', reply_message=True)
        store_ok = await fetch_and_store_user_scores(qqid, source="enable")
        sync_tip = (
            '\n首次同步已完成，「牌子统计」等将优先使用本地快照。'
            if store_ok
            else '\n首次同步未成功（查分器 Token、绑定或网络问题），可稍后发送「立即存储数据」重试。'
        )
        await enable_data_storage.finish(
            '已开启数据存储功能！\n'
            '每天凌晨 4:00 会自动存储你的全量成绩与 Rating。\n'
            '你也可以使用「立即存储数据」手动触发存储。'
            + sync_tip,
            reply_message=True,
        )
    else:
        await enable_data_storage.finish('开启数据存储失败，请稍后重试。', reply_message=True)


@disable_data_storage.handle()
async def _disable_data_storage(event: MessageEvent):
    """关闭数据存储：停止自动存储成绩"""
    qqid = event.user_id
    success = data_storage.disable_user(qqid)
    if success:
        await disable_data_storage.finish('已关闭数据存储功能。', reply_message=True)
    else:
        await disable_data_storage.finish('关闭数据存储失败，请稍后重试。', reply_message=True)


@store_data_now.handle()
async def _store_data_now(event: MessageEvent):
    """立即存储数据：手动触发成绩存储"""
    qqid = event.user_id
    
    # 检查是否已开启存储
    if not data_storage.is_enabled(qqid):
        await store_data_now.finish(
            '你尚未开启数据存储功能。\n'
            '请先发送「开启存储数据」开启自动存储。',
            reply_message=True
        )
        return
    
    await store_data_now.send('正在获取并存储你的成绩数据，请稍候...', reply_message=True)
    
    success = await fetch_and_store_user_scores(qqid)
    if success:
        # 获取今天的存储信息
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        snapshot = data_storage.load_daily_snapshot(qqid, today)
        if snapshot:
            await store_data_now.finish(
                f'成绩存储成功！\n'
                f'日期：{snapshot.date}\n'
                f'Rating：{snapshot.rating}\n'
                f'记录数：{snapshot.record_count} 首',
                reply_message=True
            )
        else:
            await store_data_now.finish('成绩存储成功！', reply_message=True)
    else:
        await store_data_now.finish(
            '成绩存储失败。\n'
            '可能原因：未绑定查分器、查分器Token过期、网络问题。\n'
            '请检查你的查分器绑定状态。',
            reply_message=True
        )


@storage_history.handle()
async def _storage_history(event: MessageEvent, message: Message = CommandArg()):
    """查询存储历史：展示最近 N 次快照摘要（默认 10 次）"""
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await storage_history.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return

    args = message.extract_plain_text().strip()
    limit = 10
    if args:
        try:
            limit = max(1, min(50, int(args)))
        except ValueError:
            limit = 10

    rows = data_storage.get_rating_history(qqid, days=limit)
    if not rows:
        await storage_history.finish('暂无存储记录，可先发送「立即存储数据」。', reply_message=True)
        return

    lines = [f'最近 {len(rows)} 次存档记录：']
    for i, r in enumerate(rows, 1):
        lines.append(
            f'{i}. {r.get("stored_at", r.get("date", ""))} | '
            f'Rating {r.get("rating", 0)} | '
            f'{r.get("record_count", 0)} 首 | '
            f'{r.get("source", "")} | '
            f'ID={r.get("snapshot_id", "")}'
        )
    lines.append('使用「查看存档 <ID>」查看某次快照详情。')
    await storage_history.finish('\n'.join(lines), reply_message=True)


@storage_snapshot.handle()
async def _storage_snapshot(event: MessageEvent, message: Message = CommandArg()):
    """查看某次存档详情：查看存档 <snapshot_id>"""
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await storage_snapshot.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return

    snapshot_id = message.extract_plain_text().strip()
    if not snapshot_id:
        latest = data_storage.list_snapshots(qqid, limit=1)
        if not latest:
            await storage_snapshot.finish('暂无存档记录，可先发送「立即存储数据」。', reply_message=True)
            return
        snapshot_id = latest[0].get("snapshot_id", "")

    snap = data_storage.load_snapshot_by_id(qqid, snapshot_id)
    if not snap:
        await storage_snapshot.finish(f'未找到存档：{snapshot_id}', reply_message=True)
        return

    top_records = sorted(snap.records, key=lambda x: x.ra, reverse=True)[:5]
    lines = [
        f'存档ID: {snap.snapshot_id}',
        f'时间: {snap.stored_at or snap.date}',
        f'来源: {snap.source}',
        f'昵称: {snap.nickname}',
        f'Rating: {snap.rating}',
        f'记录数: {snap.record_count}',
        'Top5 单曲：',
    ]
    for i, r in enumerate(top_records, 1):
        lines.append(f'{i}. {r.title} [{r.level}] {r.achievements:.4f}% {r.ds:.1f}->{r.ra} {r.rate}')
    await storage_snapshot.finish('\n'.join(lines), reply_message=True)


@weekly_report.handle()
async def _weekly_report(event: MessageEvent):
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await weekly_report.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return
    await _finish_score(weekly_report, generate_progress_report(qqid, 7), qqid)


@monthly_report.handle()
async def _monthly_report(event: MessageEvent):
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await monthly_report.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return
    await _finish_score(monthly_report, generate_progress_report(qqid, 30), qqid)


@daily_report.handle()
async def _daily_report(event: MessageEvent):
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await daily_report.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return
    await _finish_score(daily_report, generate_progress_report(qqid, 1), qqid)


@today_gain_recommend.handle()
async def _today_gain_recommend(event: MessageEvent):
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await today_gain_recommend.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return
    await _finish_score(today_gain_recommend, generate_today_gain_recommendation(qqid), qqid)


@plate_count_stats.handle()
async def _plate_count_stats(event: MessageEvent, user_id: Optional[int] = Depends(get_at_qq)):
    """牌子统计：优先本地最近快照；否则本指令内拉取 dev 全量一次再统计（不强制开启存储）。"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, "score"):
        raise IgnoredException("功能已禁用")
    qqid = user_id or event.user_id

    metas = data_storage.list_snapshots(qqid, limit=1)
    if metas:
        sid = metas[0].get("snapshot_id", "")
        snap = data_storage.load_snapshot_by_id(qqid, sid) if sid else None
        if snap and snap.records:
            await plate_count_stats.finish(format_plate_count_message(snap), reply_message=True)
            return

    await plate_count_stats.send("无本地快照或快照为空，正在拉取全量成绩并统计…", reply_message=True)
    try:
        records = await fetch_dev_records_as_score_records(qqid)
    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        await plate_count_stats.finish(str(e), reply_message=True)
        return
    except Exception as e:
        log.exception(f"[plate_count_stats] qq={qqid}")
        await plate_count_stats.finish(
            f"拉取全量成绩失败：{e}\n请检查查分器绑定与 Token。",
            reply_message=True,
        )
        return

    if not records:
        await plate_count_stats.finish(
            "未获取到任何成绩记录。\n"
            "开启数据存储并「立即存储数据」后，可优先使用本地快照以减少查分请求。",
            reply_message=True,
        )
        return

    note = "数据来源：本次指令实时拉取全量成绩（query_user_get_dev，未写入本地）"
    tip = ""
    if data_storage.is_enabled(qqid):
        tip = "\n提示：你已开启数据存储，发送「立即存储数据」后下次将优先用本地快照。"
    await plate_count_stats.finish(
        format_plate_count_message_from_records(records, note) + tip,
        reply_message=True,
    )


@compare_report.handle()
async def _compare_report(event: MessageEvent, message: Message = CommandArg()):
    qqid = event.user_id
    if not data_storage.is_enabled(qqid):
        await compare_report.finish(
            '你尚未开启数据存储功能，请先发送「开启存储数据」。',
            reply_message=True,
        )
        return
    args = message.extract_plain_text().strip().split()
    if len(args) != 2:
        await compare_report.finish(
            '用法：对比存档 <旧ID> <新ID>\n可先用「存储历史」查看ID。',
            reply_message=True,
        )
        return
    await _finish_score(compare_report, generate_progress_report_between(qqid, args[0], args[1]), qqid)


@gold_content.handle()
async def _gold_content(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(gold_content, generate_gold_content(qqid, username), None if username else qqid, unsupported_feature='含金量')


@water_content.handle()
async def _water_content(
    event: MessageEvent,
    message: Message = CommandArg(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    username = message.extract_plain_text().strip()
    await _finish_score(water_content, generate_water_content(qqid, username), None if username else qqid, unsupported_feature='含水量')


@version_b50.handle()
async def _(
    event: MessageEvent,
    match = RegexMatched(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    """版本 B50：根据版本代号筛选歌曲，按 RA 降序排序，无视类型分组"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    version_name = match.group(1) if match else ''
    if not version_name:
        await version_b50.finish('版本名称不能为空', reply_message=True)
        return
    await _finish_score(version_b50, generate_version_b50(qqid, None, version_name), None if user_id else qqid)


@legacy_b50.handle()
async def _(
    event: MessageEvent,
    match = RegexMatched(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    """历代版本 b50：使用指定版本的定数重算 rating。格式：l镜代b50 / l祭代b50"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    version_alias = match.group(1) if match else ''
    if not version_alias:
        await legacy_b50.finish('请输入版本代号，例如：l镜代b50', reply_message=True)
        return
    from ..libraries.maimaidx_version_alias import resolve_version_alias, build_legacy_ds_map, VERSION_ALIAS
    from ..libraries.maimaidx_b50_pipeline import b50_pipeline
    version_name = resolve_version_alias(version_alias)
    if not version_name:
        known = "、".join(list(VERSION_ALIAS.keys())[:12]) + "..."
        await legacy_b50.finish(
            f"未知版本代号「{version_alias}」，支持的代号：{known}\n格式：l镜代b50 / l祭代b50",
            reply_message=True,
        )
        return
    try:
        ds_map = build_legacy_ds_map(version_name)
    except FileNotFoundError:
        await legacy_b50.finish("未找到 dxdata.json 文件，无法计算历代版本 B50", reply_message=True)
        return
    except Exception as e:
        log.error(f"[legacy_b50] 构建 ds_map 失败: {e}")
        await legacy_b50.finish(f"加载定数数据失败：{e}", reply_message=True)
        return
    if not ds_map:
        await legacy_b50.finish(f"「{version_name}」版本无定数变化数据", reply_message=True)
        return
    import time as _t
    from ..libraries.maimaidx_timing import reset
    reset()
    _t0 = _t.perf_counter()
    result = await b50_pipeline(
        qqid=qqid,
        recalculate=True,
        ds_map=ds_map,
        by_group=False,
        compact_layout=True,
        hide_logo=False,
    )
    _total = _t.perf_counter() - _t0
    if isinstance(result, str):
        await legacy_b50.finish(result, reply_message=True)
    else:
        await legacy_b50.finish(result + MessageSegment.text(_build_footer(qqid, _total)), reply_message=True)


@legacy_b35.handle()
async def _(
    event: MessageEvent,
    match = RegexMatched(),
    user_id: Optional[int] = Depends(get_at_qq),
):
    """历代版本 b35：使用指定版本的定数重算 rating，仅取前 35 首。格式：l镜代b35"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    version_alias = match.group(1) if match else ''
    if not version_alias:
        await legacy_b35.finish('请输入版本代号，例如：l镜代b35', reply_message=True)
        return
    from ..libraries.maimaidx_version_alias import resolve_version_alias, build_legacy_ds_map, VERSION_ALIAS
    from ..libraries.maimaidx_b50_pipeline import b50_pipeline
    version_name = resolve_version_alias(version_alias)
    if not version_name:
        known = "、".join(list(VERSION_ALIAS.keys())[:12]) + "..."
        await legacy_b35.finish(
            f"未知版本代号「{version_alias}」，支持的代号：{known}\n格式：l镜代b35",
            reply_message=True,
        )
        return
    try:
        ds_map = build_legacy_ds_map(version_name)
    except FileNotFoundError:
        await legacy_b35.finish("未找到 dxdata.json 文件，无法计算历代版本 B35", reply_message=True)
        return
    except Exception as e:
        log.error(f"[legacy_b35] 构建 ds_map 失败: {e}")
        await legacy_b35.finish(f"加载定数数据失败：{e}", reply_message=True)
        return
    if not ds_map:
        await legacy_b35.finish(f"「{version_name}」版本无定数变化数据", reply_message=True)
        return
    import time as _t
    from ..libraries.maimaidx_timing import reset
    reset()
    _t0 = _t.perf_counter()
    result = await b50_pipeline(
        qqid=qqid,
        recalculate=True,
        ds_map=ds_map,
        by_group=False,
        max_display=35,
        compact_layout=True,
        hide_logo=False,
    )
    _total = _t.perf_counter() - _t0
    if isinstance(result, str):
        await legacy_b35.finish(result, reply_message=True)
    else:
        await legacy_b35.finish(result + MessageSegment.text(_build_footer(qqid, _total)), reply_message=True)


@dx2026_b35.handle()
async def _(event: MessageEvent, user_id: Optional[int] = Depends(get_at_qq)):
    """dx2026b35 别名：等价于 l彩代b35"""
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    from ..libraries.maimaidx_version_alias import resolve_version_alias, build_legacy_ds_map
    from ..libraries.maimaidx_b50_pipeline import b50_pipeline
    version_name = resolve_version_alias("彩代")
    try:
        ds_map = build_legacy_ds_map(version_name)
    except FileNotFoundError:
        await dx2026_b35.finish("未找到 dxdata.json 文件", reply_message=True)
        return
    except Exception as e:
        await dx2026_b35.finish(f"加载定数数据失败：{e}", reply_message=True)
        return
    if not ds_map:
        await dx2026_b35.finish(f"「{version_name}」版本无定数变化数据", reply_message=True)
        return
    import time as _t
    from ..libraries.maimaidx_timing import reset
    reset()
    _t0 = _t.perf_counter()
    result = await b50_pipeline(
        qqid=qqid,
        recalculate=True,
        ds_map=ds_map,
        by_group=False,
        max_display=35,
        compact_layout=True,
        hide_logo=False,
    )
    _total2 = _t.perf_counter() - _t0
    if isinstance(result, str):
        await dx2026_b35.finish(result, reply_message=True)
    else:
        await dx2026_b35.finish(result + MessageSegment.text(_build_footer(qqid, _total2)), reply_message=True)


@tag_analysis.handle()
async def _(event: MessageEvent, user_id: Optional[int] = Depends(get_at_qq)):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'tag_analysis'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id

    async def _gen():
        try:
            userinfo = await maiApi.query_user_b50(qqid=qqid)
        except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
            return str(e)
        stats = get_b50_tag_stats(userinfo)
        im = draw_analysis(stats)
        return MessageSegment.image(image_to_message_segment(im))

    from ..libraries.maimaidx_timing import finish_timed
    await finish_timed(tag_analysis, _gen())


@minfo.handle()
async def _(
    event: MessageEvent, 
    message: Message = CommandArg(), 
    user_id: Optional[int] = Depends(get_at_qq)
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    qqid = user_id or event.user_id
    args = message.extract_plain_text().strip()
    if not args:
        await minfo.finish('请输入曲目id或曲名', reply_message=True)

    if mai.total_list.by_id(args):
        music_id = args
    elif by_t := mai.total_list.by_title(args):
        music_id = by_t.id
    else:
        aliases = mai.total_alias_list.by_alias(args)
        if not aliases:
            await minfo.finish('未找到曲目')
        elif len(aliases) != 1:
            msg = '找到相同别名的曲目，请使用以下ID查询：\n'
            for music_id in aliases:
                msg += f'{music_id.SongID}：{music_id.Name}\n'
            await minfo.finish(msg.strip())
        else:
            music_id = str(aliases[0].SongID)
    
    from ..libraries.maimaidx_timing import finish_timed
    await finish_timed(minfo, draw_music_play_data(qqid, music_id))


@ginfo.handle()
async def _(event: MessageEvent, message: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    args = message.extract_plain_text().strip()
    if not args:
        await ginfo.finish('请输入曲目id或曲名', reply_message=True)
    if args[0] not in '绿黄红紫白':
        level_index = 3
    else:
        level_index = '绿黄红紫白'.index(args[0])
        args = args[1:].strip()
        if not args:
            await ginfo.finish('请输入曲目id或曲名', reply_message=True)
    if mai.total_list.by_id(args):
        id = args
    elif by_t := mai.total_list.by_title(args):
        id = by_t.id
    else:
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            await ginfo.finish('未找到曲目', reply_message=True)
        elif len(alias) != 1:
            msg = '找到相同别名的曲目，请使用以下ID查询：\n'
            for songs in alias:
                msg += f'{songs.SongID}：{songs.Name}\n'
            await ginfo.finish(msg.strip(), reply_message=True)
        else:
            id = str(alias[0].SongID)
    
    music = mai.total_list.by_id(id)
    if not music.stats:
        await ginfo.finish('该乐曲还没有统计信息', reply_message=True)
    if len(music.ds) == 4 and level_index == 4:
        await ginfo.finish('该乐曲没有这个等级', reply_message=True)
    if not music.stats[level_index]:
        await ginfo.finish('该等级没有统计信息', reply_message=True)
    stats = music.stats[level_index]
    data = await music_global_data(music, level_index) + dedent(f'''\
        游玩次数：{round(stats.cnt)}
        拟合难度：{stats.fit_diff:.2f}
        平均达成率：{stats.avg:.2f}%
        平均 DX 分数：{stats.avg_dx:.1f}
        谱面成绩标准差：{stats.std_dev:.2f}''')
    await ginfo.finish(data, reply_message=True)


@score.handle()
async def _(event: MessageEvent, message: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, 'score'):
        raise IgnoredException('功能已禁用')
    _args = message.extract_plain_text().strip()
    args = _args.split()
    if args and args[0] == '帮助':
        msg = dedent('''\
            此功能为查找某首歌分数线设计。
            命令格式：分数线「难度+歌曲id」「分数线」
            例如：分数线 紫799 100
            命令将返回分数线允许的「TAP」「GREAT」容错，
            以及「BREAK」50落等价的「TAP」「GREAT」数。
            以下为「TAP」「GREAT」的对应表：
                    GREAT / GOOD / MISS
            TAP         1 / 2.5  / 5
            HOLD        2 / 5    / 10
            SLIDE       3 / 7.5  / 15
            TOUCH       1 / 2.5  / 5
            BREAK       5 / 12.5 / 25 (外加200落)
        ''').strip()
        from ..libraries.maimaidx_timing import finish_timed_sync
        await finish_timed_sync(score, lambda: MessageSegment.image(text_to_bytes_io(msg)))
    else:
        try:
            result = re.search(r'([绿黄红紫白])\s?([0-9]+)', _args)
            level_labels = ['绿', '黄', '红', '紫', '白']
            level_labels2 = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:MASTER']
            level_index = level_labels.index(result.group(1))
            chart_id = result.group(2)
            line = float(args[-1])
            music = mai.total_list.by_id(chart_id)
            chart = music.charts[level_index]
            tap = int(chart.notes.tap)
            slide = int(chart.notes.slide)
            hold = int(chart.notes.hold)
            touch = int(chart.notes.touch) if len(chart.notes) == 5 else 0
            brk = int(chart.notes.brk)
            total_score = tap * 500 + slide * 1500 + hold * 1000 + touch * 500 + brk * 2500
            break_bonus = 0.01 / brk
            break_50_reduce = total_score * break_bonus / 4
            reduce = 101 - line
            if reduce <= 0 or reduce >= 101:
                raise ValueError
            msg = dedent(f'''\
                {music.title}「{level_labels2[level_index]}」
                分数线「{line}%」
                允许的最多「TAP」「GREAT」数量为 
                「{(total_score * reduce / 10000):.2f}」(每个-{10000 / total_score:.4f}%),
                「BREAK」50落(一共「{brk}」个)
                等价于「{(break_50_reduce / 100):.3f}」个「TAP」「GREAT」(-{break_50_reduce / total_score * 100:.4f}%)
            ''').strip()
            await score.finish(msg, reply_message=True)
        except (AttributeError, ValueError) as e:
            log.exception(e)
            await score.finish('格式错误，输入“分数线 帮助”以查看帮助信息', reply_message=True)
