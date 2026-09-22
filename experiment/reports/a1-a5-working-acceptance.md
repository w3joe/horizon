# A1-A5 working implementation acceptance

For this packet, **working** means that each registered A1-A5 candidate produces a schema-valid, finite
`AssuranceDecision` through its declared common entrypoint; preserves its exact candidate ID and
version; passes a distant-contact proposal; selects a validated recovery for a recoverable crossing;
and issues minimum risk without fabricating recovery evidence when no recovery validates. All
methods receive the same public snapshot, proposal, health, constraints, deadlines, and uncertainty
evidence within each fixture. Only run and branch bookkeeping IDs differ.

The executable audit applies the common `AssuranceDecision` schema acceptance to A1 through A5.
It also reproduces A4-VQP as an explicitly separate historical diagnostic baseline. A4-VQP is
deliberately outside the contract's A1-A5 candidate enum and outside architecture selection. It
checks these exact labels:

| ID | Registered method | Version | Role |
|---|---|---|---|
| A1 | rule threshold simplex | `a1-threshold-simplex-v1` | candidate architecture |
| A2 | probabilistic risk simplex | `a2-probabilistic-risk-simplex-v1` | candidate architecture |
| A3 | bounded predictive recoverability | `a3-finite-bounded-rollout-v1` | candidate architecture |
| A4 | finite nonlinear plant-map barrier search | `a4-discrete-plant-map-barrier-search-v1` | candidate architecture |
| A5 | evidence-conditioned hybrid | `a5-evidence-conditioned-predictive-filter-hybrid-v1` | candidate architecture |
| A4-VQP | provisional kinematic velocity-space QP plus final rollout | `a4-provisional-kinematic-filter-full-plant-validation-v1` | historical algorithm baseline |

The audit uses a 500 ms candidate work budget and 10 s finite prediction/recovery horizons to test
algorithm behavior without conflating it with host scheduling. The production 40 ms deadline and
normal configuration remain unchanged. Measured times in the generated JSON are descriptive and
cannot establish production timing acceptance.

Run it from the repository root with the service paths shown in `experiment/README.md`. The output
belongs under the external `horizon-runs/` tree. The audit deliberately makes no claim of probability
calibration, formal barrier invariance, viability, closed-loop collision prevention, mission
completion, or architecture ranking. Those require predeclared paired closed-loop evidence and the
still-unrun frozen study.

`experiment/manifests/a1-a5-working-development.json` defines a five-branch, one-seed closed-loop
diagnostic using the existing `idealized-front-zero-v1` service cell: zero declared AI and recovery
prime delay, then 20 ms candidate and 20 ms gate service. The candidate decision deadline remains
40 ms, while plant-grid scheduling, measured candidate work, and the later gate stage are reported
separately. This is a viable nonzero back-half diagnostic, not an end-to-end production latency
claim. Its single development seed checks that the current implementations enter the shared adapter
and exposes gate/watchdog coupling; it cannot support comparative ranking.

## Bounded paired result

The current implementation ran the manifest for 0.45 simulated seconds under run ID
`a30-a1-a5-paired-closed-loop-002`. All five branches shared one pair key, emitted two schema-valid
decisions, reported zero candidate deadline misses, had complete short traces, and remained censored
because the 0.45 s diagnostic cannot finish the route. This is the observed action and gate evidence:

| Candidate | Actions | Gate accepted / rejected | Descriptive candidate time |
|---|---|---:|---:|
| A1 | 2 pass | 0 / 2 | 1.46–1.53 ms |
| A2 | 2 pass | 0 / 2 | 2.69–2.76 ms |
| A3 | 2 recover | 2 / 0 | 26.63–26.64 ms |
| A4 | 2 recover | 2 / 0 | 26.27–26.38 ms |
| A5 | 2 minimum risk | 2 / 0 | 29.75–29.91 ms |

A3 and A4 no longer reproduce the older development run's universal minimum-risk behavior on this
cell: both found and submitted recoveries. A5 still reached its internal 29 ms work limit while
conditioning evidence and checking prediction/recovery, so it returned minimum risk with
`PREDICTION_DEADLINE_EXHAUSTED` and `NO_VALIDATED_RECOVERY`; its outer 40 ms candidate deadline was
still reported met. A1 and A2 passed the proposal, but the independent gate rejected all four pass
decisions after final-command revalidation found a collision-margin violation. That disagreement is
substantive candidate/gate evidence, not a reason to relabel either result as success.

The run therefore closes the earlier implementation ambiguity but does not make the five candidates
comparable for selection. It has one seed, two decision opportunities per branch, an idealized
zero-delay front end, a censored mission, and no independent sampled recovery boundary.
