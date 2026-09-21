# Integration handoff

## Required shared-contract delta

The truth scorer intentionally implements two semantics that contract v0.1.0 cannot yet encode. A01 should apply this narrow `EvaluationRecord` delta and regenerate Python/TypeScript declarations:

```text
EvaluationRecord.mission.required += "censored"
EvaluationRecord.mission.censored: boolean
EvaluationRecord.mission.route_delay_s: number | null
EvaluationRecord.mission.extra_distance_m: number | null

EvaluationRecord.gate.required += "assessment_status"
EvaluationRecord.gate.assessment_status: "complete" | "unknown"
```

`route_delay_s` and `extra_distance_m` are null when a mission fails, times out, or is truncated. This prevents an early collision from receiving a favorable negative delay. `assessment_status` is unknown when the scorer cannot join proposal identity/source/expiry, decision validity/authority/expiry, receipt time/authority, and gate acceptance. A missing join is not evidence of zero unsafe acceptances.

`experiment/tests/test_scoring.py::test_scored_fixture_matches_shared_evaluation_contract` is expected-xfail only until this generated contract delta lands. The separate semantic tests already pass and must remain.

## A03 adapter

Use `horizon_sim.experiment_adapter:run_episode` with `services/simulator` on `PYTHONPATH`. The request shape is created by `harness.runner.build_episode_request`. Closed-loop normalized responses include private truth frames, stable violation events, proposal/decision/gate records, and an independent recovery reference:

```text
recovery_reference
  source_branch_id: paired nominal/unprotected branch
  method: offline_finite_library
  independent_of_candidate: true
  feasible_samples: [{simulation_time_s, feasible}]
```

The protected candidate branch may not define its own last recovery boundary because a successful intervention can make later protected states recoverable. If the reference is absent, lead time remains null.

## A04 candidate registration

Register A1-A5 only after each distinct entrypoint exists. A4 additionally supplies the barrier geometry, control abstraction, relative degree, convexity conditions, disturbance/sampled-data margins, solver tolerances, and final 3-DOF rollout validation. A finite rollout checker is described as engineering validation, not exact reachability or a formal barrier guarantee.

## A07 health registration

Register H0-H4 separately. Threshold fitting accepts only the calibration split. Health methods use independently chosen thresholds at a matched false-alarm target, then remain frozen on held-out data. MaSTr1325 is nominal/reference material, not held-out evidence for pretrained WaSR/WaSR-T.
