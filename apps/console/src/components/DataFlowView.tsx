import type { ConsolePacket, ScenarioEvent } from "../types";

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

export function DataFlowView({ packet, event }: { packet: ConsolePacket; event?: ScenarioEvent }) {
  const highlighted = new Set(event?.observationIds ?? []);
  const groups = Object.entries(inputLabels).map(([id, label]) => {
    const matches = packet.observations.filter((item) => item.input_group === id);
    const capabilities = matches.map((item) => item.capability);
    const status = capabilities.includes("degraded") ? "degraded" : capabilities.includes("output_only") ? "limited" : matches.length ? "available" : "unavailable";
    return { id, label, status, matches, active: matches.some((item) => highlighted.has(item.observation_id)) };
  });
  return (
    <div className="flow-workspace workspace-scroll">
      <div className="workspace-heading">
        <div><span className="eyebrow">EVENT LINEAGE</span><h2>{event?.label ?? "Select a timeline event"}</h2></div>
        <span className="lineage-id">{event ? event.id : "no event"}</span>
      </div>
      <div className="flow-layout">
        <section className="source-bank" aria-label="Input groups">
          <div className="section-title">Eight declared input groups</div>
          {groups.map((group) => (
            <article className={`source-row ${group.active ? "is-active" : ""}`} key={group.id}>
              <span className={`status-dot ${group.status}`} aria-hidden="true" />
              <div><strong>{group.label}</strong><small>{group.matches.map((item) => item.source_id).join(" · ") || "No public event"}</small></div>
              <span className={`source-status ${group.status}`}>{group.status}</span>
            </article>
          ))}
        </section>
        <section className="pipeline" aria-label="Runtime assurance data path">
          <div className="flow-node"><span>01</span><div><strong>Evidence assembly</strong><small>Schema 0.1.0 · NED · bounded validity</small></div></div>
          <div className={`flow-edge ${event?.stage === "detect" ? "active" : ""}`}><i /><span>correlate</span></div>
          <div className="flow-node external"><span>02</span><div><strong>External decision AI</strong><small>Proposal authority only · {event?.inferenceId ?? "no linked inference"}</small></div></div>
          <div className={`flow-edge ${event?.stage === "decide" ? "active danger" : ""}`}><i /><span>proposal</span></div>
          <div className="flow-node governor"><span>03</span><div><strong>Assurance governor</strong><small>{packet.fixture ? "STUB fixture decision" : "Decision feed unavailable"}</small></div></div>
          <div className={`flow-edge ${event?.stage === "actuate" ? "active" : ""}`}><i /><span>issued command</span></div>
          <div className="flow-node"><span>04</span><div><strong>Exclusive actuator gate</strong><small>{packet.fixture ? "Fixture receipt linked" : "Gate receipt unavailable"}</small></div></div>
          <aside className="isolation-note"><strong>Read-only console</strong><span>The browser cannot write to protected actuation or evaluation truth.</span></aside>
        </section>
      </div>
      <section className="event-record">
        <span className="eyebrow">SELECTED RECORD</span>
        <p>{event?.detail ?? "Choose a marker below to follow the same event through all three workspaces."}</p>
        <div className="record-links">
          {packet.fixture && <code>{packet.decision.input_snapshot_id}</code>}
          {(event?.observationIds ?? []).map((id) => <code key={id}>{id}</code>)}
          {event?.inferenceId && <code>{event.inferenceId}</code>}
        </div>
      </section>
    </div>
  );
}
