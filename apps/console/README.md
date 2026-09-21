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

Production playback and fault controls remain disabled until A01 exposes the coordinated server-side operator API. Private tokens must remain in the platform process.

The Navigation, Data flow, and Neural sensor workspaces share the selected timeline event. Keyboard shortcuts are `1`–`3` for workspaces, Space for fixture play/pause, and `R` for fixture reset.

## Evidence boundaries

- Fixture paths, outcomes, messages, and perception outputs are synthetic and labeled in the interface.
- The water mesh is a visual presentation effect. It does not affect motion.
- The browser interpolates fixture playback only; it does not implement plant dynamics, a governor, or an actuator gate.
- Runtime-linked neural telemetry stays unavailable unless the live sample includes a trace. The recorded reproduction shows measured summaries and images without inventing activations or attribution maps.
- Live simulator snapshots are sensor-derived display state, not evaluation truth.
- The recorded perception source is a camera sequence reproduction, not a simulator camera or a live 20 Hz/GPU claim.
