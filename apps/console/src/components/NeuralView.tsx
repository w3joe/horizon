import type { ConsolePacket, PerceptionArtifactState, ScenarioEvent } from "../types";

export function NeuralView({ packet, event, artifact }: { packet: ConsolePacket; event?: ScenarioEvent; artifact: PerceptionArtifactState }) {
  if (artifact.status === "available" && artifact.manifest && artifact.frame) {
    const { manifest, frame } = artifact;
    return (
      <div className="neural-workspace workspace-scroll">
        <div className="workspace-heading">
          <div><span className="eyebrow">RECORDED REPRODUCTION · NOT SIMULATOR CAMERA</span><h2>WaSR-T · frame {frame.frame_id}</h2></div>
          <span className="health-badge recorded">measured artifact</span>
        </div>
        <div className="artifact-disclosure"><strong>Source separation</strong><span>These recorded WaSR-T example frames are inspected beside the live control event. They are not observations from the simulated encounter and do not explain its intervention.</span></div>
        <div className="recorded-frame-grid">
          <figure><img src={frame.raw_url} alt={`Recorded maritime source frame ${frame.frame_id}`} /><figcaption>Recorded raw frame · {frame.source_image_size.join(" × ")} px</figcaption></figure>
          <figure><img src={frame.mask_preview_url} alt={`WaSR-T class mask preview for frame ${frame.frame_id}`} /><figcaption>Actual class-ID mask preview · model output</figcaption></figure>
        </div>
        <div className="frame-scrubber">
          <label htmlFor="perception-frame">Frame <strong>{artifact.frameIndex + 1} / {manifest.sequence_frame_count}</strong></label>
          <input id="perception-frame" type="range" min="0" max={manifest.sequence_frame_count - 1} value={artifact.frameIndex} onChange={(input) => artifact.selectFrame(Number(input.target.value))} />
        </div>
        <div className="neural-evidence-grid">
          <section className="neural-inspection">
            <span className="section-title">Measured inference record</span>
            <dl className="artifact-metrics">
              <div><dt>Architecture</dt><dd>{manifest.architecture}</dd></div>
              <div><dt>Device</dt><dd>{manifest.device.toUpperCase()} · FP32</dd></div>
              <div><dt>Frame inference</dt><dd>{frame.inference_ms.toFixed(1)} ms</dd></div>
              <div><dt>Hook capture</dt><dd>{frame.instrumentation_ms.toFixed(1)} ms</dd></div>
              <div><dt>Temporal buffer</dt><dd>{frame.buffer_age} · {frame.cold_start ? "cold start" : "warm"}</dd></div>
              <div><dt>Timestamp</dt><dd>{frame.timestamp_source.replaceAll("_", " ")}</dd></div>
            </dl>
          </section>
          <section className="neural-inspection">
            <span className="section-title">Actual layer summaries</span>
            <div className="measured-layers">
              {frame.layers.map((layer) => <article key={layer.name}><header><code>{layer.name}</code><span className={layer.finite ? "finite" : "non-finite"}>{layer.finite ? "finite" : "non-finite"}</span></header><small>{layer.shape.join(" × ")} · {layer.dtype}</small><dl><div><dt>mean</dt><dd>{layer.mean.toFixed(4)}</dd></div><div><dt>std</dt><dd>{layer.standard_deviation.toFixed(4)}</dd></div><div><dt>range</dt><dd>{layer.minimum.toFixed(3)}…{layer.maximum.toFixed(3)}</dd></div></dl></article>)}
            </div>
          </section>
        </div>
        <section className="validation-strip">
          <div><span>85-frame local CPU run</span><strong>median {manifest.latency_ms.median.toFixed(1)} ms/frame</strong><small>maximum {manifest.latency_ms.maximum.toFixed(1)} ms · no 20 Hz claim</small></div>
          <div><span>Instrumentation check</span><strong>{manifest.instrumentation_validation.outputs_identical ? "outputs identical" : "outputs differ"}</strong><small>{manifest.instrumentation_validation.measured_frames} measured prefix frames · max |Δ| {manifest.instrumentation_validation.maximum_absolute_difference}</small></div>
          <div><span>Reset replay check</span><strong>{manifest.instrumentation_validation.reset_reproducible ? "prefix reproduced" : "not reproduced"}</strong><small>max |Δ| {manifest.instrumentation_validation.reset_maximum_absolute_difference}</small></div>
        </section>
        <div className="honesty-callout"><span>Evidence boundary</span><p>{manifest.limitations.join(" ")}</p><small>Linked console event: {event?.id ?? packet.lineage.sampleId ?? "none"}. This recorded reproduction is displayed for inspection only.</small></div>
      </div>
    );
  }

  const neural = packet.neural;
  return (
    <div className="neural-workspace workspace-scroll">
      <div className="workspace-heading"><div><span className="eyebrow">NEURAL SENSOR</span><h2>Recorded artifact unavailable</h2></div><span className={`health-badge ${neural.status}`}>{artifact.status}</span></div>
      <section className="artifact-unavailable">
        <strong>No allowlisted perception artifact is available from this console origin.</strong>
        <p>{artifact.error ?? neural.suspectedCause}</p>
        <small>No activation values, model outputs, or attribution maps are generated as a substitute.</small>
      </section>
      <section className="neural-inspection fallback-inspection">
        <span className="section-title">Runtime capability</span>
        <dl className="artifact-metrics"><div><dt>Model</dt><dd>{neural.model}</dd></div><div><dt>Capability</dt><dd>{neural.capability.replace("_", " ")}</dd></div><div><dt>Linked inference</dt><dd>{event?.inferenceId ?? neural.inferenceId}</dd></div></dl>
      </section>
      <div className="honesty-callout"><span>Evidence boundary</span><p>{neural.suspectedCause}</p><small>Layer telemetry remains unknown until the allowlisted measured artifact loads.</small></div>
    </div>
  );
}
