import { useEffect, useState } from "react";
import type { OperatorAction, OperatorState } from "../types";

export function OperatorControls({
  state,
  onAction,
}: {
  state: OperatorState;
  onAction: (action: OperatorAction, body?: Record<string, unknown>) => Promise<boolean>;
}) {
  const { capabilities } = state;
  const [selectedFault, setSelectedFault] = useState("");
  useEffect(() => {
    if (!capabilities?.declared_fault_ids.length) {
      setSelectedFault("");
      return;
    }
    if (!capabilities.declared_fault_ids.includes(selectedFault)) {
      setSelectedFault(capabilities.declared_fault_ids[0]);
    }
  }, [capabilities, selectedFault]);

  const pending = state.pendingAction !== null;
  const resetPending = state.resetRequested;
  const stateLabel = resetPending ? "reset in progress"
    : capabilities?.state === "ready" ? "ready"
      : capabilities ? "interlock not ready" : "unavailable";
  const faultEnabled = selectedFault !== "" && state.activeFaults.includes(selectedFault);
  return (
    <section className={`operator-controls ${resetPending ? "reset-pending" : ""}`} aria-label="Mediated operator controls">
      <div className="operator-state">
        <span>OPERATOR</span>
        <strong>{state.pendingAction ? `${state.pendingAction} pending` : stateLabel}</strong>
        {capabilities && <small>plant e{capabilities.plant_epoch ?? "?"} · gate e{capabilities.gate_epoch ?? "?"} · recovery {capabilities.startup_recovery_ready === true ? "ready" : capabilities.startup_recovery_ready === false ? "not ready" : "unknown"}</small>}
      </div>
      <div className="operator-buttons">
        <button type="button" disabled={!capabilities || pending} onClick={() => void onAction("pause")}>Pause</button>
        <button type="button" disabled={!capabilities?.resume_permitted || pending} onClick={() => void onAction("resume")}>Resume</button>
        <button type="button" className="critical-action" disabled={!capabilities || pending} onClick={() => void onAction("reset")}>Reset</button>
        <button type="button" disabled={!capabilities || pending} onClick={() => void onAction("acknowledge")}>Acknowledge</button>
      </div>
      {capabilities?.declared_fault_ids.length ? (
        <div className="fault-controls">
          <label><span>Declared fault</span><select value={selectedFault} disabled={pending} onChange={(event) => setSelectedFault(event.target.value)}>{capabilities.declared_fault_ids.map((fault) => <option key={fault} value={fault}>{fault}</option>)}</select></label>
          <button type="button" disabled={!selectedFault || pending} onClick={() => void onAction("fault", { fault_id: selectedFault, enabled: !faultEnabled })}>{faultEnabled ? "Disable" : "Enable"}</button>
        </div>
      ) : <span className="operator-note">No faults are declared for this scenario.</span>}
      {resetPending && <span className="operator-alert">{capabilities?.resume_permitted ? "Fresh recovery evidence is ready. Resume requires a separate operator action." : "Reset is paused pending a fresh epoch and accepted recovery prime. Resume remains disabled."}</span>}
      {state.error && <span className="operator-error" role="alert">{state.error}</span>}
      {state.lastAccepted === true && state.lastAction && <span className="operator-success">{state.lastAction} accepted</span>}
    </section>
  );
}
