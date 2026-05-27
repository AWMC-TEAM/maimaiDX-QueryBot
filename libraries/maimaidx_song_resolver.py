"""
歌曲解析器：统一封装歌曲 ID/曲名/别名的解析逻辑。

支持：
- 纯数字 -> 按 ID 查找
- 曲名 -> 按标题查找
- 别名 -> 按别名查找（支持异步别名服务器查询）

用法：
    music = await SongResolver.resolve("ガヴリールドロップキック")
    if music:
        print(music.id, music.title)
"""

from __future__ import annotations

from typing import Optional

from ..config import log
from .maimaidx_music import mai
from .maimaidx_music_info import get_music_by_alias


class SongResolver:
    """歌曲解析器：将用户输入解析为歌曲对象。"""

    @classmethod
    async def resolve(cls, text: str) -> Optional[object]:
        """
        解析歌曲输入，返回歌曲对象。

        解析顺序：
            1. 纯数字 -> 按 ID 查找
            2. 曲名 -> 按标题查找
            3. 别名 -> 按别名查找（异步查询别名服务器）

        Returns:
            歌曲对象 或 None
        """
        text = text.strip()
        log.debug(f"[SongResolver] 开始解析歌曲输入: '{text}'")

        if not text:
            log.debug("[SongResolver] 输入为空，返回 None")
            return None

        # 1. 按 ID 查找
        if text.isdigit():
            log.debug(f"[SongResolver] 输入为数字，尝试按 ID 查找: {text}")
            music = mai.total_list.by_id(text)
            if music:
                log.debug(f"[SongResolver] 按 ID 找到歌曲: {getattr(music, 'title', 'unknown')} (id={getattr(music, 'id', 'unknown')})")
                return music
            log.debug(f"[SongResolver] 按 ID 未找到歌曲: {text}")

        # 2. 按标题查找
        log.debug(f"[SongResolver] 尝试按标题查找: '{text}'")
        music = mai.total_list.by_title(text)
        if music:
            log.debug(f"[SongResolver] 按标题找到歌曲: {getattr(music, 'title', 'unknown')} (id={getattr(music, 'id', 'unknown')})")
            return music
        log.debug(f"[SongResolver] 按标题未找到歌曲: '{text}'")

        # 3. 按别名查找
        log.debug(f"[SongResolver] 尝试按别名查找: '{text}'")
        try:
            alias_result = await get_music_by_alias(text)
            if alias_result:
                log.debug(f"[SongResolver] 按别名找到歌曲: {getattr(alias_result, 'title', 'unknown')} (id={getattr(alias_result, 'id', 'unknown')})")
                return alias_result
            log.debug(f"[SongResolver] 按别名未找到歌曲: '{text}'")
        except Exception as e:
            log.warning(f"[SongResolver] 按别名查找时出错: {type(e).__name__}: {e}")

        log.debug(f"[SongResolver] 所有查找方式均未找到: '{text}'")
        return None

    @classmethod
    async def resolve_with_suggestions(cls, text: str) -> tuple[Optional[object], Optional[str]]:
        """
        解析歌曲输入，如果找到多个相似结果返回建议列表。

        Returns:
            (歌曲对象, 建议文本)
            - 找到唯一结果：(music, None)
            - 找到多个别名：(None, "找到相同别名的曲目...")
            - 未找到：(None, None)
        """
        text = text.strip()
        log.debug(f"[SongResolver] resolve_with_suggestions 开始解析: '{text}'")

        if not text:
            return None, None

        # 1. 按 ID 查找
        if text.isdigit():
            music = mai.total_list.by_id(text)
            if music:
                return music, None

        # 2. 按标题查找
        music = mai.total_list.by_title(text)
        if music:
            return music, None

        # 3. 按别名查找
        aliases = mai.total_alias_list.by_alias(text)
        if aliases:
            if len(aliases) == 1:
                music = mai.total_list.by_id(str(aliases[0].SongID))
                if music:
                    return music, None
            else:
                # 多个别名匹配
                msg = "找到相同别名的曲目，请使用以下ID查询：\n"
                for song in aliases:
                    msg += f"{song.SongID}：{song.Name}\n"
                return None, msg.strip()

        # 4. 尝试从别名服务器获取
        from .maimaidx_api_data import maiApi
        from .maimaidx_model import AliasStatus
        try:
            obj = await maiApi.get_songs(text)
            if obj:
                if isinstance(obj[0], AliasStatus):
                    msg = f"未找到别名为「{text}」的歌曲，但找到与此相同别名的投票：\n"
                    for s in obj:
                        msg += f"- {s.Tag}\n    ID {s.SongID}: {text}\n"
                    msg += "※ 可以使用指令「同意别名 XXXXX」进行投票"
                    return None, msg.strip()
                else:
                    # 服务器返回了别名匹配
                    if len(obj) == 1:
                        music = mai.total_list.by_id(str(obj[0].SongID))
                        if music:
                            return music, None
                    else:
                        msg = "找到相同别名的曲目，请使用以下ID查询：\n"
                        for song in obj:
                            msg += f"{song.SongID}：{song.Name}\n"
                        return None, msg.strip()
        except Exception:
            pass

        return None, None

    @classmethod
    def get_id(cls, music: object) -> str:
        """安全获取歌曲 ID。"""
        result = str(getattr(music, "id", ""))
        log.debug(f"[SongResolver] get_id: {result}")
        return result

    @classmethod
    def get_title(cls, music: object) -> str:
        """安全获取歌曲标题。"""
        result = getattr(music, "title", "未知")
        log.debug(f"[SongResolver] get_title: {result}")
        return result

    @classmethod
    def has_level(cls, music: object, level_index: int) -> bool:
        """检查歌曲是否有指定难度。"""
        levels = getattr(music, "level", [])
        result = len(levels) > level_index
        log.debug(f"[SongResolver] has_level(level_index={level_index}): levels={levels}, result={result}")
        return result
