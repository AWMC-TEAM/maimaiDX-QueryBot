import asyncio
import json
import random
from collections import defaultdict
from copy import deepcopy
from typing import Tuple

import numpy as np
from loguru import logger as log
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from ..config import *
from .image import image_to_base64, music_picture
from .maimaidx_api_data import maiApi
from .maimaidx_error import *
from .maimaidx_model import *
from .tool import openfile, writefile


def cross(
    checker: Union[List[str], List[float]], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]], 
    diff: List[int]
) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    if isinstance(elem, List):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if __e in elem:
                diff_ret.append(_j)
                ret = True
    elif isinstance(elem, Tuple):
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem[0] <= __e <= elem[1]:
                diff_ret.append(_j)
                ret = True
    else:
        for _j in (range(len(checker)) if diff is Ellipsis else diff):
            if _j >= len(checker):
                continue
            __e = checker[_j]
            if elem == __e:
                diff_ret.append(_j)
                ret = True
    return ret, diff_ret


def in_or_equal(
    checker: Union[str, int], 
    elem: Optional[Union[str, float, List[str], List[float], Tuple[float, float]]]
) -> bool:
    if elem is Ellipsis:
        return True
    if isinstance(elem, List):
        return checker in elem
    elif isinstance(elem, Tuple):
        return elem[0] <= checker <= elem[1]
    else:
        return checker == elem


class MusicList(List[Music]):
    
    def by_id(self, music_id: Union[str, int]) -> Optional[Music]:
        for music in self:
            if music.id == str(music_id):
                return music
        return None

    def by_title(self, music_title: str) -> Optional[Music]:
        for music in self:
            if music.title == music_title:
                return music
        return None
    
    def by_plan(
        self, 
        level: str
    ) -> Dict[str, Union[PlanInfo, RaMusic, Dict[int, Union[PlanInfo, RaMusic]]]]:
        lv = defaultdict(dict)
        
        def create_ra_music(music: Music, index: int) -> RaMusic:
            return RaMusic(
                id=music.id, 
                ds=music.ds[index], 
                lv=str(index), 
                lvp=music.level[index], 
                type=music.type
            )
        
        for music in self:
            if level not in music.level:
                continue
            if int(music.id) >= 100000:
                continue
            if music.level.count(level) > 1: # 同曲有相同等级
                lv[music.id] = { 
                    index: create_ra_music(music, index)
                    for index, _lv in enumerate(music.level) 
                    if _lv == level 
                }
            else:
                index = music.level.index(level)
                lv[music.id] = create_ra_music(music, index)
        return dict(lv)
    
    def by_level_list(self) -> Dict[str, Dict[str, List[RaMusic]]]:
        
        def level_range(lv: str) -> range:
            if lv == '15':
                return range(1)
            if lv.endswith('+'):
                return range(9, 5, -1)
            return range(9, -1, -1) if int(lv) <= 5 else range(5, -1, -1)
        
        _level = {
            lv: {f"{lv.rstrip('+')}.{i}": [] for i in level_range(lv)} for lv in levelList
        }
        for music in self:
            if int(music.id) >= 100000:
                continue
            for index, ds in enumerate(music.ds):
                if ds < 7:
                    continue
                ra = RaMusic(
                    id=music.id,
                    ds=ds,
                    lv=str(index),
                    lvp=music.level[index],
                    type=music.type
                )
                _level[music.level[index]][str(ds)].append(ra)
        return _level
    
    def by_id_list(self, music_id_list: List[int]) -> Optional[List[Music]]:
        musicList = []
        for music in self:
            if int(music.id) in music_id_list:
                musicList.append(music)
        return musicList
    
    def random(self) -> Music:
        return random.choice(self)

    def filter(
        self,
        *,
        level: Optional[Union[str, List[str]]] = ...,
        ds: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        title_search: Optional[str] = ...,
        artist_search: Optional[str] = ...,
        charter_search: Optional[str] = ...,
        genre: Optional[Union[str, List[str]]] = ...,
        bpm: Optional[Union[float, List[float], Tuple[float, float]]] = ...,
        type: Optional[Union[str, List[str]]] = ...,  # 谱面类型 SD=标准谱面 DX=DX谱面
        diff: List[int] = ...,
        version: Union[str, List[str]] = ...
    ) -> 'MusicList':
        new_list = MusicList()
        for music in self:
            diff2 = diff
            music = deepcopy(music)
            ret, diff2 = cross(music.level, level, diff2)
            if not ret:
                continue
            ret, diff2 = cross(music.ds, ds, diff2)
            if not ret:
                continue
            ret, diff2 = search_charts(music.charts, charter_search, diff2)
            if not ret:
                continue
            if not in_or_equal(music.basic_info.genre, genre):
                continue
            if not in_or_equal(music.type, type):
                continue
            if not in_or_equal(music.basic_info.bpm, bpm):
                continue
            if not in_or_equal(music.basic_info.version, version):
                continue
            if title_search is not Ellipsis and title_search.lower() not in music.title.lower():
                continue
            if artist_search is not Ellipsis and artist_search.lower() not in music.basic_info.artist.lower():
                continue
            music.diff = diff2
            new_list.append(music)
        return new_list


def search_charts(checker: List[Chart], elem: str, diff: List[int]) -> Tuple[bool, List[int]]:
    ret = False
    diff_ret = []
    if not elem or elem is Ellipsis:
        return True, diff
    for _j in (range(len(checker)) if diff is Ellipsis else diff):
        if elem.lower() in checker[_j].charter.lower():
            diff_ret.append(_j)
            ret = True
    return ret, diff_ret


class AliasList(List[Alias]):

    def by_id(self, music_id: Union[str, int]) -> Optional[List[Alias]]:
        alias_music = []
        for music in self:
            if music.SongID == int(music_id):
                alias_music.append(music)
        return alias_music
    
    def by_alias(self, music_alias: str) -> Optional[List[Alias]]:
        alias_list = []
        for music in self:
            if music_alias in music.Alias:
                alias_list.append(music)
        return alias_list


dataerror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/music_data" 
    将内容保存为 "music_data.json" 存放在 "static" 目录下并重启bot
''').strip()
charterror = dedent(f'''
    未找到文件，请自行使用浏览器访问 "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
    将内容保存为 "music_chart.json" 存放在 "static" 目录下并重启bot
''').strip()
aliaserror = dedent('''
    本地暂存别名文件为空，请自行使用浏览器访问 "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias" 
    获取别名数据并保存在 "static/music_alias.json" 文件中并重启bot
''').strip()


async def get_music_list(force: bool = False) -> MusicList:
    """获取所有数据。force=True 时忽略本地缓存，强制从网络刷新。"""
    from .maimaidx_data_source import get_data_source
    from .tool import is_cache_fresh
    datasource = get_data_source()
    cache_ttl = 0 if force else getattr(maiconfig, 'maimaidx_music_cache_seconds', 3600)

    # MusicData
    try:
        if is_cache_fresh(music_file, cache_ttl):
            log.opt(colors=True).info('曲库数据使用<g>本地缓存</g>（未过期，跳过网络请求）')
            music_data = await openfile(music_file)
        else:
            try:
                music_data = await datasource.get_music_data()
                await writefile(music_file, music_data)
            except asyncio.exceptions.TimeoutError:
                log.error('maimaiDX曲库数据获取失败，请检查网络环境。已切换至本地暂存文件')
                music_data = await openfile(music_file)
    except FileNotFoundError:
        log.error(dataerror)
        raise FileNotFoundError

    # ChartStats
    try:
        if is_cache_fresh(chart_file, cache_ttl):
            chart_stats = await openfile(chart_file)
        else:
            try:
                chart_stats = await datasource.get_chart_stats()
                await writefile(chart_file, chart_stats)
            except asyncio.exceptions.TimeoutError:
                log.error('maimaiDX数据获取错误，请检查网络环境，已切换至本地暂存文件')
                chart_stats = await openfile(chart_file)
    except FileNotFoundError:
        log.error(charterror)
        raise FileNotFoundError

    total_list = MusicList()
    for music in music_data:
        if music['id'] in chart_stats['charts']:
            _stats = [
                _data if _data else None
                for _data in chart_stats['charts'][music['id']]
            ] if {} in chart_stats['charts'][music['id']] else \
            chart_stats['charts'][music['id']]
        else:
            _stats = None
        total_list.append(Music(stats=_stats, **music))

    return total_list


async def get_music_alias_list(force: bool = False) -> AliasList:
    """获取所有别名。force=True 时忽略本地缓存，强制从网络刷新。"""
    from .tool import is_cache_fresh
    cache_ttl = 0 if force else getattr(maiconfig, 'maimaidx_music_cache_seconds', 3600)

    if local_alias_file.exists():
        local_alias_data = await openfile(local_alias_file)
    else:
        local_alias_data = {}
    alias_data: List[Dict[str, Union[int, str, List[str]]]] = []

    # 本地别名缓存未过期则直接使用，跳过网络请求
    if is_cache_fresh(alias_file, cache_ttl):
        try:
            alias_data = await openfile(alias_file)
        except FileNotFoundError:
            alias_data = []

    if not alias_data:
        try:
            alias_data = await maiApi.get_alias()
            await writefile(alias_file, alias_data)
        except asyncio.exceptions.TimeoutError:
            log.error('获取别名超时，已切换至本地暂存文件')
            alias_data = await openfile(alias_file)
            if not alias_data:
                log.error(aliaserror)
                raise ValueError
        except ServerError as e:
            log.error(str(e) + '。已切换至本地暂存文件')
            alias_data = await openfile(alias_file)
        except UnknownError:
            log.error('获取所有曲目别名信息错误，请检查网络环境。已切换至本地暂存文件')
            alias_data = await openfile(alias_file)
            if not alias_data:
                log.error(aliaserror)
                raise ValueError

    total_alias_list = AliasList()
    for _a in filter(lambda x: mai.total_list.by_id(x['SongID']), alias_data):
        if (song_id := str(_a['SongID'])) in local_alias_data:
            _a['Alias'].extend(local_alias_data[song_id])
        total_alias_list.append(Alias.model_validate(_a))

    return total_alias_list


async def update_local_alias(id: str, alias_name: str) -> bool:
    try:
        if local_alias_file.exists():
            local_alias_data: Dict[str, List[str]] = await openfile(local_alias_file)
        else:
            local_alias_data: Dict[str, List[str]] = {}
        if id not in local_alias_data:
            local_alias_data[id] = []
        
        local_alias_data[id].append(alias_name.lower())
        mai.total_alias_list.by_id(id)[0].Alias.append(alias_name.lower())
        await writefile(local_alias_file, local_alias_data)
        return True
    except Exception as e:
        log.error(f'添加本地别名失败: {e}')
        return False


class MaiMusic:

    total_list: MusicList
    """曲目数据"""
    total_alias_list: AliasList
    """别名数据"""
    total_plate_id_list: Dict[str, List[int]]
    """牌子ID列表数据"""
    total_level_data: Dict[str, Dict[str, List[RaMusic]]]
    """等级列表数据"""
    hot_music_ids: List = []
    """游玩次数超过1w次的曲目数据"""
    guess_data: List[Music]
    """猜歌数据"""

    def __init__(self) -> None:
        """封装所有曲目信息以及猜歌数据，便于更新"""

    async def get_music(self, force: bool = False) -> None:
        """获取所有曲目数据。force=True 时强制从网络刷新，忽略本地缓存。"""
        self.total_list = await get_music_list(force=force)
        self.total_level_data = self.total_list.by_level_list()

    async def get_music_alias(self, force: bool = False) -> None:
        """获取所有曲目别名。force=True 时强制从网络刷新，忽略本地缓存。"""
        self.total_alias_list = await get_music_alias_list(force=force)
        
    async def get_plate_json(self) -> None:
        """获取所有牌子数据"""
        self.total_plate_id_list = await maiApi.get_plate_json()

    def guess(self):
        """初始化猜歌数据"""
        for music in self.total_list:
            if music.stats:
                count = 0
                for stats in music.stats:
                    if stats:
                        count += stats.cnt if stats.cnt else 0
                if count > 10000:
                    self.hot_music_ids.append(music.id)
        self.guess_data = list(filter(lambda x: x.id in self.hot_music_ids, self.total_list))


mai = MaiMusic()


class Guess:
    
    Group: Dict[int, Union[GuessDefaultData, GuessPicData]] = {}
    switch: GuessSwitch

    def __init__(self) -> None:
        """猜歌类"""
        if not guess_file.exists():
            self.switch = GuessSwitch()
        else:
            self.switch = GuessSwitch.model_validate(
                json.load(open(guess_file, 'r', encoding='utf-8'))
            )

    def _log_guess_start(self, mode: str, gid: int) -> None:
        data = self.Group[gid]
        music = data.music
        log.info(
            f'[Guess] 开始{mode}！本次答案： {music.title} 。ID: {music.id} 。'
        )

    def start(self, gid: int):
        """开始猜歌"""
        self.Group[gid] = self.guessData()
        self._log_guess_start('猜歌', gid)

    def startpic(self, gid: int):
        """开始猜曲绘"""
        self.Group[gid] = self.guesspicdata()
        self._log_guess_start('猜曲绘', gid)
        
    def calculate_frequency_weights(self, image: Image.Image) -> np.ndarray:
        """
        计算图像的频率权重，用于在图像中选择裁剪区域
        
        Params:
            `image`: PIL.Image.Image, 输入图像
        Returns:
            `np.ndarray` 频率权重矩阵
        """
        gray_image = np.array(image.convert('L'))
        freq = np.fft.fft2(gray_image)
        freq_shift = np.fft.fftshift(freq)
        magnitude = np.abs(freq_shift)
        normalized_magnitude = magnitude / magnitude.max()
        weights = normalized_magnitude ** 2
        return weights

    def select_crop_region(
        self, 
        weights: np.ndarray, 
        crop_width: int, 
        crop_height: int, 
        top_p: int
    ) -> Tuple[int, int]:
        h, w = weights.shape
        valid_regions = weights[:h - crop_height + 1, :w - crop_width + 1]
        flattened_weights = valid_regions.flatten()
        threshold = np.percentile(flattened_weights, top_p)
        valid_indices = np.where(flattened_weights >= threshold)[0]
        probabilities = flattened_weights[valid_indices]
        probabilities /= probabilities.sum()
        chosen_index = np.random.choice(valid_indices, p=probabilities)
        top_left_y = chosen_index // valid_regions.shape[1]
        top_left_x = chosen_index % valid_regions.shape[1]
        return top_left_x, top_left_y
    
    PIC_INTERFERENCE = [
        ('hue', '色相'),
        ('invert', '反转'),
        ('blur', '模糊'),
        ('desaturate', '低饱和'),
        ('saturate', '高饱和'),
        ('mirror', '水平镜像'),
        ('flip', '垂直翻转'),
        ('pixelate', '像素化'),
        ('noise', '噪点'),
        ('low_contrast', '低对比'),
        ('overexpose', '过曝'),
        ('underexpose', '欠曝'),
        ('rotate', '旋转'),
        ('emboss', '浮雕'),
        ('solarize', '曝光'),
        ('posterize', '色阶'),
    ]

    PIC_SOLO_INTERFERENCE = {'pixelate', 'emboss'}

    PIC_INTERFERENCE_COUNT = {
        1: (1, 2),
        2: (2, 3),
        3: (2, 4),
    }

    PIC_DIFFICULTY = {
        1: {'initial': 0.12, 'max': 0.50, 'expansions': 3},
        2: {'initial': 0.09, 'max': 0.42, 'expansions': 4},
        3: {'initial': 0.07, 'max': 0.35, 'expansions': 4},
    }

    def _pick_pic_interferences(self, difficulty: int) -> Tuple[List[str], List[str]]:
        count_range = self.PIC_INTERFERENCE_COUNT[difficulty]
        pick_count = random.randint(*count_range)
        selected = random.sample(self.PIC_INTERFERENCE, pick_count)
        keys = [key for key, _ in selected]
        label_map = dict(self.PIC_INTERFERENCE)

        solo_hits = [key for key in keys if key in self.PIC_SOLO_INTERFERENCE]
        if solo_hits:
            solo = random.choice(solo_hits) if len(solo_hits) > 1 else solo_hits[0]
            return [solo], [label_map[solo]]

        return keys, [label_map[key] for key in keys]

    def _get_pic_crop_box(
        self,
        cx: int,
        cy: int,
        scale: float,
        full_w: int,
        full_h: int,
    ) -> Tuple[int, int, int, int]:
        w2, h2 = int(full_w * scale), int(full_h * scale)
        x = max(0, min(cx - w2 // 2, full_w - w2))
        y = max(0, min(cy - h2 // 2, full_h - h2))
        return x, y, w2, h2

    def _apply_single_pic_interference(self, im: Image.Image, kind: str) -> Image.Image:
        if kind == 'hue':
            hsv = im.convert('HSV')
            h, s, v = hsv.split()
            s = s.point(lambda x: int(x * 0.12))
            h = h.point(lambda x: (x + 48) % 256)
            return Image.merge('HSV', (h, s, v)).convert('RGB')
        if kind == 'invert':
            return ImageOps.invert(im)
        if kind == 'blur':
            return im.filter(ImageFilter.GaussianBlur(radius=4))
        if kind == 'desaturate':
            return ImageEnhance.Color(im).enhance(0.0)
        if kind == 'saturate':
            return ImageEnhance.Color(im).enhance(2.5)
        if kind == 'mirror':
            return ImageOps.mirror(im)
        if kind == 'flip':
            return ImageOps.flip(im)
        if kind == 'pixelate':
            w, h = im.size
            small = im.resize((max(1, w // 12), max(1, h // 12)), Image.NEAREST)
            return small.resize((w, h), Image.NEAREST)
        if kind == 'noise':
            arr = np.array(im, dtype=np.int16)
            noise = np.random.randint(-40, 41, arr.shape, dtype=np.int16)
            arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
            return Image.fromarray(arr)
        if kind == 'low_contrast':
            return ImageEnhance.Contrast(im).enhance(0.35)
        if kind == 'overexpose':
            return ImageEnhance.Brightness(im).enhance(1.8)
        if kind == 'underexpose':
            return ImageEnhance.Brightness(im).enhance(0.35)
        if kind == 'rotate':
            return im.rotate(random.choice([-90, 90, 180]), expand=False)
        if kind == 'emboss':
            return im.filter(ImageFilter.EMBOSS)
        if kind == 'solarize':
            return ImageOps.solarize(im, threshold=128)
        if kind == 'posterize':
            return ImageOps.posterize(im, bits=3)
        return im

    def _apply_pic_interference(self, im: Image.Image, kinds: List[str]) -> Image.Image:
        im = im.convert('RGB')
        for kind in kinds:
            im = self._apply_single_pic_interference(im, kind)
        return im

    def _load_pic_source(self, data: GuessPicData) -> Image.Image:
        return Image.open(music_picture(data.music.id)).convert('RGB')

    def _render_pic_masked_region(
        self,
        data: GuessPicData,
        *,
        apply_interference: bool,
        output_size: int = 400,
        draw_border: bool = False,
    ) -> str:
        im = self._load_pic_source(data)
        x, y, w, h = self._get_pic_crop_box(
            data.crop_cx, data.crop_cy, data.current_scale, data.full_w, data.full_h
        )
        crop = im.crop((x, y, x + w, y + h))
        if apply_interference:
            crop = self._apply_pic_interference(crop, data.interferences)
        crop = crop.resize((output_size, output_size), Image.LANCZOS)
        if draw_border:
            draw = ImageDraw.Draw(crop)
            draw.rectangle(
                [1, 1, output_size - 2, output_size - 2],
                outline=(255, 255, 255),
                width=3,
            )
        return image_to_base64(crop)

    def render_pic_crop(self, data: GuessPicData, output_size: int = 400) -> str:
        return self._render_pic_masked_region(
            data, apply_interference=True, output_size=output_size
        )

    def render_pic_global(self, data: GuessPicData, max_width: int = 560) -> str:
        return self._render_pic_canvas_view(
            data, apply_interference=True, max_width=max_width
        )

    def render_pic_clear(self, data: GuessPicData, max_width: int = 560) -> str:
        return self._render_pic_canvas_view(
            data, apply_interference=False, max_width=max_width
        )

    def _render_pic_canvas_view(
        self,
        data: GuessPicData,
        *,
        apply_interference: bool,
        max_width: int = 560,
    ) -> str:
        im = self._load_pic_source(data)
        scale = max_width / im.width
        canvas_h = int(im.height * scale)
        canvas = Image.new('RGB', (max_width, canvas_h), (255, 255, 255))

        x, y, w, h = self._get_pic_crop_box(
            data.crop_cx, data.crop_cy, data.current_scale, data.full_w, data.full_h
        )
        region = im.crop((x, y, x + w, y + h))
        if apply_interference:
            region = self._apply_pic_interference(region, data.interferences)

        sx, sy = int(x * scale), int(y * scale)
        sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
        region = region.resize((sw, sh), Image.LANCZOS)
        canvas.paste(region, (sx, sy))
        return image_to_base64(canvas)

    def render_pic_reveal(self, data: GuessPicData, max_width: int = 600) -> str:
        im = self._load_pic_source(data)
        x, y, w, h = self._get_pic_crop_box(
            data.crop_cx, data.crop_cy, data.initial_scale, data.full_w, data.full_h
        )
        region = im.crop((x, y, x + w, y + h))
        bright = ImageEnhance.Brightness(region).enhance(1.35)
        bright = ImageEnhance.Contrast(bright).enhance(1.15)
        im.paste(bright, (x, y))

        scale = max_width / im.width
        full_h = int(im.height * scale)
        result = im.resize((max_width, full_h), Image.LANCZOS)

        sx, sy = int(x * scale), int(y * scale)
        sw, sh = int(w * scale), int(h * scale)
        draw = ImageDraw.Draw(result)
        for offset in (0, 2):
            draw.rectangle(
                [sx - offset, sy - offset, sx + sw + offset, sy + sh + offset],
                outline=(255, 220, 50) if offset == 0 else (255, 255, 255),
                width=3,
            )
        return image_to_base64(result)

    def expand_pic_crop(self, data: GuessPicData) -> None:
        step = (data.max_scale - data.initial_scale) / data.expansion_count
        data.current_scale = min(data.current_scale + step, data.max_scale)

    def pic(self, music: Music) -> Image.Image:
        """裁切曲绘"""
        im = Image.open(music_picture(music.id))
        w, h = im.size
        weights = self.calculate_frequency_weights(im)
        scale = random.uniform(0.15, 0.4)  # 裁剪尺寸范围 可在此修改
        w2, h2 = int(w * scale), int(h * scale)
        top_p = min(1.3 - np.power(scale, 0.4), 0.95) * 100
        x, y = self.select_crop_region(weights, w2, h2, top_p)
        im = im.crop((x, y, x + w2, y + h2))
        return im

    def guesspicdata(self) -> GuessPicData:
        """猜曲绘数据"""
        music = random.choice(mai.guess_data)
        im = Image.open(music_picture(music.id))
        w, h = im.size
        weights = self.calculate_frequency_weights(im)

        difficulty = random.randint(1, 3)
        cfg = self.PIC_DIFFICULTY[difficulty]
        initial_scale = cfg['initial']
        max_scale = cfg['max']
        expansion_count = cfg['expansions']

        temp_w, temp_h = int(w * initial_scale), int(h * initial_scale)
        top_p = min(1.3 - np.power(initial_scale, 0.4), 0.95) * 100
        x, y = self.select_crop_region(weights, temp_w, temp_h, top_p)
        cx, cy = x + temp_w // 2, y + temp_h // 2

        interferences, interference_labels = self._pick_pic_interferences(difficulty)
        answer = mai.total_alias_list.by_id(music.id)[0].Alias
        answer.append(music.id)
        return GuessPicData(
            music=music,
            img='',
            answer=answer,
            end=False,
            crop_cx=cx,
            crop_cy=cy,
            current_scale=initial_scale,
            initial_scale=initial_scale,
            max_scale=max_scale,
            full_w=w,
            full_h=h,
            interferences=interferences,
            interference_labels=interference_labels,
            difficulty=difficulty,
            expansion_count=expansion_count,
        )

    GUESS_SONG_EXCLUDED_GENRES = {'宴会場', '宴会场'}

    def _pick_guess_song_music(self) -> Music:
        pool = [
            m for m in mai.guess_data
            if m.basic_info.genre not in self.GUESS_SONG_EXCLUDED_GENRES
        ]
        return random.choice(pool or mai.guess_data)

    def guessData(self) -> GuessDefaultData:
        """猜歌数据"""
        music = self._pick_guess_song_music()
        guess_options = random.sample([
            f'的 Expert 难度是 {music.level[2]}',
            f'的 Master 难度是 {music.level[3]}',
            f'的分类是 {music.basic_info.genre}',
            f'的版本是 {music.basic_info.version}',
            f'的艺术家是 {music.basic_info.artist}',
            f'{"不" if music.type == "SD" else ""}是 DX 谱面',  # 谱面类型 SD=标准 DX=DX谱面
            f'{"没" if len(music.ds) == 4 else ""}有白谱',
            f'的 BPM 是 {music.basic_info.bpm}'
        ], 6)
        answer = mai.total_alias_list.by_id(music.id)[0].Alias
        answer.append(music.id)
        pic = self.pic(music)
        return GuessDefaultData(
            music=music, 
            img=image_to_base64(pic), 
            answer=answer, 
            end=False, 
            options=guess_options
        )

    def end(self, gid: int):
        """结束猜歌"""
        del self.Group[gid]

    async def on(self, gid: int) -> str:
        """开启猜歌"""
        if gid not in self.switch.enable:
            self.switch.enable.append(gid)
        if gid in self.switch.disable:
            self.switch.disable.remove(gid)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已开启'

    async def off(self, gid: int) -> str:
        """关闭猜歌"""
        if gid not in self.switch.disable:
            self.switch.disable.append(gid)
        if gid in self.switch.enable:
            self.switch.enable.remove(gid)
        if gid in self.Group:
            self.end(gid)
        await writefile(guess_file, self.switch.model_dump())
        return '群猜歌功能已关闭'


guess = Guess()


class GroupAlias:

    push: AliasesPush

    def __init__(self) -> None:
        """别名推送类"""
        if not group_alias_file.exists():
            self.push = AliasesPush()
        else:
            self.push = AliasesPush.model_validate(
                json.load(open(group_alias_file, 'r', encoding='utf-8'))
            )

    async def on(self, gid: int) -> str:
        """开启推送"""
        if gid not in self.push.enable:
            self.push.enable.append(gid)
        if gid in self.push.disable:
            self.push.disable.remove(gid)
        await writefile(group_alias_file, self.push.model_dump())
        return '群别名推送功能已开启'

    async def off(self, gid: int) -> str:
        """关闭推送"""
        if gid not in self.push.disable:
            self.push.disable.append(gid)
        if gid in self.push.enable:
            self.push.enable.remove(gid)
        await writefile(group_alias_file, self.push.model_dump())
        return '群别名推送功能已关闭'

    async def alias_global_change(self, switch: bool, group_list: List[int]):
        """修改全局开关"""
        if switch:
            self.push.disable.clear()
            self.push.enable.clear()
            self.push.enable.extend(group_list)
        else:
            self.push.enable.clear()
            self.push.disable.clear()
            self.push.disable.extend(group_list)
        await writefile(group_alias_file, self.push.model_dump())


alias = GroupAlias()


def _get_all_feature_names_from_model() -> list:
    """从 FeatureSwitch 模型获取所有功能名（便于新功能加入时自动纳入）。"""
    return list(FeatureSwitch.model_fields.keys())


class FeatureManager:
    """功能开关管理类"""
    
    switch: FeatureSwitch
    
    def __init__(self) -> None:
        """初始化功能开关；若有新功能（模型有而 JSON 无），按当前群总开关为其设置 disable。"""
        from ..config import group_feature_switch_file
        if not group_feature_switch_file.exists():
            self.switch = FeatureSwitch()
            return
        with open(group_feature_switch_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        known_keys = set(raw.keys())
        self.switch = FeatureSwitch.model_validate(raw)
        all_feature_names = _get_all_feature_names_from_model()
        new_features = [f for f in all_feature_names if f not in known_keys]
        if not new_features:
            return
        # 群总开关关闭 = 该群在所有已有功能的 disable 列表中 → 新功能对该群也禁用
        old_features = [f for f in all_feature_names if f in known_keys]
        if not old_features:
            return
        groups_all_disabled = None
        for f in old_features:
            s = set(getattr(self.switch, f).disable)
            if groups_all_disabled is None:
                groups_all_disabled = s
            else:
                groups_all_disabled &= s
        if groups_all_disabled is None:
            groups_all_disabled = set()
        for f in new_features:
            sw = getattr(self.switch, f)
            sw.disable = list(groups_all_disabled)
        try:
            with open(group_feature_switch_file, 'w', encoding='utf-8') as f:
                json.dump(self.switch.model_dump(), f, ensure_ascii=False, indent=4)
        except Exception:
            pass
    
    def _get_feature_switch(self, feature_name: str) -> Switch:
        """获取指定功能的开关"""
        if not hasattr(self.switch, feature_name):
            raise ValueError(f'未知功能: {feature_name}')
        return getattr(self.switch, feature_name)
    
    def is_enabled(self, gid: int, feature_name: str) -> bool:
        """检查功能是否在群组中启用。

        注意：本项目已将“禁用/启用”统一交由 `nonebot_plugin_plugin_manager` 处理，
        通过拦截触发词来实现按群禁用。为避免与插件管理的开关重复/冲突，这里默认始终启用。
        """
        return True
    
    async def enable(self, gid: int, feature_name: str) -> str:
        """在群组中启用功能"""
        from ..config import group_feature_switch_file
        switch = self._get_feature_switch(feature_name)
        if gid not in switch.enable:
            switch.enable.append(gid)
        if gid in switch.disable:
            switch.disable.remove(gid)
        await writefile(group_feature_switch_file, self.switch.model_dump())
        feature_names = self._feature_display_names()
        return f'群组 {feature_names.get(feature_name, feature_name)} 已启用'
    
    async def disable(self, gid: int, feature_name: str) -> str:
        """在群组中禁用功能"""
        from ..config import group_feature_switch_file
        switch = self._get_feature_switch(feature_name)
        if gid not in switch.disable:
            switch.disable.append(gid)
        if gid in switch.enable:
            switch.enable.remove(gid)
        await writefile(group_feature_switch_file, self.switch.model_dump())
        feature_names = self._feature_display_names()
        return f'群组 {feature_names.get(feature_name, feature_name)} 已禁用'
    
    def _feature_display_names(self) -> dict:
        """功能名 -> 展示名（新功能未配置时用 key 作为展示名）"""
        return {
            'query': '查询功能',
            'search': '搜索功能',
            'score': '成绩查询功能',
            'tag_analysis': '底力分析功能',
            'random': '随机推荐功能',
            'today': '今日运势功能',
            'ranking': '排名功能',
        }

    def get_status(self, gid: int) -> str:
        """获取群组所有功能状态（含模型中新功能，展示名未配置时用功能名）"""
        display_names = self._feature_display_names()
        status_list = []
        for feature_name in self._get_all_feature_names():
            display_name = display_names.get(feature_name, feature_name)
            enabled = self.is_enabled(gid, feature_name)
            status_list.append(f'{display_name}: {"启用" if enabled else "禁用"}')
        return '\n'.join(status_list)
    
    def _get_all_feature_names(self) -> list:
        """获取所有功能名称列表（与模型一致，新功能自动纳入）"""
        return _get_all_feature_names_from_model()
    
    async def enable_all(self, gid: int) -> str:
        """在群组中启用所有功能"""
        from ..config import group_feature_switch_file
        feature_names = self._get_all_feature_names()
        for feature_name in feature_names:
            switch = self._get_feature_switch(feature_name)
            if gid not in switch.enable:
                switch.enable.append(gid)
            if gid in switch.disable:
                switch.disable.remove(gid)
        await writefile(group_feature_switch_file, self.switch.model_dump())
        return '群组所有 maimai 功能已启用'
    
    async def disable_all(self, gid: int) -> str:
        """在群组中禁用所有功能"""
        from ..config import group_feature_switch_file
        feature_names = self._get_all_feature_names()
        for feature_name in feature_names:
            switch = self._get_feature_switch(feature_name)
            if gid not in switch.disable:
                switch.disable.append(gid)
            if gid in switch.enable:
                switch.enable.remove(gid)
        await writefile(group_feature_switch_file, self.switch.model_dump())
        return '群组所有 maimai 功能已禁用'


feature_manager = FeatureManager()
