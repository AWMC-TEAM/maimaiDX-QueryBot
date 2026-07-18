#!/usr/bin/env python3
"""
轻量脚本：在有项目依赖（nonebot、配置等）的环境中运行，以触发单个用户的成绩存储。
用法：
  python3 run_save_user_scores.py 3801477277
或
  python3 run_save_user_scores.py --qq 3801477277 --source upload
"""
import argparse
import asyncio
import sys

from loguru import logger


async def _main(qqid: int, source: str, target_date: str | None):
    try:
        from libraries.maimaidx_data_scheduler import fetch_and_store_user_scores
    except Exception as e:
        logger.error(f"无法导入 fetch_and_store_user_scores: {e}")
        return 2

    try:
        ok = await fetch_and_store_user_scores(qqid, source=source, target_date=target_date)
        logger.info(f"存储结果: {ok}")
        return 0 if ok else 1
    except Exception as e:
        logger.exception("执行存储时出错")
        return 3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("qq", type=int, help="目标 QQ 号")
    p.add_argument("--source", default="manual", help="来源标记（manual/auto/upload）")
    p.add_argument("--date", default=None, help="指定快照日期（YYYY-MM-DD），用于补存）")
    args = p.parse_args()
    code = asyncio.run(_main(args.qq, args.source, args.date))
    sys.exit(code)


if __name__ == '__main__':
    main()
