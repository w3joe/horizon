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
```

`plan` refuses methods until the relevant production entrypoint is registered in
`configs/capabilities.json`. A missing algorithm is never substituted or aliased. A1/A3 and the
H0-H4 entrypoint scaffolds are registered; A2/A4/A5 remain unsupported in this packet. Generated
runs belong under the external sibling `horizon-runs/`; datasets, weights, activation caches, and
raw traces belong under `horizon-data/`.

The held-out template declares 1,200 final episodes and at least 30 paired seeds for each stochastic headline cell. It is deliberately non-executable until method versions and calibration hashes replace the pending values. No held-out run has been executed yet.

## Integration boundary

The harness consumes the shared `GovernorInput`, `AssuranceDecision`, `RunManifest`, and `EvaluationRecord` contracts from `packages/contracts`. Evaluation truth and fault labels go only to the independent scorer. Production simulation is supplied by A03 through a run adapter; this directory contains no parallel vessel dynamics.

`experiment.harness.closed_loop:run_assured_episode` joins A03 simulation, A05 collection/fusion,
A04 A1/A3 evaluation, and the A04 gate. It builds control input only from public observations.
Private simulator truth is read after the run for scoring and separate assumption audits.
`harness.runner.run_adapter_jobs` checks complete response identity and provenance, refuses duplicate
or overwritten jobs, and routes replay to response-only scoring or closed loop to truth scoring.

The external data status is deliberately explicit:

- MaSTr1325 is nominal/reference material with training overlap for published WaSR/WaSR-T weights. It is not held-out evidence.
- The official MODS download is currently access-blocked through the available route. MODD2 must not be relabeled as the MODS benchmark.
- A failed central preflight incurred USD 0.00515378 and produced no GPU evidence. A02 launched no
  cloud compute. Central attempt 002 built its image but stopped before L4 activation because the
  provider requires a payment method. It produced no GPU evidence, and no further launch is pending.
