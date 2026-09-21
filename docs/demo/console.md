# Console demo guide

## Initial walkthrough

1. Start the console on port 5176 and confirm the amber **INITIAL FIXTURE MODE** disclosure.
2. Use **Oblique** and **Tactical** cameras to inspect the same NED harbor scene. The patrol vessel hull is 12 × 3 m. Cyan is the accepted path, dashed red is the rejected proposal, and dashed amber is the unprotected predicted branch.
3. Select **S19 · Camera mismatch**. Playback begins at the synthetic fault marker. Pause, scrub, reset, or change speed without changing event timestamps.
4. Select the intervention marker, then move between Navigation, Data flow, and Neural sensor. The event ID, observation IDs, and inference ID remain linked.
5. Select MV Kestrel in the scene to inspect its status, evidence age, supporting source IDs, and uncertainty bound.
6. In Neural sensor, verify that output-only capability and unavailable layers are explicit. The camera image is a synthetic fixture preview, and the suspected cause is separate from the physical hazard reason.

## Live simulator seam

Set `VITE_HORIZON_API_URL` to A03's local simulator origin. The console consumes the public SSE snapshot stream and switches to **LIVE PUBLIC MODE** after receiving a schema `0.1.0` `SimulationSnapshot`.

The initial live mode intentionally exposes only vessel state from the public stream. It clears fixture observations, paths, events, uncertainty, and neural data rather than combining them with live data. The operator controls remain disabled until A03 supplies a separate authorized demo-control API. Assurance action, reasons, proposed/accepted trajectories, and gate receipts require a public A04 event feed.

## Component handoff

| Area | Current input | Later integration |
|---|---|---|
| 3D scene | `SimulationSnapshot` plus local fixture paths | A03 snapshot stream; A04 proposal/decision trajectory; A09 licensed meshes; A10 ocean presentation package |
| Evidence panel | Fixture `AssuranceDecision` and contact record | A04 decision/gate feed and A05 fused-track lineage |
| Data flow | Eight schema-valid `Observation` fixture groups | A05 observations and network delivery/consume events |
| Neural inspector | Output-only capability fixture | A07 measured health/layer telemetry and linked frame references |
| Timeline | Local deterministic fixture events | Logged common event IDs from collector/evidence service |
| Controls | Local fixture playback | A03 authorized operator-control API; never privileged gate or truth routes |

The NED-to-render conversion is centralized in `src/lib/coordinates.ts`: east maps to scene X, north maps to negative scene Z, and clockwise-from-north heading maps to negative scene yaw.
