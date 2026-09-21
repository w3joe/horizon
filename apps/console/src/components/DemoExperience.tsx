import { useMemo, useRef, useState } from "react";
import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import { useDemoReplay } from "../hooks/useDemoReplay";
import type { DemoBranch, DemoCameraMode, DemoFrame, DemoReplay, DemoStoryStage } from "../lib/demoTypes";
import { DemoScene } from "./DemoScene";

interface TrailPoint {
  north: number;
  east: number;
}

function HorizonMark() {
  return <svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" /><path d="M7 23c6-6 10 6 17 0s8 0 9 0M20 6v9m-4-4 4 4 4-4" /></svg>;
}

function PlayIcon({ playing }: { playing: boolean }) {
  return playing
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z" /></svg>;
}

function ReplayIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5.4 7.3A8 8 0 1 1 4 14h2.2a5.8 5.8 0 1 0 1-4.8L10 12H3V5z" /></svg>;
}

function formatTime(timeS: number) {
  const minutes = Math.floor(timeS / 60);
  const seconds = timeS - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function formatHeading(rad: number | null | undefined) {
  if (rad === null || rad === undefined || !Number.isFinite(rad)) return "—";
  return `${((((rad * 180) / Math.PI) + 360) % 360).toFixed(0)}°`;
}

function formatSpeed(speed: number | null | undefined) {
  return speed === null || speed === undefined || !Number.isFinite(speed) ? "—" : `${speed.toFixed(1)} m/s`;
}

function humanize(value: string) {
  return value.toLowerCase().replaceAll("_", " ");
}

function latestAt<T extends { time_s: number }>(items: T[], timeS: number): T | null {
  let latest: T | null = null;
  for (const item of items) {
    if (item.time_s > timeS) break;
    latest = item;
  }
  return latest;
}

function mix(a: number, b: number, amount: number) {
  return a + (b - a) * amount;
}

function mixAngle(a: number, b: number, amount: number) {
  const delta = Math.atan2(Math.sin(b - a), Math.cos(b - a));
  return a + delta * amount;
}

function interpolateSnapshot(replay: DemoReplay, timeS: number, branch: DemoBranch): SimulationSnapshot {
  const frames = replay.timeline.frames;
  let left = frames[0];
  let right = frames[frames.length - 1];
  for (let index = 0; index < frames.length; index += 1) {
    if (frames[index].time_s <= timeS) left = frames[index];
    if (frames[index].time_s >= timeS) { right = frames[index]; break; }
  }
  const start = left[branch];
  const end = right[branch];
  const span = right.time_s - left.time_s;
  const amount = span > 0 ? Math.max(0, Math.min(1, (timeS - left.time_s) / span)) : 0;
  const traffic = start.traffic.map((vessel) => {
    const next = end.traffic.find((item) => item.vessel_id === vessel.vessel_id) ?? vessel;
    return {
      ...vessel,
      position_ne_m: [
        mix(vessel.position_ne_m[0], next.position_ne_m[0], amount),
        mix(vessel.position_ne_m[1], next.position_ne_m[1], amount),
      ] as [number, number],
      heading_rad: mixAngle(vessel.heading_rad, next.heading_rad, amount),
      speed_mps: mix(vessel.speed_mps, next.speed_mps, amount),
    };
  });
  return {
    ...start,
    simulation_time_s: timeS,
    ownship: {
      ...start.ownship,
      position_ne_m: [
        mix(start.ownship.position_ne_m[0], end.ownship.position_ne_m[0], amount),
        mix(start.ownship.position_ne_m[1], end.ownship.position_ne_m[1], amount),
      ],
      heading_rad: mixAngle(start.ownship.heading_rad, end.ownship.heading_rad, amount),
      speed_mps: mix(start.ownship.speed_mps, end.ownship.speed_mps, amount),
      ...(start.ownship.heave_down_m !== undefined && end.ownship.heave_down_m !== undefined ? {
        heave_down_m: mix(start.ownship.heave_down_m, end.ownship.heave_down_m, amount),
      } : {}),
      ...(start.ownship.attitude_rp_rad && end.ownship.attitude_rp_rad ? {
        attitude_rp_rad: [
          mixAngle(start.ownship.attitude_rp_rad[0], end.ownship.attitude_rp_rad[0], amount),
          mixAngle(start.ownship.attitude_rp_rad[1], end.ownship.attitude_rp_rad[1], amount),
        ] as [number, number],
      } : {}),
    },
    traffic,
  };
}

function trailFor(replay: DemoReplay, timeS: number, branch: DemoBranch): TrailPoint[] {
  return replay.timeline.frames
    .filter((frame) => frame.time_s <= timeS)
    .map((frame) => ({ north: frame[branch].ownship.position_ne_m[0], east: frame[branch].ownship.position_ne_m[1] }));
}

function interventionSampleTime(replay: DemoReplay): number {
  const exactTime = replay.intervention.time_s;
  if (exactTime === null) return replay.timeline.end_s;
  const eligibleFrames = replay.timeline.frames.filter((frame) => frame.time_s >= exactTime);
  const matched = replay.intervention.command_id
    ? eligibleFrames.find((frame) => frame.protected_command?.command_id === replay.intervention.command_id)
    : null;
  return (matched ?? eligibleFrames[0])?.time_s ?? replay.timeline.end_s;
}

function storyFor(replay: DemoReplay): DemoStoryStage[] {
  const { start_s: start, end_s: end } = replay.timeline;
  const interventionTime = interventionSampleTime(replay);
  const firstProposal = replay.public_evidence.proposals[0]?.time_s ?? start;
  const collisionTime = replay.outcome_summary.counterfactual.first_collision_time_s ?? end;
  const mechanism = replay.intervention.mechanism === "gate_watchdog"
    ? "The independent actuator-gate watchdog applies the stored recovery command."
    : replay.intervention.mechanism === "assurance_decision"
      ? "The assurance supervisor sends a recovery command through the independent actuator gate."
      : "No protected intervention is recorded in this run.";
  const interventionSummary = replay.intervention.mode === "preventive_guard"
    ? "Recovery was applied before an unsafe command was observed active at the protected plant."
    : mechanism;
  const counterfactual = replay.outcome_summary.counterfactual;
  const protectedOutcome = replay.outcome_summary.protected;
  return [
    {
      id: "proposal",
      number: "01",
      timeS: firstProposal,
      eyebrow: "Autonomy request",
      title: "Unsafe course requested",
      summary: "The external decision AI requests the recorded course. Both branches start from the same physical state; the comparison branch holds the straight-ahead command.",
    },
    {
      id: "takeover",
      number: "02",
      timeS: interventionTime,
      eyebrow: "Actual intervention",
      title: replay.intervention.occurred
        ? replay.intervention.mode === "preventive_guard" ? "Preventive safety guard" : "The safety layer takes authority"
        : "No takeover was recorded",
      summary: interventionSummary,
    },
    {
      id: "collision",
      number: "03",
      timeS: collisionTime,
      eyebrow: "Comparison marker",
      title: counterfactual.collision_count > 0 ? "Without RTA, the vessels collide" : "The comparison branch continues",
      summary: counterfactual.collision_count > 0
        ? `Post-run evaluation records the counterfactual collision at ${formatTime(collisionTime)}. This marker is never used by the online controller.`
        : "Post-run evaluation records no collision in the counterfactual branch.",
    },
    {
      id: "outcome",
      number: "04",
      timeS: end,
      eyebrow: "Recorded outcome",
      title: protectedOutcome.collision_count === 0 && counterfactual.collision_count > 0
        ? "One encounter, two different outcomes"
        : "Compare the recorded outcomes",
      summary: `Protected minimum clearance: ${protectedOutcome.min_hull_clearance_m.toFixed(1)} m. Without-RTA minimum clearance: ${counterfactual.min_hull_clearance_m.toFixed(1)} m.`,
    },
  ];
}

function CurrentEvidence({ replay, frame, timeS }: { replay: DemoReplay; frame: DemoFrame; timeS: number }) {
  const proposal = latestAt(replay.public_evidence.proposals, timeS)?.record ?? null;
  const decision = latestAt(replay.public_evidence.decisions, timeS)?.record ?? null;
  const receipt = latestAt(replay.public_evidence.receipts, timeS)?.record ?? null;
  const protectedCommand = frame.protected_command;
  const interventionReached = replay.intervention.occurred
    && replay.intervention.time_s !== null
    && timeS >= replay.intervention.time_s;
  const reasons = interventionReached
    ? replay.intervention.reason_codes
    : receipt?.reason_codes.length ? receipt.reason_codes : decision?.reason_codes ?? [];
  const interventionLabel = replay.intervention.mode === "preventive_guard" ? "Safety guard engaged" : "Safety takeover recorded";
  return (
    <section className="demo-evidence" aria-label="Current replay evidence">
      <div className="demo-evidence-heading">
        <span>At {formatTime(timeS)}</span>
        <strong>{interventionReached ? interventionLabel : proposal ? "Autonomy is proposing a command" : "Waiting for the first proposal"}</strong>
      </div>
      <div className="command-compare">
        <article className="command-card proposed-command">
          <span>AI proposed</span>
          <strong>{formatHeading(proposal?.command.heading_rad)} <small>heading</small></strong>
          <b>{formatSpeed(proposal?.command.speed_mps)}</b>
        </article>
        <div className="command-arrow" aria-hidden="true">→</div>
        <article className="command-card issued-command">
          <span>Actually issued</span>
          {protectedCommand ? <><strong>{formatHeading(protectedCommand.heading_rad)} <small>heading</small></strong><b>{formatSpeed(protectedCommand.speed_mps)}</b></> : <strong className="command-unmatched">No command match at this sample</strong>}
        </article>
      </div>
      <div className="reason-line">
        <span>Why</span>
        <p>{reasons.length ? reasons.map(humanize).join(" · ") : "No intervention reason has been recorded at this point."}</p>
      </div>
    </section>
  );
}

function OutcomeCard({ title, outcome, tone }: { title: string; outcome: DemoReplay["outcome_summary"]["protected"]; tone: DemoBranch }) {
  return (
    <article className={`outcome-card ${tone}`}>
      <header><span>{title}</span><strong>{outcome.collision_count > 0 ? "Collision recorded" : "No collision recorded"}</strong></header>
      <dl>
        <div><dt>Minimum hull clearance</dt><dd>{outcome.min_hull_clearance_m.toFixed(1)} m</dd></div>
        <div><dt>Final hull clearance</dt><dd>{outcome.final_hull_clearance_m.toFixed(1)} m</dd></div>
      </dl>
    </article>
  );
}

export function DemoExperience() {
  const demo = useDemoReplay();
  const [cameraMode, setCameraMode] = useState<DemoCameraMode>("oblique");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const branchSection = useRef<HTMLElement | null>(null);

  const story = useMemo(() => demo.replay ? storyFor(demo.replay) : [], [demo.replay]);
  const activeStage = story.reduce((selected, stage, index) => demo.timeS >= stage.timeS ? index : selected, 0);
  const protectedSnapshot = useMemo(() => demo.replay ? interpolateSnapshot(demo.replay, demo.timeS, "protected") : null, [demo.replay, demo.timeS]);
  const counterfactualSnapshot = useMemo(() => demo.replay ? interpolateSnapshot(demo.replay, demo.timeS, "counterfactual") : null, [demo.replay, demo.timeS]);
  const protectedTrail = useMemo(() => demo.replay ? trailFor(demo.replay, demo.timeS, "protected") : [], [demo.replay, demo.timeS]);
  const counterfactualTrail = useMemo(() => demo.replay ? trailFor(demo.replay, demo.timeS, "counterfactual") : [], [demo.replay, demo.timeS]);

  const showReplay = () => {
    branchSection.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  };

  if (demo.loadState === "loading-catalog" || demo.loadState === "loading-replay") {
    return (
      <main className="demo-shell demo-state-page">
        <div className="demo-state-card" role="status"><span className="recorded-badge">Recorded simulation</span><div className="loading-orbit" /><h1>Preparing the safety replay</h1><p>Loading the recorded run manifest and synchronized branch record.</p></div>
      </main>
    );
  }

  if (demo.loadState === "error" || !demo.replay || !demo.frame || !protectedSnapshot || !counterfactualSnapshot) {
    return (
      <main className="demo-shell demo-state-page">
        <div className="demo-state-card error"><span className="recorded-badge">Recorded simulation unavailable</span><h1>The replay could not be verified</h1><p>{demo.error ?? "The recorded run is missing."}</p><button type="button" onClick={demo.retryCatalog}>Try again</button><small>No fixture or synthetic result has been substituted.</small></div>
      </main>
    );
  }

  const { replay, frame } = demo;
  const atEnd = demo.timeS >= replay.timeline.end_s;
  const interventionReached = replay.intervention.occurred && replay.intervention.time_s !== null && demo.timeS >= replay.intervention.time_s;
  const protectedCollision = replay.outcome_summary.protected.first_collision_time_s != null && demo.timeS >= replay.outcome_summary.protected.first_collision_time_s;
  const counterfactualCollision = replay.outcome_summary.counterfactual.first_collision_time_s != null && demo.timeS >= replay.outcome_summary.counterfactual.first_collision_time_s;
  const interventionLabel = replay.intervention.mode === "preventive_guard" ? "Safety guard engaged" : "Safety takeover recorded";

  return (
    <main className="demo-shell">
      <header className="demo-header">
        <div className="demo-brand"><span><HorizonMark /></span><div><strong>HORIZON</strong><small>Runtime assurance at sea</small></div></div>
        <div className="demo-header-meta"><span className="recorded-badge"><i />Recorded simulation</span><span>{replay.manifest.scenario.id} · {replay.manifest.duration_s.toFixed(0)} seconds</span></div>
      </header>

      <section className="demo-hero">
        <div className="demo-hero-copy">
          <span className="hero-kicker">A real local service run, replayed</span>
          <h1>When autonomy fails,<br /><em>safety stays in control.</em></h1>
          <p>Watch the same maritime encounter unfold twice. The red branch continues without runtime assurance. The cyan branch shows the command that the protected plant actually received.</p>
          <div className="hero-actions">
            <button type="button" className="demo-primary" onClick={() => { if (demo.playing) { demo.togglePlaying(); return; } if (atEnd) demo.replayFromStart(); else demo.togglePlaying(); showReplay(); }}><PlayIcon playing={demo.playing} />{demo.playing ? "Pause demo" : atEnd ? "Replay safety demo" : demo.timeS > replay.timeline.start_s ? "Continue demo" : "Play safety demo"}</button>
            <button type="button" className="demo-secondary" onClick={() => { demo.seek(story[1]?.timeS ?? replay.timeline.start_s); showReplay(); }}>Jump to intervention</button>
          </div>
        </div>
        <aside className="demo-story" aria-label="Four-stage safety story">
          {story.map((stage, index) => (
            <button key={stage.id} type="button" className={index === activeStage ? "active" : index < activeStage ? "complete" : ""} onClick={() => { demo.seek(stage.timeS); if (stage.id === "takeover" || stage.id === "collision") showReplay(); }}>
              <span>{stage.number}</span><div><small>{stage.eyebrow}</small><strong>{stage.title}</strong></div><i />
            </button>
          ))}
        </aside>
      </section>

      <section ref={branchSection} className="branch-section" aria-label="Synchronized branch comparison">
        <header className="branch-section-header">
          <div><span className="section-kicker">Synchronized comparison</span><h2>Same encounter. Different authority.</h2></div>
          <div className="demo-camera-toggle" aria-label="Camera angle"><button type="button" aria-pressed={cameraMode === "oblique"} onClick={() => setCameraMode("oblique")}>Oblique</button><button type="button" aria-pressed={cameraMode === "tactical"} onClick={() => setCameraMode("tactical")}>Tactical</button></div>
        </header>
        <div className="branch-grid">
          <article className="branch-view protected">
            <header><div><i /><span>RTA protected</span></div><strong>{interventionReached ? interventionLabel : "Monitoring"}</strong></header>
            <div className="demo-scene-wrap"><DemoScene snapshot={protectedSnapshot} trail={protectedTrail} timeS={demo.timeS} branch="protected" cameraMode={cameraMode} collision={protectedCollision} intervention={interventionReached} playing={demo.playing} /></div>
            <footer><span>Recorded outcome · actual protected plant</span><b>{replay.outcome_summary.protected.min_hull_clearance_m.toFixed(1)} m minimum clearance</b></footer>
          </article>
          <article className="branch-view counterfactual">
            <header><div><i /><span>Without RTA</span></div><strong>Evaluation-only counterfactual</strong></header>
            <div className="demo-scene-wrap"><DemoScene snapshot={counterfactualSnapshot} trail={counterfactualTrail} timeS={demo.timeS} branch="counterfactual" cameraMode={cameraMode} collision={counterfactualCollision} intervention={false} playing={demo.playing} /></div>
            <footer><span>Recorded outcome · post-run comparison</span><b>{replay.outcome_summary.counterfactual.collision_count > 0 ? "Collision recorded" : `${replay.outcome_summary.counterfactual.min_hull_clearance_m.toFixed(1)} m minimum clearance`}</b></footer>
          </article>
        </div>
        <div className="replay-controls">
          <button className="round-control" type="button" aria-label={demo.playing ? "Pause replay" : "Play replay"} onClick={demo.togglePlaying}><PlayIcon playing={demo.playing} /></button>
          <button className="round-control" type="button" aria-label="Replay from start" onClick={demo.replayFromStart}><ReplayIcon /></button>
          <strong>{formatTime(demo.timeS)}</strong>
          <div className="demo-scrubber"><input type="range" min={replay.timeline.start_s} max={replay.timeline.end_s} step={replay.timeline.sample_period_s} value={demo.timeS} onChange={(event) => demo.seek(Number(event.target.value))} aria-label="Replay time" /><span style={{ width: `${demo.progress * 100}%` }} /></div>
          <strong>{formatTime(replay.timeline.end_s)}</strong>
          <label><span>Speed</span><select value={demo.rate} onChange={(event) => demo.setRate(Number(event.target.value))}>{demo.playbackRates.map((rate) => <option key={rate} value={rate}>{rate}×</option>)}</select></label>
        </div>
        <p className="visual-disclosure">Scene positions are interpolated between recorded public snapshots. Water and lighting are visual context only. <a href="https://sketchfab.com/3d-models/assault-boat-0d4fca4f2e014cd7b6aefd4653b1508c" target="_blank" rel="noreferrer">Vessel: tnnv · CC BY 4.0</a></p>
      </section>

      <section className="story-detail">
        <div className="current-story"><span>{story[activeStage].eyebrow}</span><h2>{story[activeStage].title}</h2><p>{story[activeStage].summary}</p></div>
        <CurrentEvidence replay={replay} frame={frame} timeS={demo.timeS} />
      </section>

      <section className="outcome-section">
        <header><span className="section-kicker">Actual recorded results</span><h2>The safety layer changed what reached the actuators.</h2><p>Outcome metrics come from evaluation-only post-run scoring. They are not online inputs to autonomy or assurance.</p></header>
        <div className="outcome-grid"><OutcomeCard title="RTA protected" outcome={replay.outcome_summary.protected} tone="protected" /><OutcomeCard title="Without RTA" outcome={replay.outcome_summary.counterfactual} tone="counterfactual" /></div>
      </section>

      <section className="demo-details">
        <button type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen((open) => !open)}><span>Technical details</span><small>{detailsOpen ? "Hide provenance and identifiers" : "Show provenance and identifiers"}</small><b>{detailsOpen ? "−" : "+"}</b></button>
        {detailsOpen && <div className="details-grid">
          <dl><div><dt>Run</dt><dd>{replay.run_id}</dd></div><div><dt>Source commit</dt><dd>{replay.manifest.source_commit}</dd></div><div><dt>Scenario version</dt><dd>{replay.manifest.scenario.version}</dd></div><div><dt>Replay digest</dt><dd>{replay.manifest.replay_sha256}</dd></div></dl>
          <dl><div><dt>Snapshot</dt><dd>{frame.protected.snapshot_id}</dd></div><div><dt>Proposal</dt><dd>{latestAt(replay.public_evidence.proposals, demo.timeS)?.record.command_id ?? "not yet available"}</dd></div><div><dt>Intervention</dt><dd>{replay.intervention.mechanism ? humanize(replay.intervention.mechanism) : "none recorded"}</dd></div><div><dt>Exact intervention time</dt><dd>{replay.intervention.time_s === null ? "none recorded" : `${replay.intervention.time_s.toFixed(3)} s`}</dd></div><div><dt>Receipt</dt><dd>{replay.intervention.source_receipt_id ?? "none recorded"}</dd></div></dl>
          <div className="details-notes"><strong>Boundaries of this evidence</strong><ul>{replay.manifest.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul><p>Recorded neural artifacts, when inspected in the live console, are separate evidence. This replay does not claim that a neural model caused the intervention.</p></div>
        </div>}
      </section>

      {demo.catalog && demo.catalog.runs.length > 1 && <section className="demo-run-picker"><label><span>Recorded run</span><select value={demo.selectedRunId ?? ""} onChange={(event) => demo.selectRun(event.target.value)}>{demo.catalog.runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.title}</option>)}</select></label></section>}
      <footer className="demo-footer"><div className="demo-brand compact"><span><HorizonMark /></span><strong>HORIZON</strong></div><p>Recorded local simulation · public replay evidence · no live-vessel claim</p><span>{replay.manifest.title}</span></footer>
    </main>
  );
}
