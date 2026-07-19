/**
 * 猜铺面录制页：仅渲染谱面动画，不加载背景 PV / UI。
 * withAudio=1 时用 HTMLAudioElement.captureStream 与画面同轨录制 BGM（音画同步）；
 * 否则只录画面，正解音时间轴由 hitOffsetsMs 交给后端混音。
 */
import { useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChartCanvas } from './chart/components/ChartCanvas';
import { getAvailableDifficulties, parseSimaiChart } from './chart/core/parser/ChartParser';
import { useGameSettingsStore } from './chart/stores/useGameSettingsStore';
import { playbackTimeRef, useGameStore } from './chart/stores/useGameStore';
import type { Chart, ChartDifficulty, Note } from './chart/types';
import { ANSWER_SOUND_BASE_OFFSET_MS } from './chart/utils/constants';
import {
  chartFileIdForSong,
  fetchSimaiText,
  musicMp3UrlForChartFileId,
  type ChartKind,
} from './lxns/chartResolve';
import { parsePreviewUrlParams } from './previewUrlParams';

/** 与 useMusicPlayer 一致：谱面时间轴前有 4 拍静音导入 */
const LEAD_IN_BEATS = 4;

type GuessState = 'loading' | 'ready' | 'playing' | 'done' | 'error';

type GuessBridge = {
  state: GuessState;
  error: string | null;
  durationSec: number;
  startSec: number;
  /** 原曲文件内应对齐的起点（秒），已扣除 lead-in */
  musicStartSec: number;
  songId: number | null;
  kind: ChartKind | null;
  diff: ChartDifficulty | null;
  /** 相对播放起点的正解音时间（毫秒） */
  hitOffsetsMs: number[];
  /** 录制开始 → play() 的前置空白（毫秒），用于音画对齐 */
  recordLeadMs: number;
  /** 是否已在 webm 内嵌 BGM（无需后端再叠） */
  hasEmbeddedAudio: boolean;
  /** MediaRecorder 产出的 webm（base64，无 data: 前缀） */
  videoBase64: string | null;
  videoMime: string | null;
};

declare global {
  interface Window {
    __GUESS_CHART__: GuessBridge;
  }
}

type BpmEvent = { timing: number; bpm: number };

function msToBeats(ms: number, bpmEvents: BpmEvent[] | null, defaultBpm: number): number {
  if (!bpmEvents || bpmEvents.length === 0) {
    return (ms * defaultBpm) / 60000;
  }

  let remainingMs = ms;
  let totalBeats = 0;
  let lastBeat = 0;
  let currentBpm = bpmEvents[0].bpm;

  for (const event of bpmEvents) {
    const segmentBeats = event.timing - lastBeat;
    const segmentMs = (60000 * segmentBeats) / currentBpm;

    if (remainingMs <= segmentMs) {
      totalBeats += (remainingMs * currentBpm) / 60000;
      return totalBeats;
    }

    remainingMs -= segmentMs;
    totalBeats += segmentBeats;
    lastBeat = event.timing;
    currentBpm = event.bpm;
  }

  totalBeats += (remainingMs * currentBpm) / 60000;
  return totalBeats;
}

function beatsToMs(beats: number, bpmEvents: BpmEvent[] | null, defaultBpm: number): number {
  if (!bpmEvents || bpmEvents.length === 0) {
    return (60000 * beats) / defaultBpm;
  }

  let totalMs = 0;
  let lastBeat = 0;
  let currentBpm = bpmEvents[0].bpm;

  for (const event of bpmEvents) {
    if (event.timing >= beats) break;
    totalMs += (60000 * (event.timing - lastBeat)) / currentBpm;
    lastBeat = event.timing;
    currentBpm = event.bpm;
  }

  return totalMs + (60000 * (beats - lastBeat)) / currentBpm;
}

function parsePositiveFloat(raw: string | null, fallback: number): number {
  if (raw == null || raw === '') return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

function shouldPlayAnswerSound(note: Note): boolean {
  switch (note.type) {
    case 'tap':
    case 'break':
    case 'simultaneous':
    case 'hold-start':
    case 'hold-start-simultaneous':
    case 'slide':
    case 'touch':
    case 'touch-hold-start':
    case 'touch-hold-end':
    case 'hold-end':
    case 'hold-end-simultaneous':
      return true;
    default:
      return false;
  }
}

/** 收集录制窗口内的正解音相对偏移（与 AudioManager 调度一致） */
function collectHitOffsetsMs(chart: Chart, startMs: number, durationMs: number): number[] {
  const endMs = startMs + durationMs;
  const seen = new Set<string>();
  const hits: number[] = [];
  for (let i = 0; i < chart.notes.length; i++) {
    const note = chart.notes[i];
    if (!shouldPlayAnswerSound(note)) continue;
    // 同刻多押只响一次；用 index 区分极端重合以外的 note
    const key = `${note.type}:${note.timingMs.toFixed(3)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    // timingOffset=-50 → 正解音略早于判定时刻
    const absMs = note.timingMs + ANSWER_SOUND_BASE_OFFSET_MS;
    if (absMs < startMs - 1 || absMs > endMs + 1) continue;
    hits.push(Math.max(0, Math.min(durationMs, absMs - startMs)));
  }
  hits.sort((a, b) => a - b);
  return hits;
}

function pickRecorderMime(): string {
  const types = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
  for (const t of types) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const idx = dataUrl.indexOf(',');
      resolve(idx >= 0 ? dataUrl.slice(idx + 1) : '');
    };
    reader.onerror = () => reject(reader.error || new Error('FileReader failed'));
    reader.readAsDataURL(blob);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function ensureBridge(): GuessBridge {
  if (!window.__GUESS_CHART__) {
    window.__GUESS_CHART__ = {
      state: 'loading',
      error: null,
      durationSec: 40,
      startSec: 0,
      songId: null,
      kind: null,
      diff: null,
      hitOffsetsMs: [],
      recordLeadMs: 0,
      musicStartSec: 0,
      hasEmbeddedAudio: false,
      videoBase64: null,
      videoMime: null,
    };
  }
  return window.__GUESS_CHART__;
}

function loadAudioElement(url: string, timeoutMs = 12000): Promise<HTMLAudioElement> {
  return new Promise((resolve, reject) => {
    const audio = new Audio();
    audio.crossOrigin = 'anonymous';
    audio.preload = 'auto';
    let settled = false;
    const timer = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error('audio load timeout'));
    }, timeoutMs);
    const onReady = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(audio);
    };
    const onErr = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error('audio load failed'));
    };
    const cleanup = () => {
      window.clearTimeout(timer);
      audio.removeEventListener('canplaythrough', onReady);
      audio.removeEventListener('error', onErr);
    };
    audio.addEventListener('canplaythrough', onReady, { once: true });
    audio.addEventListener('error', onErr, { once: true });
    audio.src = url;
    audio.load();
  });
}

function setBridge(patch: Partial<GuessBridge>) {
  const bridge = ensureBridge();
  Object.assign(bridge, patch);
}

export default function RecordPage() {
  const [searchParams] = useSearchParams();
  const startedRef = useRef(false);

  const params = useMemo(() => {
    const { songId, kind, diff } = parsePreviewUrlParams(searchParams);
    const durationSec = Math.min(120, Math.max(5, parsePositiveFloat(searchParams.get('duration'), 40)));
    const startSec = parsePositiveFloat(searchParams.get('start'), -1);
    // tail>0：从曲末往前截取（秒），覆盖随机 start
    const tailSec = Math.min(120, parsePositiveFloat(searchParams.get('tail'), 0));
    const withAudio = searchParams.get('withAudio') === '1' || searchParams.get('audio') === '1';
    const hiSpeed = Math.min(9, Math.max(3, parsePositiveFloat(searchParams.get('hispeed'), 6)));
    return { songId, kind, diff, durationSec, startSec, tailSec, withAudio, hiSpeed };
  }, [searchParams]);

  const reset = useGameStore((s) => s.reset);
  const setRawSimaiText = useGameStore((s) => s.setRawSimaiText);
  const setMusicUrl = useGameStore((s) => s.setMusicUrl);
  const setChartData = useGameStore((s) => s.setChartData);
  const setAvailableDifficulties = useGameStore((s) => s.setAvailableDifficulties);
  const setSelectedDifficulty = useGameStore((s) => s.setSelectedDifficulty);
  const setPreciseTime = useGameStore((s) => s.setPreciseTime);
  const play = useGameStore((s) => s.play);
  const pause = useGameStore((s) => s.pause);
  const chartData = useGameStore((s) => s.chartData);

  const setSoundEnabled = useGameSettingsStore((s) => s.setSoundEnabled);
  const setMusicVolume = useGameSettingsStore((s) => s.setMusicVolume);
  const setHiSpeed = useGameSettingsStore((s) => s.setHiSpeed);

  useEffect(() => {
    ensureBridge();
    setBridge({
      state: 'loading',
      error: null,
      durationSec: params.durationSec,
      startSec: Math.max(0, params.startSec),
      songId: params.songId,
      kind: params.kind,
      diff: params.diff,
      hitOffsetsMs: [],
      recordLeadMs: 0,
      musicStartSec: 0,
      hasEmbeddedAudio: false,
      videoBase64: null,
      videoMime: null,
    });
    setSoundEnabled(false);
    setMusicVolume(0);
    setMusicUrl('');
    setHiSpeed(params.hiSpeed);
  }, [
    params.durationSec,
    params.startSec,
    params.tailSec,
    params.withAudio,
    params.songId,
    params.kind,
    params.diff,
    params.hiSpeed,
    setSoundEnabled,
    setMusicVolume,
    setMusicUrl,
    setHiSpeed,
  ]);

  useEffect(() => {
    startedRef.current = false;
    let cancelled = false;

    (async () => {
      if (params.songId == null || params.kind == null || params.diff == null) {
        setBridge({ state: 'error', error: 'missing song/kind/diff' });
        return;
      }

      reset();
      setMusicUrl('');
      setSoundEnabled(false);
      setMusicVolume(0);

      try {
        const chartFileId = chartFileIdForSong(params.songId, params.kind);
        const simai = await fetchSimaiText(chartFileId);
        if (cancelled) return;
        if (!simai) {
          setBridge({ state: 'error', error: `chart not found: ${chartFileId}` });
          return;
        }

        setRawSimaiText(simai);
        const available = getAvailableDifficulties(simai);
        setAvailableDifficulties(available);

        let diffToUse = params.diff;
        if (!available[diffToUse]) {
          const availableList = Object.keys(available)
            .map(Number)
            .sort((a, b) => b - a) as ChartDifficulty[];
          diffToUse = (availableList[0] ?? diffToUse) as ChartDifficulty;
        }

        setSelectedDifficulty(diffToUse);
        const chart = parseSimaiChart(simai, diffToUse);
        if (cancelled) return;
        setChartData(chart);

        const bpmEvents = chart.bpmEvents ?? null;
        const bpm = chart.bpm || 120;
        const totalBeats = (chart.measures || 1) * 4;
        const totalMs = beatsToMs(totalBeats, bpmEvents, bpm);
        const playMs = params.durationSec * 1000;
        const maxStart = Math.max(0, totalMs - playMs - 500);
        let startMs = params.startSec;
        if (params.tailSec > 0) {
          const tailMs = Math.min(playMs, Math.max(0, totalMs));
          startMs = Math.max(0, totalMs - tailMs);
        } else if (startMs < 0) {
          startMs = maxStart > 0 ? Math.floor(Math.random() * maxStart) : 0;
        } else {
          startMs = Math.min(Math.max(0, startMs * 1000), maxStart);
        }

        const startBeats = msToBeats(startMs, bpmEvents, bpm);
        playbackTimeRef.current = startBeats;
        setPreciseTime(startBeats, true);
        const hitOffsetsMs = collectHitOffsetsMs(chart, startMs, playMs);
        // 与预览器一致：musicTime = chartMs - leadIn
        const leadInMs = (60000 * LEAD_IN_BEATS) / bpm;
        const musicStartSec = Math.max(0, (startMs - leadInMs) / 1000);
        setBridge({
          state: 'ready',
          startSec: startMs / 1000,
          musicStartSec,
          durationSec: params.durationSec,
          diff: diffToUse,
          error: null,
          hitOffsetsMs,
          recordLeadMs: 0,
          hasEmbeddedAudio: false,
          videoBase64: null,
          videoMime: null,
        });
      } catch (e) {
        console.error(e);
        if (!cancelled) {
          setBridge({
            state: 'error',
            error: e instanceof Error ? e.message : 'parse failed',
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    params.songId,
    params.kind,
    params.diff,
    params.durationSec,
    params.startSec,
    params.tailSec,
    reset,
    setMusicUrl,
    setSoundEnabled,
    setMusicVolume,
    setRawSimaiText,
    setAvailableDifficulties,
    setSelectedDifficulty,
    setChartData,
    setPreciseTime,
  ]);

  useEffect(() => {
    if (!chartData || startedRef.current) return;
    if (window.__GUESS_CHART__?.state !== 'ready') return;

    startedRef.current = true;
    let cancelled = false;
    let captureAudio: HTMLAudioElement | null = null;

    (async () => {
      // 等 canvas 首绘稳定
      await sleep(400);
      if (cancelled) return;

      const canvas = document.querySelector(
        '[data-guess-chart-canvas] canvas'
      ) as HTMLCanvasElement | null;
      if (!canvas) {
        setBridge({ state: 'error', error: 'canvas not found' });
        return;
      }
      if (typeof MediaRecorder === 'undefined') {
        setBridge({ state: 'error', error: 'MediaRecorder unavailable' });
        return;
      }

      const musicStartSec = window.__GUESS_CHART__?.musicStartSec ?? 0;
      let hasEmbeddedAudio = false;
      // 30fps：高负载下比 60fps 更不易掉帧，后段 ffmpeg 再统一 CFR
      const tracks: MediaStreamTrack[] = [...canvas.captureStream(30).getVideoTracks()];

      if (params.withAudio && params.songId != null && params.kind != null) {
        try {
          const chartFileId = chartFileIdForSong(params.songId, params.kind);
          const musicUrl = musicMp3UrlForChartFileId(chartFileId);
          captureAudio = await loadAudioElement(musicUrl);
          if (cancelled) return;
          captureAudio.currentTime = musicStartSec;
          const aStream =
            typeof (captureAudio as HTMLAudioElement & { captureStream?: () => MediaStream })
              .captureStream === 'function'
              ? (captureAudio as HTMLAudioElement & { captureStream: () => MediaStream }).captureStream()
              : null;
          if (aStream?.getAudioTracks().length) {
            tracks.push(...aStream.getAudioTracks());
            hasEmbeddedAudio = true;
          }
        } catch (e) {
          console.warn('[RecordPage] embedded BGM unavailable, fallback mux', e);
        }
      }

      const stream = new MediaStream(tracks);
      const mime = pickRecorderMime();
      const recorder = mime
        ? new MediaRecorder(stream, {
            mimeType: mime,
            videoBitsPerSecond: 8_000_000,
            audioBitsPerSecond: 192_000,
          })
        : new MediaRecorder(stream, {
            videoBitsPerSecond: 8_000_000,
            audioBitsPerSecond: 192_000,
          });
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };

      const stopped = new Promise<Blob>((resolve, reject) => {
        recorder.onerror = () => reject(new Error('MediaRecorder error'));
        recorder.onstop = () => {
          resolve(new Blob(chunks, { type: recorder.mimeType || mime || 'video/webm' }));
        };
      });

      let paintRaf = 0;
      const paintLoop = () => {
        try {
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx?.getImageData(0, 0, 1, 1);
        } catch {
          // ignore
        }
        paintRaf = window.requestAnimationFrame(paintLoop);
      };

      try {
        // 先对齐音频时钟，再几乎同时 start 录制 + play 谱面
        if (captureAudio && hasEmbeddedAudio) {
          captureAudio.currentTime = musicStartSec;
          captureAudio.volume = 1;
        }
        const tRec = performance.now();
        recorder.start(100);
        paintRaf = window.requestAnimationFrame(paintLoop);
        setBridge({
          state: 'playing',
          hasEmbeddedAudio,
          videoBase64: null,
          videoMime: recorder.mimeType || mime || 'video/webm',
        });
        const playPromises: Promise<unknown>[] = [];
        if (captureAudio && hasEmbeddedAudio) {
          playPromises.push(captureAudio.play().catch(() => undefined));
        }
        play();
        await Promise.all(playPromises);
        const recordLeadMs = Math.max(0, performance.now() - tRec);
        setBridge({ recordLeadMs, hasEmbeddedAudio });
        await sleep(params.durationSec * 1000);
        if (cancelled) return;
        pause();
        if (captureAudio) {
          try {
            captureAudio.pause();
          } catch {
            // ignore
          }
        }
        await sleep(200);
        if (recorder.state !== 'inactive') recorder.stop();
        const blob = await stopped;
        if (cancelled) return;
        if (!blob.size) {
          setBridge({ state: 'error', error: 'empty recording' });
          return;
        }
        const videoBase64 = await blobToBase64(blob);
        setBridge({
          state: 'done',
          recordLeadMs,
          hasEmbeddedAudio,
          musicStartSec,
          videoBase64,
          videoMime: blob.type || mime || 'video/webm',
        });
      } catch (e) {
        console.error(e);
        try {
          if (recorder.state !== 'inactive') recorder.stop();
        } catch {
          // ignore
        }
        if (!cancelled) {
          setBridge({
            state: 'error',
            error: e instanceof Error ? e.message : 'record failed',
          });
        }
      } finally {
        if (paintRaf) window.cancelAnimationFrame(paintRaf);
        stream.getTracks().forEach((t) => t.stop());
        if (captureAudio) {
          try {
            captureAudio.pause();
            captureAudio.removeAttribute('src');
            captureAudio.load();
          } catch {
            // ignore
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chartData, params.durationSec, params.withAudio, params.songId, params.kind, play, pause]);

  useEffect(() => {
    return () => {
      pause();
      reset();
    };
  }, [pause, reset]);

  return (
    <div
      style={{
        margin: 0,
        width: '100vw',
        height: '100vh',
        background: '#000',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          width: '100vmin',
          height: '100vmin',
          maxWidth: '100vw',
          maxHeight: '100vh',
        }}
        data-guess-chart-canvas
      >
        <style>{`
          [data-guess-chart-canvas] > div {
            max-width: 100% !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            aspect-ratio: auto !important;
          }
          [data-guess-chart-canvas] canvas {
            border: none !important;
            border-radius: 0 !important;
            background: #000 !important;
            width: 100% !important;
            height: 100% !important;
          }
        `}</style>
        <ChartCanvas />
      </div>
    </div>
  );
}
