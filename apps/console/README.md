# Horizon console

Browser console for the maritime runtime-assurance system. The dashboard has an explicit Connected mode for the same-origin backend and a Simulation mode with editable, browser-local sensor evidence. Simulation inputs never write to a live service or actuator.

Singapore Strait is the dashboard's default operating environment. Simulation mode creates a fresh, session-seeded mix of moving container ships, ferries, tankers, and pilot craft on every load or traffic reset. Navigation uses one consistent **2D / 3D** selector. The 3D scene uses the presentation-friendly oblique camera internally.

The dashboard is constrained to one viewport. Sensor inputs, fault profiles, evidence, operator controls, and playback are available in overlay drawers instead of extending the page vertically.

```sh
cd apps/console
npm install --package-lock=false --workspaces=false
npm run dev
```

Vite uses `http://127.0.0.1:5176`. Choose **Connected** in the dashboard to exercise a compatible same-origin backend. Each Connected selection requests a coordinated pause → reset → recovery-ready → resume cycle, so the live demonstration starts from a fresh Singapore traffic episode. This restarts the backend run state; it cannot relaunch a backend process that has exited.

The console opens `/api/v1/public/stream?branch=protected&events=0` and polls the allowlisted collector, assurance, gate, and joined-evidence routes. The browser receives no evaluation-truth, operator, gate-write, or simulator capability credential. An issued command appears only when an accepted gate receipt completes the snapshot → proposal → decision → receipt identity chain. Missing fields stay unknown. Paths appear only when a backend command actually supplies trajectory samples.

The local stack now launches `scenarios/singapore_traffic_mirror_synthetic.json` by default. Its four backend-owned moving contacts come from the checked-in synthetic Singapore traffic snapshot and remain distinct from Simulation mode's larger browser-local randomized fleet.

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

The Navigation, Data flow, and Neural sensor workspaces share the selected timeline event. Keyboard shortcuts are `1`–`3` for workspaces, Space for simulation play/pause, and `R` for a fresh Singapore traffic mix.

Development fixture mode also exposes an accessible five-stage evidence walkthrough. Each stage
retains a distinct limitation across perception/neural output, radar–camera agreement, internal
application communication age, peer claimed intent, or decision-AI telemetry. Its margin, speed, and
authority summary is computed from the displayed fixture decision and receipt. The fixture candidate
remains `STUB`; live and recorded candidate badges use the exact backend decision ID/version.

## Evidence boundaries

- Fixture paths, outcomes, messages, and perception outputs are synthetic and labeled in the interface.
- The water mesh is a visual presentation effect. It does not affect motion.
- The browser applies simple display-only constant-course motion to the generated Singapore traffic; it does not implement plant dynamics, a governor, or an actuator gate.
- Runtime-linked neural telemetry stays unavailable unless the live sample includes a trace. The recorded reproduction shows measured summaries and images without inventing activations or attribution maps.
- Live simulator snapshots are sensor-derived display state, not evaluation truth.
- Maritime GLBs never alter collision geometry, hydrodynamics, contacts, or assurance evidence.
- The recorded perception source is a camera sequence reproduction, not a simulator camera or a live 20 Hz/GPU claim.
- Singapore Strait and defensive-maritime cues are presentation context, without coordinates or a deployment claim. Procedural sensor mast/radome, EO/IR sensor, mission-bay/RHIB, and static deck/CIWS-like silhouettes are fictional and have no source-equipment claim. The silhouettes have no controls, target/contact linkage, tracking, engagement logic, firing animation, performance parameters, fire-control, or actuation behavior.
