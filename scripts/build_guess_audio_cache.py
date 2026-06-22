#!/usr/bin/env python3
"""批量预烘焙猜曲子阶段音频。用法: python scripts/build_guess_audio_cache.py 417 837 11417"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bootstrap_package() -> None:
    """允许在未安装插件 wheel 时从源码运行脚本。"""
    import importlib
    import types

    pkg_name = 'nonebot_plugin_maimaidx'
    if pkg_name in sys.modules:
        return

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ROOT)]
    sys.modules[pkg_name] = pkg
    lib = types.ModuleType(f'{pkg_name}.libraries')
    lib.__path__ = [str(ROOT / 'libraries')]
    sys.modules[f'{pkg_name}.libraries'] = lib


_bootstrap_package()

from nonebot_plugin_maimaidx.libraries.maimaidx_guess_audio import (  # noqa: E402
    build_audio_cache_sync,
    is_audio_ready,
)


def main() -> int:
    parser = argparse.ArgumentParser(description='预烘焙猜曲子音频缓存（曲库 ID）')
    parser.add_argument('ids', nargs='*', help='乐曲 ID，如 417 11417')
    parser.add_argument('--force', action='store_true', help='强制重建')
    parser.add_argument('--hot', action='store_true', help='从热门猜歌池烘焙（需已加载曲库）')
    args = parser.parse_args()

    ids = list(args.ids)
    if args.hot:
        from nonebot_plugin_maimaidx.libraries.maimaidx_guess_audio import (  # noqa: WPS433
            build_hot_audio_cache_sync,
        )

        print(build_hot_audio_cache_sync(force=args.force))
        return 0

    # 去重保序
    seen = set()
    unique_ids = []
    for mid in ids:
        s = str(mid)
        if s not in seen:
            seen.add(s)
            unique_ids.append(s)

    if not unique_ids:
        parser.print_help()
        return 1

    ok_n = 0
    for mid in unique_ids:
        if not args.force and is_audio_ready(mid):
            print(f'[skip] {mid} 已缓存')
            ok_n += 1
            continue
        print(f'[build] {mid} ...')
        ok, msg = build_audio_cache_sync(mid, force=args.force)
        print(f'  {"OK" if ok else "FAIL"}: {msg}')
        if ok:
            ok_n += 1

    print(f'完成 {ok_n}/{len(unique_ids)}')
    return 0 if ok_n else 1


if __name__ == '__main__':
    raise SystemExit(main())
