# Horizon experiment harness

This directory turns the maritime RTA research plan into executable protocol. It keeps two comparisons separate:

1. `controller_isolation_replay` sends the same frozen evidence snapshot, including point, covariance, and bounded-set representations, to each governor. It measures decisions and runtime only.
2. `full_pipeline_closed_loop` gives each branch the same initial state, seed, external-AI policy, observation tape, and exogenous fault schedule. Branch state and later observations may diverge after commands differ. Only this mode can support collision-prevention and mission-cost comparisons.

The smoke runner uses tiny analytic synthetic traces to test pairing, scoring, and artifact writing. Its output is not empirical Horizon evidence and is excluded from architecture selection.

From the repository root:

```sh
PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment validate
PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment smoke --output ../horizon-runs/a02-smoke
PYTHONPATH=.:packages/contracts/python:services/assurance:services/gate:services/simulator:services/collector:services/fusion \
  /opt/homebrew/bin/python3.12 -m experiment run-adapter \
  --study-plan experiment/manifests/stage1-cpu-development.json \
  --entrypoint experiment.harness.closed_loop:run_assured_episode \
  --run-id local-development --max-simulation-time-s 0.45 \
  --output ../horizon-runs/local-development

PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment perception-drift \
  --reference-manifest ../horizon-runs/compute/a07-modd2-dev-kope81-00006800/manifest.json \
  --reference-features ../horizon-runs/compute/a07-modd2-dev-kope81-00006800/features.jsonl \
  --candidate-manifest ../horizon-runs/compute/a07-modd2-dev-cuda-fp16/manifest.json \
  --candidate-features ../horizon-runs/compute/a07-modd2-dev-cuda-fp16/features.jsonl \
  --output ../horizon-runs/analysis/a02-perception-runtime-drift.json

PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment perception-calibrate \
  --bundle ../horizon-runs/calibration/perception-calibration-bundle.json \
  --output ../horizon-runs/calibration/perception-calibration-report.json

PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment r3-r5-calibrate \
  --bundle ../horizon-runs/calibration/r3-r5-controller-input-bundle.json \
  --output ../horizon-runs/calibration/r3-r5-controller-input-report.json

PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment r3-r5-readiness \
  --index experiment/manifests/r3-r5-calibration-readiness-index.json \
  --output ../horizon-runs/calibration/r3-r5-readiness.json

PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment controller-evidence \
  --index ../horizon-runs/development/controller-study/index.json \
  --output ../horizon-runs/development/controller-study/readiness.json

PYTHONPATH=.:packages/contracts/python:services/assurance:services/simulator \
  /opt/homebrew/bin/python3.12 -m experiment candidate-acceptance \
  --output ../horizon-runs/development/a1-a5-working-acceptance.json
```

`plan` refuses methods until the relevant production entrypoint is registered in
`configs/capabilities.json`. A missing algorithm is never substituted or aliased. A1-A5 and H0-H4
are registered as distinct implementations. A2 remains an uncalibrated analytic Gaussian trigger.
Current A4 is a finite nonlinear plant-map barrier search with a configured residual reserve and
final rollout revalidation. The earlier kinematic velocity-space QP is registered separately as
the `A4-VQP` historical baseline; results for it do not transfer to A4. Neither method label implies
formal invariance. Generated
runs belong under the external sibling `horizon-runs/`; datasets, weights, activation caches, and
raw traces belong under `horizon-data/`.

The held-out template declares 1,200 final episodes and at least 30 paired seeds for each stochastic headline cell. It is deliberately non-executable until method versions and calibration hashes replace the pending values. No held-out run has been executed yet.

## Integration boundary

The harness consumes the shared `GovernorInput`, `AssuranceDecision`, `RunManifest`, and `EvaluationRecord` contracts from `packages/contracts`. Evaluation truth and fault labels go only to the independent scorer. Production simulation is supplied by A03 through a run adapter; this directory contains no parallel vessel dynamics.

`experiment.harness.closed_loop:run_assured_episode` joins A03 simulation, A05 collection/fusion,
A04 A1-A5 evaluation, and the A04 gate. It builds control input only from public observations and
consumes live fusion source health. `H_FIXED` fixes the experimental neural-health input for
controller isolation; it does not turn stale or unavailable required sources healthy. Private
simulator truth is read after the run for scoring and separate assumption audits.

The offline runner injects one manual monotonic clock into simulation, decision AI, and gate. AI
time is a declared deterministic model; candidate, recovery-prime, gate, and watchdog work advances
the same clock by measured host duration. The recovery cache is synchronous and joined at episode
exit. AI proposals and governor evaluations currently run at 5 Hz while the plant and watchdog run
at 50 Hz. No 20 Hz fresh-state assurance claim is made, and old proposals are never reissued with
new timestamps.
`harness.runner.run_adapter_jobs` checks complete response identity and provenance, refuses duplicate
or overwritten jobs, and routes replay to response-only scoring or closed loop to truth scoring.

The stage-2 commands enforce the frozen runtime/split identities in
`configs/perception-stage2.json`. The drift command is descriptive development analysis. The
calibration command rejects heldout observations, mismatched method arms, inconsistent labels, and
unready H2-H4 artifacts. `r3-r5-calibrate` additionally measures track and AIS set coverage,
A2 reliability/ECE/Brier score, matched-FPR H0-H4 calibration curves, causal-control eligibility,
and diagnostic overhead. It rejects an AIS bound that shrinks a radar-supported bound and never
maps representation novelty into metres. `r3-r5-readiness` is the explicit no-data outcome when
valid calibration observations do not exist; it does not substitute development artifacts. The
controller command checks evidence completeness and returns no ranking.
The detailed gates are in `protocol/perception-stage2.md`.

The external data status is deliberately explicit:

- MaSTr1325 is nominal/reference material with training overlap for published WaSR/WaSR-T weights. It is not held-out evidence.
- The official MODS download is currently access-blocked through the available route. MODD2 must not be relabeled as the MODS benchmark.
- A failed central preflight incurred USD 0.00515378 and produced no GPU evidence. Attempt 002 built
  its image but stopped before L4 activation. Centrally launched attempt 003 later completed all 85
  integration frames on one NVIDIA L4 and was recovered without rerunning inference. This is bounded
  integration and timing evidence, not calibration or heldout evidence. A02 launched no cloud compute.
