# Horizon assurance service

This service implements the common
`evaluate(GovernorInput) -> AssuranceDecision` boundary. Candidate A1 is a
fixed CPA/TCPA and operating-limit monitor with Simplex switching. A2 uses a
projected Gaussian collision-risk approximation and Simplex recovery. A3 is a
finite, bounded engineering rollout with a complete checked recovery
continuation. A4 is a provisional kinematic velocity filter followed by full
3DOF rollout validation. A5 conditions explicit engineering bounds and speed
limits on required health evidence, then combines predictive recoverability
with the A4 filter. The implementations are distinct plugins.

A4 does not yet have a validated plant-to-kinematic tracking-error bound. Its
QP residuals establish only that the selected velocity satisfies the finite
kinematic optimization to numerical tolerance. The QP is not a control
barrier certificate for the 3DOF plant. Every selected command is therefore
checked again with the full plant rollout, and A4 remains a provisional
engineering candidate. A finite rollout is not exact reachability and does
not prove a formal safety property.

The service consumes only estimated state, declared uncertainty, actuator
feedback, health summaries, and a versioned public navigation reference.  It
does not read simulator truth, scenario fault labels, or evaluation tokens.
Unknown geometry, ineligible or missing bounded-error assumptions, expired
evidence, or an invalid actuator capability produce an explicit
minimum-risk/unknown result. Covariance is never converted into a hard bound.
The default covariance-only path may use named synthetic-harbor engineering
ODD bounds from `AssuranceConfig`; these bounds apply only to the characterized
plant model and, for contacts, radar-supported tracks. Their assumption IDs
remain in constraint evidence so evaluation can report ODD violations. Other
models and unsupported contact sources remain unknown.

The default `radar-led-constrained-v1` mode requires fresh navigation,
obstacle-perception, actuator-feedback, AI-consumption, and AI-telemetry health
groups. Missing or unknown required groups force fallback. Onboard-network,
peer-intent, and neural-internal groups are optional for this mode; their
unavailability remains visible in reason codes but does not by itself force a
recovery.

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

Endpoints are `GET /health`, `GET /v1/telemetry`, `GET
/v1/evidence/latest`, and `POST /v1/evaluate`.

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

`GET /v1/telemetry` returns bounded decision and control-event rings. Each
`decision_receipt`, `decision_not_submitted`, and submission-failure event
includes an `input_summary` with the exact consumed IDs, proposed command,
estimated ownship/contacts, actuator capability, and health. A successful
event also carries the complete `AssuranceDecision` and `GateReceipt`. The
feed exposes no truth state, capability token, or fabricated trajectory.
`GET /v1/evidence/latest` returns the latest accepted, identity-checked
`{governor_input, decision, receipt}` triplet and returns 503 until one exists.
It is the race-free source for a UI that needs the complete consumed input.
Rejected receipts are never cached. Run, branch, tick, proposal, snapshot, and
decision identities must all join, and an epoch synchronization clears the
prior cache before any result from the new epoch is available.

## Local latency characterization

On 2026-09-21, an Apple M1 Max (32 GiB, macOS 26.6.2) ran the default A3 60
second envelope through local fusion HTTP, the assurance loop, gate HTTP,
independent gate validation, and an in-memory plant receipt. The fixed input
budget was 40 ms. With no background inference load, 20 distant-safe samples
had median 32.50 ms, p95 33.71 ms, and max 36.50 ms; all returned accepted
`pass` receipts. Five hazardous samples had median 36.18 ms and max 36.44 ms;
three 12-contact dense samples had median 39.66 ms and max 39.68 ms. Hazardous
and dense samples exhausted the bounded prediction-work budget, returned
explicit `minimum_risk`/unknown decisions, and received gate receipts; they
did not receive a safe label.

This small development characterization includes serialization and gate
validation but uses a local in-memory plant client, so it does not establish a
deployment-wide 40 ms bound or a sensor-to-actuation deadline. The 40 ms value
bounds candidate computation and submission eligibility. Command validity is a
separate monotonic deadline enforced again by the gate and plant receiver;
actual cycle and actuation latency comes from receipts. The candidate reserves
10 ms for dispatch and stops finite geometry/recovery work at its configured
host-work limit. A late result remains invalid and is never submitted. Startup
recovery priming is a separate permission step: an unrecoverable hazardous or
dense initial state is reported as rejected and never releases protected
autonomy.
