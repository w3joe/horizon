import { useEffect, useMemo, useRef, useState } from "react";
import { DataFlowView } from "./components/DataFlowView";
import { EvidencePanel } from "./components/EvidencePanel";
import { MaritimeScene } from "./components/MaritimeScene";
import { NeuralView } from "./components/NeuralView";
import { Timeline } from "./components/Timeline";
import { useConsoleFeed } from "./hooks/useConsoleFeed";
import { SCENARIOS } from "./lib/fixtures";
import type { CameraMode, ScenarioId, Workspace } from "./types";

const DURATION_S = 60;

function HorizonMark() {
  return <svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" /><path d="M7 23c6-6 10 6 17 0s8 0 9 0M20 6v9m-4-4 4 4 4-4" /></svg>;
}

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>("navigation");
  const [scenarioId, setScenarioId] = useState<ScenarioId>("camera");
  const [cameraMode, setCameraMode] = useState<CameraMode>("oblique");
  const [timeS, setTimeS] = useState(31);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [showBranch, setShowBranch] = useState(true);
  const [selectedEventId, setSelectedEventId] = useState("evt-decide");
  const [selectedContactId, setSelectedContactId] = useState("contact-01");
  const frame = useRef<number | null>(null);
  const previous = useRef<number | null>(null);
  const { packet, connection, endpoint } = useConsoleFeed(scenarioId, timeS);
  const displayTime = packet.fixture ? timeS : packet.snapshot.simulation_time_s;
  const selectedEvent = useMemo(() => packet.events.find((event) => event.id === selectedEventId), [packet.events, selectedEventId]);

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
      if (event.key === " ") { event.preventDefault(); if (packet.fixture) setPlaying((value) => !value); }
      if (event.key === "1") setWorkspace("navigation");
      if (event.key === "2") setWorkspace("data");
      if (event.key === "3") setWorkspace("neural");
      if (event.key.toLowerCase() === "r" && packet.fixture) { setTimeS(0); setPlaying(false); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [packet.fixture]);

  const changeScenario = (id: ScenarioId) => {
    setScenarioId(id);
    setTimeS(18);
    setPlaying(true);
    setSelectedEventId("evt-fault");
  };

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand"><span className="brand-mark"><HorizonMark /></span><div><strong>HORIZON</strong><small>Runtime assurance console</small></div></div>
        <div className="mission-title"><span>COASTAL TRANSIT · SECTOR 04</span><strong>{packet.scenarioLabel}</strong></div>
        <div className="runtime-status">
          <span className={`connection-pill ${connection}`}><i />{connection === "fixture" ? "Fixture stream" : connection}</span>
          <span className="clock">T+{displayTime.toFixed(1)} s</span>
        </div>
      </header>

      <section className="control-bar">
        <nav className="workspace-tabs" aria-label="Inspection workspace">
          {(["navigation", "data", "neural"] as Workspace[]).map((item, index) => <button key={item} type="button" aria-pressed={workspace === item} onClick={() => setWorkspace(item)}><span>0{index + 1}</span>{item === "data" ? "Data flow" : item === "neural" ? "Neural sensor" : "Navigation"}</button>)}
        </nav>
        <div className="scenario-controls">
          <label><span>Scenario / inject fault</span><select value={scenarioId} disabled={!packet.fixture} onChange={(event) => changeScenario(event.target.value as ScenarioId)}>{SCENARIOS.map((scenario) => <option value={scenario.id} key={scenario.id}>{scenario.label}</option>)}</select></label>
          <button type="button" className={showBranch ? "active" : ""} disabled={!packet.fixture} onClick={() => setShowBranch((value) => !value)}>Compare branch</button>
        </div>
      </section>

      <div className="mode-disclosure">
        <strong>{packet.fixture ? "INITIAL FIXTURE MODE" : "LIVE PUBLIC MODE"}</strong>
        <span>{packet.fixture ? "Synthetic schema-valid display data · no measured performance or validated safety claim" : "Public sensor-derived simulator state · no truth, control token, or assurance decision available"}</span>
        {endpoint && connection !== "live" && <code>{endpoint}</code>}
      </div>

      <section className="console-grid">
        <div className="primary-workspace">
          {workspace === "navigation" && (
            <>
              <div className="scene-toolbar">
                <div className="camera-toggle"><button type="button" aria-pressed={cameraMode === "oblique"} onClick={() => setCameraMode("oblique")}>Oblique</button><button type="button" aria-pressed={cameraMode === "tactical"} onClick={() => setCameraMode("tactical")}>Tactical</button></div>
                <div className="scene-legend"><span className="accepted">Accepted</span><span className="proposed">Proposed</span>{showBranch && <span className="branch">Unprotected prediction</span>}</div>
              </div>
              <MaritimeScene packet={packet} cameraMode={cameraMode} selectedContactId={selectedContactId} onSelectContact={setSelectedContactId} showBranch={showBranch && packet.fixture} />
              <div className="scene-footnote"><span>{packet.physicsLabel}</span><span>Ownship hull 12 × 3 m</span></div>
            </>
          )}
          {workspace === "data" && <DataFlowView packet={packet} event={selectedEvent} />}
          {workspace === "neural" && <NeuralView packet={packet} event={selectedEvent} />}
        </div>
        <EvidencePanel packet={packet} selectedEvent={selectedEvent} />
      </section>

      <Timeline
        timeS={displayTime}
        durationS={DURATION_S}
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
        disabled={!packet.fixture}
      />
      <footer className="console-footer"><span>Keyboard: 1–3 workspaces · Space play/pause · R reset</span><span>Contract 0.1.0 · {packet.snapshot.frame}</span></footer>
    </main>
  );
}
