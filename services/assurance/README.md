# Horizon assurance service

This service implements the common
`evaluate(GovernorInput) -> AssuranceDecision` boundary.  Candidate A1 is a
fixed CPA/TCPA and operating-limit monitor with Simplex switching. Candidate
A3 is a finite, bounded engineering rollout with a complete checked recovery
continuation.  A finite rollout is not exact reachability and does not prove a
formal safety property.

The service consumes only estimated state, declared uncertainty, actuator
feedback, health summaries, and a versioned public navigation reference.  It
does not read simulator truth, scenario fault labels, or evaluation tokens.
Unknown geometry, missing bounded-error assumptions, expired evidence, or an
invalid actuator capability produce an explicit minimum-risk/unknown result.

The default prediction window is 60 seconds with the authoritative plant's
20 ms step.  Recovery candidates are a bounded, versioned library.  Each
candidate is checked for swept-hull collision, water-boundary, depth, command,
and actuator constraints.  Current error and bounded position/speed errors
inflate the occupied envelope over time.  Traffic is assumed to maintain its
estimated ground velocity within the declared speed bound.  These are finite
engineering envelopes under declared assumptions, not universal collision
avoidance.

Run locally after the simulator package is on `PYTHONPATH`:

```sh
PYTHONPATH=services/assurance:services/simulator \
  python -m horizon_assurance.http_api --port 8103
```

Endpoints are `GET /health`, `GET /v1/telemetry`, and `POST /v1/evaluate`.

## Live consuming-loop contract

The assurance-owned control loop pulls `GET
http://127.0.0.1:8104/v1/governor-input?branch=protected` at 20 Hz. A05 returns
503 until it has one complete synchronized record; a 200 response body is the
`GovernorInput` itself. Each record must have a new proposal sequence,
tick/snapshot identity, and a 40 ms decision deadline in the shared host
monotonic clock. The configuration hash is SHA-256 over the complete simulator
`/v1/reference` response serialized as canonical JSON (sorted keys and compact
separators). The loop never renews or relabels the latest input.

For a ready input the loop calls the selected in-process candidate, then posts
`{"input": GovernorInput, "decision": AssuranceDecision}` to
`http://127.0.0.1:8102/v1/decision` with the supervisor bearer capability. A
schema-valid `GateReceipt` is the only acknowledgement that a command reached
the plant authority path. Rejection, 503, timeout, or a missed decision
deadline is recorded to assurance telemetry and left to the independent gate
watchdog; the loop never writes port 8100. Replay/experiment inputs do not use
this live actuator route.
