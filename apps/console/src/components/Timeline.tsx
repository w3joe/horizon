import type { ScenarioEvent } from "../types";

interface Props {
  timeS: number;
  durationS: number;
  playing: boolean;
  rate: number;
  events: ScenarioEvent[];
  selectedEventId: string;
  onTimeChange: (value: number) => void;
  onTogglePlaying: () => void;
  onReset: () => void;
  onRateChange: (rate: number) => void;
  onSelectEvent: (id: string) => void;
  disabled?: boolean;
  disabledReason?: string;
}

export function Timeline(props: Props) {
  return (
    <section className="timeline" aria-label="Scenario replay timeline">
      <div className="playback-controls">
        <button type="button" className="icon-button" onClick={props.onTogglePlaying} disabled={props.disabled} aria-label={props.playing ? "Pause replay" : "Play replay"}>{props.playing ? "Ⅱ" : "▶"}</button>
        <button type="button" className="icon-button" onClick={props.onReset} disabled={props.disabled} aria-label="Reset replay">↺</button>
        <strong>t+{props.timeS.toFixed(1)} s</strong>
        <select value={props.rate} onChange={(event) => props.onRateChange(Number(event.target.value))} disabled={props.disabled} aria-label="Playback rate">
          <option value={0.25}>0.25×</option><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option>
        </select>
        {props.disabled && props.disabledReason && <span className="control-lock">{props.disabledReason}</span>}
      </div>
      <div className="timeline-track">
        <input type="range" min="0" max={props.durationS} step="0.1" value={props.timeS} disabled={props.disabled} onChange={(event) => props.onTimeChange(Number(event.target.value))} aria-label="Replay time" />
        <div className="event-markers">
          {props.events.map((event) => <button key={event.id} type="button" className={`${event.severity} ${props.selectedEventId === event.id ? "selected" : ""}`} style={{ left: `${Math.min(100, (event.timeS / props.durationS) * 100)}%` }} onClick={() => props.onSelectEvent(event.id)} aria-label={`${event.stage}: ${event.label} at ${event.timeS} seconds`}><i /><span>{event.stage}</span></button>)}
        </div>
      </div>
      <div className="timeline-sequence" aria-hidden="true"><span>Fault</span><i>→</i><span>Detection</span><i>→</i><span>Decision</span><i>→</i><span>Actuation</span><i>→</i><span>Clearance</span></div>
    </section>
  );
}
