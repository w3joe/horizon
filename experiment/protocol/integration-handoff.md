# Integration handoff

## Shared contract

Contract v0.1.0 now includes the required censoring and gate-assessment fields:

`route_delay_s` and `extra_distance_m` are null when a mission fails, times out, or is truncated. This prevents an early collision from receiving a favorable negative delay. `assessment_status` is unknown when the scorer cannot join proposal identity/source/expiry, decision validity/authority/expiry, receipt time/authority, and gate acceptance. A missing join is not evidence of zero unsafe acceptances.

The scorer validates its fixture against the generated schema.

## A03 adapter

Use `horizon_sim.experiment_adapter:run_episode` with `services/simulator` on `PYTHONPATH`. The request shape is created by `harness.runner.build_episode_request`. Closed-loop normalized responses include private truth frames, stable violation events, proposal/decision/gate records, and an independent recovery reference:

```text
recovery_reference
  source_branch_id: paired nominal/unprotected branch
  method: offline_finite_library
  independent_of_candidate: true
  hazard_window_end_s: first unprotected violation or closest approach
  window_end_reason: first_unprotected_violation | unprotected_closest_approach
  feasible_samples: [{simulation_time_s, feasible}]
```

The protected candidate branch may not define its own last recovery boundary because a successful intervention can make later protected states recoverable. If the reference is absent, lead time remains null.

## A04 candidate registration

A1-A5 are registered and exercised through `experiment.harness.closed_loop:run_assured_episode`.
The adapter sends delivered public observations through A05 `CollectorStore` and `FusionEngine`,
uses one injected monotonic clock across simulator, decision fixture, and gate, holds the decision
budget at 40 ms, and routes commands through the independent gate. The offline cache refresh is
synchronous, workers are joined at episode exit, and a 1,000-episode lifecycle test checks that no
worker remains. Truth enters only after the episode for scoring and assumption audits.

A05 covariance is not converted into a hard bounded set. The ODD audit compares private truth to
the exact configured `EngineeringBound` IDs and values. Fused track IDs are not treated as oracle
vessel IDs; a separately labeled truth-only positional association supplies matches and leaves
unresolved contacts explicit. A2's probability threshold is analytic and uncalibrated. A4 is a
provisional kinematic velocity-space QP whose issued command still requires final 3-DOF rollout
validation. A finite rollout checker is engineering validation, not exact reachability or a formal
barrier guarantee.

Operational authority is reconstructed after control from the truth frame's exact active command
ID and the accepted protected-command envelope. The simulator writer channel remains a separate
field. Startup passive and receiver-expiry fallback periods are preserved rather than inheriting
the authority of the latest receipt. Recovery feasibility remains null until an independent
evaluator supplies a reference.

## A07 health registration

H0-H4 entrypoints are registered separately through a deliberate adapter. Missing artifacts,
non-finite evidence, malformed nested values, identity mismatches, expiries beyond the request, or
risk bands without validated artifact lineage yield
`unknown` and cannot authorize camera free-space. Threshold fitting accepts only calibration data.
Health methods use independently chosen thresholds at a matched false-alarm target, then remain
frozen on held-out data. MaSTr1325 is nominal/reference material, not held-out evidence for
pretrained WaSR/WaSR-T.
