/**
 * 猜铺面录制页：仅渲染谱面动画，不加载音乐 / 背景视频 / UI。
 * Playwright 通过 window.__GUESS_CHART__ 状态机驱动录制。
 */
import { useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ChartCanvas } from './chart/components/ChartCanvas';
import { getAvailableDifficulties, parseSimaiChart } from './chart/core/parser/ChartParser';
import { useGameSettingsStore } from './chart/stores/useGameSettingsStore';
import { playbackTimeRef, useGameStore } from './chart/stores/useGameStore';
import type { ChartDifficulty } from './chart/types';
import {
  chartFileIdForSong,
  fetchSimaiText,
  type ChartKind,
} from './lxns/chartResolve';
import { parsePreviewUrlParams } from './previewUrlParams';

type GuessState = 'loading' | 'ready' | 'playing' | 'done' | 'error';

type GuessBridge = {
  state: GuessState;
  error: string | null;
  durationSec: number;
  startSec: number;
  songId: number | null;
  kind: ChartKind | null;
  diff: ChartDifficulty | null;
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

function ensureBridge(): GuessBridge {
  if (!window.__GUESS_CHART__) {
    window.__GUESS_CHART__ = {
      state: 'loading',
      error: null,
      durationSec: 25,
      startSec: 0,
      songId: null,
      kind: null,
      diff: null,
    };
  }
  return window.__GUESS_CHART__;
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
    const durationSec = Math.min(90, Math.max(5, parsePositiveFloat(searchParams.get('duration'), 25)));
    const startSec = parsePositiveFloat(searchParams.get('start'), -1);
    const hiSpeed = Math.min(9, Math.max(3, parsePositiveFloat(searchParams.get('hispeed'), 6)));
    return { songId, kind, diff, durationSec, startSec, hiSpeed };
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
    });
    setSoundEnabled(false);
    setMusicVolume(0);
    setMusicUrl('');
    setHiSpeed(params.hiSpeed);
  }, [
    params.durationSec,
    params.startSec,
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
        if (startMs < 0) {
          startMs = maxStart > 0 ? Math.floor(Math.random() * maxStart) : 0;
        } else {
          startMs = Math.min(Math.max(0, startMs * 1000), maxStart);
        }

        const startBeats = msToBeats(startMs, bpmEvents, bpm);
        playbackTimeRef.current = startBeats;
        setPreciseTime(startBeats, true);
        setBridge({
          state: 'ready',
          startSec: startMs / 1000,
          diff: diffToUse,
          error: null,
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
    const durationMs = params.durationSec * 1000;

    // 等一帧让 canvas 完成首绘
    const startTimer = window.setTimeout(() => {
      setBridge({ state: 'playing' });
      play();
      window.setTimeout(() => {
        pause();
        setBridge({ state: 'done' });
      }, durationMs);
    }, 400);

    return () => {
      window.clearTimeout(startTimer);
    };
  }, [chartData, params.durationSec, play, pause]);

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
