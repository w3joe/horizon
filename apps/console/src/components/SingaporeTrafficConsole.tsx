import { useEffect, useMemo, useState } from "react";
import { MaritimeScene } from "./MaritimeScene";
import {
  createSingaporePacket,
  nedToWgs84,
  singaporeDemo,
  singaporeGeography,
  type SingaporeBranch,
  type SingaporeContact,
  type SingaporeStateId,
} from "../lib/singaporeTraffic";
import "../styles/singapore.css";

const MAP_WIDTH = 1000;
const MAP_HEIGHT = 620;
const LIVE_BBOX: [number, number, number, number] = [103.55, 1.10, 104.15, 1.50];
type SourceMode = "auto" | "live" | "recorded" | "synthetic";

interface LiveTrafficSnapshot {
  schema_version: "horizon.live-traffic.v1";
  source_state: "healthy" | "degraded" | "unknown" | "unavailable";
  last_frame_age_s: number | null;
  contact_count: number;
  reason_codes: string[];
  contacts: Array<Omit<SingaporeContact, "provenance" | "source_label" | "health" | "cpa_m" | "tcpa_s" | "role"> & { latitude_deg: number; longitude_deg: number }>;
}

function initialSource(): SourceMode {
  const source = new URLSearchParams(window.location.search).get("source");
  return source === "live" || source === "recorded" || source === "synthetic" ? source : "auto";
}

function initialState(): SingaporeStateId {
  return new URLSearchParams(window.location.search).get("state") === "conflict" ? "conflict" : "overview";
}

function project(longitude: number, latitude: number, bbox = singaporeDemo.map.bbox): [number, number] {
  const [west, south, east, north] = bbox;
  return [
    ((longitude - west) / (east - west)) * MAP_WIDTH,
    ((north - latitude) / (north - south)) * MAP_HEIGHT,
  ];
}

function geoPath(coordinates: number[][][], bbox = singaporeDemo.map.bbox): string {
  return coordinates.map((ring) => ring.map(([longitude, latitude], index) => {
    const [x, y] = project(longitude, latitude, bbox);
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ") + " Z").join(" ");
}

function pointFor(contact: Pick<SingaporeContact, "north_m" | "east_m">, bbox = singaporeDemo.map.bbox): [number, number] {
  const [longitude, latitude] = nedToWgs84(contact.north_m, contact.east_m);
  return project(longitude, latitude, bbox);
}

function markerShape(contact: SingaporeContact, selected: boolean, bbox = singaporeDemo.map.bbox) {
  const [x, y] = pointFor(contact, bbox);
  const scale = bbox === singaporeDemo.map.bbox ? 2.45 : .008;
  const uncertaintyPx = Math.max(5, Math.min(24, contact.uncertainty_m * scale));
  const transform = `translate(${x} ${y}) rotate(${(contact.heading_rad * 180) / Math.PI})`;
  return (
    <g className={`traffic-marker ${contact.provenance} ${contact.health} ${contact.role} ${selected ? "selected" : ""}`} transform={transform}>
      <circle className="uncertainty-ring" r={uncertaintyPx} />
      {contact.role === "shadow_only"
        ? <path className="vessel-symbol" d="M0 -13 L8 8 L0 4 L-8 8 Z" />
        : <path className="vessel-symbol" d="M0 -14 L7 -4 L6 11 L-6 11 L-7 -4 Z" />}
      {contact.conflict.length > 0 && <path className="conflict-tick" d="M-11 -18 L11 -18" />}
    </g>
  );
}

function provenanceLabel(contact: SingaporeContact) {
  if (contact.provenance === "recorded") return "RECORDED MIRROR";
  if (contact.provenance === "ais_reported") return "AIS REPORTED · SHADOW";
  return "SYNTHETIC / RADAR";
}

function BranchCard({ branch, tone }: { branch: SingaporeBranch; tone: "protected" | "counterfactual" }) {
  const path = branch.path.map(([north, east], index) => {
    const x = 12 + ((east + 60) / 150) * 196;
    const y = 80 - ((north + 70) / 170) * 70;
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <article className={`singapore-branch-card ${tone}`}>
      <header><span>{branch.label}</span><strong>{branch.status}</strong></header>
      <svg viewBox="0 0 220 92" role="img" aria-label={`${branch.label} trajectory`}><path className="branch-gridline" d="M0 46H220 M110 0V92" /><path className="branch-track" d={path} /><circle cx="110" cy="46" r="8" className="branch-risk" /></svg>
      <dl><div><dt>CPA</dt><dd>{branch.cpa_m.toFixed(1)} m</dd></div><div><dt>TCPA</dt><dd>{branch.tcpa_s.toFixed(1)} s</dd></div></dl>
    </article>
  );
}

export function SingaporeTrafficConsole() {
  const [stateId, setStateId] = useState<SingaporeStateId>(initialState);
  const [sourceMode, setSourceMode] = useState<SourceMode>(initialSource);
  const [live, setLive] = useState<LiveTrafficSnapshot | null>(null);
  const [liveError, setLiveError] = useState(false);
  const [selectedId, setSelectedId] = useState("mirror-031");
  const state = singaporeDemo.states[stateId];
  const packet = useMemo(() => createSingaporePacket(stateId), [stateId]);
  useEffect(() => {
    if (sourceMode === "recorded" || sourceMode === "synthetic") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch("/api/collector/v1/traffic/snapshot", { cache: "no-store" });
        if (!response.ok) throw new Error(`traffic source ${response.status}`);
        const value = await response.json() as LiveTrafficSnapshot;
        if (!cancelled) { setLive(value); setLiveError(false); }
      } catch { if (!cancelled) setLiveError(true); }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [sourceMode]);

  const liveReady = live?.source_state === "healthy" && live.contacts.length > 0;
  const effectiveSource: Exclude<SourceMode, "auto"> = sourceMode === "auto"
    ? liveReady ? "live" : "recorded"
    : sourceMode === "live" && !liveReady ? "recorded" : sourceMode;
  const liveContacts: SingaporeContact[] = (live?.contacts ?? []).map((contact) => ({
    ...contact,
    provenance: "ais_reported",
    source_label: "AISStream normalized report · read-only live shadow",
    health: live?.source_state === "healthy" ? "healthy" : "degraded",
    cpa_m: contact.range_from_origin_m ?? Math.hypot(contact.north_m, contact.east_m),
    tcpa_s: contact.speed_mps > 0 ? (contact.range_from_origin_m ?? Math.hypot(contact.north_m, contact.east_m)) / contact.speed_mps : 0,
    role: "shadow_only",
  }));
  const fixtureContacts = effectiveSource === "synthetic"
    ? state.contacts.map((contact) => ({ ...contact, provenance: "synthetic" as const, source_label: "Deterministic synthetic traffic twin" }))
    : state.contacts;
  const contacts = effectiveSource === "live" && liveContacts.length ? liveContacts : fixtureContacts;
  const selected = contacts.find((contact) => contact.id === selectedId) ?? contacts[0];
  const displaySourceState = effectiveSource === "live" ? live?.source_state ?? "unavailable" : state.source_state;
  const sourceStage = singaporeDemo.source_lifecycle.indexOf(displaySourceState === "unknown" ? "connecting" : displaySourceState);
  const mapBbox = effectiveSource === "live" ? LIVE_BBOX : singaporeDemo.map.bbox;

  const chooseState = (next: SingaporeStateId) => {
    setStateId(next);
    setSelectedId("mirror-031");
    const url = new URL(window.location.href);
    url.searchParams.set("view", "singapore");
    url.searchParams.set("state", next);
    window.history.replaceState(null, "", url);
  };

  const chooseSource = (next: SourceMode) => {
    setSourceMode(next);
    setSelectedId(next === "live" ? "" : "mirror-031");
    const url = new URL(window.location.href);
    url.searchParams.set("view", "singapore");
    url.searchParams.set("source", next);
    window.history.replaceState(null, "", url);
  };

  return (
    <main className="singapore-console" data-fixture-id={singaporeDemo.fixture_id} data-demo-state={stateId}>
      <header className="singapore-header">
        <div><span className="singapore-kicker">HORIZON · SINGAPORE OPERATIONS</span><h1>Singapore Strait traffic assurance</h1><p>{effectiveSource === "live" ? `${contacts.length} normalized live contacts in read-only shadow mode` : state.headline}</p></div>
        <div className="singapore-clock"><span>{effectiveSource === "live" ? "Live AIS shadow" : "Deterministic fixture"}</span><strong>{effectiveSource === "live" ? `${contacts.length} TRACKS` : `T+${state.time_s.toFixed(1)} s`}</strong><small>{effectiveSource === "live" ? `age ${live?.last_frame_age_s?.toFixed(1) ?? "—"} s` : singaporeDemo.captured_at}</small></div>
      </header>

      <nav className="singapore-source-switch" aria-label="Singapore traffic source">
        {(["auto", "live", "recorded", "synthetic"] as SourceMode[]).map((source) => <button type="button" key={source} aria-pressed={sourceMode === source} onClick={() => chooseSource(source)}><strong>{source}</strong><small>{source === "auto" ? `using ${effectiveSource}` : source === "live" ? liveReady ? "healthy" : liveError ? "fallback · unavailable" : `fallback · ${live?.source_state ?? "connecting"}` : source === "recorded" ? "repeatable mirror" : "fault injection"}</small></button>)}
      </nav>

      <nav className="singapore-state-switch" aria-label="Stable Singapore demo states">
        <button type="button" aria-pressed={stateId === "overview"} onClick={() => chooseState("overview")}><span>01</span><strong>Traffic overview</strong><small>Healthy recorded mirror</small></button>
        <button type="button" aria-pressed={stateId === "conflict"} onClick={() => chooseState("conflict")}><span>02</span><strong>RTA intervention</strong><small>Stale / conflicting AIS</small></button>
      </nav>

      <section className="source-state-strip" aria-label="AIS source state transition">
        <div className={`source-status ${displaySourceState}`}><i /><span>Normalized AIS evidence</span><strong>{displaySourceState}</strong><small>{effectiveSource === "live" ? `${live?.last_frame_age_s?.toFixed(1) ?? "—"} s age · ${contacts.length}/50 tracks` : `${state.source_age_s.toFixed(1)} s age`}</small></div>
        <ol>{singaporeDemo.source_lifecycle.map((item, index) => <li key={item} className={index === sourceStage ? "current" : index < sourceStage ? "past" : "future"}>{item}</li>)}</ol>
        <div className="source-boundary"><span>Browser boundary</span><strong>Normalized display fields only</strong><small>Credentials and provider frames excluded</small></div>
      </section>

      <section className="singapore-paired-view" aria-label="Synchronized Singapore 2D map and 3D NED scene">
        <article className="singapore-panel map-panel">
          <header><div><span>02D · WGS84 INSET</span><strong>Singapore local traffic</strong></div><small>{singaporeDemo.map.bundle_id}</small></header>
          <div className="singapore-map">
            <svg viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`} role="img" aria-label="Offline Singapore traffic map">
              <defs><pattern id="map-grid" width="80" height="62" patternUnits="userSpaceOnUse"><path d="M80 0H0V62" /></pattern></defs>
              <rect width={MAP_WIDTH} height={MAP_HEIGHT} className="map-water" />
              <rect width={MAP_WIDTH} height={MAP_HEIGHT} fill="url(#map-grid)" className="map-grid" />
              {singaporeGeography.features.map((feature) => <path key={feature.properties.name} d={geoPath(feature.geometry.coordinates, mapBbox)} className={`geo-${feature.properties.layer}`} />)}
              <text x="740" y="94" className="map-land-label">SINGAPORE</text>
              <text x="70" y="546" className="map-water-label">SINGAPORE STRAIT · LOCAL FIXTURE</text>
              {state.branches.protected.path.length > 1 && <path className="map-route protected" d={state.branches.protected.path.map(([north, east], index) => { const [x, y] = pointFor({ north_m: north, east_m: east }); return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ")} />}
              {state.branches.counterfactual.path.length > 1 && <path className="map-route counterfactual" d={state.branches.counterfactual.path.map(([north, east], index) => { const [x, y] = pointFor({ north_m: north, east_m: east }); return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ")} />}
              {contacts.map((contact) => <g key={contact.id} role="button" tabIndex={0} aria-label={`${contact.label}, ${provenanceLabel(contact)}, ${contact.health}`} onClick={() => setSelectedId(contact.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedId(contact.id); } }}>{markerShape(contact, contact.id === selected.id, mapBbox)}</g>)}
              {(() => { const [x, y] = pointFor(state.ownship, mapBbox); return <g className="ownship-marker" transform={`translate(${x} ${y}) rotate(${(state.ownship.heading_rad * 180) / Math.PI})`}><path d="M0 -17 L9 12 L0 8 L-9 12 Z" /><text transform={`rotate(${(-state.ownship.heading_rad * 180) / Math.PI})`} x="15" y="5">OWN</text></g>; })()}
            </svg>
            <div className="map-north"><b>N</b><i /></div>
            <div className="map-scale">100 m</div>
            <div className="map-attribution"><strong>{singaporeDemo.map.bundle_version}</strong><span>{singaporeDemo.map.attribution}</span><b>{singaporeDemo.map.notice}</b></div>
          </div>
        </article>

        <article className="singapore-panel scene-panel">
          <header><div><span>03D · LOCAL NED</span><strong>Protected simulation scene</strong></div><small>Same origin · same fixture instant</small></header>
          <div className="singapore-scene"><MaritimeScene packet={packet} cameraMode="tactical" selectedContactId={selected.id} onSelectContact={setSelectedId} showBranch={stateId === "conflict"} frozen /></div>
        </article>
      </section>

      <section className="singapore-evidence-grid">
        <article className={`selected-contact ${selected.health}`} aria-live="polite">
          <header><div><span>{provenanceLabel(selected)}</span><h2>{selected.label}</h2></div><strong>{selected.health}</strong></header>
          <p>{selected.source_label}</p>
          <dl><div><dt>Report age</dt><dd>{selected.age_s.toFixed(1)} s</dd></div><div><dt>Uncertainty</dt><dd>±{selected.uncertainty_m.toFixed(0)} m</dd></div><div><dt>CPA</dt><dd>{selected.cpa_m.toFixed(1)} m</dd></div><div><dt>TCPA</dt><dd>{selected.tcpa_s.toFixed(1)} s</dd></div></dl>
          <div className="conflict-flags">{selected.conflict.length ? selected.conflict.map((item) => <span key={item}>{item.replaceAll("_", " ")}</span>) : <span className="clear">No source conflict</span>}</div>
          <small>{selected.role === "shadow_only" ? "Read-only live-shadow layer · never enters protected plant authority" : "Frozen traffic plant · protected simulation / safety evidence"}</small>
        </article>
        <div className="branch-cards" aria-label="Paired mirrored encounter display"><BranchCard branch={state.branches.protected} tone="protected" /><BranchCard branch={state.branches.counterfactual} tone="counterfactual" /></div>
      </section>

      <footer className="singapore-footer"><span>{effectiveSource === "live" ? "Live AIS is traffic context only · hashed identities · no raw frames · no actuator authority" : "Recorded and synthetic fixture data · deterministic safety demonstration"}</span><span>Origin {singaporeDemo.origin.latitude_deg.toFixed(4)}° N, {singaporeDemo.origin.longitude_deg.toFixed(4)}° E · WGS84 ↔ NED</span></footer>
    </main>
  );
}
