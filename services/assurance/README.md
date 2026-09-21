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

