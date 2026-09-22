# Bounded system acceptance evidence

## Scope and method

This packet exercises the real simulator, decision-AI fixture, collector,
fusion, assurance, and gate HTTP entry points as separate processes. Every run
uses fresh loopback ports and fresh capability files. The tests do not use the
standard demonstration ports, and they do not import component internals to
decide whether an HTTP result passed.

The initial packet used integrated base `5f6d49d` on Darwin arm64 with Python
3.12.14. The S22 follow-up was rerun after independent recovery integration on
main `4b5d21c`. Run:

```sh
PYTHONPATH=. .venv/bin/pytest -q tests/system/test_protected_navigation_acceptance.py
```

The suite checks joined proposal/decision/receipt identity, actual plant
actuation, malformed and stale proposal rejection, gate and plant deadline
enforcement, navigation-source loss, supervisor process loss, an injected
collector-to-fusion outage and restoration, and paused-reset sensor delivery
with an explicit recovery certificate. Temporary capabilities and service logs
remain in pytest's temporary directory; no raw run artifacts are committed.

These are bounded integration checks. They do not establish mission-level
acceptance, statistical safety, real-vessel validity, or the complete S01--S22
matrix.

## Executed evidence

| Evidence | Result | Observable assertion |
|---|---|---|
| S01 nominal slice | Passed | A real external proposal, A1 decision, accepted gate receipt, actual command, snapshot ID, proposal ID, and decision ID form one joined chain. |
| S07 GNSS dropout | Passed for loss-of-assurance path | A declared dropout removes fresh fusion authority; collector output contains neither fault labels nor truth keys. Bias detection remains unverified. |
| S09 expired proposal | Passed | Fusion returns 503 with `PROPOSAL_EXPIRED_IN_SIMULATION_TIME`; no external-AI authority reaches the gate. Independently validated gate-watchdog recovery remains permitted. |
| S09 stale lineage | Passed | Fusion returns 503 with `PROPOSAL_ORIGIN_MISMATCH`; no external-AI authority reaches the gate. Independently validated gate-watchdog recovery remains permitted. |
| S09 malformed proposal | Passed after `faad120` | Fusion returns 503 with `PROPOSEDCOMMAND_SCHEMA_INVALID`; no malformed GovernorInput or external-AI authority is published. |
| Decision and plant command expiry | Passed | Gate rejects an expired decision; the simulator independently rejects an expired host command deadline and leaves the active command unchanged. |
| S12 assurance process loss | Passed for takeover | After an accepted chain, SIGTERM of assurance produces an accepted `gate_watchdog` recovery command and a `SUPERVISOR_WATCHDOG` event. Restart behavior remains unverified. |
| S17 transport mechanism | Passed for outage/restoration | A loopback fault proxy makes collector-to-fusion unavailable; fusion returns 503/`UPSTREAM_ERROR`, then produces fresh input after restoration. Stale-consumption ancestry under delay remains unverified. |
| Paused reset | Passed | Physical ticks remain stopped, observation ticks advance, bare resume is rejected, and the exact current gate certificate permits explicit resume. |
| S22 hazard premise | Passed | An evaluation-only unprotected clone running straight at 6 m/s collides with the stationary barge within 45 simulated seconds. |
| S22 protected intervention | Passed for bounded clearance | After an accepted external 6 m/s command, an actuated gate-watchdog recovery precedes the comparison collision. The separate protected branch remains collision-free with positive authoritative hull clearance through at least 45 simulated seconds. |

Two defects found during development were corrected on the tested base. The
original crossing S22 recipe did not establish a collision hazard and was
replaced by `static_obstacle_approach.json` in `901e5a4`. Fusion formerly
published the fixture's string-valued speed as a GovernorInput; `faad120`
added schema and finite-number validation. The system tests retain both
regressions.

## S01--S22 coverage ledger

`executed_partial` means the named evidence ran, while at least one source-plan
acceptance condition remains. A source-plan recipe is never counted as passed
from catalogue validation or a component test.

> **Superseding implementation note (2026-09-22):** bounded S02 evidence now
> joins an accepted A1 recovery before an independently sampled recovery boundary;
> bounded S22 evidence joins an accepted A1 intervention before its paired
> counterfactual collision; and S09/S12 restart-lineage tests now require fresh
> joined chains. Mission completion, statistical coverage, and full S01–S22
> acceptance remain open, so the historical packet below is retained unchanged.

| ID | Status in this packet | Executed evidence or remaining dependency |
|---|---|---|
| S01 | executed_partial | Joined nominal actuation passed; mission completion and unnecessary-intervention rate need a full episode. |
| S02 | pending | Crossing intervention and truth-scored clearance episode not run. |
| S03 | pending | Head-on clearance trace not run. |
| S04 | pending | Overtaking and benign-close-pass intervention rate not run. |
| S05 | pending | Hull-aware corridor rejection not run end to end. |
| S06 | pending | Swept-path under-keel clearance not run end to end. |
| S07 | executed_partial | GNSS dropout removes authority without label leakage; bias detection/bounds remain. |
| S08 | pending | Live AIS/radar conflict and conservative protected command not run. |
| S09 | executed_partial | Expired, stale-lineage, malformed, gate-expiry, and plant-expiry paths passed; decision-AI kill/restart remains. |
| S10 | pending | Live slow/stuck-rudder capability update not run. |
| S11 | pending | Remote-link loss during an active avoidance with onboard recovery not run. |
| S12 | executed_partial | Assurance SIGTERM triggers watchdog recovery; restart and restored lineage remain. |
| S13 | pending | Dense-traffic recovery-margin comparison not run. |
| S14 | external_artifact_required | Recorded or declared synthetic fog/OOD perception artifact and calibrated policy are absent. |
| S15 | pending | Initially unrecoverable minimum-risk outcome and limitation report not run. |
| S16 | pending | Fault-clear quarantine, acknowledgement, and hysteretic authority release not run. |
| S17 | executed_partial | Link outage/restoration fails closed; delayed/reordered consumed-input ancestry remains. |
| S18 | pending | Live peer-claim contradiction constraint check not run. |
| S19 | external_artifact_required | Instrumented normalization-mismatch artifact is absent. |
| S20 | external_artifact_required | Integrated bounded diagnostic worker and declared load injector are absent. |
| S21 | external_artifact_required | Integrated output-only neural appliance artifact is absent. |
| S22 | executed_partial | Counterfactual collision and protected 45-second clearance passed. The observed intervention is gate-watchdog recovery; hazard-triggered A1 intervention and mission completion remain open. |

## Release blockers and limitations

1. Extend S22 beyond bounded watchdog clearance to a hazard-triggered
   supervisor intervention and mission-level route completion result.
2. Complete the physical episode and truth-scored checks for S01--S18. The
   current suite focuses on authority boundaries and failure handling.
3. Integrate real A07 artifacts before running S14 and S19--S21. Simulated
   aliases or private truth labels are not acceptable substitutes.
4. Add the remaining process restart and delayed/reordered message recipes.
   Service unavailability and a link outage do not prove stale-consumption
   ancestry handling.
