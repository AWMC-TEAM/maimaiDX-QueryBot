"""
数据存储定时任务模块

功能：
- 每天自动存储已开启用户的成绩
- 使用 nonebot 的定时任务调度器
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger as log
from nonebot import require, get_bot
from nonebot.adapters.onebot.v11 import Bot

# 使用 require 导入定时任务调度器
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from ..libraries.maimaidx_data_storage import data_storage, DailySnapshot, ScoreRecord
from ..libraries.maimaidx_api_data import maiApi
from ..libraries.maimaidx_model import UserInfo


async def fetch_and_store_user_scores(
    qqid: int, *, source: str = "manual", target_date: Optional[str] = None
) -> bool:
    """
    获取并存储用户成绩
    
    Args:
        qqid: 用户QQ号
    
    Returns:
        是否成功
    """
    try:
        # 获取用户所有成绩（需要开发者 Token）
        userinfo = await maiApi.query_user_b50(qqid=qqid)
        dev = await maiApi.query_user_get_dev(qqid=qqid)
        records = list(dev.records or [])
        
        if not records:
            log.warning(f"[DataScheduler] 用户 {qqid} 没有成绩数据")
            return False
        
        # 构建成绩记录列表
        score_records = []
        for r in records:
            score_record = ScoreRecord(
                song_id=r.song_id,
                title=r.title,
                level=r.level,
                level_index=r.level_index,
                ds=r.ds,
                achievements=r.achievements,
                rate=r.rate,
                ra=r.ra,
                fc=r.fc,
                fs=r.fs,
                dxScore=getattr(r, 'dxScore', 0),
            )
            score_records.append(score_record)
        
        # 创建每日快照
        date_str = target_date or datetime.now().strftime("%Y-%m-%d")
        snapshot = DailySnapshot(
            date=date_str,
            qqid=qqid,
            nickname=userinfo.nickname or userinfo.username or str(qqid),
            rating=userinfo.rating or 0,
            records=score_records,
            record_count=len(score_records),
            source=source,
        )
        
        # 保存快照
        success = data_storage.save_daily_snapshot(snapshot)
        if success:
            log.info(
                f"[DataScheduler] 成功存储用户 {qqid} 的 {date_str} 成绩快照，"
                f"source={source}，共 {len(score_records)} 首，rating: {snapshot.rating}"
            )
        return success
        
    except Exception as e:
        log.error(f"[DataScheduler] 获取并存储用户 {qqid} 成绩失败: {e}")
        return False


async def daily_storage_task():
    """每日存储任务：为所有开启存储的用户保存成绩"""
    log.info("[DataScheduler] 开始执行每日成绩存储任务")
    
    enabled_users = data_storage.get_enabled_users()
    if not enabled_users:
        log.info("[DataScheduler] 没有用户开启数据存储，跳过")
        return
    
    log.info(f"[DataScheduler] 共有 {len(enabled_users)} 个用户需要存储数据")
    
    # 并发处理所有用户，限制并发数为 5
    semaphore = asyncio.Semaphore(5)
    
    async def store_one(qqid: int):
        async with semaphore:
            return await fetch_and_store_user_scores(qqid, source="auto")
    
    tasks = [store_one(qqid) for qqid in enabled_users]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_count = sum(1 for r in results if r is True)
    fail_count = len(results) - success_count
    
    log.info(f"[DataScheduler] 每日存储任务完成：成功 {success_count} 个，失败 {fail_count} 个")


# 添加定时任务：每天凌晨 4:00 执行
@scheduler.scheduled_job("cron", hour=4, minute=0, id="daily_score_storage")
async def scheduled_daily_storage():
    """定时任务：每天凌晨 4:00 自动存储成绩"""
    await daily_storage_task()


# 添加定时任务：每小时检查一次（用于启动时补存）
@scheduler.scheduled_job("cron", hour="*/6", minute=0, id="periodic_storage_check")
async def periodic_storage_check():
    """定期检查：每6小时检查一次是否需要补存"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    enabled_users = data_storage.get_enabled_users()
    users_to_store = []
    
    for qqid in enabled_users:
        # 检查今天是否已经存储过
        existing = data_storage.load_daily_snapshot(qqid, today)
        if not existing:
            users_to_store.append(qqid)
    
    if users_to_store:
        log.info(f"[DataScheduler] 发现 {len(users_to_store)} 个用户今天尚未存储成绩，开始补存")
        
        semaphore = asyncio.Semaphore(5)
        
        async def store_one(qqid: int):
            async with semaphore:
                return await fetch_and_store_user_scores(
                    qqid, source="periodic_check", target_date=today
                )
        
        tasks = [store_one(qqid) for qqid in users_to_store]
        await asyncio.gather(*tasks, return_exceptions=True)


# 启动时执行一次存储（用于补存昨天的数据）
async def on_startup_storage():
    """启动时执行：检查昨天是否存储，如果没有则补存"""
    await asyncio.sleep(30)  # 等待 30 秒，确保 bot 完全启动
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    enabled_users = data_storage.get_enabled_users()
    
    users_to_store = []
    for qqid in enabled_users:
        existing = data_storage.load_daily_snapshot(qqid, yesterday)
        if not existing:
            users_to_store.append(qqid)
    
    if users_to_store:
        log.info(f"[DataScheduler] 启动补存：{len(users_to_store)} 个用户昨天未存储")
        
        semaphore = asyncio.Semaphore(5)
        
        async def store_one(qqid: int):
            async with semaphore:
                return await fetch_and_store_user_scores(
                    qqid, source="startup_backfill", target_date=yesterday
                )
        
        tasks = [store_one(qqid) for qqid in users_to_store]
        await asyncio.gather(*tasks, return_exceptions=True)


# 注册启动任务
from nonebot import get_driver
driver = get_driver()

@driver.on_bot_connect
async def _(bot):
    """Bot 连接时触发启动补存"""
    asyncio.create_task(on_startup_storage())
