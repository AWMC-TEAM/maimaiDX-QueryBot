import json
from pathlib import Path
from typing import Dict, List

from ..config import maiconfig


DIFFICULTY_ORDER = ["basic", "advanced", "expert", "master", "remaster"]


class MusicDataSource:
    """曲目数据源抽象基类，支持在 API 源和本地 dxdata.json 之间切换。"""

    async def get_music_data(self) -> list:
        """返回 diving-fish 格式的 music_data 列表。"""
        raise NotImplementedError

    async def get_chart_stats(self) -> dict:
        """返回 chart_stats 字典，若无统计则返回 {"charts": {}}。"""
        raise NotImplementedError


def _get_data_source() -> MusicDataSource:
    """根据配置创建数据源实例。"""
    source_type = maiconfig.maimaidx_data_source or ""
    if source_type == "dxdata":
        path = maiconfig.maimaidx_dxdata_path or "dxdata.json"
        return DxDataSource(path)
    from .maimaidx_api_data import maiApi
    return DivingFishSource(maiApi)


def get_data_source() -> MusicDataSource:
    """获取当前配置的数据源实例。"""
    return _get_data_source()


class DivingFishSource(MusicDataSource):
    """水鱼查分器 API 数据源（原版）。"""

    def __init__(self, api):
        from .maimaidx_api_data import MaimaiAPI
        self.api: "MaimaiAPI" = api

    async def get_music_data(self) -> list:
        return await self.api.music_data()

    async def get_chart_stats(self) -> dict:
        return await self.api.chart_stats()


class DxDataSource(MusicDataSource):
    """本地 dxdata.json 数据源。"""

    def __init__(self, filepath: str | Path):
        self.filepath = Path(filepath)
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            self._data = json.loads(self.filepath.read_text(encoding="utf-8"))
        return self._data

    async def get_music_data(self) -> list:
        data = self._load()
        result: list = []
        for song in data.get("songs", []):
            record = self._convert_song(song)
            if record:
                result.append(record)
        return result

    async def get_chart_stats(self) -> dict:
        return {"charts": {}}

    def _convert_song(self, song: dict) -> dict | None:
        sheets: list = song.get("sheets", [])
        if not sheets:
            return None

        dx_sheets: dict[str, dict] = {}
        std_sheets: dict[str, dict] = {}
        for sh in sheets:
            t = sh.get("type", "")
            diff = sh.get("difficulty", "")
            if t in ("dx",):
                dx_sheets[diff] = sh
            elif t in ("sd", "std"):
                std_sheets[diff] = sh

        has_dx = bool(dx_sheets)
        target = dx_sheets if has_dx else std_sheets
        song_type = "DX" if has_dx else "SD"

        ds_list: list = []
        level_list: list = []
        cids_list: list = []
        charts_list: list = []
        main_sheet = None

        for diff in DIFFICULTY_ORDER:
            sh = target.get(diff)
            if sh:
                ds_list.append(sh.get("internalLevelValue", 0.0))
                level_list.append(str(sh.get("level", "0")))
                cids_list.append(sh.get("internalId", 0))
                nc = sh.get("noteCounts", {})
                if "touch" in nc:
                    from .maimaidx_model import Notes2
                    notes = Notes2(
                        nc.get("tap", 0),
                        nc.get("hold", 0),
                        nc.get("slide", 0),
                        nc.get("touch", 0),
                        nc.get("break", 0),
                    )
                else:
                    from .maimaidx_model import Notes1
                    notes = Notes1(
                        nc.get("tap", 0),
                        nc.get("hold", 0),
                        nc.get("slide", 0),
                        nc.get("break", 0),
                    )
                charts_list.append({
                    "notes": notes,
                    "charter": sh.get("noteDesigner", ""),
                })
                if not main_sheet:
                    main_sheet = sh
            else:
                ds_list.append(0.0)
                level_list.append("0")
                cids_list.append(0)
                charts_list.append({"notes": None, "charter": ""})

        if not main_sheet:
            return None

        return {
            "id": str(sheets[0].get("internalId", "")),
            "title": song.get("title", ""),
            "type": song_type,
            "ds": ds_list,
            "level": level_list,
            "cids": cids_list,
            "charts": charts_list,
            "basic_info": {
                "title": song.get("title", ""),
                "artist": song.get("artist", ""),
                "genre": song.get("category", ""),
                "bpm": song.get("bpm", 0),
                "release_date": main_sheet.get("releaseDate", ""),
                "from": main_sheet.get("version", ""),
                "is_new": song.get("isNew", False),
            },
        }