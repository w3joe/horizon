import { useEffect, useState } from "react";
import type { PerceptionArtifactState, PerceptionFrame, PerceptionManifest } from "../types";

const ARTIFACT_BASE = "/api/artifacts/perception";

interface CompactFramePayload extends Omit<PerceptionFrame, "layers"> {
  layers: Record<string, Omit<PerceptionFrame["layers"][number], "standard_deviation"> & { std: number }>;
  artifacts?: { raw_image_url?: string; mask_preview_url?: string; class_mask_url?: string };
}

function frameId(index: number): string {
  return index.toString().padStart(5, "0");
}

export function usePerceptionArtifact(enabled: boolean): PerceptionArtifactState {
  const [manifest, setManifest] = useState<PerceptionManifest | null>(null);
  const [frame, setFrame] = useState<PerceptionFrame | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [status, setStatus] = useState<PerceptionArtifactState["status"]>(enabled ? "loading" : "unavailable");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setStatus("unavailable");
      return;
    }
    const controller = new AbortController();
    setStatus("loading");
    fetch(`${ARTIFACT_BASE}/manifest`, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) throw new Error(`artifact manifest HTTP ${response.status}`);
        return response.json() as Promise<PerceptionManifest>;
      })
      .then((value) => { setManifest(value); setStatus("available"); setError(null); })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setStatus("unavailable");
        setError(reason instanceof Error ? reason.message : "artifact manifest unavailable");
      });
    return () => controller.abort();
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !manifest) return;
    const controller = new AbortController();
    const id = frameId(frameIndex);
    fetch(`${ARTIFACT_BASE}/frames/${id}`, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) throw new Error(`frame metadata HTTP ${response.status}`);
        return response.json() as Promise<CompactFramePayload>;
      })
      .then((value) => {
        setFrame({
          ...value,
          layers: Object.values(value.layers).map((layer) => ({ ...layer, standard_deviation: layer.std })),
          raw_url: value.artifacts?.raw_image_url ?? value.raw_url ?? `${ARTIFACT_BASE}/raw/${id}.jpg`,
          mask_preview_url: value.artifacts?.mask_preview_url ?? value.mask_preview_url ?? `${ARTIFACT_BASE}/mask-preview/${id}.png`,
          class_mask_url: value.artifacts?.class_mask_url ?? value.class_mask_url ?? `${ARTIFACT_BASE}/class-mask/${id}.png`,
        });
        setStatus("available");
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setFrame(null);
        setStatus("error");
        setError(reason instanceof Error ? reason.message : "frame metadata unavailable");
      });
    return () => controller.abort();
  }, [enabled, frameIndex, manifest]);

  return {
    status,
    manifest,
    frame,
    frameIndex,
    error,
    selectFrame: (index) => setFrameIndex(Math.max(0, Math.min((manifest?.sequence_frame_count ?? 1) - 1, Math.round(index)))),
  };
}
