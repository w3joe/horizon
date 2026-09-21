# Horizon console

Browser console for the maritime runtime-assurance demo. The initial packet runs from schema `0.1.0` fixture data and includes an optional read-only simulator connection.

```sh
cd apps/console
npm install --package-lock=false --workspaces=false
npm run dev
```

Vite uses `http://127.0.0.1:5176`. To read the A03 simulator's public, sensor-derived display stream:

```sh
VITE_HORIZON_API_URL=http://127.0.0.1:8765 npm run dev
```

The console connects to `GET /v1/public/stream?branch=protected&events=0` with `EventSource`. It has no evaluation-truth or actuator credential. Fixture controls are disabled after the public stream is active. Assurance decisions and trajectory evidence stay unavailable in live mode until a public A04 feed is integrated.

The Navigation, Data flow, and Neural sensor workspaces share the selected timeline event. Keyboard shortcuts are `1`–`3` for workspaces, Space for fixture play/pause, and `R` for fixture reset.

## Evidence boundaries

- Fixture paths, outcomes, messages, and perception outputs are synthetic and labeled in the interface.
- The water mesh is a visual presentation effect. It does not affect motion.
- The browser interpolates fixture playback only; it does not implement plant dynamics, a governor, or an actuator gate.
- Neural layer telemetry is reported as unavailable. The console does not generate activation values or attribution maps.
- Live simulator snapshots are sensor-derived display state, not evaluation truth.
