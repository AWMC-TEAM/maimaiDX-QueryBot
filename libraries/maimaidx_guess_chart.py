"""猜铺面：用 Chart Preview 无音乐录制页生成谱面视频并缓存。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import wave
from array import array
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx
from loguru import logger as log
from playwright.async_api import async_playwright

_PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHART_CDN = 'https://assets2.lxns.net/maimai/chart'
# rev=8：多核并行录制/转码（同曲两阶段并行 + 预制并发）
CHART_VIDEO_REV = 8
DEFAULT_DURATION = 40
# 整局 120 秒：前 90 秒静音，最后 30 秒曲末带 BGM
PHASE2_DURATION = 30
STAGE_INTERVAL = 90
STAGE_FINAL_GRACE = 30
DEFAULT_VIEWPORT = 720
ANSWER_GRACE = STAGE_INTERVAL + STAGE_FINAL_GRACE  # 120
# 作答倒计时提醒节点（秒）
COUNTDOWN_MARKS = (60, 30, 20, 10)
MAX_HIT_SOUNDS = 2500
DEFAULT_CHART_BATCH_LIMIT = 20
CAPTURE_FPS = 60
VIDEO_CRF = 18
# faster：在 40 核上明显快于 medium，画质仍可接受
VIDEO_PRESET = 'faster'
AUDIO_BITRATE = '192k'


def _cpu_count() -> int:
    return max(1, int(os.cpu_count() or 4))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _default_render_workers() -> int:
    """同时进行的 Playwright 录制数（Chromium 吃内存，不宜 = 核心数）。"""
    n = _cpu_count()
    # 32+ 核：约 10～12 路录制；40 核默认 10
    if n >= 32:
        return min(12, max(8, n // 4))
    return max(2, min(8, n // 5))


def _default_batch_song_workers() -> int:
    """预制时并发曲目数；每曲最多占 2 个录制槽（静音+BGM）。"""
    rw = _default_render_workers()
    # 录制槽约一半用于同时开多曲，避免只串行一首
    return max(1, min(6, rw // 2))


def _default_ffmpeg_threads() -> int:
    n = _cpu_count()
    # 多路 ffmpeg 并行时不宜每进程吃满全核
    return max(2, min(20, n // 2))


# 可用环境变量覆盖：MAIMAIDX_CHART_RENDER_WORKERS / BATCH_SONGS / FFMPEG_THREADS
RENDER_WORKERS = _env_int('MAIMAIDX_CHART_RENDER_WORKERS', _default_render_workers())
BATCH_SONG_WORKERS = _env_int(
    'MAIMAIDX_CHART_BATCH_SONGS', _default_batch_song_workers(),
)
FFMPEG_THREADS = _env_int('MAIMAIDX_CHART_FFMPEG_THREADS', _default_ffmpeg_threads())
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
_prepare_status = ''
_prepare_status_lock = threading.Lock()
_batch_cancel = threading.Event()
_render_sem: Optional[asyncio.Semaphore] = None


def _get_render_sem() -> asyncio.Semaphore:
    global _render_sem
    if _render_sem is None:
        _render_sem = asyncio.Semaphore(RENDER_WORKERS)
        log.info(
            f'[GuessChart] 并行度 cpu={_cpu_count()} '
            f'render_workers={RENDER_WORKERS} batch_songs={BATCH_SONG_WORKERS} '
            f'ffmpeg_threads={FFMPEG_THREADS}'
        )
    return _render_sem


def _ffmpeg_x264_args() -> List[str]:
    return [
        '-threads', str(FFMPEG_THREADS),
        '-c:v', 'libx264',
        '-preset', VIDEO_PRESET,
        '-crf', str(VIDEO_CRF),
        '-pix_fmt', 'yuv420p',
        '-x264-params', f'threads={FFMPEG_THREADS}:sliced-threads=1',
    ]


def set_chart_prepare_status(msg: str) -> None:
    global _prepare_status
    with _prepare_status_lock:
        _prepare_status = msg or ''


def get_chart_prepare_status() -> str:
    with _prepare_status_lock:
        return _prepare_status


def request_chart_batch_cancel() -> None:
    _batch_cancel.set()


def _reset_chart_batch_cancel() -> None:
    _batch_cancel.clear()


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


def bgm_video_path_for(music_id: str, kind: str, diff: int) -> Path:
    return CHART_GUESS_CACHE_DIR / cache_key(music_id, kind, diff) / 'chart_bgm.mp4'


def is_chart_video_ready(music_id: str, kind: str, diff: int) -> bool:
    path = video_path_for(music_id, kind, diff)
    return path.is_file() and path.stat().st_size > 1024


def is_chart_bgm_ready(music_id: str, kind: str, diff: int) -> bool:
    path = bgm_video_path_for(music_id, kind, diff)
    return path.is_file() and path.stat().st_size > 1024


def is_chart_round_ready(music_id: str, kind: str, diff: int) -> bool:
    """阶段1 + 阶段2 均就绪。"""
    return is_chart_video_ready(music_id, kind, diff) and is_chart_bgm_ready(music_id, kind, diff)


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
    samples = array('h')
    samples.frombytes(frames)
    if channels == 2:
        samples = array(
            'h',
            (int((samples[i] + samples[i + 1]) / 2) for i in range(0, len(samples) - 1, 2)),
        )
    elif channels != 1:
        raise RuntimeError(f'不支持的声道数: {channels}')
    return rate, list(samples)


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

    # 预留完整 answer 尾音，避免最后几击被截断
    total = max(1, int(round(duration_sec * rate)) + len(answer) + rate // 10)
    mix = array('h', [0]) * total
    hits = sorted({round(float(x), 3) for x in hit_offsets_ms if float(x) >= 0})
    if len(hits) > MAX_HIT_SOUNDS:
        # 均匀抽样，避免超密谱把音轨打爆
        step = len(hits) / MAX_HIT_SOUNDS
        hits = [hits[int(i * step)] for i in range(MAX_HIT_SOUNDS)]
    placed = 0
    for hit_ms in hits:
        start = int(round(hit_ms / 1000.0 * rate))
        if start >= total:
            continue
        for i, sample in enumerate(answer):
            idx = start + i
            if idx >= total:
                break
            val = int(mix[idx]) + int(sample * 0.9)
            mix[idx] = max(-32767, min(32767, val))
        placed += 1

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(mix.tobytes())
    log.info(f'[GuessChart] 正解音轨 hits={placed}/{len(hits)} duration={duration_sec:.3f}s')
    return placed > 0


def _encode_silent_mp4(webm: Path, silent: Path) -> float:
    silent.parent.mkdir(parents=True, exist_ok=True)
    cmd_video = [
        'ffmpeg', '-y',
        '-i', str(webm),
        '-an',
        *_ffmpeg_x264_args(),
        '-vf', f'scale={DEFAULT_VIEWPORT}:{DEFAULT_VIEWPORT},fps={CAPTURE_FPS}',
        '-r', str(CAPTURE_FPS),
        '-vsync', 'cfr',
        '-movflags', '+faststart',
        str(silent),
    ]
    proc = subprocess.run(cmd_video, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 视频转码失败: {proc.stderr[-800:]}')
    return _ffprobe_duration(silent)


def _ffmpeg_remux_embedded(webm: Path, mp4: Path) -> None:
    """webm 已含音画同轨时，高质量转码为 mp4。"""
    mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = mp4.with_suffix('.tmp.mp4')
    cmd = [
        'ffmpeg', '-y',
        '-i', str(webm),
        *_ffmpeg_x264_args(),
        '-vf', f'scale={DEFAULT_VIEWPORT}:{DEFAULT_VIEWPORT},fps={CAPTURE_FPS}',
        '-r', str(CAPTURE_FPS),
        '-vsync', 'cfr',
        '-c:a', 'aac',
        '-b:a', AUDIO_BITRATE,
        '-ar', '48000',
        '-threads', str(FFMPEG_THREADS),
        '-movflags', '+faststart',
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 同轨转码失败: {proc.stderr[-800:]}')
    tmp.replace(mp4)


def _mux_video_audio(silent: Path, audio: Path, mp4: Path) -> None:
    """画面 + 音轨；音轨更长时冻结尾帧。"""
    video_dur = _ffprobe_duration(silent)
    audio_dur = _ffprobe_duration(audio)
    pad = max(0.0, audio_dur - video_dur + 0.05)
    tmp = mp4.with_suffix('.tmp.mp4')
    if pad > 0.01:
        vf = f'[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f}[v]'
        cmd_mux = [
            'ffmpeg', '-y',
            '-i', str(silent),
            '-i', str(audio),
            '-filter_complex', f'{vf};[1:a]anull[a]',
            '-map', '[v]',
            '-map', '[a]',
            *_ffmpeg_x264_args(),
            '-c:a', 'aac',
            '-b:a', AUDIO_BITRATE,
            '-shortest',
            '-movflags', '+faststart',
            str(tmp),
        ]
    else:
        cmd_mux = [
            'ffmpeg', '-y',
            '-i', str(silent),
            '-i', str(audio),
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', AUDIO_BITRATE,
            '-threads', str(FFMPEG_THREADS),
            '-shortest',
            '-movflags', '+faststart',
            str(tmp),
        ]
    proc = subprocess.run(cmd_mux, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 混音失败: {proc.stderr[-800:]}')
    tmp.replace(mp4)


def _ffmpeg_mux(
    webm: Path,
    mp4: Path,
    *,
    hit_offsets_ms: Sequence[float],
    record_lead_ms: float = 0.0,
    nominal_duration: float = DEFAULT_DURATION,
) -> None:
    """将 MediaRecorder webm 转码，并按录制时间轴混入正解音。"""
    mp4.parent.mkdir(parents=True, exist_ok=True)
    work = mp4.parent / '_encode'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    silent = work / 'silent.mp4'
    video_dur = _encode_silent_mp4(webm, silent)
    lead = max(0.0, float(record_lead_ms))
    lead_sec = lead / 1000.0
    # 按实际画面播放段 / 名义时长等比映射 hit，补偿录制时钟漂移
    play_video = max(0.05, video_dur - lead_sec)
    nominal = max(0.05, float(nominal_duration))
    scale = play_video / nominal
    aligned_hits = [lead + float(h) * scale for h in hit_offsets_ms]

    audio_wav = work / 'answers.wav'
    has_audio = _build_answer_track_wav(
        audio_wav,
        duration_sec=video_dur + 0.35,
        hit_offsets_ms=aligned_hits,
    )
    if has_audio:
        _mux_video_audio(silent, audio_wav, mp4)
    else:
        tmp = mp4.with_suffix('.tmp.mp4')
        shutil.copy2(silent, tmp)
        tmp.replace(mp4)
    shutil.rmtree(work, ignore_errors=True)


def _music_cdn_urls(music_id: str) -> List[str]:
    """原曲 mp3 URL 候选（与猜曲子一致的 ID 回落）。"""
    try:
        from .maimaidx_guess_audio import cdn_url_candidates

        return cdn_url_candidates(music_id)
    except ImportError:
        pass
    base = 'https://assets2.lxns.net/maimai/music'
    ordered: List[str] = []
    seen = set()

    def add(sid: str) -> None:
        if sid and sid not in seen:
            seen.add(sid)
            ordered.append(sid)

    add(str(music_id).strip())
    try:
        n = int(music_id)
    except (TypeError, ValueError):
        return [f'{base}/{ordered[0]}.mp3'] if ordered else []
    if n >= 10000:
        add(str(n - 10000))
    if n >= 11000:
        add(str(n - 11000))
    sid = str(music_id)
    if sid.startswith('1') and len(sid) > 1:
        add(sid[1:])
    return [f'{base}/{sid}.mp3' for sid in ordered]


def _download_music_mp3(music_id: str, dest: Path) -> None:
    """从 Lxns CDN 下载原曲 mp3。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for url in _music_cdn_urls(music_id):
            try:
                resp = client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                if not resp.content:
                    continue
                dest.write_bytes(resp.content)
                log.info(
                    f'[GuessChart] BGM 下载完成 music={music_id} '
                    f'size={len(resp.content) // 1024}KB url={url}'
                )
                return
            except Exception as e:
                last_err = e
    raise RuntimeError(f'CDN 无可用 BGM (music_id={music_id}): {last_err}')


def _ffmpeg_mux_bgm(
    webm: Path,
    mp4: Path,
    *,
    source_mp3: Path,
    music_start_sec: float,
    duration_sec: float,
    record_lead_ms: float = 0.0,
) -> None:
    """静音谱面 + 原曲裁剪（music_start_sec 已扣 lead-in）；adelay 对齐录制前置。"""
    mp4.parent.mkdir(parents=True, exist_ok=True)
    work = mp4.parent / '_encode_bgm'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    silent = work / 'silent.mp4'
    video_dur = _encode_silent_mp4(webm, silent)
    lead_ms = max(0, int(round(float(record_lead_ms))))
    lead_sec = lead_ms / 1000.0
    play_video = max(0.5, video_dur - lead_sec)
    # 音频长度贴合实际画面播放段，减少尾部错位
    clip_dur = max(0.5, min(float(duration_sec) + 0.05, play_video + 0.05))

    clip = work / 'bgm_clip.m4a'
    cmd_clip = [
        'ffmpeg', '-y',
        '-ss', f'{max(0.0, float(music_start_sec)):.3f}',
        '-t', f'{clip_dur:.3f}',
        '-i', str(source_mp3),
        '-af', f'adelay={lead_ms}|{lead_ms},aresample=48000',
        '-c:a', 'aac',
        '-b:a', AUDIO_BITRATE,
        str(clip),
    ]
    proc = subprocess.run(cmd_clip, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg BGM 裁剪失败: {proc.stderr[-800:]}')

    _mux_video_audio(silent, clip, mp4)
    shutil.rmtree(work, ignore_errors=True)


async def _capture_record_page(
    *,
    song_id: str,
    kind: str,
    diff: int,
    duration: int,
    tail: Optional[int] = None,
    with_audio: bool = False,
) -> dict:
    """打开录制页并返回 bridge meta（含 videoBase64）。"""
    port = await asyncio.to_thread(_ensure_static_server)
    q: Dict[str, str] = {
        'song': song_id,
        'kind': kind,
        'diff': str(diff),
        'duration': str(duration),
        'start': '-1',
        'hispeed': '6',
    }
    if tail and tail > 0:
        q['tail'] = str(int(tail))
        q['duration'] = str(int(tail))
    if with_audio:
        q['withAudio'] = '1'
    query = urlencode(q)
    url = f'http://127.0.0.1:{port}/#/record?{query}'

    meta: dict = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding',
                '--disable-backgrounding-occluded-windows',
                '--autoplay-policy=no-user-gesture-required',
            ],
        )
        context = await browser.new_context(
            viewport={'width': DEFAULT_VIEWPORT, 'height': DEFAULT_VIEWPORT},
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
            await page.wait_for_function(
                """() => window.__GUESS_CHART__
                    && (window.__GUESS_CHART__.state === 'done'
                        || window.__GUESS_CHART__.state === 'error')""",
                timeout=(duration + 90) * 1000,
            )
            bridge = await page.evaluate(
                """() => {
                    const g = window.__GUESS_CHART__ || {};
                    return {
                        state: g.state,
                        error: g.error,
                        durationSec: g.durationSec,
                        startSec: g.startSec,
                        songId: g.songId,
                        kind: g.kind,
                        diff: g.diff,
                        hitOffsetsMs: g.hitOffsetsMs || [],
                        recordLeadMs: g.recordLeadMs || 0,
                        musicStartSec: g.musicStartSec || 0,
                        hasEmbeddedAudio: !!g.hasEmbeddedAudio,
                        videoBase64: g.videoBase64 || null,
                        videoMime: g.videoMime || null,
                    };
                }"""
            )
            if bridge.get('state') == 'error':
                raise RuntimeError(bridge.get('error') or '谱面录制失败')
            meta = dict(bridge or {})
        finally:
            await page.close()
            await context.close()
            await browser.close()

    b64 = meta.get('videoBase64') or ''
    if not b64:
        raise RuntimeError('页面未返回录制视频（videoBase64 为空）')
    return meta


async def _render_chart_video(
    *,
    song_id: str,
    kind: str,
    diff: int,
    out_mp4: Path,
    duration: int = DEFAULT_DURATION,
    tail: Optional[int] = None,
    music_id: Optional[str] = None,
    mix_bgm: bool = False,
) -> dict:
    async with _get_render_sem():
        work = out_mp4.parent / ('_work_bgm' if mix_bgm else '_work')
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        # BGM 回退混流时与录制并行下载原曲，吃满网络+CPU 空隙
        dl_task: Optional[asyncio.Task] = None
        src = work / 'source.mp3'
        if mix_bgm and music_id:
            dl_task = asyncio.create_task(
                asyncio.to_thread(_download_music_mp3, music_id, src)
            )

        meta = await _capture_record_page(
            song_id=song_id,
            kind=kind,
            diff=diff,
            duration=duration,
            tail=tail,
            with_audio=mix_bgm,
        )
        webm = work / 'capture.webm'
        webm.write_bytes(base64.b64decode(meta.get('videoBase64') or ''))
        hits = meta.get('hitOffsetsMs') or []
        if not isinstance(hits, list):
            hits = []
        try:
            lead_ms = float(meta.get('recordLeadMs') or 0)
        except (TypeError, ValueError):
            lead_ms = 0.0
        try:
            start_sec = float(meta.get('startSec') or 0)
        except (TypeError, ValueError):
            start_sec = 0.0
        try:
            music_start_sec = float(meta.get('musicStartSec') or start_sec)
        except (TypeError, ValueError):
            music_start_sec = start_sec
        embedded = bool(meta.get('hasEmbeddedAudio'))

        if mix_bgm and embedded:
            if dl_task is not None:
                dl_task.cancel()
                try:
                    await dl_task
                except (asyncio.CancelledError, Exception):
                    pass
            await asyncio.to_thread(_ffmpeg_remux_embedded, webm, out_mp4)
        elif mix_bgm:
            if not music_id:
                raise RuntimeError('BGM 混流需要 music_id')
            if dl_task is not None:
                await dl_task
            elif not src.is_file():
                await asyncio.to_thread(_download_music_mp3, music_id, src)
            await asyncio.to_thread(
                _ffmpeg_mux_bgm,
                webm,
                out_mp4,
                source_mp3=src,
                music_start_sec=music_start_sec,
                duration_sec=float(tail or duration),
                record_lead_ms=lead_ms,
            )
        else:
            if dl_task is not None:
                dl_task.cancel()
                try:
                    await dl_task
                except (asyncio.CancelledError, Exception):
                    pass
            await asyncio.to_thread(
                _ffmpeg_mux,
                webm,
                out_mp4,
                hit_offsets_ms=hits,
                record_lead_ms=lead_ms,
                nominal_duration=float(duration),
            )

        shutil.rmtree(work, ignore_errors=True)
        meta.pop('videoBase64', None)
        meta['hit_count'] = len(hits)
        meta['recordLeadMs'] = lead_ms
        meta['startSec'] = start_sec
        meta['musicStartSec'] = music_start_sec
        meta['hasEmbeddedAudio'] = embedded
        return meta


def _entry_with_paths(
    music_id: str,
    kind: str,
    diff: int,
    *,
    song_id: str,
    title: str,
    duration: int,
    out: Path,
    out_bgm: Optional[Path],
    meta: dict,
    meta_bgm: Optional[dict],
    elapsed: int,
) -> dict:
    entry = {
        'music_id': str(music_id),
        'song_id': song_id,
        'kind': kind,
        'diff': diff,
        'diff_name': CHART_DIFF_NAMES.get(diff, str(diff)),
        'duration': duration,
        'bgm_duration': PHASE2_DURATION,
        'start_sec': meta.get('startSec'),
        'path': str(out.resolve()),
        'path_bgm': str(out_bgm.resolve()) if out_bgm and out_bgm.is_file() else '',
        'has_bgm': bool(out_bgm and out_bgm.is_file()),
        'size': out.stat().st_size,
        'size_bgm': out_bgm.stat().st_size if out_bgm and out_bgm.is_file() else 0,
        'rev': CHART_VIDEO_REV,
        'built_at': int(time.time()),
        'elapsed_sec': elapsed,
        'title': title,
        'bgm_start_sec': (meta_bgm or {}).get('startSec'),
    }
    return entry


async def ensure_chart_video_ready(
    music_id: str,
    *,
    music_type: str,
    title: str = '',
    level_count: int = 5,
    duration: int = DEFAULT_DURATION,
) -> Tuple[bool, str, Optional[Path], dict]:
    """确保缓存中有阶段1视频；尽力同时准备阶段2 BGM 视频。"""
    primary_kind = chart_kind(music_type)
    song_id = preview_song_id(music_id, music_type)
    diff = pick_chart_diff(level_count)
    kind_candidates = [primary_kind]
    alt = 'standard' if primary_kind == 'dx' else 'dx'
    kind_candidates.append(alt)

    last_err = ''
    set_chart_prepare_status('探测谱面资源…')
    for kind in kind_candidates:
        key = cache_key(music_id, kind, diff)
        out = video_path_for(music_id, kind, diff)
        out_bgm = bgm_video_path_for(music_id, kind, diff)

        if is_chart_video_ready(music_id, kind, diff):
            entry = get_chart_manifest_entry(music_id, kind, diff)
            if not is_chart_bgm_ready(music_id, kind, diff):
                async with _lock_for(key + '_bgm'):
                    if not is_chart_bgm_ready(music_id, kind, diff):
                        try:
                            set_chart_prepare_status('补渲染曲末 BGM 谱面…')
                            log.info(
                                f'[GuessChart] 补渲染 BGM music={music_id} '
                                f'kind={kind} diff={diff}'
                            )
                            meta_bgm = await _render_chart_video(
                                song_id=song_id,
                                kind=kind,
                                diff=diff,
                                out_mp4=out_bgm,
                                duration=PHASE2_DURATION,
                                tail=PHASE2_DURATION,
                                music_id=str(music_id),
                                mix_bgm=True,
                            )
                            entry = dict(entry)
                            entry['path_bgm'] = str(out_bgm.resolve())
                            entry['has_bgm'] = True
                            entry['size_bgm'] = out_bgm.stat().st_size
                            entry['bgm_duration'] = PHASE2_DURATION
                            entry['bgm_start_sec'] = meta_bgm.get('startSec')
                            manifest = _load_manifest()
                            manifest.setdefault('entries', {})[key] = entry
                            _save_manifest(manifest)
                        except Exception as e:
                            log.warning(f'[GuessChart] BGM 补渲染失败 music={music_id}: {e}')
                            entry = dict(entry)
                            entry['has_bgm'] = False
                            entry['path_bgm'] = ''
            else:
                entry = dict(entry)
                entry.setdefault('path_bgm', str(out_bgm.resolve()))
                entry['has_bgm'] = True
            set_chart_prepare_status('命中缓存')
            return True, 'cache', out, entry

        async with _lock_for(key):
            if is_chart_video_ready(music_id, kind, diff):
                set_chart_prepare_status('命中缓存')
                return True, 'cache', out, get_chart_manifest_entry(music_id, kind, diff)

            if not await chart_simai_exists(song_id, kind):
                last_err = f'CDN 无谱面（{song_id}/{kind}）'
                continue

            log.info(
                f'[GuessChart] 开始并行渲染 music={music_id} title={title!r} '
                f'song={song_id} kind={kind} diff={diff} '
                f'duration={duration}s+{PHASE2_DURATION}s '
                f'workers={RENDER_WORKERS}'
            )
            started = time.time()
            set_chart_prepare_status(
                f'并行录制静音谱面与曲末 BGM（约 {max(duration, PHASE2_DURATION)}s）…'
            )
            mute_task = asyncio.create_task(
                _render_chart_video(
                    song_id=song_id,
                    kind=kind,
                    diff=diff,
                    out_mp4=out,
                    duration=duration,
                )
            )
            bgm_task = asyncio.create_task(
                _render_chart_video(
                    song_id=song_id,
                    kind=kind,
                    diff=diff,
                    out_mp4=out_bgm,
                    duration=PHASE2_DURATION,
                    tail=PHASE2_DURATION,
                    music_id=str(music_id),
                    mix_bgm=True,
                )
            )
            mute_res, bgm_res = await asyncio.gather(
                mute_task, bgm_task, return_exceptions=True,
            )
            if not isinstance(mute_res, dict):
                last_err = str(mute_res)
                log.warning(f'[GuessChart] 渲染失败 music={music_id} kind={kind}: {mute_res}')
                if isinstance(bgm_res, dict) and out_bgm.is_file():
                    out_bgm.unlink(missing_ok=True)
                continue

            meta = mute_res
            meta_bgm: Optional[dict] = None
            bgm_ok: Optional[Path] = None
            if not isinstance(bgm_res, dict):
                log.warning(f'[GuessChart] BGM 渲染失败 music={music_id}: {bgm_res}')
            else:
                meta_bgm = bgm_res
                if out_bgm.is_file():
                    bgm_ok = out_bgm

            elapsed = int(time.time() - started)
            entry = _entry_with_paths(
                music_id, kind, diff,
                song_id=song_id,
                title=title,
                duration=duration,
                out=out,
                out_bgm=bgm_ok,
                meta=meta,
                meta_bgm=meta_bgm,
                elapsed=elapsed,
            )
            manifest = _load_manifest()
            manifest.setdefault('entries', {})[key] = entry
            _save_manifest(manifest)
            set_chart_prepare_status('渲染完成')
            log.info(
                f'[GuessChart] 渲染完成 music={music_id} size={entry["size"]} '
                f'bgm={entry["has_bgm"]} elapsed={elapsed}s '
                f'(parallel mute+bgm)'
            )
            return True, 'built', out, entry

    set_chart_prepare_status(last_err or '无可用谱面')
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


async def build_hot_chart_cache(
    *,
    force: bool = False,
    limit: Optional[int] = None,
) -> str:
    """烘焙热门池猜铺面视频（含曲末 BGM）。limit 限制本次新建/重建数量。"""
    from .maimaidx_music import guess, mai

    _reset_chart_batch_cancel()
    if not mai.total_list:
        return '曲库未加载，请等待 bot 初始化完成后再试。'
    pool = guess._guess_music_pool()
    if not pool:
        return '热门池为空，无法烘焙。'

    build_limit = DEFAULT_CHART_BATCH_LIMIT if limit is None else max(1, int(limit))
    if force and limit is None:
        build_limit = len(pool)

    ok_ids: List[str] = []
    skip_ids: List[str] = []
    fail_lines: List[str] = []
    cancelled = False
    t0 = time.time()

    # 先收集待建列表，再并发处理
    todo: List = []
    for music in pool:
        mid = str(music.id)
        kind = chart_kind(music.type)
        diff = pick_chart_diff(len(music.ds))
        alt = 'standard' if kind == 'dx' else 'dx'
        if not force and (
            is_chart_round_ready(mid, kind, diff) or is_chart_round_ready(mid, alt, diff)
        ):
            skip_ids.append(mid)
            continue
        todo.append(music)
        if len(todo) >= build_limit:
            break

    song_sem = asyncio.Semaphore(BATCH_SONG_WORKERS)
    done_count = 0
    done_lock = asyncio.Lock()

    set_chart_prepare_status(
        f'热门池预制开始（待建 {len(todo)}，并发曲目 {BATCH_SONG_WORKERS}，'
        f'录制槽 {RENDER_WORKERS}）…'
    )
    log.info(
        f'[GuessChart] 热门池并行预制 todo={len(todo)} skip={len(skip_ids)} '
        f'batch_songs={BATCH_SONG_WORKERS} render_workers={RENDER_WORKERS} '
        f'ffmpeg_threads={FFMPEG_THREADS} cpu={_cpu_count()}'
    )

    async def _build_one(music, idx: int) -> Tuple[str, bool, str, dict]:
        nonlocal cancelled, done_count
        mid = str(music.id)
        kind = chart_kind(music.type)
        diff = pick_chart_diff(len(music.ds))
        alt = 'standard' if kind == 'dx' else 'dx'
        async with song_sem:
            if _batch_cancel.is_set():
                cancelled = True
                return mid, False, '烘焙任务已取消', {}
            async with done_lock:
                set_chart_prepare_status(
                    f'预制并发中 {done_count}/{len(todo)} '
                    f'（槽位 {BATCH_SONG_WORKERS} 曲 × 录制 {RENDER_WORKERS}）…'
                )
            log.info(
                f'[GuessChart] 热门池预制 [{idx}/{len(todo)}] {mid} {music.title} '
                f'force={force}'
            )
            try:
                if force:
                    for k in (kind, alt):
                        for p in (
                            video_path_for(mid, k, diff),
                            bgm_video_path_for(mid, k, diff),
                        ):
                            if p.is_file():
                                p.unlink()
                ok, msg, _path, entry = await ensure_chart_video_ready(
                    mid,
                    music_type=music.type,
                    title=music.title,
                    level_count=len(music.ds),
                )
            except asyncio.CancelledError:
                request_chart_batch_cancel()
                cancelled = True
                raise
            except Exception as e:
                ok, msg, entry = False, str(e), {}
            async with done_lock:
                done_count += 1
            return mid, ok, msg, entry if isinstance(entry, dict) else {}

    results = await asyncio.gather(
        *[_build_one(m, i) for i, m in enumerate(todo, 1)],
        return_exceptions=True,
    )
    for item, music in zip(results, todo):
        if isinstance(item, BaseException):
            if isinstance(item, asyncio.CancelledError):
                cancelled = True
            fail_lines.append(f'{music.id} {music.title}: {item}')
            continue
        mid, ok, msg, entry = item
        if ok:
            ok_ids.append(mid)
            bgm = '有BGM' if entry.get('has_bgm') else '无BGM'
            log.info(f'[GuessChart] 预制成功 {mid} {msg} {bgm}')
        elif msg == '烘焙任务已取消':
            cancelled = True
        else:
            fail_lines.append(f'{mid} {music.title}: {msg}')
            log.warning(f'[GuessChart] 预制失败 {mid}: {msg}')

    elapsed = int(time.time() - t0)
    set_chart_prepare_status('预制结束')
    lines = [
        f'猜铺面热门池预制完成（rev={CHART_VIDEO_REV}）',
        f'扫描 {len(pool)} 首，本次新建/重建 {len(ok_ids)}，跳过 {len(skip_ids)}，'
        f'失败 {len(fail_lines)}，耗时 {elapsed}s',
        f'并发：曲目×{BATCH_SONG_WORKERS} / 录制槽×{RENDER_WORKERS} / '
        f'ffmpeg线程×{FFMPEG_THREADS}（CPU {_cpu_count()}）',
        f'上限 {build_limit}；增量默认每次最多 {DEFAULT_CHART_BATCH_LIMIT} 首，'
        f'可用「更新猜铺面 50」或「更新猜铺面 -full」调整。',
    ]
    if cancelled:
        lines.append('（任务已取消）')
    if ok_ids[:15]:
        lines.append('成功：' + ', '.join(ok_ids[:15]) + ('…' if len(ok_ids) > 15 else ''))
    if fail_lines[:8]:
        lines.append('失败示例：')
        lines.extend(fail_lines[:8])
    return '\n'.join(lines)
