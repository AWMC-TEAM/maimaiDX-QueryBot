"""
幻之成绩命令处理

用法：
    /幻之成绩 15000  — 生成目标 15000 的幻之成绩单
    /幻之成绩       — 使用默认目标（15000）
    /幻b50 14500   — 别名
"""
import re
import traceback
from typing import Optional, Tuple

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageEvent,
)
from nonebot.params import CommandArg

from ..libraries.maimaidx_phantom_score import (
    generate_phantom_score,
    format_phantom_score_text,
)
from ..config import log


phantom_score_cmd = on_command('幻之成绩', aliases={'幻之b50', 'phantom_score', 'phantom_b50', '幻b50'})


def _parse_target_rating(
    args: Message,
) -> Tuple[bool, Optional[int], str]:
    """
    解析命令参数，提取目标 Rating。

    Returns:
        (success, target_rating, error_message)
    """
    arg_text = args.extract_plain_text().strip()

    if not arg_text:
        return True, 15000, ''

    numbers = re.findall(r'\d+', arg_text)
    if not numbers:
        return False, None, '请提供有效的目标 Rating 数值（如 15000）。'

    target = int(numbers[0])

    if target < 500:
        return False, None, f'目标 Rating {target} 过低，最低 500。'
    if target > 17000:
        return False, None, f'目标 Rating {target} 过高，最高 17000（理论极限约 16800）。'

    return True, target, ''


@phantom_score_cmd.handle()
async def handle_phantom_score(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    """处理幻之成绩请求"""
    success, target, error = _parse_target_rating(args)
    if not success:
        await phantom_score_cmd.finish(error)

    try:
        b35_list, b15_list, actual = generate_phantom_score(target)
        text = format_phantom_score_text(b35_list, b15_list, target, actual)

        max_len = 4000
        if len(text) <= max_len:
            await phantom_score_cmd.finish(text)
        else:
            segments = []
            current = ''
            for line in text.split('\n'):
                if len(current) + len(line) + 1 > max_len:
                    segments.append(current)
                    current = line
                else:
                    if current:
                        current += '\n' + line
                    else:
                        current = line
            if current:
                segments.append(current)

            for seg in segments:
                await phantom_score_cmd.send(seg)

            await phantom_score_cmd.finish()

    except ValueError as e:
        await phantom_score_cmd.finish(str(e))
    except Exception as e:
        log.error(traceback.format_exc())
        await phantom_score_cmd.finish(f'生成幻之成绩时出错: {e}')
