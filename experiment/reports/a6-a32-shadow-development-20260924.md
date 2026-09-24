# A6 shadow on the A32 A1-A5 development design

**Run:** external `horizon-runs/development/a6-a32-shadow-30s-20260924-v2/`

**Result index SHA-256:** `3b299a0cd0f2c5dda465e6128e39e992b4135e5b8358a6d3c9b35102cdf04efe`

**Analysis SHA-256:** `f18221263d9c14fc2e16eca5c8be466b951eab60cab9d45811d49d93b2d88357`

## Design

This development rerun preserves the original A32 experiment's 12 paired
episode identities: four scenarios, three seeds per scenario, the same
observation-tape hashes, fault-schedule hashes, decision-AI policy versions,
30-second horizon, `H_FIXED` health policy, and
`idealized-front-zero-v1` timing profile. The current A5 implementation remains
the physical controller. A6 observes each completed A5 decision without
advancing simulated time and without access to the gate or plant.

The A6 bundle, declared lookout capabilities, Singapore vessel profile, and
public-state evidence adapter are synthetic development fixtures. They are not
authoritative policy ingestion or legal validation.

## Results

| Measure | Result |
|---|---:|
| Episodes | 12 |
| A6 assessments | 1,788 |
| A6 supported | 0 |
| A6 withheld | 1,788 |
| A6 latency p50 / p95 / p99 | 0.178 / 0.281 / 0.318 ms |
| A6 maximum latency | 0.394 ms |
| A5 gate accepted / rejected | 1,773 / 15 |
| A5 recover / minimum-risk / invalid | 888 / 885 / 15 |
| Collision episodes | 3 |

Each scenario produced 447 A6 assessments and withheld all 447. All 1,788
assessments recorded `PHYSICAL_A5_SAFETY_DOMINATES`, because A5 produced no
`pass` or `modify` result in this experiment. Fifteen assessments additionally
recorded `A5_DECISION_NOT_VALID`. The three collision episodes were the three
predeclared initially-unrecoverable cases, matching the original scenario
stratification.

## Interpretation

A6 cannot be inserted as a sixth row in the original controller ranking: it
does not issue commands and therefore has no gate-acceptance or collision-rate
metric of its own. The matched result instead verifies the composition's main
safety invariant across the complete development design: A6 never turns an A5
recovery, minimum-risk, or invalid result into positive policy support.

The current-code rerun recorded 15 A5 invalid decisions, compared with three in
the historical A32 artifact from revision `28eb923`. Host wall time can affect
A5's emitted deadline result, and the development timing profile is not a
production latency model. The historical and current-revision counts must
therefore remain separately labelled rather than silently replacing one
another.

## Reproduction

```sh
PYTHONPATH=.:packages/contracts/python:services/assurance:services/gate:services/simulator:services/collector:services/fusion \
  .venv/bin/python -m experiment run-adapter \
  --study-plan experiment/manifests/a6-a32-shadow-development.json \
  --entrypoint experiment.harness.closed_loop:run_assured_episode \
  --run-id a6-a32-shadow-30s-20260924-v2 \
  --max-simulation-time-s 30 \
  --output ../horizon-runs/development/a6-a32-shadow-30s-20260924-v2
```
