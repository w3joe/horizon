import { DEMO_STAGES } from "../lib/fixtures";
import type { ConsolePacket, ScenarioId } from "../types";

function sentence(value: string) {
  return value.replaceAll("_", " ");
}

export function GuidedStageSelector({ packet, onSelect }: { packet: ConsolePacket; onSelect: (id: ScenarioId) => void }) {
  const decision = packet.decision;
  const stage = packet.fixtureStage;
  const constraint = decision?.constraints.find((item) => item.kind === "collision") ?? null;
  const issued = packet.receipt?.accepted ? packet.receipt.actual_command : decision?.issued_command ?? null;
  const expandedBy = stage && constraint ? constraint.minimum_margin - stage.baselineMarginM : 0;
  const speedReduction = packet.proposedCommand && issued ? packet.proposedCommand.speedMps - issued.speed_mps : 0;

  return (
    <section className="guided-stages" aria-labelledby="guided-stage-title">
      <header>
        <div><span className="eyebrow">GUIDED EVIDENCE SEQUENCE</span><h2 id="guided-stage-title">Five ways evidence can weaken</h2></div>
        <p>Each stage is a schema-valid synthetic fixture. Select a stage to inspect its retained evidence and recorded STUB response.</p>
      </header>
      <ol>
        {DEMO_STAGES.map((item) => {
          const selected = packet.scenarioId === item.id;
          return (
            <li key={item.id}>
              <button type="button" aria-current={selected ? "step" : undefined} onClick={() => onSelect(item.id)}>
                <span>{item.number}</span><strong>{item.shortLabel}</strong><small>{item.title}</small>
              </button>
            </li>
          );
        })}
      </ol>
      {stage && <div className="stage-reading" aria-live="polite">
        <div className="stage-copy"><span>Stage {stage.number}</span><h3>{stage.title}</h3><p>{stage.summary}</p><small>{stage.sourceNote}</small></div>
        <dl className="stage-consequences" aria-label="Fixture decision consequences">
          <div><dt>Margin</dt><dd>{expandedBy > 0.001 ? `${stage.baselineMarginM.toFixed(1)} → ${constraint?.minimum_margin.toFixed(1)} m` : `${constraint?.minimum_margin.toFixed(1) ?? "—"} m`}</dd><small>{expandedBy > 0.001 ? `expanded by ${expandedBy.toFixed(1)} m` : "no expansion recorded at this time"}</small></div>
          <div><dt>Speed</dt><dd>{speedReduction > 0.001 && issued ? `${packet.proposedCommand?.speedMps.toFixed(1)} → ${issued.speed_mps.toFixed(1)} m/s` : issued ? `${issued.speed_mps.toFixed(1)} m/s` : "unavailable"}</dd><small>{speedReduction > 0.001 ? `restricted by ${speedReduction.toFixed(1)} m/s` : packet.proposedCommand ? "no restriction recorded at this time" : "AI proposal unavailable"}</small></div>
          <div><dt>Authority</dt><dd>{decision ? sentence(decision.authority) : "unavailable"}</dd><small>{decision ? `${decision.candidate_id} fixture · ${decision.action}` : "no linked fixture decision"}</small></div>
        </dl>
      </div>}
    </section>
  );
}
