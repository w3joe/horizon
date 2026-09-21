# Horizon independent actuator gate

The gate is a separate process on port 8102 and is the only holder of the
simulator's per-run plant capability.  A supervisor bearer token authorizes a
decision submission but does not make it safe: the gate independently checks
schema/identity, run and branch, monotonic tick sequence, origin snapshot,
expiry, finite numerics, decision deadline, solver status, authority, and the
final issued command against collision, boundary, depth, and actuator limits.
The gate revalidates the command over its 400 ms plant-command validity window;
the supervisor owns the declared 60 second predictive envelope. A complete
60 second recovery-library check is cached asynchronously so plant I/O and the
watchdog state lock remain bounded. Before that first cache is ready, a killed
supervisor produces an explicit unknown/no-command watchdog event rather than
a fabricated safe recovery.

The watchdog uses host monotonic time, independent of accelerated or paused
simulation time.  If supervisor output stops it continues the last complete,
validated recovery while its evidence certificate remains fresh.  After that
expiry it emits an explicit `unknown` minimum-risk command; it does not claim
that stopping is universally safe.  Recovery release requires hysteresis and
an authenticated operator acknowledgement.

A live plant reset reuses tick numbers, so it cannot silently reuse old
decisions.  A tick/snapshot regression quarantines the gate.  The operator
must call the reset handshake, which clears cached recovery and rotates the
supervisor token written to its mode-0600 token file.  The gate's plant-facing
sequence is independent and remains monotonic.

```sh
PYTHONPATH=services/gate:services/assurance:services/simulator \
  python -m horizon_gate.http_api \
  --run-id demo --branch-id protected \
  --plant-token-file /tmp/horizon-gate.token \
  --decision-token-file /tmp/horizon-supervisor.token \
  --operator-token-file /tmp/horizon-gate-operator.token
```

Endpoints are `GET /health`, `GET /v1/telemetry`, `POST /v1/decision`,
`POST /v1/operator/acknowledge`, and `POST /v1/operator/reset-handshake`.

## Plant receive contract

Every gate-to-plant envelope carries both `expires_simulation_time_s` and
`expires_monotonic_ns`. The plant endpoint must use the same host monotonic
clock, verify the latter immediately after authentication and immediately
before changing actuator state, and reject an expired envelope even when its
simulation-time expiry is still ahead. It must also keep the gate sequence
strictly increasing across a live reset or rotate the run/epoch identity in an
explicit reset handshake. Gate-side queue checks reduce the race window but
cannot revoke bytes after the HTTP request reaches the plant process.
