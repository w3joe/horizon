import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  parseDemoCatalog,
  parseDemoReplay,
  type DemoCatalog,
  type DemoFrame,
  type DemoLoadState,
  type DemoReplay,
} from "../lib/demoTypes";

const PLAYBACK_RATES = [0.5, 1, 2, 4] as const;

async function fetchJson(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json() as Promise<unknown>;
}

export function frameAtTime(replay: DemoReplay, timeS: number): DemoFrame {
  let selected = replay.timeline.frames[0];
  for (const frame of replay.timeline.frames) {
    if (frame.time_s > timeS) break;
    selected = frame;
  }
  return selected;
}

export function useDemoReplay() {
  const [catalog, setCatalog] = useState<DemoCatalog | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [replay, setReplay] = useState<DemoReplay | null>(null);
  const [loadState, setLoadState] = useState<DemoLoadState>("loading-catalog");
  const [error, setError] = useState<string | null>(null);
  const [timeS, setTimeS] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRateState] = useState<(typeof PLAYBACK_RATES)[number]>(2);
  const previousAnimationTime = useRef<number | null>(null);

  const retryCatalog = useCallback(() => {
    setCatalog(null);
    setSelectedRunId(null);
    setReplay(null);
    setPlaying(false);
    setError(null);
    setLoadState("loading-catalog");
  }, []);

  useEffect(() => {
    if (loadState !== "loading-catalog") return;
    const controller = new AbortController();
    void fetchJson("/api/demo/catalog", controller.signal)
      .then(parseDemoCatalog)
      .then((nextCatalog) => {
        if (nextCatalog.runs.length === 0) throw new Error("No recorded simulation is available.");
        setCatalog(nextCatalog);
        setSelectedRunId(nextCatalog.runs[0].run_id);
        setLoadState("loading-replay");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "The recorded-run catalog is unavailable.");
        setLoadState("error");
      });
    return () => controller.abort();
  }, [loadState]);

  useEffect(() => {
    if (loadState !== "loading-replay" || !catalog || !selectedRunId) return;
    const selected = catalog.runs.find((run) => run.run_id === selectedRunId);
    if (!selected) {
      setError("The selected recorded run is no longer in the catalog.");
      setLoadState("error");
      return;
    }
    const controller = new AbortController();
    void fetchJson(selected.replay_url, controller.signal)
      .then(parseDemoReplay)
      .then((nextReplay) => {
        if (nextReplay.run_id !== selected.run_id || nextReplay.manifest.replay_sha256 !== selected.replay_sha256) {
          throw new Error("The replay does not match the catalog manifest.");
        }
        setReplay(nextReplay);
        setTimeS(nextReplay.timeline.start_s);
        setPlaying(false);
        setError(null);
        setLoadState("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "The recorded replay is unavailable.");
        setLoadState("error");
      });
    return () => controller.abort();
  }, [catalog, loadState, selectedRunId]);

  useEffect(() => {
    if (!playing || !replay) return;
    let animationFrame = 0;
    const update = (now: number) => {
      if (previousAnimationTime.current !== null) {
        const elapsedS = Math.min(0.1, (now - previousAnimationTime.current) / 1000);
        setTimeS((current) => {
          const next = current + elapsedS * rate;
          if (next >= replay.timeline.end_s) {
            setPlaying(false);
            return replay.timeline.end_s;
          }
          return next;
        });
      }
      previousAnimationTime.current = now;
      animationFrame = requestAnimationFrame(update);
    };
    animationFrame = requestAnimationFrame(update);
    return () => {
      cancelAnimationFrame(animationFrame);
      previousAnimationTime.current = null;
    };
  }, [playing, rate, replay]);

  const selectRun = useCallback((runId: string) => {
    if (!catalog?.runs.some((run) => run.run_id === runId)) return;
    setSelectedRunId(runId);
    setReplay(null);
    setPlaying(false);
    setError(null);
    setLoadState("loading-replay");
  }, [catalog]);

  const seek = useCallback((nextTimeS: number) => {
    if (!replay) return;
    setTimeS(Math.max(replay.timeline.start_s, Math.min(replay.timeline.end_s, nextTimeS)));
  }, [replay]);

  const togglePlaying = useCallback(() => {
    if (!replay) return;
    setTimeS((current) => current >= replay.timeline.end_s ? replay.timeline.start_s : current);
    setPlaying((current) => !current);
  }, [replay]);

  const replayFromStart = useCallback(() => {
    if (!replay) return;
    setTimeS(replay.timeline.start_s);
    setPlaying(true);
  }, [replay]);

  const setRate = useCallback((value: number) => {
    if (PLAYBACK_RATES.includes(value as (typeof PLAYBACK_RATES)[number])) {
      setRateState(value as (typeof PLAYBACK_RATES)[number]);
    }
  }, []);

  const frame = useMemo(() => replay ? frameAtTime(replay, timeS) : null, [replay, timeS]);
  const progress = replay ? (timeS - replay.timeline.start_s) / (replay.timeline.end_s - replay.timeline.start_s) : 0;

  return {
    catalog,
    selectedRunId,
    replay,
    frame,
    loadState,
    error,
    timeS,
    progress,
    playing,
    rate,
    playbackRates: PLAYBACK_RATES,
    selectRun,
    seek,
    togglePlaying,
    replayFromStart,
    setRate,
    retryCatalog,
  };
}
