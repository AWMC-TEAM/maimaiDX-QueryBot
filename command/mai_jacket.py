
from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from ..config import coverdir
from ..libraries.maimaidx_timing import finish_timed_sync

jacket_extractor = on_command("提取曲绘", priority=5)

@jacket_extractor.handle()
async def _(arg: Message = CommandArg()):
    song_id = arg.extract_plain_text().strip()
    if not song_id or not song_id.isdigit():
        await jacket_extractor.finish("请输入有效的歌曲ID。")
        return

    image_path = coverdir / f"{song_id}.png"

    if not image_path.exists():
        await jacket_extractor.finish(f"未找到ID为 {song_id} 的曲绘。")
        return

    await finish_timed_sync(jacket_extractor, lambda: MessageSegment.image(image_path))
