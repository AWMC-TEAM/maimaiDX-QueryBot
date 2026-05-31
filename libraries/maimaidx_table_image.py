"""定数表 / 完成表静态资源与路径（对齐 beta 分支）"""

from pathlib import Path
from typing import ClassVar, Dict, List, Optional

from PIL import Image

from ..config import maiconfig, maimaidir, plate_versiondir, rating_table_dir, ratingdir
from .maimaidx_theme import Theme, pic, resolve_theme_path


class RatingGridConfig:
    start_x = 140
    start_y = 450
    gap = 85
    row_count = 14
    stats_first_line_x = 534
    stats_first_line_y = 238
    stats_second_line_x = 292
    stats_second_line_y = 323


class PlateGridConfig:
    start_x = 180
    start_y = 490
    gap = 96
    row_count = 12


class TableImageAssets:
    font_color = (114, 188, 254, 255)
    default_text_color = (124, 129, 255, 255)
    diff_text_color = [
        (255, 255, 255, 255),
        (255, 255, 255, 255),
        (255, 255, 255, 255),
        (255, 255, 255, 255),
        (138, 0, 226, 255),
    ]
    id_text_color = [
        (129, 217, 85, 255),
        (245, 189, 21, 255),
        (255, 129, 141, 255),
        (159, 81, 220, 255),
        (138, 0, 226, 255),
    ]

    _loaded: ClassVar[bool] = False
    _rank_cache: ClassVar[Dict[str, Image.Image]] = {}
    _fc_cache: ClassVar[Dict[str, Image.Image]] = {}

    table_type_bg: ClassVar[Dict[str, Image.Image]] = {}
    table_dx_small_bg: ClassVar[Optional[Image.Image]] = None
    table_complete_bg: ClassVar[Optional[Image.Image]] = None
    rating_unfinished_bg: ClassVar[Optional[Image.Image]] = None
    rating_complete_bg: ClassVar[Optional[Image.Image]] = None
    plate_finished_bg: ClassVar[List[Image.Image]] = []
    plate_complete_bg: ClassVar[Optional[Image.Image]] = None
    plate_progress_big: ClassVar[Optional[Image.Image]] = None
    plate_progress_bg: ClassVar[Optional[Image.Image]] = None
    plate_progress_wu_bg: ClassVar[Optional[Image.Image]] = None
    plate_progress_small: ClassVar[Optional[Image.Image]] = None
    plate_progress_small_wu: ClassVar[Optional[Image.Image]] = None
    table_id_bg: ClassVar[Optional[Image.Image]] = None
    table_wu_rms_id_bg: ClassVar[Optional[Image.Image]] = None
    table_diff_bg: ClassVar[List[Image.Image]] = []
    separator_bg: ClassVar[Optional[Image.Image]] = None
    chart_white_bg: ClassVar[Optional[Image.Image]] = None
    aurora_bg: ClassVar[Optional[Image.Image]] = None
    shines_bg: ClassVar[Optional[Image.Image]] = None
    pattern_bg: ClassVar[Optional[Image.Image]] = None
    rainbow_bg: ClassVar[Optional[Image.Image]] = None
    rainbow_bottom_bg: ClassVar[Optional[Image.Image]] = None

    @classmethod
    def _open(cls, path: Path) -> Image.Image:
        with Image.open(path) as image:
            return image.convert('RGBA')

    @classmethod
    def load(cls) -> None:
        if cls._loaded and maiconfig.saveinmem:
            return
        cls.table_type_bg = {
            'SD': cls._open(pic('SD.png')),
            'DX': cls._open(pic('DX.png')),
        }
        cls.table_dx_small_bg = cls.table_type_bg['DX'].resize((44, 16))
        cls.table_complete_bg = cls._open(pic('complete.png'))
        cls.rating_unfinished_bg = cls._open(pic('unfinished_1.png'))
        cls.rating_complete_bg = cls._open(pic('complete_1.png'))
        cls.plate_finished_bg = [cls._open(pic(f't_{i}.png')) for i in range(5)]
        cls.plate_complete_bg = cls._open(pic('complete_2.png'))
        cls.plate_progress_big = cls._open(pic('progress_big.png'))
        cls.plate_progress_bg = cls._open(pic('plate_progress.png'))
        cls.plate_progress_wu_bg = cls._open(pic('plate_progress_wu.png'))
        cls.plate_progress_small = cls._open(pic('progress_small.png'))
        cls.plate_progress_small_wu = cls._open(pic('progress_small_wu.png'))
        cls.table_id_bg = cls._open(pic('border_table_base.png'))
        cls.table_wu_rms_id_bg = cls._open(pic('border_table_remaster.png'))
        cls.table_diff_bg = [
            cls._open(pic('border_basic.png')),
            cls._open(pic('border_advanced.png')),
            cls._open(pic('border_expert.png')),
            cls._open(pic('border_master.png')),
            cls._open(pic('border_remaster.png')),
        ]
        cls.separator_bg = cls._open(pic('separator.png'))
        cls.chart_white_bg = cls._open(pic('chart_white.png'))
        cls.aurora_bg = cls._open(pic('aurora.png')).resize((1400, 220))
        cls.shines_bg = cls._open(pic('bg_shines.png'))
        cls.pattern_bg = cls._open(pic('pattern.png'))
        cls.rainbow_bg = cls._open(pic('rainbow.png'))
        cls.rainbow_bottom_bg = cls._open(pic('rainbow_bottom.png')).resize((1200, 200))
        cls._loaded = True

    @classmethod
    def ensure_loaded(cls) -> None:
        cls.load()

    @classmethod
    def generate_bg(cls, height: int, separator_height: int) -> Image.Image:
        cls.ensure_loaded()
        from .image import tricolor_gradient_prism_plus

        im = tricolor_gradient_prism_plus(1400, height)
        im.alpha_composite(cls.aurora_bg, (0, 0))
        im.alpha_composite(cls.shines_bg, (11, 6))
        im.alpha_composite(cls.rainbow_bg, (318, height - 545))
        im.alpha_composite(cls.rainbow_bottom_bg, (122, height - 305))
        for h in range((height // 358) + 1):
            im.alpha_composite(cls.pattern_bg, (0, (358 + 7) * h))
        im.alpha_composite(cls.separator_bg, (100, separator_height))
        return im

    @classmethod
    def get_rank_icon(cls, rate: str, theme: Optional[str] = None) -> Optional[Image.Image]:
        if theme is None:
            theme = Theme.get_default().value
        if rate not in cls._rank_cache:
            path = resolve_theme_path(maimaidir, theme, f'UI_TTR_Rank_{rate}.png')
            if path.exists():
                cls._rank_cache[rate] = cls._open(path)
        return cls._rank_cache.get(rate)

    @classmethod
    def get_fc_icon(cls, fc: str) -> Optional[Image.Image]:
        from ..config import fcl

        if fc not in cls._fc_cache:
            path = pic(f'UI_MSS_MBase_Icon_{fcl[fc]}.png')
            if path.exists():
                cls._fc_cache[fc] = cls._open(path).resize((50, 50))
        return cls._fc_cache.get(fc)


def rating_table_path(rating: str) -> Path:
    path = rating_table_dir / f'{rating}.png'
    if path.exists():
        return path
    return ratingdir / f'{rating}.png'


def plate_version_path(plate_name: str) -> Path:
    """牌子图：static/mai/plate_version/{name}.png"""
    return plate_versiondir / f'{plate_name}.png'


def open_plate_image(plate_name: Optional[str], fallback_path: Path) -> Image.Image:
    """加载牌子图，优先 plate_version 目录"""
    if plate_name:
        path = plate_version_path(plate_name)
        if path.exists():
            return Image.open(path).convert('RGBA')
    return Image.open(fallback_path).convert('RGBA')
