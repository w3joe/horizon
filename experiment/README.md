# Horizon experiment harness

This directory turns the maritime RTA research plan into executable protocol. It keeps two comparisons separate:

1. `controller_isolation_replay` sends the same frozen evidence snapshot, including point, covariance, and bounded-set representations, to each governor. It measures decisions and runtime only.
2. `full_pipeline_closed_loop` gives each branch the same initial state, seed, external-AI policy, observation tape, and exogenous fault schedule. Branch state and later observations may diverge after commands differ. Only this mode can support collision-prevention and mission-cost comparisons.

The smoke runner uses tiny analytic synthetic traces to test pairing, scoring, and artifact writing. Its output is not empirical Horizon evidence and is excluded from architecture selection.

From the repository root:

```sh
PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment validate
PYTHONPATH=. /opt/homebrew/bin/python3.12 -m experiment smoke --output ../horizon-runs/a02-smoke
PYTHONPATH=.:packages/contracts/python .venv/bin/pytest -q experiment/tests
```

`plan` refuses A1-A5 or H0-H4 until the relevant production entrypoint is registered in `configs/capabilities.json`. A missing algorithm is never substituted or aliased. Generated runs belong under the external sibling `horizon-runs/`; datasets, weights, activation caches, and raw traces belong under `horizon-data/`.

The held-out template declares 1,200 final episodes and at least 30 paired seeds for each stochastic headline cell. It is deliberately non-executable until method versions and calibration hashes replace the pending values. No held-out run has been executed yet.

## Integration boundary

The harness consumes the shared `GovernorInput`, `AssuranceDecision`, `RunManifest`, and `EvaluationRecord` contracts from `packages/contracts`. Evaluation truth and fault labels go only to the independent scorer. Production simulation is supplied by A03 through a run adapter; this directory contains no parallel vessel dynamics.

The agreed A03 entrypoint is `horizon_sim.experiment_adapter:run_episode` with `services/simulator` on `PYTHONPATH`. `harness.runner.run_adapter_jobs` builds the paired request, checks response identity, and routes replay to response-only scoring or closed loop to truth scoring. A03 currently supports only its explicit `STUB` path and raises for A1-A5 until A04 integration; that behavior preserves the missing-method rule.

The external data status is deliberately explicit:

- MaSTr1325 is nominal/reference material with training overlap for published WaSR/WaSR-T weights. It is not held-out evidence.
- The official MODS download is currently access-blocked through the available route. MODD2 must not be relabeled as the MODS benchmark.
- No GPU was provisioned and no compute spend was incurred by this packet.
