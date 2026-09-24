import { DEFAULT_SENSOR_INPUTS, normalizeSensorInputs } from "../lib/sensorSimulation";
import type { SensorInputs } from "../types";

type NumericKey = "rangeM" | "bearingDeg" | "reportAgeS" | "uncertaintyM" | "reportedSpeedMps";

const numericFields: Array<{ key: NumericKey; label: string; unit: string; min: number; max: number; step: number }> = [
  { key: "rangeM", label: "Contact range", unit: "m", min: 1, max: 500, step: 1 },
  { key: "bearingDeg", label: "Bearing", unit: "deg", min: 0, max: 359, step: 1 },
  { key: "reportedSpeedMps", label: "Reported speed", unit: "m/s", min: 0, max: 25, step: 0.1 },
  { key: "reportAgeS", label: "Report age", unit: "s", min: 0, max: 30, step: 0.1 },
  { key: "uncertaintyM", label: "Uncertainty", unit: "m", min: 0, max: 250, step: 1 },
];

export function SensorInputPanel({ value, onChange, onRegenerateTraffic }: { value: SensorInputs; onChange: (value: SensorInputs) => void; onRegenerateTraffic?: () => void }) {
  const setNumber = (key: NumericKey, next: number) => onChange(normalizeSensorInputs({ ...value, [key]: next }));
  const state = !value.radarDetection && !value.cameraDetection ? "CONTACT OMITTED"
    : value.radarDetection !== value.cameraDetection ? "SENSOR CONFLICT"
      : value.reportAgeS > 1 ? "STALE REPORT" : "SENSORS AGREE";
  return (
    <section className="sensor-input-panel" aria-label="Editable simulated sensor inputs">
      <header>
        <div><span>SIMULATION INPUTS</span><strong>Sensor laboratory</strong><small>Edit the synthetic readings and inspect how they propagate through the dashboard.</small></div>
        <div className={`sensor-derived-state ${state === "SENSORS AGREE" ? "healthy" : "fault"}`}><span>Derived state</span><strong>{state}</strong></div>
      </header>
      <div className="sensor-input-grid">
        {numericFields.map((field) => <label key={field.key}><span>{field.label}</span><div><input aria-label={field.label} type="number" value={value[field.key]} min={field.min} max={field.max} step={field.step} onChange={(event) => setNumber(field.key, event.target.valueAsNumber)} /><b>{field.unit}</b></div></label>)}
        <label className="sensor-check"><input type="checkbox" checked={value.radarDetection} onChange={(event) => onChange({ ...value, radarDetection: event.target.checked })} /><span><strong>Radar detection</strong><small>Contact present in radar report</small></span></label>
        <label className="sensor-check"><input type="checkbox" checked={value.cameraDetection} onChange={(event) => onChange({ ...value, cameraDetection: event.target.checked })} /><span><strong>Camera detection</strong><small>Contact present in camera output</small></span></label>
        <button type="button" className="sensor-reset" onClick={() => onChange(DEFAULT_SENSOR_INPUTS)}>Reset inputs</button>
        {onRegenerateTraffic && <button type="button" className="sensor-reset" onClick={onRegenerateTraffic}>New traffic</button>}
      </div>
      <p>These values modify synthetic display evidence only. The selected fault profile provides the illustrative STUB response; no protected service, actuator, or live sensor is written.</p>
    </section>
  );
}
