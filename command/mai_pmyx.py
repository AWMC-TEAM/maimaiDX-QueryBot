# 谱面印象：查看/上传/回复/点赞（对接 API_谱面印象使用说明.md）
from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..config import diffs, maiconfig
from ..libraries.maimaidx_music import feature_manager, mai
from ..libraries.maimaidx_pmyx_api import PmyxAPI


def _pmyx_api() -> PmyxAPI:
    base = maiconfig.pmyx_api_base_url or "https://mai.mai2dx.shop"
    return PmyxAPI(base)


def _resolve_music_id(args: str) -> Optional[str]:
    """曲目 id/曲名/别名 -> music_id，未找到返回 None。"""
    args = args.strip()
    if not args:
        return None
    if mai.total_list.by_id(args):
        return args
    if by_t := mai.total_list.by_title(args):
        return by_t.id
    aliases = mai.total_alias_list.by_alias(args)
    if not aliases:
        return None
    if len(aliases) != 1:
        return None
    return str(aliases[0].SongID)


def _user_nickname(event: MessageEvent) -> str:
    """发送者昵称。"""
    try:
        if hasattr(event, "sender") and event.sender:
            return getattr(event.sender, "nickname", None) or getattr(event.sender, "card", None) or ""
    except Exception:
        pass
    return ""


# ---------- 查看谱面印象 ----------
pmyx_get = on_command("谱面印象", aliases={"查谱面印象", "曲目印象"})


@pmyx_get.handle()
async def _(
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, "score"):
        raise IgnoredException("功能已禁用")
    text = arg.extract_plain_text().strip()
    music_id = _resolve_music_id(text) if text else None
    if not music_id:
        await matcher.finish("用法：谱面印象 <曲目id或曲名>", reply_message=True)
        return
    api = _pmyx_api()
    try:
        items = await api.get_impressions(music_id)
    except Exception as e:
        await matcher.finish(f"请求失败：{e}", reply_message=True)
        return
    music = mai.total_list.by_id(music_id)
    title = music.title if music else music_id
    if not items:
        await matcher.finish(f"「{title}」暂无谱面印象", reply_message=True)
        return
    lines = [f"【{title}】谱面印象（共 {len(items)} 条）"]
    for i, x in enumerate(items[:20], 1):
        diff_idx = x.get("difficulty", 3)
        diff_name = diffs[diff_idx] if 0 <= diff_idx < len(diffs) else str(diff_idx)
        nick = x.get("nickname", "?")
        rating = x.get("rating", 0)
        imp = (x.get("impression") or "").strip()
        adm = x.get("admiration", 0)
        date = x.get("date", "")
        # 新格式：玩家昵称 评分
        line = f"{i}. [{diff_name}] {nick} {rating}"
        # 时间 👍
        line += f"\n   {date} 👍{adm}"
        # 详细
        if imp:
            line += f"\n   {imp[:80]}{'…' if len(imp) > 80 else ''}"
        replies = x.get("replies") or []
        if replies:
            line += f" （{len(replies)} 条回复）"
        lines.append(line)
    if len(items) > 20:
        lines.append(f"… 仅展示前 20 条，共 {len(items)} 条")
    await matcher.finish("\n".join(lines), reply_message=True)


# ---------- 写谱面印象 ----------
pmyx_write = on_command("写谱面印象", aliases={"上传谱面印象", "添加谱面印象"})


@pmyx_write.handle()
async def _(
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, "score"):
        raise IgnoredException("功能已禁用")
    text = arg.extract_plain_text().strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await matcher.finish(
            "用法：写谱面印象 <曲目id或曲名> <难度0-4> <印象内容>\n"
            "难度：0=Basic 1=Advanced 2=Expert 3=Master 4=Re:Master",
            reply_message=True,
        )
        return
    music_id = _resolve_music_id(parts[0])
    if not music_id:
        await matcher.finish(f"未找到曲目：{parts[0]}", reply_message=True)
        return
    try:
        difficulty = int(parts[1])
        if difficulty < 0 or difficulty > 4:
            raise ValueError("难度需 0-4")
    except ValueError:
        await matcher.finish("难度须为 0-4 的整数（0=Basic 1=Advanced 2=Expert 3=Master 4=Re:Master）", reply_message=True)
        return
    impression_text = parts[2].strip()
    nickname = _user_nickname(event) or str(event.user_id)
    api = _pmyx_api()
    try:
        res = await api.update_impression(
            qq_id=event.user_id,
            nickname=nickname,
            song_id=int(music_id),
            difficulty=difficulty,
            impression_text=impression_text,
            rating=0,
            total_achievement=0,
            total_play_count=0,
            admiration=0,
        )
    except Exception as e:
        await matcher.finish(f"请求失败：{e}", reply_message=True)
        return
    if res.get("returnCode") == 0:
        diff_name = diffs[difficulty] if 0 <= difficulty < len(diffs) else str(difficulty)
        await matcher.finish(f"已提交「{diff_name}」谱面印象", reply_message=True)
    else:
        await matcher.finish(f"提交失败：{res.get('message', '未知错误')}", reply_message=True)


# ---------- 回复谱面印象 ----------
pmyx_reply = on_command("回复谱面印象", aliases={"回复印象"})


@pmyx_reply.handle()
async def _(
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, "score"):
        raise IgnoredException("功能已禁用")
    text = arg.extract_plain_text().strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await matcher.finish("用法：回复谱面印象 <歌曲id> <评论id> <回复内容>", reply_message=True)
        return
    try:
        music_id = int(parts[0])
        comment_id = int(parts[1])
    except ValueError:
        await matcher.finish("歌曲id 和 评论id 须为数字", reply_message=True)
        return
    reply_content = parts[2].strip()
    if not reply_content:
        await matcher.finish("回复内容不能为空", reply_message=True)
        return
    nickname = _user_nickname(event) or str(event.user_id)
    api = _pmyx_api()
    try:
        res = await api.add_reply(
            music_id=music_id,
            comment_id=comment_id,
            reply_content=reply_content,
            reply_qq_id=event.user_id,
            reply_nickname=nickname,
        )
    except Exception as e:
        await matcher.finish(f"请求失败：{e}", reply_message=True)
        return
    if res.get("returnCode") == 0:
        await matcher.finish("回复已添加", reply_message=True)
    else:
        await matcher.finish(f"回复失败：{res.get('message', '未知错误')}", reply_message=True)


# ---------- 点赞谱面印象 ----------
pmyx_like = on_command("点赞谱面印象", aliases={"点赞印象"})


@pmyx_like.handle()
async def _(
    event: MessageEvent,
    matcher: Matcher,
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent) and not feature_manager.is_enabled(event.group_id, "score"):
        raise IgnoredException("功能已禁用")
    text = arg.extract_plain_text().strip().split()
    if len(text) < 2:
        await matcher.finish("用法：点赞谱面印象 <歌曲id> <评论id>", reply_message=True)
        return
    try:
        music_id = int(text[0])
        comment_id = int(text[1])
    except ValueError:
        await matcher.finish("歌曲id 和 评论id 须为数字", reply_message=True)
        return
    api = _pmyx_api()
    try:
        items = await api.get_impressions(str(music_id))
    except Exception as e:
        await matcher.finish(f"请求失败：{e}", reply_message=True)
        return
    comment = next((x for x in items if x.get("Id") == comment_id), None)
    if not comment:
        await matcher.finish("未找到该评论", reply_message=True)
        return
    new_admiration = (comment.get("admiration") or 0) + 1
    try:
        res = await api.update_admiration(
            music_id=music_id,
            comment_id=comment_id,
            new_admiration=new_admiration,
        )
    except Exception as e:
        await matcher.finish(f"请求失败：{e}", reply_message=True)
        return
    if res.get("returnCode") == 0:
        await matcher.finish(f"已点赞（当前 👍{new_admiration}）", reply_message=True)
    else:
        await matcher.finish(f"点赞失败：{res.get('message', '未知错误')}", reply_message=True)
