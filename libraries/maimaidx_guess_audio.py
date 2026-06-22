"""猜曲子：从 Lxns CDN 拉取试听、分轨混音并缓存阶段音频。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx
from loguru import logger as log

_PKG_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CDN_BASE = 'https://assets2.lxns.net/maimai/music'
STAGE_COUNT = 4
STAGE_DURATION = 30
STAGE_INTERVAL = 30
# 分轨前先从原曲裁一段，降低 CPU/内存压力（整首 demucs 易 OOM）
SEPARATION_CLIP_DURATION = STAGE_DURATION + 15
# htdemucs 有效 segment 上限约 7.8s，CLI 只接受整数故用 7
DEMUCS_SEGMENT = 7
STAGE_LABELS = ('仅鼓点', '鼓点 + 贝斯', '加入伴奏', '完整混音')
# demucs 四阶段分别混入的轨（第 4 阶段为全轨含人声，不再用原曲片段）
DEMUCS_STAGE_STEMS: Tuple[Tuple[str, ...], ...] = (
    ('drums',),
    ('drums', 'bass'),
    ('drums', 'bass', 'other'),
    ('drums', 'bass', 'other', 'vocals'),
)

AUDIO_GUESS_DIR = _PKG_ROOT / 'data' / 'audio_guess'
AUDIO_GUESS_CACHE_DIR = AUDIO_GUESS_DIR / 'cache'
AUDIO_GUESS_MANIFEST = AUDIO_GUESS_DIR / 'manifest.json'

_BUILD_LOCKS: Dict[str, asyncio.Lock] = {}
_active_subprocess: Optional[subprocess.Popen] = None
_batch_cancel = threading.Event()
_shutdown_hook_registered = False


class GuessAudioCancelled(RuntimeError):
    """猜曲音频烘焙被用户或 bot 关闭中断。"""


def request_hot_batch_cancel() -> None:
    _batch_cancel.set()
    cancel_active_subprocess()


def _reset_hot_batch_cancel() -> None:
    _batch_cancel.clear()


def cancel_active_subprocess() -> None:
    global _active_subprocess
    proc = _active_subprocess
    if proc is None or proc.poll() is not None:
        return
    log.warning('[GuessAudio] 终止进行中的子进程…')
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _ensure_shutdown_hook() -> None:
    global _shutdown_hook_registered
    if _shutdown_hook_registered:
        return
    try:
        from nonebot import get_driver

        @get_driver().on_shutdown
        async def _on_guess_audio_shutdown() -> None:
            if _batch_cancel.is_set() or _active_subprocess is not None:
                log.info('[GuessAudio] bot 关闭，停止猜曲烘焙子进程')
            request_hot_batch_cancel()

        _shutdown_hook_registered = True
    except Exception:
        pass


def _cdn_base() -> str:
    try:
        from ..config import maiconfig
        return getattr(maiconfig, 'maimaidx_audio_cdn_base', None) or DEFAULT_CDN_BASE
    except Exception:
        return DEFAULT_CDN_BASE


def _lock_for(music_id: str) -> asyncio.Lock:
    if music_id not in _BUILD_LOCKS:
        _BUILD_LOCKS[music_id] = asyncio.Lock()
    return _BUILD_LOCKS[music_id]


def cdn_url_candidates(music_id: str) -> List[str]:
    """优先使用曲库新 ID，再尝试常见 CDN 回落 ID。"""
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
        return [f'{_cdn_base()}/{ordered[0]}.mp3'] if ordered else []

    if n >= 10000:
        add(str(n - 10000))
    if n >= 11000:
        add(str(n - 11000))
    sid = str(music_id)
    if sid.startswith('1') and len(sid) > 1:
        add(sid[1:])
    return [f'{_cdn_base()}/{sid}.mp3' for sid in ordered]


def _song_cache_dir(music_id: str) -> Path:
    return AUDIO_GUESS_CACHE_DIR / str(music_id)


def _stage_path(music_id: str, stage: int) -> Path:
    return _song_cache_dir(music_id) / f'stage_{stage:02d}.mp3'


def _load_manifest() -> Dict[str, dict]:
    if not AUDIO_GUESS_MANIFEST.exists():
        return {}
    try:
        return json.loads(AUDIO_GUESS_MANIFEST.read_text(encoding='utf-8'))
    except Exception as e:
        log.warning(f'[GuessAudio] manifest 读取失败: {e}')
        return {}


def _save_manifest(data: Dict[str, dict]) -> None:
    AUDIO_GUESS_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_GUESS_MANIFEST.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def is_audio_ready(music_id: str) -> bool:
    mid = str(music_id)
    manifest = _load_manifest()
    entry = manifest.get(mid)
    if not entry or not entry.get('ready'):
        return False
    stages = int(entry.get('stages', STAGE_COUNT))
    return all(_stage_path(mid, i).is_file() for i in range(1, stages + 1))


def list_stage_files(music_id: str) -> List[Path]:
    mid = str(music_id)
    manifest = _load_manifest()
    stages = int((manifest.get(mid) or {}).get('stages', STAGE_COUNT))
    return [_stage_path(mid, i) for i in range(1, stages + 1)]


def _run(cmd: List[str], *, timeout: int = 600) -> None:
    global _active_subprocess
    if _batch_cancel.is_set():
        raise GuessAudioCancelled('烘焙任务已取消')
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _active_subprocess = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        if _batch_cancel.is_set():
            raise GuessAudioCancelled('烘焙任务已取消')
        if proc.returncode != 0:
            detail = (stderr or stdout or '').strip()
            tail = detail[-2000:] if detail else '(无 stderr/stdout)'
            log.error(f'[GuessAudio] 命令失败: {" ".join(cmd)}\n{tail}')
            raise RuntimeError(f'exit {proc.returncode}: {tail}')
    except subprocess.TimeoutExpired as e:
        proc.kill()
        proc.communicate()
        log.error(f'[GuessAudio] 命令超时 ({timeout}s): {" ".join(cmd)}')
        raise RuntimeError(f'超时 ({timeout}s)') from e
    finally:
        if _active_subprocess is proc:
            _active_subprocess = None


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
        ],
        timeout=30,
    )
    return float(out.decode().strip())


def _pick_clip_offset(duration: float) -> float:
    if duration <= STAGE_DURATION + 2:
        return max(0.0, (duration - STAGE_DURATION) / 2)
    start = max(20.0, duration * 0.28)
    return min(start, max(0.0, duration - STAGE_DURATION - 3))


def get_audio_manifest_entry(music_id: str) -> dict:
    return _load_manifest().get(str(music_id), {})


def _file_digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _verify_stage_audio(music_id: str) -> None:
    paths = [_stage_path(music_id, i) for i in range(1, STAGE_COUNT + 1)]
    digests = [_file_digest(p) for p in paths]
    sizes = [p.stat().st_size for p in paths]
    log.info(
        f'[GuessAudio] 阶段校验 music_id={music_id} '
        f'sizes={sizes} digests={[d[:8] for d in digests]}'
    )
    if digests[0] == digests[-1]:
        raise RuntimeError('阶段 1 与阶段 4 音频相同，分轨无效')
    if len(set(digests)) < 2:
        raise RuntimeError('各阶段音频完全相同，分轨无效')


def _export_stem_mix(
    inputs: List[Path],
    output: Path,
    *,
    duration: int = STAGE_DURATION,
) -> None:
    """将 demucs 分轨按阶段混音并裁切为固定时长。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    n = len(inputs)
    chains = [
        f'[{i}:a]atrim=start=0:end={duration},asetpts=PTS-STARTPTS[a{i}]'
        for i in range(n)
    ]
    mix_inputs = ''.join(f'[a{i}]' for i in range(n))
    filt = (
        ';'.join(chains)
        + f';{mix_inputs}amix=inputs={n}:duration=longest:dropout_transition=0[out]'
    )
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error']
    for p in inputs:
        cmd += ['-i', str(p)]
    cmd += [
        '-filter_complex', filt,
        '-map', '[out]',
        '-ac', '2', '-ar', '44100', '-b:a', '128k',
        str(output),
    ]
    _run(cmd)


def _export_clip(
    inputs: List[Path],
    output: Path,
    *,
    offset: float,
    filters: Optional[str] = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(inputs) == 1 and filters:
        _run([
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-ss', f'{offset:.3f}', '-t', str(STAGE_DURATION),
            '-i', str(inputs[0]),
            '-filter_complex', filters,
            '-map', '[out]',
            '-ac', '2', '-ar', '44100', '-b:a', '128k',
            str(output),
        ])
        return
    if len(inputs) == 1 and not filters:
        _run([
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-ss', f'{offset:.3f}', '-t', str(STAGE_DURATION),
            '-i', str(inputs[0]),
            '-ac', '2', '-ar', '44100', '-b:a', '128k',
            str(output),
        ])
        return

    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error']
    for p in inputs:
        cmd += ['-i', str(p)]
    if filters:
        cmd += ['-filter_complex', filters, '-map', '[out]']
    else:
        ins = ''.join(f'[{i}:a]' for i in range(len(inputs)))
        cmd += [
            '-filter_complex',
            f'{ins}amix=inputs={len(inputs)}:duration=longest:dropout_transition=0[out]',
            '-map', '[out]',
        ]
    cmd += [
        '-ss', f'{offset:.3f}', '-t', str(STAGE_DURATION),
        '-ac', '2', '-ar', '44100', '-b:a', '128k',
        str(output),
    ]
    _run(cmd)


def _download_source_sync(music_id: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for url in cdn_url_candidates(music_id):
            try:
                resp = client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                cdn_id = url.rsplit('/', 1)[-1].removesuffix('.mp3')
                size_kb = len(resp.content) // 1024
                log.info(
                    f'[GuessAudio] 下载完成 music_id={music_id} '
                    f'cdn_id={cdn_id} size={size_kb}KB url={url}'
                )
                return cdn_id
            except Exception as e:
                last_err = e
                log.debug(f'[GuessAudio] CDN 尝试失败 music_id={music_id} url={url}: {e}')
    raise RuntimeError(f'CDN 无可用音频 (music_id={music_id}): {last_err}')


def _demucs_available() -> bool:
    if not shutil.which('demucs'):
        return False
    try:
        import lameenc  # noqa: F401
    except ImportError:
        log.warning('[GuessAudio] demucs 已安装但缺少 lameenc，无法输出 mp3 分轨')
        return False
    return True


def _demucs_stem_paths(base: Path) -> Dict[str, Path]:
    """demucs 使用 --mp3 输出，避免 torchaudio 保存 wav 依赖 torchcodec。"""
    stems: Dict[str, Path] = {}
    for name in ('drums', 'bass', 'other', 'vocals'):
        for ext in ('.mp3', '.wav'):
            path = base / f'{name}{ext}'
            if path.is_file():
                stems[name] = path
                break
    return stems


def _extract_separation_clip(source: Path, clip: Path, offset: float) -> None:
    """从原曲截取短片段供 demucs 分轨，避免整首处理导致内存不足。"""
    log.info(
        f'[GuessAudio] 裁剪分轨片段 offset={offset:.2f}s '
        f'duration={SEPARATION_CLIP_DURATION}s -> {clip.name}'
    )
    clip.parent.mkdir(parents=True, exist_ok=True)
    _run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', f'{offset:.3f}', '-t', str(SEPARATION_CLIP_DURATION),
        '-i', str(source),
        '-ac', '2', '-ar', '44100',
        str(clip),
    ])


def _demucs_device() -> str:
    try:
        from ..config import maiconfig
        return getattr(maiconfig, 'maimaidx_demucs_device', None) or 'cpu'
    except Exception:
        return 'cpu'


def _separate_demucs(clip: Path, work_dir: Path) -> Dict[str, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    device = _demucs_device()
    log.info(
        f'[GuessAudio] demucs 开始 model=htdemucs device={device} '
        f'segment={DEMUCS_SEGMENT} output=mp3 input={clip.name}'
    )
    t0 = time.perf_counter()
    cmd = [
        'demucs',
        '-n', 'htdemucs',
        '-d', device,
        '--segment', str(DEMUCS_SEGMENT),
        '--mp3',
        '-o', str(work_dir),
        str(clip),
    ]
    _run(cmd, timeout=900)
    base = work_dir / 'htdemucs' / clip.stem
    stems = _demucs_stem_paths(base)
    missing = [k for k in ('drums', 'bass', 'other', 'vocals') if k not in stems]
    if missing:
        raise RuntimeError(f'Demucs 分轨不完整: {missing} (目录 {base})')
    elapsed = time.perf_counter() - t0
    log.info(f'[GuessAudio] demucs 完成 elapsed={elapsed:.1f}s output={base}')
    return stems


def _build_stages_demucs(source: Path, music_id: str, offset: float) -> None:
    log.info(f'[GuessAudio] demucs 分轨流程开始 music_id={music_id}')
    t0 = time.perf_counter()
    work_dir = _song_cache_dir(music_id) / '_work'
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    clip = work_dir / 'separation_clip.wav'
    _extract_separation_clip(source, clip, offset)
    stems = _separate_demucs(clip, work_dir)
    for stage_idx, names in enumerate(DEMUCS_STAGE_STEMS, 1):
        _export_stem_mix(
            [stems[name] for name in names],
            _stage_path(music_id, stage_idx),
        )
    _verify_stage_audio(music_id)
    shutil.rmtree(work_dir, ignore_errors=True)
    log.info(
        f'[GuessAudio] demucs 分轨流程完成 music_id={music_id} '
        f'elapsed={time.perf_counter() - t0:.1f}s'
    )


def _build_stages_ffmpeg(source: Path, music_id: str, offset: float) -> None:
    """无 Demucs 时用 EQ / 伴奏提取近似分轨（效果弱于 AI 分轨）。"""
    log.info(f'[GuessAudio] ffmpeg 近似分轨开始 music_id={music_id} offset={offset:.2f}s')
    t0 = time.perf_counter()
    s1 = _stage_path(music_id, 1)
    _export_clip(
        [source], s1, offset=offset,
        filters=(
            '[0:a]highpass=f=250,lowpass=f=2200,'
            'volume=2.5,alimiter=limit=0.95[out]'
        ),
    )
    s2 = _stage_path(music_id, 2)
    _export_clip(
        [source], s2, offset=offset,
        filters=(
            f'[0:a]lowpass=f=280,highpass=f=40,volume=1.6[dr];'
            f'[0:a]highpass=f=180,lowpass=f=3500,volume=1.4[hh];'
            f'[dr][hh]amix=inputs=2:duration=longest:dropout_transition=0,'
            f'alimiter=limit=0.95[out]'
        ),
    )
    s3 = _stage_path(music_id, 3)
    _export_clip(
        [source], s3, offset=offset,
        filters=(
            f'[0:a]pan=stereo|c0=c0-0.35*c1|c1=c1-0.35*c0,'
            f'highpass=f=80,alimiter=limit=0.95[out]'
        ),
    )
    _export_clip([source], _stage_path(music_id, 4), offset=offset)
    _verify_stage_audio(music_id)
    log.info(
        f'[GuessAudio] ffmpeg 近似分轨完成 music_id={music_id} '
        f'elapsed={time.perf_counter() - t0:.1f}s'
    )


def build_audio_cache_sync(
    music_id: str,
    *,
    title: str = '',
    force: bool = False,
) -> Tuple[bool, str]:
    """同步构建单首曲目的阶段音频缓存。供脚本或线程池调用。"""
    if shutil.which('ffmpeg') is None:
        return False, '服务器未安装 ffmpeg，无法处理音频'

    mid = str(music_id)
    if _batch_cancel.is_set():
        return False, '烘焙任务已取消'

    if not force and is_audio_ready(mid):
        log.debug(f'[GuessAudio] 跳过已缓存 music_id={mid}')
        return True, '已缓存'

    label = f' {title}' if title else ''
    log.info(f'[GuessAudio] 开始构建 music_id={mid}{label} force={force}')
    t0 = time.perf_counter()

    cache_dir = _song_cache_dir(mid)
    cache_dir.mkdir(parents=True, exist_ok=True)
    source = cache_dir / 'source.mp3'

    try:
        cdn_id = _download_source_sync(mid, source)
    except RuntimeError as e:
        log.warning(f'[GuessAudio] 下载失败 music_id={mid}: {e}')
        return False, str(e)

    try:
        duration = _probe_duration(source)
        offset = _pick_clip_offset(duration)
        log.info(
            f'[GuessAudio] 源文件就绪 music_id={mid} '
            f'duration={duration:.1f}s clip_offset={offset:.2f}s cdn_id={cdn_id}'
        )
        mode = 'ffmpeg'
        if _demucs_available():
            try:
                _build_stages_demucs(source, mid, offset)
                mode = 'demucs'
            except Exception as demucs_err:
                err_text = str(demucs_err)
                if 'torchcodec' in err_text.lower():
                    log.warning(
                        '[GuessAudio] demucs 保存分轨需要 torchcodec 或 lameenc；'
                        '请 pip install lameenc 后重试，当前将回退 ffmpeg'
                    )
                log.warning(
                    f'[GuessAudio] demucs 分轨失败 music_id={mid}，回退 ffmpeg: {demucs_err}'
                )
                shutil.rmtree(cache_dir / '_work', ignore_errors=True)
                for i in range(1, STAGE_COUNT + 1):
                    p = _stage_path(mid, i)
                    if p.exists():
                        p.unlink()
                _build_stages_ffmpeg(source, mid, offset)
                mode = 'ffmpeg_fallback'
        else:
            log.warning('[GuessAudio] 未检测到 demucs，使用 ffmpeg 近似分轨')
            _build_stages_ffmpeg(source, mid, offset)

        manifest = _load_manifest()
        manifest[mid] = {
            'ready': True,
            'stages': STAGE_COUNT,
            'title': title,
            'cdn_id': cdn_id,
            'mode': mode,
            'clip_offset': round(offset, 2),
        }
        _save_manifest(manifest)
        elapsed = time.perf_counter() - t0
        log.info(
            f'[GuessAudio] 构建成功 music_id={mid}{label} mode={mode} '
            f'elapsed={elapsed:.1f}s'
        )
        return True, f'已生成 {STAGE_COUNT} 段 × {STAGE_DURATION}s（{mode}）'
    except GuessAudioCancelled as e:
        log.warning(f'[GuessAudio] 构建取消 music_id={mid}{label}: {e}')
        shutil.rmtree(cache_dir, ignore_errors=True)
        manifest = _load_manifest()
        manifest.pop(mid, None)
        _save_manifest(manifest)
        return False, '烘焙任务已取消'
    except Exception as e:
        log.exception(
            f'[GuessAudio] 构建失败 music_id={mid}{label} '
            f'elapsed={time.perf_counter() - t0:.1f}s: {e}'
        )
        shutil.rmtree(cache_dir, ignore_errors=True)
        manifest = _load_manifest()
        manifest.pop(mid, None)
        _save_manifest(manifest)
        return False, f'分轨失败: {e}'


async def ensure_audio_ready(music_id: str, *, title: str = '') -> Tuple[bool, str]:
    if is_audio_ready(music_id):
        return True, 'ready'
    log.info(f'[GuessAudio] 懒加载构建 music_id={music_id} title={title or "-"}')
    async with _lock_for(str(music_id)):
        if is_audio_ready(music_id):
            return True, 'ready'
        ok, msg = await asyncio.to_thread(
            build_audio_cache_sync, str(music_id), title=title,
        )
        if ok:
            log.info(f'[GuessAudio] 懒加载完成 music_id={music_id}: {msg}')
        else:
            log.warning(f'[GuessAudio] 懒加载失败 music_id={music_id}: {msg}')
        return ok, msg


def _format_hot_batch_report(
    pool_size: int,
    ok_ids: List[str],
    skip_ids: List[str],
    fail_lines: List[str],
    *,
    cancelled: bool = False,
) -> str:
    lines = []
    if cancelled:
        lines.append('猜曲音频烘焙已取消（已完成部分如下）。')
    lines.extend([
        f'猜曲音频烘焙完成（热门池共 {pool_size} 首）。',
        f'新建/重建：{len(ok_ids)}',
        f'已跳过（有缓存）：{len(skip_ids)}',
        f'失败：{len(fail_lines)}',
    ])
    if fail_lines:
        preview = fail_lines[:8]
        lines.append('失败示例：')
        lines.extend(f'· {line}' for line in preview)
        if len(fail_lines) > 8:
            lines.append(f'… 另有 {len(fail_lines) - 8} 条')
    return '\n'.join(lines)


def _run_hot_batch_loop(
    pool,
    *,
    force: bool,
    build_one: Callable[[str, str], Tuple[bool, str]],
) -> Tuple[List[str], List[str], List[str], bool]:
    ok_ids: List[str] = []
    skip_ids: List[str] = []
    fail_lines: List[str] = []
    cancelled = False

    for idx, music in enumerate(pool, 1):
        if _batch_cancel.is_set():
            cancelled = True
            log.warning(f'[GuessAudio] 热门池烘焙取消于 {idx}/{len(pool)}')
            break
        mid = str(music.id)
        if not force and is_audio_ready(mid):
            skip_ids.append(mid)
            if idx % 50 == 0 or idx == len(pool):
                log.info(
                    f'[GuessAudio] 热门池进度 {idx}/{len(pool)} '
                    f'ok={len(ok_ids)} skip={len(skip_ids)} fail={len(fail_lines)}'
                )
            continue
        log.info(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 处理 {mid} {music.title}')
        ok, msg = build_one(mid, music.title)
        if ok:
            ok_ids.append(mid)
            log.info(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 成功 {mid}: {msg}')
        elif msg == '烘焙任务已取消':
            cancelled = True
            break
        else:
            fail_lines.append(f'{mid} {music.title}: {msg}')
            log.warning(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 失败 {mid}: {msg}')

    return ok_ids, skip_ids, fail_lines, cancelled


async def build_hot_audio_cache(*, force: bool = False) -> str:
    """异步烘焙热门池（逐首执行，支持 Ctrl+C / bot 关闭中断）。"""
    from .maimaidx_music import guess, mai

    _ensure_shutdown_hook()
    _reset_hot_batch_cancel()

    if not mai.total_list:
        return '曲库未加载，请等待 bot 初始化完成后再试。'
    pool = guess._guess_music_pool()
    if not pool:
        return '热门池为空，无法烘焙。'

    demucs_on = _demucs_available()
    log.info(
        f'[GuessAudio] 热门池烘焙开始 total={len(pool)} force={force} '
        f'demucs={"yes" if demucs_on else "no"} device={_demucs_device() if demucs_on else "-"}'
    )
    batch_t0 = time.perf_counter()

    async def _build_one(mid: str, title: str) -> Tuple[bool, str]:
        return await asyncio.to_thread(
            build_audio_cache_sync, mid, title=title, force=force,
        )

    ok_ids: List[str] = []
    skip_ids: List[str] = []
    fail_lines: List[str] = []
    cancelled = False

    for idx, music in enumerate(pool, 1):
        if _batch_cancel.is_set():
            cancelled = True
            log.warning(f'[GuessAudio] 热门池烘焙取消于 {idx}/{len(pool)}')
            break
        await asyncio.sleep(0)
        mid = str(music.id)
        if not force and is_audio_ready(mid):
            skip_ids.append(mid)
            if idx % 50 == 0 or idx == len(pool):
                log.info(
                    f'[GuessAudio] 热门池进度 {idx}/{len(pool)} '
                    f'ok={len(ok_ids)} skip={len(skip_ids)} fail={len(fail_lines)}'
                )
            continue
        log.info(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 处理 {mid} {music.title}')
        try:
            ok, msg = await _build_one(mid, music.title)
        except asyncio.CancelledError:
            request_hot_batch_cancel()
            cancelled = True
            log.warning(f'[GuessAudio] 热门池烘焙收到取消信号于 {idx}/{len(pool)}')
            break
        if ok:
            ok_ids.append(mid)
            log.info(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 成功 {mid}: {msg}')
        elif msg == '烘焙任务已取消':
            cancelled = True
            break
        else:
            fail_lines.append(f'{mid} {music.title}: {msg}')
            log.warning(f'[GuessAudio] 热门池 [{idx}/{len(pool)}] 失败 {mid}: {msg}')

    elapsed = time.perf_counter() - batch_t0
    log.info(
        f'[GuessAudio] 热门池烘焙结束 total={len(pool)} '
        f'ok={len(ok_ids)} skip={len(skip_ids)} fail={len(fail_lines)} '
        f'cancelled={cancelled} elapsed={elapsed:.1f}s'
    )
    return _format_hot_batch_report(
        len(pool), ok_ids, skip_ids, fail_lines, cancelled=cancelled,
    )


def build_hot_audio_cache_sync(*, force: bool = False) -> str:
    """同步烘焙热门池（供脚本调用）。"""
    from .maimaidx_music import guess, mai

    _reset_hot_batch_cancel()

    if not mai.total_list:
        return '曲库未加载，请等待 bot 初始化完成后再试。'
    pool = guess._guess_music_pool()
    if not pool:
        return '热门池为空，无法烘焙。'

    demucs_on = _demucs_available()
    log.info(
        f'[GuessAudio] 热门池烘焙开始 total={len(pool)} force={force} '
        f'demucs={"yes" if demucs_on else "no"} device={_demucs_device() if demucs_on else "-"}'
    )
    batch_t0 = time.perf_counter()

    ok_ids, skip_ids, fail_lines, cancelled = _run_hot_batch_loop(
        pool,
        force=force,
        build_one=lambda mid, title: build_audio_cache_sync(
            mid, title=title, force=force,
        ),
    )

    elapsed = time.perf_counter() - batch_t0
    log.info(
        f'[GuessAudio] 热门池烘焙结束 total={len(pool)} '
        f'ok={len(ok_ids)} skip={len(skip_ids)} fail={len(fail_lines)} '
        f'cancelled={cancelled} elapsed={elapsed:.1f}s'
    )
    return _format_hot_batch_report(
        len(pool), ok_ids, skip_ids, fail_lines, cancelled=cancelled,
    )
