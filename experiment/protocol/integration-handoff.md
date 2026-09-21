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

A1 and A3 are registered and exercised through `experiment.harness.closed_loop:run_assured_episode`.
The adapter sends delivered public observations through A05 `CollectorStore` and `FusionEngine`,
uses the explicit simulator monotonic clock, holds the decision budget at 40 ms, and routes commands
through the independent gate. Truth enters only after the episode for scoring and assumption audits.
A05 covariance is not converted into a hard bounded set. A2/A4/A5 remain
unsupported until distinct implementations land. A4 additionally supplies the barrier geometry,
control abstraction, relative degree, convexity conditions, disturbance/sampled-data margins,
solver tolerances, and final 3-DOF rollout validation. A finite rollout checker is engineering
validation, not exact reachability or a formal barrier guarantee.

## A07 health registration

H0-H4 entrypoints are registered separately through a deliberate adapter. Missing artifacts,
non-finite evidence, identity mismatches, or risk bands without validated artifact lineage yield
`unknown` and cannot authorize camera free-space. Threshold fitting accepts only calibration data.
Health methods use independently chosen thresholds at a matched false-alarm target, then remain
frozen on held-out data. MaSTr1325 is nominal/reference material, not held-out evidence for
pretrained WaSR/WaSR-T.
