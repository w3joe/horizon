# Horizon console

Browser console for the maritime runtime-assurance demo. Vite development mode uses schema `0.1.0` fixture data by default. A production build consumes only A01's allowlisted, same-origin read APIs.

```sh
cd apps/console
npm install --package-lock=false --workspaces=false
npm run dev
```

Vite uses `http://127.0.0.1:5176`. A developer may exercise the live same-origin seam with:

```sh
VITE_HORIZON_LIVE=1 npm run dev
```

The console opens `/api/v1/public/stream?branch=protected&events=0` and polls the allowlisted collector, assurance, gate, and joined-evidence routes. The browser receives no evaluation-truth, operator, gate-write, or simulator capability credential. An issued command appears only when an accepted gate receipt completes the snapshot → proposal → decision → receipt identity chain. Missing fields stay unknown. Paths appear only when a backend command actually supplies trajectory samples.

The Neural sensor workspace reads the allowlisted `/api/artifacts/perception` reproduction routes. It labels the 85-frame CPU WaSR-T run as a recorded reproduction, independent from the simulator encounter. Raw images, class-mask previews, measured frame timing, and per-layer summary statistics are loaded from the server; pooled feature arrays never enter the browser.

## Maritime display assets

Both navigation canvases use the checked-in maritime registry as the source of
truth for browser paths and required glTF root nodes. The RIB remains the
ownship display proxy. The Sketlux container ship is scaled to each contact's
public hull dimensions, while the cargo stack and Poly Haven buoy provide a
small, deterministic harbor set. These models are visual proxies: public hull
records remain authoritative for navigation and safety evidence.

The renderer shares loader caches and underlying geometry between instances,
limits detailed cargo models in dense traffic, and retains lightweight local
geometry during downloads or asset errors. Source links and licenses remain
visible in each scene. Full hashes, bounds, attribution, and reproduction
instructions live in `assets/maritime/registry.json` and
`docs/realism/assets/`.

Production playback and fault controls remain disabled until A01 exposes the coordinated server-side operator API. Private tokens must remain in the platform process.

The Navigation, Data flow, and Neural sensor workspaces share the selected timeline event. Keyboard shortcuts are `1`–`3` for workspaces, Space for fixture play/pause, and `R` for fixture reset.

Development fixture mode also exposes an accessible five-stage evidence walkthrough. Each stage
retains a distinct limitation across perception/neural output, radar–camera agreement, internal
application communication age, peer claimed intent, or decision-AI telemetry. Its margin, speed, and
authority summary is computed from the displayed fixture decision and receipt. The fixture candidate
remains `STUB`; live and recorded candidate badges use the exact backend decision ID/version.

## Evidence boundaries

- Fixture paths, outcomes, messages, and perception outputs are synthetic and labeled in the interface.
- The water mesh is a visual presentation effect. It does not affect motion.
- The browser interpolates fixture playback only; it does not implement plant dynamics, a governor, or an actuator gate.
- Runtime-linked neural telemetry stays unavailable unless the live sample includes a trace. The recorded reproduction shows measured summaries and images without inventing activations or attribution maps.
- Live simulator snapshots are sensor-derived display state, not evaluation truth.
- Maritime GLBs never alter collision geometry, hydrodynamics, contacts, or assurance evidence.
- The recorded perception source is a camera sequence reproduction, not a simulator camera or a live 20 Hz/GPU claim.
- Singapore Strait and defensive-maritime cues are presentation context, without coordinates or a deployment claim. Procedural sensor mast/radome, EO/IR sensor, mission-bay/RHIB, and static deck/CIWS-like silhouettes are fictional and have no source-equipment claim. The silhouettes have no controls, target/contact linkage, tracking, engagement logic, firing animation, performance parameters, fire-control, or actuation behavior.
