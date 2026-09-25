# Horizon independent actuator gate

The standalone service defaults to A6 policy enforcement. Normal A5 commands
require a gate-owned policy authorization; missing policy/evidence fails closed
to recovery. Configure `--a6-policy-config` and `--a6-evidence-file` together.
Use `--a6-mode disabled` only for explicit legacy development baselines. See
[A6 configuration and semantics](../assurance/docs/a6-policy-enforcement.md).

The gate is a separate process on port 8102 and is the only holder of the
simulator's per-run plant capability.  A supervisor bearer token authorizes a
decision submission but does not make it safe: the gate independently checks
schema/identity, run and branch, monotonic tick sequence, origin snapshot,
expiry, finite numerics, decision deadline, solver status, authority, and the
final issued command against collision, boundary, depth, and actuator limits.
Decision flags use their exact schema types; strings such as `"false"` are
rejected rather than interpreted by Python truthiness. Malformed authenticated
submissions produce a rejection receipt and never reach the plant.
The gate revalidates the command over its 400 ms plant-command validity window;
the supervisor owns the declared 60 second predictive envelope. A complete
60 second recovery-library check is cached asynchronously so plant I/O and the
watchdog state lock remain bounded. Protected autonomy is interlocked until a
recovery has been explicitly primed and remains fresh. Before that point, a
killed supervisor produces an explicit unknown/no-command watchdog event
rather than a fabricated safe recovery.

The gate also polls fusion's sensor-only `RecoveryInput` endpoint on its own
thread. This path has a separate capability, contains no decision-AI proposal,
and can refresh the stored recovery while the primary AI or assurance process
is unavailable. Required navigation, radar, and actuator health and the
selected recovery option keep their original expiries. Legacy prime and
independent refresh share one bounded in-flight validation slot, so repeated
requests cannot queue concurrent recovery searches.

The watchdog uses host monotonic time, independent of accelerated or paused
simulation time.  If supervisor output stops it continues the last complete,
validated recovery while its evidence certificate remains fresh.  After that
expiry it emits an explicit `unknown` minimum-risk command; it does not claim
that stopping is universally safe.  Recovery release requires hysteresis and
an authenticated operator acknowledgement.

`minimum_risk` is a constrained action, not a label that bypasses validation.
The canonical command holds the estimated current heading and limits requested
speed to the range from zero through the lesser of 1 m/s and estimated surge
speed. The gate derives this command independently and rejects a supervisor
decision that marks any other command as `minimum_risk`.

A live plant reset reuses tick numbers, so it cannot silently reuse old
decisions.  A tick/snapshot regression quarantines the gate.  The operator
must call the reset handshake, which clears cached recovery and rotates the
supervisor token written to its mode-0600 token file.  The gate's plant-facing
sequence is independent and remains monotonic.

```sh
PYTHONPATH=services/gate:services/assurance:services/simulator \
  python -m horizon_gate.http_api \
  --run-id demo --branch-id protected \
  --fusion-url http://127.0.0.1:8104 \
  --plant-token-file /tmp/horizon-gate.token \
  --decision-token-file /tmp/horizon-supervisor.token \
  --recovery-token-file /tmp/horizon-recovery.token \
  --operator-token-file /tmp/horizon-gate-operator.token
```

Endpoints are `GET /health`, `GET /v1/telemetry`, `POST /v1/decision`,
`POST /v1/recovery/prime`, `POST /v1/recovery/refresh`, `POST
/v1/operator/acknowledge`, and `POST /v1/operator/reset-handshake`. Operator
routes require the operator bearer, decision/prime routes require the
supervisor bearer, and refresh accepts only its dedicated recovery bearer.
These files stay server-side. The internal fusion poller calls the same refresh
method with that dedicated capability.

Gate status includes `observed_monotonic_ns`, sampled from the same injected
host clock used for receipt and expiry checks. A remote display can combine
that anchor with elapsed time since receipt to age authority data without
mixing it with wall time or a browser-specific monotonic epoch. The field is
public telemetry and contains no capability token or simulator truth.

While the stored startup recovery remains eligible, status also exposes
`startup_recovery_certificate` with its run, branch, gate validation, input
kind, input, input snapshot, proposal, and plant-epoch identities plus
`original_host_valid_until_ns`. A sensor-only recovery certificate has
`input_kind: "RecoveryInput"` and `proposal_id: null`; a GovernorInput-derived
certificate keeps its proposal ID.
The object becomes `null` at expiry, after an epoch change, or when its stored
identity is inconsistent. Its expiry is the original host-monotonic ceiling;
reading status never recomputes or extends it. An operator proxy can carry this
public evidence identity to an atomic simulator resume request. The simulator
must still validate the authenticated request's epoch and strict host expiry
under the same lock that clears pause.

Deterministic experiment harnesses should inject their
`ManualMonotonicClock`, set `GateConfig(asynchronous_recovery_cache=False)`,
prime recovery before the first protected command, and call `gate.close()` at
episode teardown. Production keeps asynchronous cache refresh so a 60 second
recovery search does not block plant dispatch.

## Plant receive contract

Every gate-to-plant envelope carries both `expires_simulation_time_s` and
`expires_monotonic_ns`. The plant endpoint must use the same host monotonic
clock, verify the latter immediately after authentication and immediately
before changing actuator state, and reject an expired envelope even when its
simulation-time expiry is still ahead. It must also keep the gate sequence
strictly increasing across a live reset or rotate the run/epoch identity in an
explicit reset handshake. Gate-side queue checks reduce the race window but
cannot revoke bytes after the HTTP request reaches the plant process.

The GovernorInput decision deadline bounds candidate computation and dispatch
eligibility. It is distinct from the command expiry enforced by the gate and
plant receiver. The current 40 ms decision budget is therefore not a claim
that plant actuation completes within 40 ms; end-to-end timing is reported from
the actual gate receipt and plant actuation timestamps.

A decision cannot extend the lifetime of its evidence. Its expiry must be no
later than the snapshot and proposal expiry, and no later than its recovery
certificate when one is present. A `recover` action requires that certificate.
The gate checks the immutable ceiling on
arrival, after command assessment, and immediately before plant dispatch using
the same injected host monotonic clock. These checks never remap or renew an
expired source timestamp.

Normal and filtered autonomy also require exactly one current record for each
configured required health source. Their original expiries cap authority even
when the snapshot or proposal lasts longer. The gate independently checks this
qualification. Validated recovery requires fresh independent sensor evidence;
primary-AI telemetry and AI-consumption health are not prerequisites for that
recovery. Missing radar coverage yields explicit minimum-risk behavior under
unknown assurance, rather than a validated obstacle-free recovery claim.
