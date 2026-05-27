"""
历代版本代号映射 + 定数查询。

用法：
    from .maimaidx_version_alias import VERSION_ALIAS, build_legacy_ds_map

    version_name = VERSION_ALIAS.get("镜代")           # → "PRiSM"
    ds_map = build_legacy_ds_map("PRiSM")                # → {(song_id, level_index): ds, ...}
"""

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

VERSION_ALIAS: Dict[str, str] = {
    "初代": "maimaiでらっくす",
    "dx初代": "maimaiでらっくす",
    "dx2020": "maimaiでらっくす PLUS",
    "初代plus": "maimaiでらっくす PLUS",
    "初代+": "maimaiでらっくす PLUS",
    "华代": "maimaiでらっくす PLUS",
    "熊代": "maimaiでらっくす",
    "dx2021": "Splash",
    "爽代": "Splash",
    "dx2022": "UNiVERSE",
    "煌代": "Splash PLUS",
    "爽plus": "Splash PLUS",
    "宙代": "UNiVERSE",
    "星代": "UNiVERSE PLUS",
    "宙plus": "UNiVERSE PLUS",
    "dx2023": "FESTiVAL",
    "祭代": "FESTiVAL",
    "祝代": "FESTiVAL PLUS",
    "祭plus": "FESTiVAL PLUS",
    "双代": "BUDDiES",
    "宴代": "BUDDiES PLUS",
    "双plus": "BUDDiES PLUS",
    "dx2024": "BUDDiES",
    "dx2025": "PRiSM",
    "镜代": "PRiSM",
    "彩代": "PRiSM PLUS",
    "镜plus": "PRiSM PLUS",
    "丸代": "CiRCLE",
    "丸plus": "CiRCLE PLUS",
}

DIFFICULTY_ORDER = ["basic", "advanced", "expert", "master", "remaster"]


def build_legacy_ds_map(
    version_name: str,
    dxdata_path: Optional[str] = None,
) -> Dict[Tuple[str, int], float]:
    """
    从 dxdata.json 构建版本定数映射。

    Args:
        version_name: 目标版本名（如 "PRiSM"）
        dxdata_path: dxdata.json 路径，None 则使用默认 "dxdata.json"

    Returns:
        {(song_id, level_index): ds_value} 映射
    """
    path = Path(dxdata_path) if dxdata_path else Path("dxdata.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    result: Dict[Tuple[str, int], float] = {}

    for song in data.get("songs", []):
        for sh in song.get("sheets", []):
            sid = sh.get("internalId")
            if sid is None:
                continue
            diff = sh.get("difficulty", "")
            if diff not in DIFFICULTY_ORDER:
                continue
            level_index = DIFFICULTY_ORDER.index(diff)

            mv = sh.get("multiverInternalLevelValue")
            if mv and isinstance(mv, dict) and version_name in mv:
                result[(str(sid), level_index)] = float(mv[version_name])

    return result


def resolve_version_alias(alias: str) -> Optional[str]:
    """解析版本代号，返回 dxdata 版本名，未匹配返回 None。"""
    return VERSION_ALIAS.get(alias)