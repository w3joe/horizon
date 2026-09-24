import { useEffect, useMemo, useRef, useState } from "react";
import { DataFlowView } from "./components/DataFlowView";
import { DemoExperience } from "./components/DemoExperience";
import { EvidencePanel } from "./components/EvidencePanel";
import { GuidedStageSelector } from "./components/GuidedStageSelector";
import { MaritimeScene } from "./components/MaritimeScene";
import { NeuralView } from "./components/NeuralView";
import { OperatorControls } from "./components/OperatorControls";
import { SensorInputPanel } from "./components/SensorInputPanel";
import { TacticalChart } from "./components/TacticalChart";
import { Timeline } from "./components/Timeline";
import { useConsoleFeed } from "./hooks/useConsoleFeed";
import { useOperatorControls } from "./hooks/useOperatorControls";
import { usePerceptionArtifact } from "./hooks/usePerceptionArtifact";
import { SCENARIOS } from "./lib/fixtures";
import { applyConnectedCounterfactual } from "./lib/connectedCounterfactual";
import { collisionContact } from "./lib/collision";
import { applySensorInputs, createSingaporeTraffic, DEFAULT_SENSOR_INPUTS } from "./lib/sensorSimulation";
import type { ScenarioId, SensorInputs, SystemMode, Workspace } from "./types";
import "./styles/demo.css";

const DURATION_S = 300;

function HorizonMark() {
  return <svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" /><path d="M7 23c6-6 10 6 17 0s8 0 9 0M20 6v9m-4-4 4 4 4-4" /></svg>;
}

export function App() {
  const [experience, setExperience] = useState<"dashboard" | "demo">(() => {
    const view = new URLSearchParams(window.location.search).get("view");
    return view === "demo" ? view : "dashboard";
  });
  const [systemMode, setSystemMode] = useState<SystemMode>(() => {
    const mode = new URLSearchParams(window.location.search).get("mode");
    if (mode === "connected" || mode === "simulation") return mode;
    return import.meta.env.PROD ? "connected" : "simulation";
  });
  const [connectRequest, setConnectRequest] = useState(0);

  const chooseExperience = (view: "dashboard" | "demo") => {
    setExperience(view);
    const url = new URL(window.location.href);
    url.searchParams.set("view", view);
    window.history.replaceState(null, "", url);
  };

  const chooseMode = (mode: SystemMode) => {
    setSystemMode(mode);
    if (mode === "connected") setConnectRequest((value) => value + 1);
    setExperience("dashboard");
    const url = new URL(window.location.href);
    url.searchParams.set("view", "dashboard");
    url.searchParams.set("mode", mode);
    window.history.replaceState(null, "", url);
  };

  return (
    <>
      <nav className="experience-switcher" aria-label="Console experience">
        <button type="button" aria-pressed={experience === "dashboard"} onClick={() => chooseExperience("dashboard")}>Dashboard</button>
        <button type="button" aria-pressed={experience === "demo"} onClick={() => chooseExperience("demo")}>Guided replay</button>
      </nav>
      {experience === "dashboard" ? <LiveConsole systemMode={systemMode} connectRequest={connectRequest} onModeChange={chooseMode} /> : <DemoExperience />}
    </>
  );
}

function LiveConsole({ systemMode, connectRequest, onModeChange }: { systemMode: SystemMode; connectRequest: number; onModeChange: (mode: SystemMode) => void }) {
  type DrawerId = "sensors" | "faults" | "evidence" | "timeline" | "operator";
  const [workspace, setWorkspace] = useState<Workspace>("navigation");
  const [scenarioId, setScenarioId] = useState<ScenarioId>("perception");
  const [displayMode, setDisplayMode] = useState<"2d" | "3d">("3d");
  const [timeS, setTimeS] = useState(31);
  const [playing, setPlaying] = useState(systemMode === "simulation");
  const [rate, setRate] = useState(1);
  const [showBranch, setShowBranch] = useState(true);
  const [horizonEnabled, setHorizonEnabled] = useState(true);
  const [selectedEventId, setSelectedEventId] = useState("evt-decide");
  const [selectedContactId, setSelectedContactId] = useState("contact-01");
  const [sensorInputs, setSensorInputs] = useState<SensorInputs>(DEFAULT_SENSOR_INPUTS);
  const [trafficRevision, setTrafficRevision] = useState(0);
  const [drawer, setDrawer] = useState<DrawerId | null>(null);
  const [collisionHalted, setCollisionHalted] = useState(false);
  const frame = useRef<number | null>(null);
  const previous = useRef<number | null>(null);
  const startedConnectRequest = useRef(-1);
  const singaporeTraffic = useMemo(() => createSingaporeTraffic(), [trafficRevision]);
  const feed = useConsoleFeed(scenarioId, timeS, systemMode === "connected");
  const { packet: sourcePacket, connection, endpoint } = feed;
  const packet = useMemo(() => systemMode === "simulation"
    ? applySensorInputs(sourcePacket, sensorInputs, singaporeTraffic, horizonEnabled)
    : applyConnectedCounterfactual(sourcePacket, horizonEnabled), [horizonEnabled, sensorInputs, singaporeTraffic, sourcePacket, systemMode]);
  const operator = useOperatorControls(endpoint !== null, feed.clearLiveEvidence);
  const perceptionArtifact = usePerceptionArtifact(true);
  const operatorLocked = systemMode === "connected";
  const displayTime = packet.fixture ? timeS : packet.snapshot.simulation_time_s;
  const timelineDuration = packet.fixture ? DURATION_S : Math.max(DURATION_S, Math.ceil(displayTime / 60) * 60);
  const selectedEvent = useMemo(() => packet.events.find((event) => event.id === selectedEventId), [packet.events, selectedEventId]);
  const showProjectedTrack = packet.fixture ? showBranch && horizonEnabled : true;
  const collidedContact = useMemo(() => collisionContact(packet.snapshot), [packet.snapshot]);

  useEffect(() => {
    setPlaying(systemMode === "simulation");
    setCollisionHalted(false);
    setDrawer(null);
    previous.current = null;
  }, [systemMode]);

  useEffect(() => {
    setCollisionHalted(false);
  }, [connectRequest, horizonEnabled, trafficRevision]);

  useEffect(() => {
    if (!collidedContact || collisionHalted) return;
    setCollisionHalted(true);
    setPlaying(false);
    if (systemMode === "connected") void operator.invoke("pause");
  }, [collidedContact, collisionHalted, operator, systemMode]);

  useEffect(() => {
    if (systemMode !== "connected" || !operator.state.capabilities || startedConnectRequest.current === connectRequest) return;
    startedConnectRequest.current = connectRequest;
    void operator.invoke("restart");
  }, [connectRequest, operator.state.capabilities, systemMode]);

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
            return next - DURATION_S;
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
    setSelectedEventId(reached?.id ?? "");
  }, [packet.events, packet.fixture, playing, timeS]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "Escape") { setDrawer(null); return; }
      if (["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === " ") { event.preventDefault(); if (packet.fixture && !operatorLocked && !collisionHalted) setPlaying((value) => !value); }
      if (event.key === "1") setWorkspace("navigation");
      if (event.key === "2") setWorkspace("data");
      if (event.key === "3") setWorkspace("neural");
      if (event.key.toLowerCase() === "r" && packet.fixture && !operatorLocked) { setCollisionHalted(false); setTimeS(0); setPlaying(true); setTrafficRevision((value) => value + 1); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [collisionHalted, operatorLocked, packet.fixture]);

  const changeScenario = (id: ScenarioId) => {
    setScenarioId(id);
    setTimeS(25);
    setPlaying(true);
    setSelectedEventId("evt-decide");
    setDrawer(null);
    if (id === "perception") setWorkspace("neural");
    else setWorkspace("data");
  };

  const toggleHorizon = () => {
    setCollisionHalted(false);
    setHorizonEnabled((value) => !value);
    if (systemMode === "connected") void operator.invoke("restart");
  };

  const changeMotionRate = (value: number) => {
    setRate(value);
    if (systemMode === "connected") void operator.invoke("rate", { multiplier: value });
  };

  return (
    <main className="app-shell compact-dashboard">
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
        <div className="system-mode-toggle" role="group" aria-label="Dashboard data source">
          <span>System mode</span>
          <button type="button" aria-pressed={systemMode === "connected"} onClick={() => onModeChange("connected")}><i />Connected</button>
          <button type="button" aria-pressed={systemMode === "simulation"} onClick={() => onModeChange("simulation")}>Simulation</button>
        </div>
        <div className="panel-toggles" aria-label="Dashboard panels">
          {systemMode === "simulation" && <button type="button" aria-pressed={drawer === "sensors"} onClick={() => setDrawer((value) => value === "sensors" ? null : "sensors")}>Sensors</button>}
          {systemMode === "simulation" && <button type="button" aria-pressed={drawer === "faults"} onClick={() => setDrawer((value) => value === "faults" ? null : "faults")}>Fault profiles</button>}
          {operatorLocked && <button type="button" aria-pressed={drawer === "operator"} onClick={() => setDrawer((value) => value === "operator" ? null : "operator")}>Operator</button>}
          <button type="button" aria-pressed={drawer === "evidence"} onClick={() => setDrawer((value) => value === "evidence" ? null : "evidence")}>Evidence</button>
          <button type="button" aria-pressed={drawer === "timeline"} onClick={() => setDrawer((value) => value === "timeline" ? null : "timeline")}>Playback</button>
        </div>
      </section>

      <div className="mode-disclosure">
        <strong>{collisionHalted ? "COLLISION · SIMULATION HALTED" : operator.state.pendingAction === "restart" ? "RESTARTING CONNECTED DEMO" : packet.fixture ? operatorLocked ? "CONNECTED MODE · BACKEND UNAVAILABLE" : "SIMULATION MODE" : !horizonEnabled ? "CONNECTED COUNTERFACTUAL · HORIZON OFF" : "CONNECTED SYSTEM"}</strong>
        <span>{collisionHalted ? `Physical overlap with ${collidedContact?.vessel_id ?? "traffic vessel"} detected at T+${displayTime.toFixed(1)} s. Restart or generate new traffic to continue.` : operator.state.pendingAction === "restart" ? "Resetting the Singapore plant, waiting for fresh recovery evidence, then resuming automatically" : packet.fixture ? operatorLocked ? "Waiting for the live backend · synthetic layout fallback only" : "Editable synthetic sensor evidence · no measured performance or live safety claim" : !horizonEnabled ? "Live backend sensor traffic and clock · display-only unprotected ownship · protected actuator authority remains intact" : `Public online evidence · ${packet.authority.state} authority · ${packet.authority.explanation}`}</span>
        {endpoint && connection !== "live" && <code>{endpoint}</code>}
      </div>
      {!packet.fixture && <div className="service-strip" aria-label="Upstream service freshness">{Object.entries(packet.serviceFreshness).map(([name, freshness]) => <span key={name} className={freshness.state}><i />{name} <b>{freshness.state}</b>{freshness.lastSuccessAt !== null && freshness.state !== "live" ? <small>{Math.max(0, (Date.now() - freshness.lastSuccessAt) / 1000).toFixed(1)}s since update</small> : null}</span>)}</div>}

      <section className="console-grid">
        <div className="primary-workspace">
          {workspace === "navigation" && (
            <>
              <div className="scene-toolbar">
                <div className="scene-view-controls">
                  <div className="dimension-toggle" role="group" aria-label="Navigation view"><button type="button" aria-pressed={displayMode === "2d"} onClick={() => setDisplayMode("2d")}>2D</button><button type="button" aria-pressed={displayMode === "3d"} onClick={() => setDisplayMode("3d")}>3D</button></div>
                  <button type="button" className="horizon-toggle" aria-pressed={horizonEnabled} disabled={systemMode === "connected" && operator.state.pendingAction !== null} title={systemMode === "connected" ? "Restart the encounter and compare the live sensor traffic with an unprotected counterfactual" : "Compare the protected route with the unprotected collision course"} onClick={toggleHorizon}><i />Horizon {horizonEnabled ? "ON" : "OFF"}</button>
                  <div className="motion-rate" role="group" aria-label="Demo motion speed"><span>Motion</span>{[1, 2, 4].map((value) => <button key={value} type="button" aria-pressed={rate === value} disabled={systemMode === "connected" && operator.state.pendingAction !== null} onClick={() => changeMotionRate(value)}>{value}×</button>)}</div>
                </div>
                <div className="scene-legend">{packet.acceptedPath.length > 1 ? <span className="accepted">{!packet.fixture ? horizonEnabled ? packet.authority.state === "current" ? "Horizon-protected actual command" : "Live plant trajectory" : "Protected backend comparison" : !horizonEnabled ? "Unprotected collision course" : "Protected trajectory"}</span> : <span className="unavailable">Applied trajectory unavailable</span>}{packet.proposedPath.length > 1 ? <span className="proposed">{packet.fixture ? "AI-proposed trajectory" : horizonEnabled ? "Unprotected ownship projection" : "Active unprotected counterfactual"}</span> : <span className="unavailable">Proposed trajectory unavailable</span>}{showProjectedTrack && packet.branchPath.length > 1 && <span className="branch">{packet.fixture ? "Unprotected comparison" : "Collision-course vessel prediction"}</span>}</div>
              </div>
              {displayMode === "3d"
                ? <MaritimeScene packet={packet} cameraMode="oblique" selectedContactId={selectedContactId} onSelectContact={setSelectedContactId} showBranch={showProjectedTrack} motionRate={rate} frozen={collisionHalted} />
                : <TacticalChart packet={packet} selectedContactId={selectedContactId} onSelectContact={setSelectedContactId} showBranch={showProjectedTrack} frozen={collisionHalted} />}
              <div className="scene-footnote"><span>{packet.physicsLabel}</span><span>Ownship hull 12 × 3 m</span></div>
            </>
          )}
          {workspace === "data" && <DataFlowView packet={packet} event={selectedEvent} />}
          {workspace === "neural" && <NeuralView packet={packet} event={selectedEvent} artifact={perceptionArtifact} />}
        </div>
      </section>

      {drawer && <>
        <button type="button" className="drawer-backdrop" aria-label="Close panel" onClick={() => setDrawer(null)} />
        <aside className={`dashboard-drawer ${drawer === "timeline" ? "timeline-drawer" : ""}`} aria-label={`${drawer} panel`}>
          <header><div><span>DASHBOARD PANEL</span><strong>{drawer === "sensors" ? "Simulation sensors" : drawer === "faults" ? "Fault profiles" : drawer === "evidence" ? "Runtime evidence" : drawer === "operator" ? "Connected controls" : "Playback and events"}</strong></div><button type="button" aria-label="Close panel" onClick={() => setDrawer(null)}>×</button></header>
          <div className="drawer-content">
            {drawer === "sensors" && <SensorInputPanel value={sensorInputs} onChange={setSensorInputs} onRegenerateTraffic={() => { setCollisionHalted(false); setTrafficRevision((value) => value + 1); setTimeS(0); setPlaying(true); }} />}
            {drawer === "faults" && <><div className="drawer-scenario-controls"><label><span>Active response profile</span><select value={scenarioId} onChange={(event) => changeScenario(event.target.value as ScenarioId)}>{SCENARIOS.map((scenario) => <option value={scenario.id} key={scenario.id}>{scenario.label}</option>)}</select></label><button type="button" className={showBranch ? "active" : ""} onClick={() => setShowBranch((value) => !value)}>Compare branch {showBranch ? "on" : "off"}</button></div><GuidedStageSelector packet={packet} onSelect={changeScenario} /></>}
            {drawer === "operator" && <OperatorControls state={operator.state} onAction={operator.invoke} />}
            {drawer === "evidence" && <EvidencePanel packet={packet} selectedEvent={selectedEvent} />}
            {drawer === "timeline" && <Timeline
              timeS={displayTime}
              durationS={timelineDuration}
              playing={playing}
              rate={rate}
              events={packet.events}
              selectedEventId={selectedEventId}
              onTimeChange={(value) => { setTimeS(value); setPlaying(false); const reached = [...packet.events].reverse().find((event) => event.timeS <= value); setSelectedEventId(reached?.id ?? ""); }}
              onTogglePlaying={() => { if (!collisionHalted) setPlaying((value) => !value); }}
              onReset={() => { setCollisionHalted(false); setTimeS(0); setPlaying(systemMode === "simulation"); setSelectedEventId("evt-fault"); if (systemMode === "simulation") setTrafficRevision((value) => value + 1); }}
              onRateChange={setRate}
              onSelectEvent={(id) => { const event = packet.events.find((item) => item.id === id); setSelectedEventId(id); if (event) { setTimeS(event.timeS); setPlaying(false); } }}
              disabled={collisionHalted || operatorLocked || !packet.fixture}
              disabledReason={collisionHalted ? "Collision detected; restart the encounter to continue" : operator.state.resetRequested ? "Live run paused while reset waits for fresh recovery evidence" : "Live playback follows the plant; use the mediated operator controls above"}
            />}
          </div>
        </aside>
      </>}
      <footer className="console-footer"><span>Singapore Strait · {systemMode === "simulation" ? `${singaporeTraffic.vessels.length + 1} moving vessels · Space pause · R new traffic` : "backend traffic"}</span><span>Contract 0.1.0 · {packet.snapshot.frame}</span></footer>
    </main>
  );
}
