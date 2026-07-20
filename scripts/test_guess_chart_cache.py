#!/usr/bin/env python3
"""猜铺面缓存/视频路径最小回归（不启动 NoneBot）。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART_PY = ROOT / 'libraries' / 'maimaidx_guess_chart.py'
PLATFORM_PY = ROOT / 'libraries' / 'maimaidx_platform.py'


def _load_chart_module():
    pkg = types.ModuleType('nonebot_plugin_maimaidx')
    pkg.__path__ = [str(ROOT)]
    lib = types.ModuleType('nonebot_plugin_maimaidx.libraries')
    lib.__path__ = [str(ROOT / 'libraries')]
    sys.modules['nonebot_plugin_maimaidx'] = pkg
    sys.modules['nonebot_plugin_maimaidx.libraries'] = lib
    # 本地无 playwright 时用桩，仅测缓存/路径逻辑
    if 'playwright' not in sys.modules:
        pw = types.ModuleType('playwright')
        async_api = types.ModuleType('playwright.async_api')
        async_api.async_playwright = lambda: None  # type: ignore
        sys.modules['playwright'] = pw
        sys.modules['playwright.async_api'] = async_api
    if 'httpx' not in sys.modules:
        sys.modules['httpx'] = types.ModuleType('httpx')
    if 'loguru' not in sys.modules:
        loguru = types.ModuleType('loguru')
        class _L:
            def info(self, *a, **k): pass
            def debug(self, *a, **k): pass
            def warning(self, *a, **k): pass
            def error(self, *a, **k): pass
        loguru.logger = _L()
        sys.modules['loguru'] = loguru
    spec = importlib.util.spec_from_file_location(
        'nonebot_plugin_maimaidx.libraries.maimaidx_guess_chart', CHART_PY,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class GuessChartCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gc = _load_chart_module()

    def test_low_priority_cmd_prefixes_nice(self):
        cmd = self.gc._low_priority_cmd(['ffmpeg', '-version'])
        self.assertEqual(cmd[0], 'nice')
        self.assertIn('ffmpeg', cmd)

    def test_env_int_allows_zero_for_bg_fill(self):
        self.assertEqual(self.gc._env_int('__no_such__', 1, minimum=0), 1)
        import os
        os.environ['MAIMAIDX_TEST_BG_FILL'] = '0'
        try:
            self.assertEqual(
                self.gc._env_int('MAIMAIDX_TEST_BG_FILL', 2, minimum=0), 0,
            )
        finally:
            os.environ.pop('MAIMAIDX_TEST_BG_FILL', None)

    def test_online_defaults_are_conservative(self):
        # 多核机默认应保守，避免在线时打满
        self.assertLessEqual(self.gc._default_render_workers(), 2)
        self.assertLessEqual(self.gc._default_bg_fill_workers(), 1)
        self.assertLessEqual(self.gc._default_cpu_pool_workers(), 6)

    def test_adaptive_targets_cut_bg_fill_first(self):
        gc = self.gc
        # critical：硬底
        r, b, batch, tier = gc._adaptive_targets(0.85, 0.0)
        self.assertEqual(tier, 'critical')
        self.assertEqual(r, gc.RENDER_MIN)
        self.assertEqual(b, 0)
        self.assertEqual(batch, gc.BATCH_SONG_MIN)
        # busy：补洞关，录制可保留少量
        r, b, _, tier = gc._adaptive_targets(0.55, 0.0)
        self.assertEqual(tier, 'busy')
        self.assertEqual(b, 0)
        self.assertGreaterEqual(r, gc.RENDER_MIN)
        # elevated：仍关补洞
        _, b, _, tier = gc._adaptive_targets(0.40, 0.0)
        self.assertEqual(tier, 'elevated')
        self.assertEqual(b, 0)
        # lag 也能触发 busy/critical
        _, b, _, tier = gc._adaptive_targets(0.10, gc.ADAPTIVE_LAG_BUSY_MS + 1)
        self.assertEqual(tier, 'busy')
        self.assertEqual(b, 0)
        # idle：允许升到上限
        r, b, batch, tier = gc._adaptive_targets(0.05, 0.0)
        self.assertEqual(tier, 'idle')
        self.assertEqual(r, gc.RENDER_MAX)
        self.assertEqual(b, gc.BG_FILL_MAX)
        self.assertEqual(batch, gc.BATCH_SONG_MAX)
        # warmup
        r, b, _, tier = gc._adaptive_targets(0.05, 0.0, warmup=True)
        self.assertEqual(tier, 'warmup')
        self.assertEqual(r, gc.RENDER_MIN)
        self.assertEqual(b, gc.BG_FILL_MIN)

    def test_step_toward_aggressive_down(self):
        self.assertEqual(self.gc._step_toward(4, 1, aggressive_down=True), 1)
        self.assertEqual(self.gc._step_toward(1, 4, aggressive_down=False), 2)
        self.assertEqual(self.gc._step_toward(2, 2, aggressive_down=True), 2)

    def test_cache_key_and_round_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'
            self.gc.CHART_GUESS_CACHE_DIR = cache
            mid, kind, diff = '10030', 'dx', 5
            key = self.gc.cache_key(mid, kind, diff)
            d = cache / key
            d.mkdir(parents=True)
            mute = d / 'chart.mp4'
            bgm = d / 'chart_bgm.mp4'
            mute.write_bytes(b'x' * 2048)
            self.assertTrue(self.gc.is_chart_video_ready(mid, kind, diff))
            self.assertFalse(self.gc.is_chart_bgm_ready(mid, kind, diff))
            self.assertFalse(self.gc.is_chart_round_ready(mid, kind, diff))
            bgm.write_bytes(b'y' * 2048)
            self.assertTrue(self.gc.is_chart_round_ready(mid, kind, diff))
            holes = self.gc.list_mute_without_bgm()
            self.assertEqual(holes, [])
            ready = self.gc.list_ready_chart_rounds()
            self.assertEqual(ready, [(mid, kind, diff)])

    def test_list_mute_without_bgm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'
            self.gc.CHART_GUESS_CACHE_DIR = cache
            mid, kind, diff = '22', 'standard', 6
            d = cache / self.gc.cache_key(mid, kind, diff)
            d.mkdir(parents=True)
            (d / 'chart.mp4').write_bytes(b'x' * 2048)
            (d / '_work_bgm').mkdir()
            (d / '_work_bgm' / 'source.mp3').write_bytes(b'mp3')
            holes = self.gc.list_mute_without_bgm()
            self.assertEqual(holes, [(mid, kind, diff)])
            removed = self.gc.cleanup_stale_chart_workdirs()
            self.assertGreaterEqual(removed, 1)
            self.assertFalse((d / '_work_bgm').exists())


class LocalVideoPathTests(unittest.TestCase):
    def test_onebot_video_path_helpers(self):
        # 轻量：只测路径解析函数源码存在且 file:// 逻辑可读
        text = PLATFORM_PY.read_text(encoding='utf-8')
        self.assertIn('def local_video_segment', text)
        self.assertIn('def _onebot_video_path', text)
        self.assertIn('urlparse', text)


if __name__ == '__main__':
    unittest.main()
