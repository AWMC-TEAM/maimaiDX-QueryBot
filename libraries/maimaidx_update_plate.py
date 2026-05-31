import time
from io import BytesIO
from typing import Dict, List, Optional

import aiofiles
from PIL import Image, ImageDraw

from ..config import (
    levelList,
    log,
    maiconfig,
    footer_generated,
    plate_tabledir,
    plate_to_dx_version,
    platecn,
    rating_table_dir,
    TBFONT,
    version_map,
)
from .image import DrawText, generate_frosted_card, music_picture
from .maimaidx_music import Music, mai
from .maimaidx_table_image import TableImageAssets
from .maimaidx_theme import pic


class UpdateTable:
    def __init__(self):
        TableImageAssets.ensure_loaded()
        self.level_list = levelList[6:]
        self.version_list = list(_ for _ in plate_to_dx_version.keys())[1:]

    async def _save_image(self, im: Image.Image, path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        by = BytesIO()
        im.save(by, 'PNG')
        async with aiofiles.open(path, 'wb') as f:
            await f.write(by.getbuffer())

    def _get_level_dict(self) -> Dict[str, List[Music]]:
        return {lv: [] for lv in reversed(levelList)}

    def _get_song_list(self, version_name: str) -> List[Music]:
        song_id_list = mai.total_plate_id_list[version_name]
        return mai.total_list.by_id_list(song_id_list)

    async def update_level_15_rating_table(self) -> None:
        draw_time = time.time()
        assets = TableImageAssets
        lv15 = mai.total_level_data['15']['15.0']
        count = len(lv15)
        lines = (count // 3) + (1 if count % 3 else 0)
        height = 650 + lines * 450

        im = assets.generate_bg(height, 360)
        dr = ImageDraw.Draw(im)
        fot = DrawText(dr, TBFONT)
        fot.draw(495, 160, 70, 'Level.', assets.font_color, 'ld', 8, (255, 255, 255, 255))
        fot.draw(750, 160, 100, '15', assets.font_color, 'ld', 8, (255, 255, 255, 255))
        fot.draw(700, height - 75, 30, 'Designed by Yuri-YuzuChaN & BlueDeer233.', assets.font_color, 'mm')
        fot.draw(700, height - 30, 30, footer_generated(), assets.font_color, 'mm')

        im.alpha_composite(assets.table_complete_bg, (251, 190))
        unknown_chart = Image.open(music_picture(0)).convert('RGBA').resize((330, 330))
        for i in range(lines * 3):
            row, col = divmod(i, 3)
            x = 100 + col * 425
            y = 500 + row * 450
            im.alpha_composite(assets.chart_white_bg, (x, y))
            if i < count:
                ra = lv15[i]
                chart = Image.open(music_picture(ra.id)).convert('RGBA')
                im.alpha_composite(chart.resize((330, 330)), (x + 10, y + 10))
                im.alpha_composite(assets.table_type_bg[ra.type], (x + 200, y + 345))
                full = mai.total_list.by_id(ra.id)
                if full:
                    ver_img = pic(f'{full.basic_info.version}.png')
                    if ver_img.exists():
                        im.alpha_composite(Image.open(ver_img).resize((332, 160)), (x + 9, y - 80))
                fot.draw(x + 100, y + 370, 35, ra.id, assets.font_color, 'mm')
            else:
                im.alpha_composite(unknown_chart, (x + 10, y + 10))
                im.alpha_composite(assets.table_type_bg['DX'], (x + 200, y + 345))
                fot.draw(x + 100, y + 370, 35, '????', assets.font_color, 'mm')
                fot.draw(x + 175, y + 280, 30, 'UNKNOWN', assets.font_color, 'mm', 8, (255, 255, 255, 255))

        await self._save_image(im, rating_table_dir / '15.png')
        log.info(f'lv.15 定数表更新完成，耗时：{time.time() - draw_time:.3f}s')

    async def update_rating_table(self) -> str:
        assets = TableImageAssets
        rating_table_dir.mkdir(parents=True, exist_ok=True)
        all_time = 0.0
        for lv in self.level_list[:-1]:
            single_time = time.time()
            lvlist = mai.total_level_data[lv]
            grid_step = 85
            start_x = 140
            current_y = 450
            for songs in lvlist.values():
                if not songs:
                    continue
                rows = (len(songs) - 1) // 14 + 1
                current_y += rows * grid_step + 30
            height = current_y + 230

            _im = assets.generate_bg(height, 360)
            im = generate_frosted_card(_im, (50, 404, 1350, current_y))
            dr = ImageDraw.Draw(im)
            tb = DrawText(dr, TBFONT)
            fot = DrawText(dr, TBFONT)
            fot.draw(700, height - 75, 30, 'Designed by Yuri-YuzuChaN & BlueDeer233.', assets.font_color, 'mm')
            fot.draw(700, height - 30, 30, footer_generated(), assets.font_color, 'mm')

            start_y = 450
            for ds, songs in lvlist.items():
                if not songs:
                    continue
                sub_ds = ds.split('.')[-1]
                fot.draw(70, start_y + 35, 40, f'.{sub_ds}', assets.font_color, 'lm', 4, (255, 255, 255, 255))
                max_row = 0
                for num, music in enumerate(songs):
                    row, col = divmod(num, 14)
                    max_row = max(max_row, row)
                    x = start_x + col * grid_step
                    y = start_y + row * grid_step
                    cover = Image.open(music_picture(music.id)).resize((75, 75))
                    im.alpha_composite(cover, (x, y))
                    lv_idx = int(music.lv)
                    im.alpha_composite(assets.table_diff_bg[lv_idx], (x - 5, y - 5))
                    tb.draw(
                        x + 56, y + 4, 13, music.id,
                        assets.diff_text_color[lv_idx], 'mm',
                    )
                start_y += (max_row + 1) * grid_step + 30

            await self._save_image(im, rating_table_dir / f'{lv}.png')
            elapsed = round(time.time() - single_time, 3)
            all_time += elapsed
            log.info(f'lv.{lv} 定数表更新完成，耗时：{elapsed}s')
        return f'定数表更新完成，耗时：{all_time}s'

    def _draw_plate(
        self,
        level_dict: Dict[str, List[Music]],
        remaster_id_list: Optional[List[int]] = None,
        remaster_songs: Optional[List[Music]] = None,
        pages: Optional[int] = None,
    ) -> Image.Image:
        assets = TableImageAssets
        grid_step = 96
        start_x = 180
        current_y = 490
        for songs in level_dict.values():
            if not songs:
                continue
            rows = (len(songs) - 1) // 12 + 1
            current_y += rows * grid_step + 30
        height = current_y + 180

        _im = assets.generate_bg(height, 400)
        im = generate_frosted_card(_im, (50, 444, 1350, current_y))
        dr = ImageDraw.Draw(im)
        tb = DrawText(dr, TBFONT)
        fot = DrawText(dr, TBFONT)
        if pages is not None:
            fot.draw(700, height - 140, 40, f'Pages {pages + 1}/2', assets.font_color, 'mm')
        fot.draw(700, height - 75, 30, 'Designed by Yuri-YuzuChaN & BlueDeer233.', assets.font_color, 'mm')
        fot.draw(700, height - 30, 30, footer_generated(), assets.font_color, 'mm')

        remaster_set = set(remaster_id_list or [])
        start_y = 490
        for ds, songs in level_dict.items():
            if not songs:
                continue

            def sort_key(m: Music) -> float:
                if remaster_id_list is not None and int(m.id) in remaster_set:
                    return m.ds[4] if len(m.ds) > 4 else m.ds[3]
                return m.ds[3]

            songs.sort(key=sort_key, reverse=True)
            fot.draw(72, start_y + 40, 40, ds, assets.font_color, 'lm', 4, (255, 255, 255, 255))
            max_row = 0
            for num, music in enumerate(songs):
                row, col = divmod(num, 12)
                max_row = max(max_row, row)
                x = start_x + col * grid_step
                y = start_y + row * grid_step
                cover = Image.open(music_picture(music.id)).resize((80, 80))
                im.alpha_composite(cover, (x, y))
                is_remaster = remaster_id_list is not None and int(music.id) in remaster_set
                id_bg = assets.table_wu_rms_id_bg if is_remaster else assets.table_id_bg
                im.alpha_composite(id_bg, (x - 5, y - 5))
                id_color = (138, 0, 226, 255) if is_remaster else (255, 255, 255, 255)
                tb.draw(x + 56, y + 4, 16, music.id, id_color, 'mm')
            start_y += (max_row + 1) * grid_step + 30
        return im

    async def update_wu_plate_table(self) -> str:
        single_time = time.time()
        song_list = self._get_song_list('舞')
        remaster_id_list = mai.total_plate_id_list['舞ReMASTER']
        remaster_songs = self._get_song_list('舞ReMASTER')
        all_level_dict = self._get_level_dict()
        for s in song_list:
            if int(s.id) in remaster_id_list and len(s.level) > 4:
                all_level_dict[s.level[4]].append(s)
            else:
                all_level_dict[s.level[3]].append(s)
        keys = list(all_level_dict.keys())
        idx = keys.index('13')
        for pages, level_dict in enumerate([
            {k: all_level_dict[k] for k in keys[:idx]},
            {k: all_level_dict[k] for k in keys[idx:]},
        ]):
            im = self._draw_plate(level_dict, remaster_id_list, remaster_songs, pages)
            await self._save_image(im, plate_tabledir / f'舞-{pages + 1}.png')
        log.info(f'舞/霸者完成表更新完成，耗时：{time.time() - single_time:.3f}s')
        return '舞/霸者完成表更新完成'

    async def update_plate_table(self) -> str:
        plate_tabledir.mkdir(parents=True, exist_ok=True)
        all_time = 0.0
        for name in self.version_list:
            single_time = time.time()
            if name in platecn:
                name = platecn[name]
            _, version_name = version_map.get(name, ([plate_to_dx_version.get(name)], name))
            song_list = self._get_song_list(version_name)
            level_dict = self._get_level_dict()
            for s in song_list:
                level_dict[s.level[3]].append(s)
            im = self._draw_plate(level_dict)
            await self._save_image(im, plate_tabledir / f'{name}.png')
            elapsed = round(time.time() - single_time, 3)
            all_time += elapsed
            log.info(f'{name}代牌子更新完成，耗时：{elapsed}s')
        wu_result = await self.update_wu_plate_table()
        log.info(wu_result)
        return f'完成表更新完成，耗时：{all_time}s'


async def update_rating_table() -> str:
    try:
        updater = UpdateTable()
        result = await updater.update_rating_table()
        await updater.update_level_15_rating_table()
        return result
    except Exception as e:
        log.error(__import__('traceback').format_exc())
        return f'定数表更新失败，Error: {e}'


async def update_plate_table() -> str:
    try:
        updater = UpdateTable()
        return await updater.update_plate_table()
    except Exception as e:
        log.error(__import__('traceback').format_exc())
        return f'完成表更新失败，Error: {e}'
