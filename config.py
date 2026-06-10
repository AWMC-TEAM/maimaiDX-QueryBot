"""
nonebot-plugin-maimaidx 插件配置与路径。

- Config：从 nonebot 配置/环境变量加载，含查分器 token、代理、自定义背景等。
- 下方全局变量：静态路径（static、maimaidir、字体等）、业务常量。多人协作时修改配置请同步注释。
"""
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger as log
from nonebot import get_driver, get_plugin_config
from pydantic import BaseModel

driver = get_driver()


class Config(BaseModel):
    """插件配置项；字段可通过 pyproject.toml [tool.nonebot.plugin] 或环境变量注入。"""
    
    maimaidxtoken: Optional[str] = None
    maimaidxpath: str
    maimaidxproberproxy: bool = False
    maimaidxaliasproxy: bool = False
    maimaidxaliaspush: bool = True
    saveinmem: Optional[bool] = True
    botName: str = list(driver.config.nickname)[0] if driver.config.nickname else 'maimai'
    dxrating_combined_tags_url: Optional[str] = 'https://derrakuma.dxrating.net/functions/v1/combined-tags'
    dxrating_token: Optional[str] = None
    dxrating_tags_json_path: Optional[str] = None
    # 谱面印象 API（舞萌 DX 谱面印象），默认 http://103.45.162.66:37913
    pmyx_api_base_url: Optional[str] = "http://103.45.162.66:37913"
    # SDGBT API 地址（SDGB 定数表），可通过环境变量 SDGBTECHAPI 设置
    sdgbtechapi: Optional[str] = None
    # SDGBT 机台参数（用于 PC 数拉取），可通过环境变量 SDGBT_CLIENT_ID / SDGBT_REGION_ID / SDGBT_PLACE_ID 设置
    sdgbt_client_id: Optional[str] = None
    sdgbt_region_id: int = 1
    sdgbt_place_id: int = 1403
    sdgbt_region_name: Optional[str] = '北京'
    sdgbt_place_name: Optional[str] = '默认机台'
    # ---------- 曲目数据源切换（可选） ----------
    # 留空/不设置 = 使用水鱼查分器 API（默认）
    # "dxdata" = 使用本地 dxdata.json 文件，无需网络
    maimaidx_data_source: Optional[str] = None
    # dxdata.json 文件路径（相对于项目根目录或绝对路径），默认 "dxdata.json"
    maimaidx_dxdata_path: Optional[str] = None
    # ---------- 落雪查分器（Lxns）配置 ----------
    lxns_dev_token: Optional[str] = None          # 开发者 Token（查曲库/按QQ查别人b50）
    lx_client_id: Optional[str] = None            # OAuth 应用 Client ID
    lx_client_secret: Optional[str] = None        # OAuth 应用 Client Secret
    # redirect_uri 留空 = 无回调模式（用户在落雪页面直接看到授权码）
    lx_redirect_uri: Optional[str] = None

    # ---------- 自定义背景（可选，便于多人协作时统一风格） ----------
    # 我有多菜：自定义背景图路径（相对 static 或绝对路径），未配置则使用常规 B50 背景 b50_bg.png
    maimaidx_how_weak_bg: Optional[str] = None
    # 底力分析：自定义背景图路径（相对 static 或绝对路径），未配置则使用常规 B50 背景 b50_bg.png
    maimaidx_tag_analysis_bg: Optional[str] = None
    # 我有多菜 / 我在群里有多菜：rating 数据缓存时长（秒），默认 15 分钟；可通过 .env 设置 MAIMAIDX_RATING_CACHE_SECONDS
    maimaidx_rating_cache_seconds: int = 900
    # 玩家成绩（B50 + 全量 dev）SQLite 缓存时长（秒），默认 15 分钟；0=关闭。MAIMAIDX_PLAYER_CACHE_SECONDS
    maimaidx_player_cache_seconds: int = 900
    # 是否允许用「数据存储」最近快照作兜底（默认 24h 内有效）。MAIMAIDX_PLAYER_CACHE_USE_STORAGE
    maimaidx_player_cache_use_storage: bool = True
    maimaidx_player_storage_fallback_seconds: int = 86400
    # 曲库/谱面/别名数据本地缓存时长（秒），默认 1 小时。
    # 启动时若本地缓存文件未过期则直接读取，跳过网络请求，加快启动速度。
    # 设为 0 则每次启动都从网络获取（旧行为）。可通过 .env 设置 MAIMAIDX_MUSIC_CACHE_SECONDS
    maimaidx_music_cache_seconds: int = 3600
    # 友人对战：同一 QQ 两次发起之间的冷却（秒），默认 3 分钟；设为 0 关闭冷却。可通过 .env 设置 MAIMAIDX_FRIEND_BATTLE_COOLDOWN_SECONDS
    maimaidx_friend_battle_cooldown_seconds: int = 180
    # 友人对战读取本地成绩缓存的最长有效期（秒），默认 7 天；重启后仍可用 SQLite/存档，减少重复拉取水鱼。MAIMAIDX_FRIEND_BATTLE_CACHE_SECONDS
    maimaidx_friend_battle_cache_seconds: int = 604800


maiconfig = get_plugin_config(Config)

BOT_QQ_GROUP = '1072033605'
UPSTREAM_REPO_URL = 'https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx'
FORK_TEAM_URL = 'https://github.com/AWMC-TEAM'
IMAGE_DESIGNER = 'Yuri-YuzuChaN & BlueDeer233'


def project_attribution_message() -> str:
    """项目地址 / 关于：完整致谢文案。"""
    return (
        f'本机器人基于 项目地址：{UPSTREAM_REPO_URL}\n\n'
        f'由 AWMC TEAM 进行深度重制，{FORK_TEAM_URL}。\n\n'
        f'QQ Group {BOT_QQ_GROUP} | AWMC BOT Made By AWMC TEAM'
    )


def footer_generated(bot_name: Optional[str] = None) -> str:
    """图片 / 文本回复底部短署名。"""
    name = bot_name or maiconfig.botName
    return f'QQ Group {BOT_QQ_GROUP} | {name} Bot Made By AWMC TEAM'


def footer_designed_generated(designer: str = IMAGE_DESIGNER, bot_name: Optional[str] = None) -> str:
    return f'Designed by {designer}. {footer_generated(bot_name)}'


def footer_designed_pipe_generated(designer: str, bot_name: Optional[str] = None) -> str:
    return f'Designed by {designer} | {footer_generated(bot_name)}'

# 谱面标签展示配置（后续多处复用，此处统一配置，勿在业务里写死）
TAG_DISPLAY_ORDER: List[str] = ['配置', '难度', '评价']
TAG_PILL_COLORS: Dict[str, Tuple[int, int, int]] = {
    '配置': (173, 216, 230),
    '难度': (200, 162, 220),
    '评价': (255, 182, 193),
}


vote_url: str = 'https://www.yuzuchan.moe/vote'

# ws
UUID = uuid.uuid1()


# echartsjs
SNAPSHOT_JS = (
    "echarts.getInstanceByDom(document.querySelector('div[_echarts_instance_]'))."
    "getDataURL({type: 'PNG', pixelRatio: 2, excludeComponents: ['toolbox']})"
)


# 文件路径
Root: Path = Path(__file__).parent
if maiconfig.maimaidxpath:
    static: Path = Path(maiconfig.maimaidxpath)
else:
    raise ValueError(
        '`nonebot-plugin-maimaidx` 插件未检测到静态文件夹 `static`，'
        '请根据 README 配置页说明进行下载静态文件'
    )
alias_file: Path = static / 'music_alias.json'                  # 别名暂存文件
local_alias_file: Path = static / 'local_music_alias.json'      # 本地别名文件
music_file: Path = static / 'music_data.json'                   # 曲目暂存文件
chart_file: Path = static / 'music_chart.json'                  # 谱面数据暂存文件
guess_file: Path = static / 'group_guess_switch.json'           # 猜歌开关群文件
group_alias_file: Path = static / 'group_alias_switch.json'     # 别名推送开关群文件
group_feature_switch_file: Path = static / 'group_feature_switch.json'  # 功能开关群文件
pie_html_file: Path = static / 'temp_pie.html'                  # 饼图html文件


# 静态资源路径
maimaidir: Path = static / 'mai' / 'pic'
coverdir: Path = static / 'mai' / 'cover'
ratingdir: Path = static / 'mai' / 'rating'
rating_table_dir: Path = static / 'mai' / 'rating_table'
platedir: Path = static / 'mai' / 'plate'
plate_versiondir: Path = static / 'mai' / 'plate_version'
plate_tabledir: Path = static / 'mai' / 'plate_table'


# 字体路径
fontdir: Path = static / 'font'
SIYUAN: Path = fontdir / 'ResourceHanRoundedCN-Bold.ttf'
SHANGGUMONO: Path = fontdir / 'ShangguMonoSC-Regular.otf'
TBFONT: Path = fontdir / 'Torus SemiBold.otf'


# 定义（全插件统一，以下四者互不影响）:
# - 谱面类型: SD = 标准谱面, DX = DX谱面（曲目/成绩的 type 字段）
# - B35 = 前 35 首槽位, B15 = 后 15 首槽位（游戏 B50 排版）
# - 查分器约定: charts.sd 表示 B35，charts.dx 表示 B15（与谱面类型 SD/DX 无关）

# 常用变量
SONGS_PER_PAGE: int = 25
scoreRank: List[str] = ['d', 'c', 'b', 'bb', 'bbb', 'a', 'aa', 'aaa', 's', 's+', 'ss', 'ss+', 'sss', 'sss+']
score_Rank: List[str] = ['d', 'c', 'b', 'bb', 'bbb', 'a', 'aa', 'aaa', 's', 'sp', 'ss', 'ssp', 'sss', 'sssp']
STATISTICS_KEYS: List[str] = [
    'clear', 's', 'sp', 'ss', 'ssp', 'sss', 'sssp',
    'sync', 'fc', 'fcp', 'ap', 'app', 'fs', 'fsp', 'fsd', 'fsdp',
]
COMBO_SP: List[str] = ['fc', 'fcp', 'ap', 'app']
SYNC_D_SP: List[str] = ['fs', 'fsp', 'fsd', 'fsdp']
score_Rank_l: Dict[str, str] = {
    'd': 'D', 
    'c': 'C', 
    'b': 'B', 
    'bb': 'BB', 
    'bbb': 'BBB', 
    'a': 'A', 
    'aa': 'AA', 
    'aaa': 'AAA', 
    's': 'S', 
    'sp': 'Sp', 
    'ss': 'SS', 
    'ssp': 'SSp', 
    'sss': 'SSS', 
    'sssp': 'SSSp'
}
comboRank: List[str] = ['fc', 'fc+', 'ap', 'ap+']
combo_rank: List[str] = ['fc', 'fcp', 'ap', 'app']
syncRank: List[str] = ['fs', 'fs+', 'fdx', 'fdx+']
sync_rank: List[str] = ['fs', 'fsp', 'fsd', 'fsdp']
sync_rank_p: List[str] = ['fs', 'fsp', 'fdx', 'fdxp']
diffs: List[str] = ['Basic', 'Advanced', 'Expert', 'Master', 'Re:Master']
levelList: List[str] = [
    '1', 
    '2', 
    '3', 
    '4', 
    '5', 
    '6', 
    '7', 
    '7+', 
    '8', 
    '8+', 
    '9', 
    '9+', 
    '10', 
    '10+', 
    '11', 
    '11+', 
    '12', 
    '12+', 
    '13', 
    '13+', 
    '14', 
    '14+', 
    '15'
]
achievementList: List[float] = [50.0, 60.0, 70.0, 75.0, 80.0, 90.0, 94.0, 97.0, 98.0, 99.0, 99.5, 100.0, 100.5]
BaseRaSpp: List[float] = [7.0, 8.0, 9.6, 11.2, 12.0, 13.6, 15.2, 16.8, 20.0, 20.3, 20.8, 21.1, 21.6, 22.4]
fcl: Dict[str, str] = {'fc': 'FC', 'fcp': 'FCp', 'ap': 'AP', 'app': 'APp'}
fsl: Dict[str, str] = {'fs': 'FS', 'fsp': 'FSp', 'fsd': 'FSD', 'fdx': 'FSD', 'fsdp': 'FSDp', 'fdxp': 'FSDp', 'sync': 'Sync'}
plate_to_sd_version: Dict[str, str] = {
    '初': 'maimai',
    '真': 'maimai PLUS',
    '超': 'maimai GreeN',
    '檄': 'maimai GreeN PLUS',
    '橙': 'maimai ORANGE',
    '暁': 'maimai ORANGE PLUS',
    '晓': 'maimai ORANGE PLUS',
    '桃': 'maimai PiNK',
    '櫻': 'maimai PiNK PLUS',
    '樱': 'maimai PiNK PLUS',
    '紫': 'maimai MURASAKi',
    '菫': 'maimai MURASAKi PLUS',
    '堇': 'maimai MURASAKi PLUS',
    '白': 'maimai MiLK',
    '雪': 'MiLK PLUS',
    '輝': 'maimai FiNALE',
    '辉': 'maimai FiNALE'
}
plate_to_dx_version: Dict[str, str] = {
    **plate_to_sd_version,
    '熊': 'maimai でらっくす',
    '華': 'maimai でらっくす PLUS',
    '华': 'maimai でらっくす PLUS',
    '爽': 'maimai でらっくす Splash',
    '煌': 'maimai でらっくす Splash PLUS',
    '宙': 'maimai でらっくす UNiVERSE',
    '星': 'maimai でらっくす UNiVERSE PLUS',
    '祭': 'maimai でらっくす FESTiVAL',
    '祝': 'maimai でらっくす FESTiVAL PLUS',
    '双': 'maimai でらっくす BUDDiES',
    '宴': 'maimai でらっくす BUDDiES PLUS',
    '镜': 'maimai でらっくす PRiSM',
    '彩': 'maimai でらっくす PRiSM PLUS'
}
version_map = {
    '真': ([plate_to_dx_version['真'], plate_to_dx_version['初']], '真'),
    '超': ([plate_to_sd_version['超']], '超'),
    '檄': ([plate_to_sd_version['檄']], '檄'),
    '橙': ([plate_to_sd_version['橙']], '橙'),
    '暁': ([plate_to_sd_version['暁']], '暁'),
    '桃': ([plate_to_sd_version['桃']], '桃'),
    '櫻': ([plate_to_sd_version['櫻']], '櫻'),
    '紫': ([plate_to_sd_version['紫']], '紫'),
    '菫': ([plate_to_sd_version['菫']], '菫'),
    '白': ([plate_to_sd_version['白']], '白'),
    '雪': ([plate_to_sd_version['雪']], '雪'),
    '輝': ([plate_to_sd_version['輝']], '輝'),
    '霸': (list(set(plate_to_sd_version.values())), '舞'),
    '舞': (list(set(plate_to_sd_version.values())), '舞'),
    '熊': ([plate_to_dx_version['熊']], '熊&华'),
    '华': ([plate_to_dx_version['熊']], '熊&华'),
    '華': ([plate_to_dx_version['熊']], '熊&华'),
    '爽': ([plate_to_dx_version['爽']], '爽&煌'),
    '煌': ([plate_to_dx_version['爽']], '爽&煌'),
    '宙': ([plate_to_dx_version['宙']], '宙&星'),
    '星': ([plate_to_dx_version['宙']], '宙&星'),
    '祭': ([plate_to_dx_version['祭']], '祭&祝'),
    '祝': ([plate_to_dx_version['祭']], '祭&祝'),
    '双': ([plate_to_dx_version['双']], '双&宴'),
    '宴': ([plate_to_dx_version['双']], '双&宴'),
    '镜': ([plate_to_dx_version['镜']], '镜&彩'),
    '彩': ([plate_to_dx_version['镜']], '镜&彩')
}


def resolve_plate_id_list(
    plate_data: Optional[Dict[str, List[int]]],
    plate_key: str,
) -> Optional[List[int]]:
    """
    解析牌子曲目 ID 列表。优先用组合键（如 镜&彩）；别名库若仅有分键（镜、彩）则合并去重。
    """
    if not plate_data:
        return None
    direct = plate_data.get(plate_key)
    if direct:
        return list(direct)
    if '&' not in plate_key:
        return None
    merged: List[int] = []
    seen: set[int] = set()
    for part in plate_key.split('&'):
        part = part.strip()
        if not part:
            continue
        for sid in plate_data.get(part) or []:
            i = int(sid)
            if i in seen:
                continue
            seen.add(i)
            merged.append(i)
    return merged or None


platecn = {
    '晓': '暁',
    '樱': '櫻',
    '堇': '菫',
    '辉': '輝',
    '华': '華'
}
category: Dict[str, str] = {
    '流行&动漫': 'anime',
    '舞萌': 'maimai',
    'niconico & VOCALOID': 'niconico',
    '东方Project': 'touhou',
    '其他游戏': 'game',
    '音击&中二节奏': 'ongeki',
    'POPSアニメ': 'anime',
    'maimai': 'maimai',
    'niconicoボーカロイド': 'niconico',
    '東方Project': 'touhou',
    'ゲームバラエティ': 'game',
    'オンゲキCHUNITHM': 'ongeki',
    '宴会場': '宴会场'
}