import nonebot
from nonebot.plugin import PluginMetadata, require

from .command import *
nonebot.load_plugin("nonebot_plugin_maimaidx.command.mai_jacket")
from .libraries.maimaidx_music_info import get_music_tags, _get_dxrating_token

scheduler = require('nonebot_plugin_apscheduler')

from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name='nonebot-plugin-maimaidx',
    description='移植自 mai-bot 开源项目，基于 nonebot2 的街机音游 舞萌DX 的查询插件',
    usage='请使用 帮助maimaiDX 指令查看使用方法',
    type='application',
    config=Config,
    homepage='https://github.com/Yuri-YuzuChaN/nonebot-plugin-maimaidx',
    supported_adapters={'~onebot.v11'}
)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath('plugins').resolve())
)


@driver.on_startup
async def get_music():
    """
    bot启动时开始获取所有数据
    """
    _tag_token = _get_dxrating_token()
    log.opt(colors=True).info('谱面标签(combined-tags): ' + ('<g>已配置</g>' if _tag_token else '<y>未配置 token，详情图不显示标签</y>'))
    if maiconfig.maimaidxproberproxy:
        log.info('正在使用代理服务器访问查分器')
    if maiconfig.maimaidxaliasproxy:
        log.info('正在使用代理服务器访问别名服务器')
    maiApi.load_token_proxy()
    if maiconfig.maimaidxaliaspush:
        log.opt(colors=True).info('别名推送为「<g>开启</g>」状态')
        asyncio.ensure_future(ws_alias_server())
    else:
        log.opt(colors=True).info('别名推送为「<r>关闭</r>」状态')
    log.info('正在获取maimai所有曲目信息')
    await mai.get_music()
    log.info('正在获取maimai牌子数据')
    await mai.get_plate_json()
    log.info('正在获取maimai所有曲目别名信息')
    await mai.get_music_alias()
    mai.guess()
    log.success('maimai数据获取完成')
    if maiconfig.saveinmem:
        ScoreBaseImage._load_image()
        log.success('已将图片保存在内存中')
    
    if not ratingdir.exists() or not list(ratingdir.iterdir()):
        log.opt(colors=True).warning(
            '<y>注意！注意！</y>检测到定数表文件夹为空！'
            '可能导致「定数表」「完成表」指令无法使用，'
            '请及时私聊BOT使用指令「更新定数表」进行生成。'
        )
    plate_list = [name for name in list(plate_to_dx_version.keys())[1:]]
    platedir_list = [f.name.split('.')[0] for f in platedir.iterdir()] if platedir.exists() else []
    cn_list = [name for name in list(platecn.keys())]
    notin = set(plate_list) - set(platedir_list) - set(cn_list)
    if notin:
        anyname = '，'.join(notin)
        log.opt(colors=True).warning(
            f'<y>注意！注意！</y>未检测到牌子文件夹中的牌子：<y>{anyname}</y>，'
            '可能导致这些牌子的「完成表」指令无法使用，'
            '请及时私聊BOT使用指令「更新完成表」进行生成。'
        )
    log.opt(colors=True).success('<g>maimaiDX 插件初始化完成，等待客户端连接</g>')

scheduler.add_job(update_daily, 'cron', hour=4)