import { radiansToCompass } from "../lib/coordinates";
import type { ConsolePacket, ScenarioEvent } from "../types";

export function EvidencePanel({ packet, selectedEvent }: { packet: ConsolePacket; selectedEvent?: ScenarioEvent }) {
  const decisionVisible = packet.fixture;
  const modified = decisionVisible && packet.decision.action !== "pass";
  const heading = packet.decision.issued_command?.heading_rad ?? packet.snapshot.ownship.heading_rad;
  return (
    <aside className="evidence-panel" aria-live="polite">
      <section>
        <span className="eyebrow">CONTROL AUTHORITY</span>
        <div className={`authority ${modified ? "filtered" : "nominal"}`}><i />{decisionVisible ? packet.decision.authority.replace("_", " ") : "Decision unavailable"}</div>
        <p className="panel-note">{decisionVisible ? `Decision ${packet.decision.decision_id} · ${packet.decision.candidate_id}` : "Waiting for a public assurance decision feed."}</p>
      </section>
      <section className="intervention-card">
        <span className="eyebrow">WHY INTERVENED</span>
        <h2>{modified ? packet.decision.reason_codes[0]?.replaceAll("_", " ") : decisionVisible ? "Monitoring encounter" : "No linked decision"}</h2>
        <p>{decisionVisible ? packet.contact.reason : "The live simulator stream contains display state only. No intervention reason is inferred in the browser."}</p>
        {decisionVisible && <div className="reason-tags">{packet.decision.reason_codes.map((reason) => <span key={reason}>{reason.replaceAll("_", " ")}</span>)}</div>}
      </section>
      <section>
        <span className="eyebrow">COMMAND CHANGE</span>
        {packet.proposedCommand && <div className="proposal-row"><span>AI proposal</span><strong>{radiansToCompass(packet.proposedCommand.headingRad).toFixed(0)}° · {packet.proposedCommand.speedMps.toFixed(1)} m/s</strong></div>}
        <div className="command-grid"><div><strong>{decisionVisible ? `${radiansToCompass(heading).toFixed(0)}°` : "—"}</strong><span>Issued heading</span></div><div><strong>{decisionVisible ? `${packet.decision.issued_command?.speed_mps.toFixed(1)} m/s` : "—"}</strong><span>Issued speed</span></div></div>
      </section>
      <section>
        <span className="eyebrow">SELECTED CONTACT</span>
        <button className="contact-card" type="button">
          <span><strong>{packet.contact.label}</strong><small>{packet.contact.contactId}</small></span><b className={packet.contact.status}>{packet.contact.status}</b>
        </button>
        <dl className="contact-metrics"><div><dt>Range</dt><dd>{packet.contact.rangeM.toFixed(1)} m</dd></div><div><dt>Bearing</dt><dd>{packet.contact.bearingDeg.toFixed(0)}°</dd></div><div><dt>Evidence age</dt><dd>{packet.contact.ageS.toFixed(2)} s</dd></div><div><dt>Bound</dt><dd>{packet.contact.uncertaintyRadiusM ? `${packet.contact.uncertaintyRadiusM.toFixed(1)} m` : "unavailable"}</dd></div></dl>
        <div className="evidence-sources"><span>Supporting sources</span><strong>{packet.contact.sourceIds.join(" · ") || "unavailable"}</strong>{packet.contact.contradictingObservationIds.length > 0 && <small>{packet.contact.contradictingObservationIds.length} contradicting observation</small>}</div>
      </section>
      <section className="selected-event-card">
        <span className="eyebrow">LINKED EVENT</span>
        <strong>{selectedEvent?.label ?? "No event selected"}</strong>
        <small>{selectedEvent ? `t+${selectedEvent.timeS.toFixed(1)} s · ${selectedEvent.id}` : "Use the timeline to inspect lineage."}</small>
      </section>
    </aside>
  );
}
