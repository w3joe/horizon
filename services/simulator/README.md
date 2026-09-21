# Horizon authoritative simulator

This service owns the one authoritative horizontal plant for each simulation
branch. It implements a fixed 50 Hz, three-degree-of-freedom model with NED
pose `[north, east, heading]`, body velocity `[surge, sway, yaw_rate]`, first-
order/rate-limited rudder and propulsion, bounded wind/current input, and a
12 m × 3 m × 1 m synthetic hull. Heading and yaw are clockwise from north;
body sway is positive starboard. Coefficients are simulation assumptions, not
measurements from a real vessel.

`horizon_sim.geometry.swept_hulls_intersect` conservatively subdivides a plant
step and checks convex swept hull occupancy, so a between-tick overlap is not
skipped. Truth-led collision, boundary, under-keel, actuator, and mission data
is recorded privately for independent scoring.

The protected plant endpoint authenticates an unguessable per-run bearer
capability. A claimed `source_id` or `authority` in JSON grants no access. The
evaluation capability is separate and is never returned in public responses.
The browser stream and decision-AI fixture receive sensor-derived snapshots;
fault labels and evaluation truth remain private.

Every protected gate envelope must include a strictly increasing `sequence`,
the current integer `epoch`, `expires_simulation_time_s`, and
`expires_monotonic_ns`. The receiver checks the shared-host monotonic deadline
after bearer authentication, again immediately before actuator mutation, and
on every plant step while the command remains applied. Missing, expired, or
more-than-two-seconds-future host deadlines fail closed. Simulation expiry is a
separate boundary. Either expiry switches the target to zero-speed neutral
heading and records a private `command_expired` event.

A live reset increments the plant epoch and preserves the last gate sequence.
The coordinated order is simulator reset followed by the gate's
`reset-handshake`; until both report the same epoch, protected commands fail
closed. This prevents an unseen delayed command from the prior epoch from
reviving when tick numbers restart. Snapshot and observation opaque IDs include
the epoch, while `/v1/observations` exposes `plant_epoch` on its non-schema
batch wrapper. The static `/v1/reference` digest remains epoch-free.

Run a manual-step service from the repository root:

```sh
PYTHONPATH=services/simulator:packages/contracts/python \
  /opt/homebrew/bin/python3.12 -m horizon_sim.http_api \
  --scenario scenarios/crossing_recoverable.json \
  --manual-step --gate-token-file /tmp/horizon-gate.token \
  --evaluation-token-file /tmp/horizon-evaluation.token
```

Public routes:

- `GET /health`
- `GET /v1/public/snapshot?branch=protected`
- `GET /v1/public/stream?branch=protected&events=0` (SSE, `snapshot` events)
- `GET /v1/observations?branch=protected`
- `GET /v1/reference?branch=protected` (static geometry/model assumptions; no truth/faults)

Privileged routes:

- `POST /v1/gate/command?branch=protected` with the gate bearer capability
- `GET /v1/evaluation/truth?branch=protected&after_tick=-1`
- `POST /v1/evaluation/{step,reset,clone}` with the evaluation capability
- `POST /v1/evaluation/command` only for an explicitly unprotected branch

Offline evaluation does not relax the live gate endpoint. Create a simulator
with `monotonic_ns=ManualMonotonicClock(...)`, advance that clock explicitly in
the experiment runner, and call `submit_counterfactual_command` with the
matching `offline_monotonic_ns`. Simulation time remains fixed-step; measured
candidate compute duration and the gate's 40 ms deadline stay host-timed.

Local console controls use a third bearer capability supplied through
`--operator-token-file`: `POST /v1/operator/{pause,resume,reset}` and
`POST /v1/operator/fault` with a scenario-declared `fault_id`. The operator
capability grants neither actuation nor evaluation truth. CORS is restricted to
`http://localhost:5176`.

The pure `horizon_sim.rollout.rollout_from_estimate` function shares the same
physical model with assurance code, but accepts only an estimated-state record
and explicit `ActuatorCapability`; it cannot clone online simulator truth.

`horizon_sim.experiment_adapter.run_episode(request)` implements the A02
normalized runner boundary for the visible `STUB` CPU smoke candidate. A1–A5
must use the separate assurance/gate integration and intentionally raise until
that adapter is connected.
