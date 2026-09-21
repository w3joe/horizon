import type { ConsolePacket, ScenarioEvent } from "../types";

export function NeuralView({ packet, event }: { packet: ConsolePacket; event?: ScenarioEvent }) {
  const neural = packet.neural;
  return (
    <div className="neural-workspace workspace-scroll">
      <div className="workspace-heading">
        <div><span className="eyebrow">LINKED SENSOR RECORD</span><h2>{neural.frameId}</h2></div>
        <span className={`health-badge ${neural.status}`}>{neural.status}</span>
      </div>
      <div className="neural-layout">
        <section className="sensor-frame" aria-label="Synthetic camera fixture preview">
          <div className="frame-sky"><i className="sun" /></div>
          <div className="frame-sea">
            <i className="frame-horizon" />
            <i className="detected-vessel" />
            <i className="radar-bearing" />
          </div>
          <div className="frame-overlay"><span>CAMERA 01</span><span>{packet.fixture ? "SYNTHETIC FIXTURE" : "PREVIEW UNAVAILABLE"}</span></div>
          <div className="frame-legend"><span><i className="vision-mark" />Perception output</span><span><i className="radar-mark" />Radar-supported bearing</span></div>
        </section>
        <section className="neural-inspection">
          <div className="inspection-meta"><span>Inference</span><strong>{event?.inferenceId ?? neural.inferenceId}</strong><span>Capability</span><strong>{neural.capability.replace("_", " ")}</strong><span>Model</span><strong>{neural.model}</strong></div>
          <div className="output-list">
            <div className="section-title">Observed outputs</div>
            {neural.observedOutputs.length ? neural.observedOutputs.map((output) => <div className="output-row" key={output.label}><span>{output.label}</span><strong>{output.value}</strong></div>) : <p className="empty-state">No public perception output is linked to this snapshot.</p>}
          </div>
          <div className="layer-list">
            <div className="section-title">Selected layer telemetry</div>
            {neural.layerTelemetry.length ? neural.layerTelemetry.map((layer) => (
              <div className="layer-row" key={layer.layer}><code>{layer.layer}</code><span className={layer.status}>{layer.status}</span></div>
            )) : <p className="empty-state">No layer telemetry supplied.</p>}
          </div>
        </section>
      </div>
      <div className="honesty-callout">
        <span>Suspected cause</span>
        <p>{neural.suspectedCause}</p>
        <small>No activation values or attribution maps are fabricated. Layer rows report capability only until A07 supplies measured telemetry.</small>
      </div>
    </div>
  );
}
