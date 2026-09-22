import { useEffect, useMemo, useRef, useState } from "react";
import { DataFlowView } from "./components/DataFlowView";
import { DemoExperience } from "./components/DemoExperience";
import { EvidencePanel } from "./components/EvidencePanel";
import { GuidedStageSelector } from "./components/GuidedStageSelector";
import { MaritimeScene } from "./components/MaritimeScene";
import { NeuralView } from "./components/NeuralView";
import { OperatorControls } from "./components/OperatorControls";
import { Timeline } from "./components/Timeline";
import { useConsoleFeed } from "./hooks/useConsoleFeed";
import { useOperatorControls } from "./hooks/useOperatorControls";
import { usePerceptionArtifact } from "./hooks/usePerceptionArtifact";
import { SCENARIOS } from "./lib/fixtures";
import type { CameraMode, ScenarioId, Workspace } from "./types";
import "./styles/demo.css";

const DURATION_S = 60;

function HorizonMark() {
  return <svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" /><path d="M7 23c6-6 10 6 17 0s8 0 9 0M20 6v9m-4-4 4 4 4-4" /></svg>;
}

export function App() {
  const [experience, setExperience] = useState<"demo" | "live">("demo");

  return (
    <>
      <nav className="experience-switcher" aria-label="Console experience">
        <button type="button" aria-pressed={experience === "demo"} onClick={() => setExperience("demo")}>Guided demo</button>
        <button type="button" aria-pressed={experience === "live"} onClick={() => setExperience("live")}>Live console</button>
      </nav>
      {experience === "demo" ? <DemoExperience /> : <LiveConsole />}
    </>
  );
}

function LiveConsole() {
  const [workspace, setWorkspace] = useState<Workspace>("navigation");
  const [scenarioId, setScenarioId] = useState<ScenarioId>("perception");
  const [cameraMode, setCameraMode] = useState<CameraMode>("oblique");
  const [timeS, setTimeS] = useState(31);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [showBranch, setShowBranch] = useState(true);
  const [selectedEventId, setSelectedEventId] = useState("evt-decide");
  const [selectedContactId, setSelectedContactId] = useState("contact-01");
  const frame = useRef<number | null>(null);
  const previous = useRef<number | null>(null);
  const feed = useConsoleFeed(scenarioId, timeS);
  const { packet, connection, endpoint } = feed;
  const operator = useOperatorControls(endpoint !== null, feed.clearLiveEvidence);
  const perceptionArtifact = usePerceptionArtifact(true);
  const operatorLocked = endpoint !== null;
  const displayTime = packet.fixture ? timeS : packet.snapshot.simulation_time_s;
  const timelineDuration = packet.fixture ? DURATION_S : Math.max(DURATION_S, Math.ceil(displayTime / 60) * 60);
  const selectedEvent = useMemo(() => packet.events.find((event) => event.id === selectedEventId), [packet.events, selectedEventId]);

  useEffect(() => {
    if (packet.fixture || packet.events.length === 0 || packet.events.some((event) => event.id === selectedEventId)) return;
    setSelectedEventId(packet.events.at(-1)?.id ?? "");
  }, [packet.events, packet.fixture, selectedEventId]);

  useEffect(() => {
    if (!playing || !packet.fixture) return;
    const update = (now: number) => {
      if (previous.current !== null) {
        const delta = Math.min(0.1, (now - previous.current) / 1000);
        setTimeS((current) => {
          const next = current + delta * rate;
          if (next >= DURATION_S) {
            setPlaying(false);
            return DURATION_S;
          }
          return next;
        });
      }
      previous.current = now;
      frame.current = requestAnimationFrame(update);
    };
    frame.current = requestAnimationFrame(update);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
      previous.current = null;
    };
  }, [playing, rate, packet.fixture]);

  useEffect(() => {
    if (!playing || !packet.fixture) return;
    const reached = [...packet.events].reverse().find((event) => event.timeS <= timeS);
    if (reached) setSelectedEventId(reached.id);
  }, [packet.events, packet.fixture, playing, timeS]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === " ") { event.preventDefault(); if (packet.fixture && !operatorLocked) setPlaying((value) => !value); }
      if (event.key === "1") setWorkspace("navigation");
      if (event.key === "2") setWorkspace("data");
      if (event.key === "3") setWorkspace("neural");
      if (event.key.toLowerCase() === "r" && packet.fixture && !operatorLocked) { setTimeS(0); setPlaying(false); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [operatorLocked, packet.fixture]);

  const changeScenario = (id: ScenarioId) => {
    setScenarioId(id);
    setTimeS(25);
    setPlaying(false);
    setSelectedEventId("evt-decide");
    if (id === "perception") setWorkspace("neural");
    else setWorkspace("data");
  };

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand"><span className="brand-mark"><HorizonMark /></span><div><strong>HORIZON</strong><small>Runtime assurance console</small></div></div>
        <div className="mission-title"><span>SINGAPORE STRAIT · SOUTHERN APPROACH</span><strong>{packet.scenarioLabel}</strong></div>
        <div className="runtime-status">
          <span className="candidate-pill"><small>{packet.fixture ? "Fixture candidate" : "Selected candidate"}</small><strong>{packet.decision ? `${packet.decision.candidate_id} · ${packet.decision.candidate_version}` : "unavailable"}</strong></span>
          <span className={`connection-pill ${connection}`}><i />{connection === "fixture" ? "Fixture stream" : connection}</span>
          <span className="clock">T+{displayTime.toFixed(1)} s</span>
        </div>
      </header>

      <section className="control-bar">
        <nav className="workspace-tabs" aria-label="Inspection workspace">
          {(["navigation", "data", "neural"] as Workspace[]).map((item, index) => <button key={item} type="button" aria-pressed={workspace === item} onClick={() => setWorkspace(item)}><span>0{index + 1}</span>{item === "data" ? "Data flow" : item === "neural" ? "Neural sensor" : "Navigation"}</button>)}
        </nav>
        <div className="scenario-controls">
          {operatorLocked
            ? <div className="live-scenario-summary"><span>Scenario</span><strong>Current launched scenario</strong></div>
            : <><label><span>Scenario / inject fault</span><select value={scenarioId} disabled={!packet.fixture} onChange={(event) => changeScenario(event.target.value as ScenarioId)}>{SCENARIOS.map((scenario) => <option value={scenario.id} key={scenario.id}>{scenario.label}</option>)}</select></label><button type="button" className={showBranch ? "active" : ""} disabled={!packet.fixture} onClick={() => setShowBranch((value) => !value)}>Compare branch</button></>}
          {operatorLocked && <span className="control-pending">Scenario changes require a new coordinated run.</span>}
        </div>
      </section>

      {packet.fixture && !operatorLocked && <GuidedStageSelector packet={packet} onSelect={changeScenario} />}

      {operatorLocked && <OperatorControls state={operator.state} onAction={operator.invoke} />}

      <div className="mode-disclosure">
        <strong>{packet.fixture ? operatorLocked ? "FIXTURE FALLBACK · LIVE DISCONNECTED" : "INITIAL FIXTURE MODE" : "LIVE PUBLIC MODE"}</strong>
        <span>{packet.fixture ? operatorLocked ? "Synthetic layout sample only · no live evidence claim" : "Synthetic schema-valid display data · no measured performance or validated safety claim" : `Public online evidence · ${packet.authority.state} authority · ${packet.authority.explanation}`}</span>
        {endpoint && connection !== "live" && <code>{endpoint}</code>}
      </div>
      {!packet.fixture && <div className="service-strip" aria-label="Upstream service freshness">{Object.entries(packet.serviceFreshness).map(([name, freshness]) => <span key={name} className={freshness.state}><i />{name} <b>{freshness.state}</b>{freshness.lastSuccessAt !== null && freshness.state !== "live" ? <small>{Math.max(0, (Date.now() - freshness.lastSuccessAt) / 1000).toFixed(1)}s since update</small> : null}</span>)}</div>}

      <section className="console-grid">
        <div className="primary-workspace">
          {workspace === "navigation" && (
            <>
              <div className="scene-toolbar">
                <div className="camera-toggle"><button type="button" aria-pressed={cameraMode === "oblique"} onClick={() => setCameraMode("oblique")}>Oblique</button><button type="button" aria-pressed={cameraMode === "tactical"} onClick={() => setCameraMode("tactical")}>Tactical</button></div>
                <div className="scene-legend">{packet.acceptedPath.length > 1 ? <span className="accepted">{packet.authority.state === "current" ? "Plant-active trajectory" : "Receipt trajectory (historical)"}</span> : <span className="unavailable">Applied trajectory unavailable</span>}{packet.proposedPath.length > 1 ? <span className="proposed">AI-proposed trajectory</span> : <span className="unavailable">Proposed trajectory unavailable</span>}{showBranch && packet.fixture && <span className="branch">Fixture branch prediction</span>}</div>
              </div>
              <MaritimeScene packet={packet} cameraMode={cameraMode} selectedContactId={selectedContactId} onSelectContact={setSelectedContactId} showBranch={showBranch && packet.fixture} />
              <div className="scene-footnote"><span>{packet.physicsLabel}</span><span>Ownship hull 12 × 3 m</span></div>
            </>
          )}
          {workspace === "data" && <DataFlowView packet={packet} event={selectedEvent} />}
          {workspace === "neural" && <NeuralView packet={packet} event={selectedEvent} artifact={perceptionArtifact} />}
        </div>
        <EvidencePanel packet={packet} selectedEvent={selectedEvent} />
      </section>

      <Timeline
        timeS={displayTime}
        durationS={timelineDuration}
        playing={playing}
        rate={rate}
        events={packet.events}
        selectedEventId={selectedEventId}
        onTimeChange={(value) => {
          setTimeS(value);
          setPlaying(false);
          const reached = [...packet.events].reverse().find((event) => event.timeS <= value);
          setSelectedEventId(reached?.id ?? "");
        }}
        onTogglePlaying={() => setPlaying((value) => !value)}
        onReset={() => { setTimeS(0); setPlaying(false); setSelectedEventId("evt-fault"); }}
        onRateChange={setRate}
        onSelectEvent={(id) => { const event = packet.events.find((item) => item.id === id); setSelectedEventId(id); if (event) { setTimeS(event.timeS); setPlaying(false); } }}
        disabled={operatorLocked || !packet.fixture}
        disabledReason={operator.state.resetRequested ? "Live run paused while reset waits for fresh recovery evidence" : "Live playback follows the plant; use the mediated operator controls above"}
      />
      <footer className="console-footer"><span>Keyboard: 1–3 workspaces · Space play/pause · R reset</span><span>Contract 0.1.0 · {packet.snapshot.frame}</span></footer>
    </main>
  );
}
