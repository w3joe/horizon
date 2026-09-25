import { radiansToCompass } from "../lib/coordinates";
import type { ConsolePacket, ScenarioEvent } from "../types";

export function EvidencePanel({ packet, selectedEvent }: { packet: ConsolePacket; selectedEvent?: ScenarioEvent }) {
  const { decision, receipt, lineage } = packet;
  const issued = receipt?.accepted ? receipt.actual_command : null;
  const proposed = packet.proposedCommand;
  const authority = packet.authority.state === "current" && issued
    ? receipt?.authority.replaceAll("_", " ")
    : packet.authority.state === "historical" ? "Historical receipt" : lineage.status === "invalid" || lineage.status === "rejected" ? "No new command" : "Authority unknown";
  const mainReason = lineage.reasonCodes[0]?.replaceAll("_", " ") ?? (decision ? "No intervention reason" : "No linked decision");
  const constraint = decision?.constraints[0];
  const computeMs = decision ? decision.compute_time_ns / 1_000_000 : null;
  const statusLabel = packet.authority.state === "current" ? "Current authority verified" : packet.authority.state === "historical" ? "Historical acceptance only" : lineage.status;

  return (
    <aside className="evidence-panel" aria-live="polite">
      <section>
        <span className="eyebrow">CONTROL AUTHORITY</span>
        <div className={`authority ${packet.authority.state}`}><i />{authority}</div>
        <p className="panel-note">{packet.authority.explanation}</p>
        <div className={`issue-state ${packet.authority.state}`}><span>{statusLabel}</span><code>{receipt?.receipt_id ?? lineage.eventType}</code></div>
        <dl className="gate-readiness">
          <div><dt>Candidate</dt><dd>{decision ? `${decision.candidate_id} · ${decision.candidate_version}` : "unknown"}</dd></div>
          <div><dt>Gate epoch</dt><dd>{packet.gateStatus?.epoch ?? "unknown"}</dd></div>
          <div><dt>A6 policy enforcement</dt><dd>{packet.serviceFreshness.gate.state === "live" ? packet.gateStatus?.a6_mode ?? "unknown" : "status unavailable"}</dd></div>
          <div><dt>Policy decisions (run)</dt><dd>{packet.gateStatus?.policy_counts ? `${packet.gateStatus.policy_counts.authorize} authorized · ${packet.gateStatus.policy_counts.withhold} withheld` : "unavailable"}</dd></div>
          <div><dt>Recovery primed</dt><dd>{packet.gateStatus?.startup_recovery_ready === true ? "yes" : packet.gateStatus?.startup_recovery_ready === false ? "no" : "unknown"}</dd></div>
          <div><dt>Quarantine</dt><dd>{packet.gateStatus?.quarantined === true ? "active" : packet.gateStatus?.quarantined === false ? "clear" : "unknown"}</dd></div>
          <div><dt>Receipt age</dt><dd>{packet.authority.receiptAgeS === null ? "unknown" : `${packet.authority.receiptAgeS.toFixed(2)} s`}</dd></div>
          <div><dt>Evidence API</dt><dd>{packet.serviceFreshness.evidence.state}</dd></div>
        </dl>
      </section>
      <section className="intervention-card">
        <span className="eyebrow">WHY INTERVENED</span>
        <h2>{mainReason}</h2>
        <p>{decision ? packet.contact.reason : "No intervention reason is inferred from vessel motion or missing telemetry."}</p>
        {lineage.reasonCodes.length > 0 && <div className="reason-tags">{lineage.reasonCodes.map((reason) => <span key={reason}>{reason.replaceAll("_", " ")}</span>)}</div>}
      </section>
      <section>
        <span className="eyebrow">COMMAND CHAIN</span>
        <div className="command-chain">
          <div><span>AI proposal</span><strong>{proposed ? `${radiansToCompass(proposed.headingRad).toFixed(0)}° · ${proposed.speedMps.toFixed(1)} m/s` : "unavailable"}</strong></div>
          <i aria-hidden="true">→</i>
          <div><span>{packet.authority.state === "current" ? "Plant-active command" : "Receipt command (historical)"}</span><strong>{issued ? `${radiansToCompass(issued.heading_rad).toFixed(0)}° · ${issued.speed_mps.toFixed(1)} m/s` : "no confirmed command"}</strong></div>
        </div>
        <div className="deadline-row"><span>Supervisor compute</span><strong className={decision?.deadline_met ? "met" : decision ? "missed" : "unknown"}>{computeMs === null ? "unknown" : `${computeMs.toFixed(1)} ms · ${decision?.deadline_met ? "deadline met" : "deadline missed"}`}</strong></div>
        {lineage.cycleTimeNs !== null && <div className="deadline-row"><span>Control cycle</span><strong>{(lineage.cycleTimeNs / 1_000_000).toFixed(1)} ms</strong></div>}
      </section>
      <section>
        <span className="eyebrow">BINDING EVIDENCE</span>
        {constraint ? <div className="constraint-card"><strong>{constraint.kind}</strong><span>{constraint.minimum_margin.toFixed(2)} {constraint.units}</span><small>{constraint.representation} · {constraint.coverage === null ? "coverage not applicable / unavailable" : `${(constraint.coverage * 100).toFixed(0)}% coverage`} · {constraint.assumption_id}</small></div> : <p className="empty-state">No constraint evidence is linked.</p>}
      </section>
      <section>
        <span className="eyebrow">SELECTED CONTACT</span>
        <button className="contact-card" type="button">
          <span><strong>{packet.contact.label}</strong><small>{packet.contact.contactId}</small></span><b className={packet.contact.status}>{packet.contact.status}</b>
        </button>
        <dl className="contact-metrics"><div><dt>Range</dt><dd>{packet.contact.rangeM.toFixed(1)} m</dd></div><div><dt>Bearing</dt><dd>{packet.contact.bearingDeg.toFixed(0)}°</dd></div><div><dt>Evidence age</dt><dd>{packet.contact.ageS === null ? "unknown" : `${packet.contact.ageS.toFixed(2)} s`}</dd></div><div><dt>Engineering bound</dt><dd>{packet.contact.uncertaintyRadiusM === null ? "unknown" : `${packet.contact.uncertaintyRadiusM.toFixed(1)} m`}</dd></div></dl>
        <div className="evidence-sources"><span>Supporting sources</span><strong>{packet.contact.sourceIds.join(" · ") || "unavailable"}</strong>{packet.contact.contradictingObservationIds.length > 0 && <small>{packet.contact.contradictingObservationIds.length} contradicting observation</small>}</div>
      </section>
      <section className="selected-event-card">
        <span className="eyebrow">LINKED EVENT</span>
        <strong>{selectedEvent?.label ?? lineage.eventType.replaceAll("_", " ")}</strong>
        <small>{selectedEvent ? `t+${selectedEvent.timeS.toFixed(2)} s · ${selectedEvent.id}` : lineage.sampleId ?? "No event sample ID"}</small>
      </section>
    </aside>
  );
}
