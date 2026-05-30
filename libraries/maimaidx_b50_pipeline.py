"""
B50 生成管道：抽象通用流程，减少各变体 B50 的重复代码。

核心流程：
    1. 获取用户数据（query_user_b50 + query_user_get_dev）
    2. 筛选/转换成绩（filter_fn / transform_fn）
    3. 重新计算 rating（可选）
    4. 按 B35/B15 分组或取前 50
    5. 构建 UserInfo 并绘图

用法：
    result = await b50_pipeline(
        qqid=123456,
        filter_fn=lambda r: r.level_index == 3,  # 只保留紫谱
        by_group=True,
        title_suffix="紫谱",
    )
"""

from __future__ import annotations

import traceback
from typing import Callable, List, Optional, Union

from nonebot.adapters.onebot.v11 import MessageSegment

from ..config import log
from .image import image_to_base64
from .maimaidx_api_data import maiApi
from .maimaidx_best_50 import DrawBest, _is_latest_version, computeRa, filter_utage_records
from .maimaidx_error import UserDisabledQueryError, UserNotExistsError, UserNotFoundError
from .maimaidx_model import ChartInfo, Data, PlayInfoDev, UserInfo


# 筛选函数类型：接收 PlayInfoDev，返回是否保留
RecordFilter = Callable[[PlayInfoDev], bool]
# 转换函数类型：接收 PlayInfoDev，返回转换后的 PlayInfoDev（或 None 表示丢弃）
RecordTransform = Callable[[PlayInfoDev], Optional[PlayInfoDev]]


async def _fetch_user_data(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
) -> tuple[UserInfo, List[PlayInfoDev]]:
    """
    获取用户基础信息和全量成绩。根据用户数据源偏好选择水鱼/落雪。

    Returns:
        (userinfo, records) 元组
    Raises:
        UserNotFoundError, UserNotExistsError, UserDisabledQueryError, LxnsDataError
    """
    log.debug(f"[b50_pipeline] _fetch_user_data: qqid={qqid}, username={username}")
    if username:
        qqid = None

    from .maimaidx_datasource import get_user_records
    userinfo, records = await get_user_records(qqid=qqid, username=username)
    log.debug(f"[b50_pipeline] 获取到 userinfo: nickname={userinfo.nickname}, rating={userinfo.rating}")
    log.debug(f"[b50_pipeline] 获取到 {len(records)} 条成绩记录")

    # 过滤掉宴谱成绩
    records = filter_utage_records(records)
    log.debug(f"[b50_pipeline] 过滤宴谱后剩余 {len(records)} 条成绩记录")

    return userinfo, records


def _recalculate_rating(records: List[PlayInfoDev], ds_map: Optional[dict] = None) -> List[PlayInfoDev]:
    """
    使用当前曲目表定数重新计算每条成绩的 rating 和 rate。
    这是多个 B50 变体共用的操作（ab50、fc、ap、寸、锁血、越级、难度等）。

    Args:
        records: 成绩列表
        ds_map: 可选，自定义定数映射 {(song_id, level_index): ds_value}
                当提供时，优先使用此映射中的定数值（用于历代版本 B50）
    """
    from .maimaidx_music import mai

    log.debug(f"[b50_pipeline] 开始重算 {len(records)} 条成绩的 rating")
    recalculated: List[PlayInfoDev] = []
    for i, r in enumerate(records):
        current_ds = r.ds
        if ds_map:
            custom = ds_map.get((str(r.song_id), r.level_index))
            if custom is not None:
                current_ds = round(float(custom), 1)
        if ds_map is None or (str(r.song_id), r.level_index) not in (ds_map or {}):
            try:
                music = mai.total_list.by_id(str(r.song_id))
                if music and r.level_index < len(music.ds):
                    current_ds = round(float(music.ds[r.level_index]), 1)
            except Exception:
                pass

        new_ra, new_rate = computeRa(current_ds, r.achievements, israte=True)
        log.debug(f"[b50_pipeline] 重算记录 {i}: song_id={r.song_id}, ds={current_ds}, ra={new_ra}, rate={new_rate}")

        # 使用 model_copy 创建新对象（如果支持）
        if hasattr(r, "model_copy"):
            new_r = r.model_copy(update={"ds": current_ds, "ra": new_ra, "rate": new_rate})
        else:
            # 回退：直接修改（不推荐，但兼容旧模型）
            r.ds = current_ds
            r.ra = new_ra
            r.rate = new_rate
            new_r = r

        recalculated.append(new_r)
    log.debug(f"[b50_pipeline] 重算完成")
    return recalculated


def _group_records(
    records: List[PlayInfoDev],
    by_group: bool,
    max_display: int = 50,
) -> tuple[List[PlayInfoDev], List[PlayInfoDev]]:
    """
    将记录分配到 B35/B15 区。

    Args:
        records: 已排序（ra 倒序）的记录列表
        by_group: True=按版本分组（旧版->B35, 新版->B15），False=直接取前 max_display 再切分
        max_display: 最大显示条数（50=B50, 35=B35）

    Returns:
        (b35_list, b15_list)
    """
    log.debug(f"[b50_pipeline] 分组: by_group={by_group}, max_display={max_display}, 总记录数={len(records)}")
    if by_group:
        b15 = sorted([r for r in records if _is_latest_version(r)], key=lambda x: -x.ra)[:15]
        b35 = sorted([r for r in records if not _is_latest_version(r)], key=lambda x: -x.ra)[:35]
    else:
        head = records[:max_display]
        if max_display >= 50:
            b35 = head[:35]
            b15 = head[35:50]
        else:
            b35 = head[:max_display]
            b15 = []
    log.debug(f"[b50_pipeline] 分组结果: b35={len(b35)}, b15={len(b15)}")
    return b35, b15


def _build_userinfo(
    base_userinfo: UserInfo,
    b35: List[PlayInfoDev],
    b15: List[PlayInfoDev],
) -> UserInfo:
    """根据 B35/B15 列表构建新的 UserInfo（重算总 rating）。"""
    total_ra = int(sum(r.ra for r in b35) + sum(r.ra for r in b15))
    log.debug(f"[b50_pipeline] 构建 UserInfo: total_ra={total_ra}, b35={len(b35)}, b15={len(b15)}")
    return UserInfo(
        nickname=base_userinfo.nickname or base_userinfo.username or "未知",
        plate=base_userinfo.plate,
        additional_rating=base_userinfo.additional_rating
        if base_userinfo.additional_rating is not None
        else 0,
        rating=total_ra,
        username=base_userinfo.username,
        charts=Data(sd=b35, dx=b15),
    )


async def b50_pipeline(
    qqid: Optional[int] = None,
    username: Optional[str] = None,
    *,
    filter_fn: Optional[RecordFilter] = None,
    transform_fn: Optional[RecordTransform] = None,
    recalculate: bool = True,
    ds_map: Optional[dict] = None,
    by_group: bool = True,
    max_display: int = 50,
    compact_layout: Optional[bool] = None,
    hide_logo: bool = False,
    empty_message: str = "没有符合条件的成绩数据（需开发者 Token 获取全量成绩）",
) -> Union[MessageSegment, str]:
    """
    B50 生成通用管道。

    Args:
        qqid: QQ 号
        username: 用户名（与 qqid 二选一）
        filter_fn: 筛选函数，接收 PlayInfoDev 返回 bool
        transform_fn: 转换函数，接收 PlayInfoDev 返回新的 PlayInfoDev（或 None 丢弃）
        recalculate: 是否使用当前定数重新计算 rating
        ds_map: 可选，自定义定数映射 {(song_id, level_index): ds_value}（用于历代版本 B50）
        by_group: True=按 B35/B15 版本分组，False=无视分组取前 N
        max_display: 最大显示条数（50=B50, 35=B35）
        compact_layout: 是否使用紧凑布局（None 时自动根据 by_group 推断）
        hide_logo: 是否隐藏左侧 logo
        empty_message: 无数据时的提示消息

    Returns:
        MessageSegment.image 或错误提示字符串
    """
    log.debug(f"[b50_pipeline] 开始执行: qqid={qqid}, username={username}, recalculate={recalculate}, by_group={by_group}")

    try:
        # 1. 获取数据
        userinfo, records = await _fetch_user_data(qqid, username)

        if not records:
            log.debug(f"[b50_pipeline] 无成绩记录，返回空消息")
            return empty_message

        # 2. 筛选
        if filter_fn:
            log.debug(f"[b50_pipeline] 开始筛选，筛选前记录数: {len(records)}")
            filtered = [r for r in records if filter_fn(r)]
            log.debug(f"[b50_pipeline] 筛选后记录数: {len(filtered)}")
            records = filtered

        if not records:
            log.debug(f"[b50_pipeline] 筛选后无记录，返回空消息")
            return empty_message

        # 3. 转换
        if transform_fn:
            log.debug(f"[b50_pipeline] 开始转换，转换前记录数: {len(records)}")
            transformed: List[PlayInfoDev] = []
            for r in records:
                new_r = transform_fn(r)
                if new_r is not None:
                    transformed.append(new_r)
            records = transformed
            log.debug(f"[b50_pipeline] 转换后记录数: {len(records)}")

        if not records:
            log.debug(f"[b50_pipeline] 转换后无记录，返回空消息")
            return empty_message

        # 4. 重新计算 rating（可选）
        if recalculate:
            records = _recalculate_rating(records, ds_map=ds_map)

        # 5. 按 ra 倒序排序
        records.sort(key=lambda x: -x.ra)
        log.debug(f"[b50_pipeline] 排序后前5条: {[(r.song_id, r.ra) for r in records[:5]]}")

        # 6. 分组
        b35, b15 = _group_records(records, by_group, max_display)

        if not b35 and not b15:
            log.debug(f"[b50_pipeline] 分组后无记录，返回空消息")
            return empty_message

        # 7. 构建 UserInfo 并绘图
        new_userinfo = _build_userinfo(userinfo, b35, b15)
        _compact = compact_layout if compact_layout is not None else (not by_group)
        log.debug(f"[b50_pipeline] 绘图: compact_layout={_compact}, hide_logo={hide_logo}")
        draw_best = DrawBest(new_userinfo, qqid, compact_layout=_compact, hide_logo=hide_logo, max_display=max_display)
        msg = MessageSegment.image(image_to_base64(await draw_best.draw()))
        log.debug(f"[b50_pipeline] 绘图完成")

    except (UserNotFoundError, UserNotExistsError, UserDisabledQueryError) as e:
        log.debug(f"[b50_pipeline] 用户相关错误: {e}")
        msg = str(e)
    except Exception as e:
        log.error(f"[b50_pipeline] 未知错误: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
        msg = f"未知错误：{type(e).__name__}\n请联系Bot管理员"
    return msg
