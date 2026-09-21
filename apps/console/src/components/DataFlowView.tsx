import type { ConsolePacket, EvidenceObservation, ScenarioEvent } from "../types";

const inputLabels: Record<string, string> = {
  navigation_environment: "Navigation + environment",
  obstacle_perception: "Obstacle perception",
  ship_actuator_feedback: "Ship + actuator feedback",
  onboard_network: "Onboard network",
  internal_ship_communications: "Internal communications",
  inter_ship_communications: "Inter-ship communications",
  decision_ai_telemetry: "Decision AI telemetry",
  neural_sensor_internals: "Neural sensor internals",
};

function groupOf(item: EvidenceObservation): string {
  return item.contract_type === "NetworkObservation" ? "onboard_network" : item.input_group;
}

function newest(items: EvidenceObservation[]): EvidenceObservation | undefined {
  return [...items].sort((a, b) => b.sequence - a.sequence)[0];
}

function observationDetail(item: EvidenceObservation | undefined): string {
  if (!item) return "No observation received";
  const provenance = item.provenance.kind;
  const eventTime = item.time.event_time_s.toFixed(2);
  return `${item.source_id} · event t+${eventTime}s · ±${item.time.clock_uncertainty_ms.toFixed(0)}ms · ${provenance}`;
}

export function DataFlowView({ packet, event }: { packet: ConsolePacket; event?: ScenarioEvent }) {
  const highlighted = new Set(event?.observationIds ?? packet.lineage.governorInput?.evidence?.observation_ids ?? []);
  const groups = Object.entries(inputLabels).map(([id, label]) => {
    const matches = packet.observations.filter((item) => groupOf(item) === id);
    const latest = newest(matches);
    const diagnostic = packet.collectorDiagnostics?.groups[id];
    const capability = diagnostic?.capability ?? (latest?.contract_type === "Observation" ? latest.capability : latest ? "available" : "unavailable");
    const status = capability === "output_only" ? "limited" : capability;
    return { id, label, status, latest, active: matches.some((item) => highlighted.has(item.observation_id)) };
  });
  const input = packet.lineage.governorInput;
  const trace = input?.ai_trace;
  const traceMs = trace ? (trace.completed_monotonic_ns - trace.started_monotonic_ns) / 1_000_000 : null;
  const decision = packet.lineage.decision;
  const receipt = packet.lineage.receipt;
  const peerIntentCount = input?.peer_intents?.length ?? 0;
  return (
    <div className="flow-workspace workspace-scroll">
      <div className="workspace-heading">
        <div><span className="eyebrow">EVENT-COHERENT LINEAGE</span><h2>{event?.label ?? packet.lineage.eventType.replaceAll("_", " ")}</h2></div>
        <span className={`lineage-id ${packet.lineage.status}`}>{packet.lineage.status}</span>
      </div>
      <div className="flow-layout">
        <section className="source-bank" aria-label="Input groups">
          <div className="section-title">Eight declared input groups</div>
          {groups.map((group) => (
            <article className={`source-row ${group.active ? "is-active" : ""}`} key={group.id}>
              <span className={`status-dot ${group.status}`} aria-hidden="true" />
              <div><strong>{group.label}</strong><small>{observationDetail(group.latest)}</small></div>
              <span className={`source-status ${group.status}`}>{group.status}</span>
            </article>
          ))}
          {packet.collectorDiagnostics && <div className="collector-summary"><span>Collector queue</span><strong>{packet.collectorDiagnostics.queue ? `${packet.collectorDiagnostics.queue.size}/${packet.collectorDiagnostics.queue.capacity}` : "unknown"}</strong><span>Capture / source loss</span><strong>{packet.collectorDiagnostics.capture_loss} / {packet.collectorDiagnostics.source_loss}</strong></div>}
        </section>
        <section className="pipeline" aria-label="Runtime assurance data path">
          <div className={`flow-node ${input ? "confirmed" : "unknown"}`}><span>01</span><div><strong>Consumed evidence snapshot</strong><small>{input?.snapshot.snapshot_id ?? "No matching consumed snapshot"}</small></div></div>
          <div className="flow-edge active"><i /><span>{input?.evidence?.observation_ids?.length ?? "unknown"} linked observations</span></div>
          <div className={`flow-node external ${trace?.status === "ok" ? "confirmed" : "unknown"}`}><span>02</span><div><strong>External decision AI</strong><small>{input?.proposal.command_id ?? "Proposal unavailable"}{traceMs === null ? "" : ` · ${traceMs.toFixed(1)} ms`}</small></div></div>
          <div className={`flow-edge ${decision ? "active danger" : ""}`}><i /><span>{decision ? `${decision.action} · ${decision.deadline_met ? "deadline met" : "deadline missed"}` : "decision unknown"}</span></div>
          <div className={`flow-node governor ${decision?.valid ? "confirmed" : "unknown"}`}><span>03</span><div><strong>Assurance governor</strong><small>{decision ? `${decision.candidate_id} · ${decision.decision_id}` : "No linked decision"}</small></div></div>
          <div className={`flow-edge ${receipt?.accepted ? "active" : "danger"}`}><i /><span>{receipt?.accepted ? "accepted receipt" : receipt ? "gate rejected" : "receipt unknown"}</span></div>
          <div className={`flow-node ${receipt?.accepted ? "confirmed" : "unknown"}`}><span>04</span><div><strong>Exclusive actuator gate</strong><small>{receipt?.receipt_id ?? "No matching gate receipt"}</small></div></div>
          <aside className="isolation-note"><strong>Read-only console</strong><span>The browser receives public evidence and cannot write to protected actuation or evaluation truth.</span></aside>
        </section>
      </div>
      <div className="flow-evidence-row">
        <section className="event-record">
          <span className="eyebrow">SELECTED RECORD</span>
          <p>{event?.detail ?? packet.lineage.explanation}</p>
          <div className="record-links">
            {input && <code>{input.snapshot.snapshot_id}</code>}
            {input && <code>{input.proposal.command_id}</code>}
            {decision && <code>{decision.decision_id}</code>}
            {receipt && <code>{receipt.receipt_id}</code>}
            {(event?.observationIds ?? []).map((id) => <code key={id}>{id}</code>)}
          </div>
        </section>
        <section className="uncertainty-record">
          <span className="eyebrow">UNCERTAINTY REPRESENTATION</span>
          <strong>{packet.contact.uncertaintyKind?.replaceAll("_", " ") ?? "unknown"}</strong>
          <p>{packet.contact.uncertaintyRadiusM ? `Engineering bound radius ${packet.contact.uncertaintyRadiusM.toFixed(1)} m.` : "No engineering bound is available."} {packet.contact.covarianceCoverage === null || packet.contact.covarianceCoverage === undefined ? "Covariance coverage unavailable." : `Covariance coverage ${(packet.contact.covarianceCoverage * 100).toFixed(0)}%.`}</p>
          <small>Covariance is probabilistic evidence; it is not presented as a guaranteed bound.</small>
        </section>
        <section className="peer-record">
          <span className="eyebrow">PEER CLAIMED INTENT</span>
          <strong>{peerIntentCount ? `${peerIntentCount} report${peerIntentCount === 1 ? "" : "s"}` : "No current report"}</strong>
          <p>Claimed intent remains separate from radar-supported observed motion.</p>
        </section>
      </div>
    </div>
  );
}
