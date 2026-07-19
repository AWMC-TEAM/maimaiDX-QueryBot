"""猜铺面：用 Chart Preview 无音乐录制页生成谱面视频并缓存。"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import struct
import subprocess
import threading
import time
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx
from loguru import logger as log
from playwright.async_api import async_playwright

_PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHART_CDN = 'https://assets2.lxns.net/maimai/chart'
# rev=2：加长片段 + 混入正解音，使旧缓存失效
CHART_VIDEO_REV = 2
DEFAULT_DURATION = 40
DEFAULT_VIEWPORT = 720
ANSWER_GRACE = 120
# 作答倒计时提醒节点（秒）
COUNTDOWN_MARKS = (100, 80, 60, 40, 20)
# Playwright 收尾等待，用于裁剪出纯播放段
RECORD_END_PAD_SEC = 0.45
MAX_HIT_SOUNDS = 80
CHART_DIFF_NAMES = {
    2: '绿',
    3: '黄',
    4: '红',
    5: '紫',
    6: '白',
}

CHART_GUESS_DIR = _PKG_ROOT / 'data' / 'chart_guess'
CHART_GUESS_CACHE_DIR = CHART_GUESS_DIR / 'cache'
CHART_GUESS_MANIFEST = CHART_GUESS_DIR / 'manifest.json'

_BUILD_LOCKS: Dict[str, asyncio.Lock] = {}
_static_server: Optional[ThreadingHTTPServer] = None
_static_port: Optional[int] = None
_static_lock = threading.Lock()


def chart_preview_dir() -> Path:
    """优先包内构建产物，其次配置 static 目录。"""
    bundled = _PKG_ROOT / 'static' / 'chart_preview'
    if (bundled / 'index.html').is_file():
        return bundled
    try:
        from ..config import static as cfg_static

        alt = Path(cfg_static) / 'chart_preview'
        if (alt / 'index.html').is_file():
            return alt
    except Exception:
        pass
    return bundled


def _lock_for(key: str) -> asyncio.Lock:
    if key not in _BUILD_LOCKS:
        _BUILD_LOCKS[key] = asyncio.Lock()
    return _BUILD_LOCKS[key]


def preview_song_id(music_id: str, music_type: str) -> str:
    """与「谱面」指令一致：DX 曲库 ID 去掉前缀 1。"""
    mid = str(music_id).strip()
    if music_type == 'DX' and mid.startswith('1') and len(mid) > 1:
        return mid[1:]
    return mid


def chart_kind(music_type: str) -> str:
    return 'standard' if music_type == 'SD' else 'dx'


def chart_file_id(song_id: str, kind: str) -> int:
    n = int(song_id)
    if kind == 'utage':
        return n
    if kind == 'dx' and n < 100000:
        return n + 10000
    return n


def pick_chart_diff(level_count: int) -> int:
    """优先白/紫/红，返回 Chart Preview 的 diff（2–6）。"""
    for idx in (4, 3, 2, 1, 0):
        if idx < level_count:
            return idx + 2
    return 5


def cache_key(music_id: str, kind: str, diff: int) -> str:
    return f'{music_id}_{kind}_{diff}_r{CHART_VIDEO_REV}'


def video_path_for(music_id: str, kind: str, diff: int) -> Path:
    return CHART_GUESS_CACHE_DIR / cache_key(music_id, kind, diff) / 'chart.mp4'


def is_chart_video_ready(music_id: str, kind: str, diff: int) -> bool:
    path = video_path_for(music_id, kind, diff)
    return path.is_file() and path.stat().st_size > 1024


def _load_manifest() -> dict:
    if not CHART_GUESS_MANIFEST.exists():
        return {'version': CHART_VIDEO_REV, 'entries': {}}
    try:
        return json.loads(CHART_GUESS_MANIFEST.read_text(encoding='utf-8'))
    except Exception:
        return {'version': CHART_VIDEO_REV, 'entries': {}}


def _save_manifest(data: dict) -> None:
    CHART_GUESS_DIR.mkdir(parents=True, exist_ok=True)
    CHART_GUESS_MANIFEST.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def get_chart_manifest_entry(music_id: str, kind: str, diff: int) -> dict:
    key = cache_key(music_id, kind, diff)
    return (_load_manifest().get('entries') or {}).get(key, {})


async def chart_simai_exists(song_id: str, kind: str) -> bool:
    fid = chart_file_id(song_id, kind)
    url = f'{DEFAULT_CHART_CDN}/{fid}.txt'
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # 部分 CDN 对 HEAD 不友好，优先短 GET
            resp = await client.get(url, headers={'Range': 'bytes=0-64'})
            if resp.status_code < 400 and resp.content:
                return True
            resp = await client.get(url)
            return resp.status_code < 400 and bool(resp.text.strip())
    except Exception as e:
        log.warning(f'[GuessChart] 探测谱面失败 id={fid}: {e}')
        return False


def _ensure_static_server() -> int:
    global _static_server, _static_port
    with _static_lock:
        if _static_server is not None and _static_port is not None:
            return _static_port

        root = chart_preview_dir()
        if not (root / 'index.html').is_file():
            raise FileNotFoundError(
                f'未找到谱面预览静态页：{root}\n'
                '请先执行：cd chart_preview && npm install --legacy-peer-deps && npm run build'
            )

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def log_message(self, fmt: str, *args) -> None:
                return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()

        server = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _static_server = server
        _static_port = port
        log.info(f'[GuessChart] 静态服务已启动 http://127.0.0.1:{port}/ → {root}')
        return port


def _ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=nw=1:nk=1',
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f'ffprobe 失败: {proc.stderr[-400:]}')
    try:
        return max(0.1, float(proc.stdout.strip()))
    except ValueError as e:
        raise RuntimeError(f'ffprobe 时长无效: {proc.stdout!r}') from e


def _answer_wav_path() -> Path:
    return chart_preview_dir() / 'assets' / 'maimai' / 'chart' / 'answer.wav'


def _load_wav_mono_pcm16(path: Path) -> Tuple[int, List[int]]:
    with wave.open(str(path), 'rb') as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise RuntimeError(f'answer.wav 需为 16-bit PCM，实际 sampwidth={width}')
    samples = list(struct.unpack('<' + 'h' * (len(frames) // 2), frames))
    if channels == 2:
        samples = [
            int((samples[i] + samples[i + 1]) / 2)
            for i in range(0, len(samples) - 1, 2)
        ]
    elif channels != 1:
        raise RuntimeError(f'不支持的声道数: {channels}')
    return rate, samples


def _build_answer_track_wav(
    out_wav: Path,
    *,
    duration_sec: float,
    hit_offsets_ms: Sequence[float],
) -> bool:
    """按击打时间铺正解音轨；失败返回 False（仍可输出无声视频）。"""
    answer_path = _answer_wav_path()
    if not answer_path.is_file():
        log.warning(f'[GuessChart] 未找到正解音文件: {answer_path}')
        return False
    try:
        rate, answer = _load_wav_mono_pcm16(answer_path)
    except Exception as e:
        log.warning(f'[GuessChart] 读取正解音失败: {e}')
        return False

    total = max(1, int(duration_sec * rate) + len(answer))
    mix = [0] * total
    hits = sorted(float(x) for x in hit_offsets_ms if float(x) >= 0)[:MAX_HIT_SOUNDS]
    for hit_ms in hits:
        start = int(hit_ms / 1000.0 * rate)
        if start >= total:
            continue
        for i, sample in enumerate(answer):
            idx = start + i
            if idx >= total:
                break
            val = mix[idx] + int(sample * 0.85)
            mix[idx] = max(-32767, min(32767, val))

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack('<' + 'h' * len(mix), *mix))
    return True


def _ffmpeg_finalize(
    webm: Path,
    mp4: Path,
    *,
    duration: int,
    hit_offsets_ms: Sequence[float],
) -> None:
    """裁剪播放段、混入正解音并压成 H.264 mp4。"""
    mp4.parent.mkdir(parents=True, exist_ok=True)
    work = mp4.parent / '_encode'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    silent = work / 'silent.mp4'
    video_dur = _ffprobe_duration(webm)
    play_start = max(0.0, video_dur - float(duration) - RECORD_END_PAD_SEC)
    cmd_video = [
        'ffmpeg', '-y',
        '-ss', f'{play_start:.3f}',
        '-i', str(webm),
        '-t', str(duration),
        '-an',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '28',
        '-pix_fmt', 'yuv420p',
        '-vf', f'scale={DEFAULT_VIEWPORT}:{DEFAULT_VIEWPORT}',
        str(silent),
    ]
    proc = subprocess.run(cmd_video, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 视频转码失败: {proc.stderr[-800:]}')

    audio_wav = work / 'answers.wav'
    has_audio = _build_answer_track_wav(
        audio_wav,
        duration_sec=float(duration) + 0.2,
        hit_offsets_ms=hit_offsets_ms,
    )
    tmp = mp4.with_suffix('.tmp.mp4')
    if has_audio:
        cmd_mux = [
            'ffmpeg', '-y',
            '-i', str(silent),
            '-i', str(audio_wav),
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-shortest',
            '-movflags', '+faststart',
            str(tmp),
        ]
    else:
        cmd_mux = [
            'ffmpeg', '-y',
            '-i', str(silent),
            '-c:v', 'copy',
            '-an',
            '-movflags', '+faststart',
            str(tmp),
        ]
    proc = subprocess.run(cmd_mux, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 混音失败: {proc.stderr[-800:]}')
    tmp.replace(mp4)
    shutil.rmtree(work, ignore_errors=True)


async def _render_chart_video(
    *,
    song_id: str,
    kind: str,
    diff: int,
    out_mp4: Path,
    duration: int = DEFAULT_DURATION,
) -> dict:
    port = await asyncio.to_thread(_ensure_static_server)
    query = urlencode({
        'song': song_id,
        'kind': kind,
        'diff': str(diff),
        'duration': str(duration),
        'start': '-1',
        'hispeed': '6',
    })
    url = f'http://127.0.0.1:{port}/#/record?{query}'

    work = out_mp4.parent / '_work'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    meta: dict = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': DEFAULT_VIEWPORT, 'height': DEFAULT_VIEWPORT},
            record_video_dir=str(work),
            record_video_size={'width': DEFAULT_VIEWPORT, 'height': DEFAULT_VIEWPORT},
            device_scale_factor=1,
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_function(
                """() => window.__GUESS_CHART__
                    && ['ready','playing','done','error'].includes(window.__GUESS_CHART__.state)""",
                timeout=90000,
            )
            bridge = await page.evaluate('() => window.__GUESS_CHART__')
            if bridge.get('state') == 'error':
                raise RuntimeError(bridge.get('error') or '谱面加载失败')
            meta = dict(bridge or {})
            await page.wait_for_function(
                """() => window.__GUESS_CHART__
                    && (window.__GUESS_CHART__.state === 'done'
                        || window.__GUESS_CHART__.state === 'error')""",
                timeout=(duration + 60) * 1000,
            )
            bridge = await page.evaluate('() => window.__GUESS_CHART__')
            if bridge.get('state') == 'error':
                raise RuntimeError(bridge.get('error') or '谱面录制失败')
            meta = dict(bridge or {})
            # 尾帧多留一点，避免录制提前截断
            await page.wait_for_timeout(int(RECORD_END_PAD_SEC * 1000))
        finally:
            await page.close()
            await context.close()
            await browser.close()

    webms = sorted(work.glob('*.webm'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        raise RuntimeError('Playwright 未产出 webm 视频')
    hits = meta.get('hitOffsetsMs') or []
    if not isinstance(hits, list):
        hits = []
    await asyncio.to_thread(
        _ffmpeg_finalize,
        webms[0],
        out_mp4,
        duration=duration,
        hit_offsets_ms=hits,
    )
    shutil.rmtree(work, ignore_errors=True)
    return meta


async def ensure_chart_video_ready(
    music_id: str,
    *,
    music_type: str,
    title: str = '',
    level_count: int = 5,
    duration: int = DEFAULT_DURATION,
) -> Tuple[bool, str, Optional[Path], dict]:
    """确保缓存中有可用猜铺面视频。"""
    primary_kind = chart_kind(music_type)
    song_id = preview_song_id(music_id, music_type)
    diff = pick_chart_diff(level_count)
    # 优先曲库类型；若 CDN 无对应 simai，回退另一种 kind
    kind_candidates = [primary_kind]
    alt = 'standard' if primary_kind == 'dx' else 'dx'
    kind_candidates.append(alt)

    last_err = ''
    for kind in kind_candidates:
        key = cache_key(music_id, kind, diff)
        out = video_path_for(music_id, kind, diff)

        if is_chart_video_ready(music_id, kind, diff):
            return True, 'cache', out, get_chart_manifest_entry(music_id, kind, diff)

        async with _lock_for(key):
            if is_chart_video_ready(music_id, kind, diff):
                return True, 'cache', out, get_chart_manifest_entry(music_id, kind, diff)

            if not await chart_simai_exists(song_id, kind):
                last_err = f'CDN 无谱面（{song_id}/{kind}）'
                continue

            log.info(
                f'[GuessChart] 开始渲染 music={music_id} title={title!r} '
                f'song={song_id} kind={kind} diff={diff} duration={duration}s'
            )
            started = time.time()
            try:
                meta = await _render_chart_video(
                    song_id=song_id,
                    kind=kind,
                    diff=diff,
                    out_mp4=out,
                    duration=duration,
                )
            except Exception as e:
                last_err = str(e)
                log.warning(f'[GuessChart] 渲染失败 music={music_id} kind={kind}: {e}')
                continue

            elapsed = int(time.time() - started)
            entry = {
                'music_id': str(music_id),
                'song_id': song_id,
                'kind': kind,
                'diff': diff,
                'diff_name': CHART_DIFF_NAMES.get(diff, str(diff)),
                'duration': duration,
                'start_sec': meta.get('startSec'),
                'path': str(out.resolve()),
                'size': out.stat().st_size,
                'rev': CHART_VIDEO_REV,
                'built_at': int(time.time()),
                'elapsed_sec': elapsed,
                'title': title,
            }
            manifest = _load_manifest()
            manifest.setdefault('entries', {})[key] = entry
            _save_manifest(manifest)
            log.info(
                f'[GuessChart] 渲染完成 music={music_id} size={entry["size"]} '
                f'elapsed={elapsed}s file={out}'
            )
            return True, 'built', out, entry

    return False, last_err or '无可用谱面', None, {}


def list_ready_chart_music_ids() -> List[str]:
    manifest = _load_manifest()
    ids = []
    for entry in (manifest.get('entries') or {}).values():
        mid = str(entry.get('music_id') or '')
        path = Path(entry.get('path') or '')
        if mid and path.is_file():
            ids.append(mid)
    return ids
