# Research requirement matrix

| Research requirement | Executable artifact | Evidence/status |
|---|---|---|
| A1-A5 are distinct | `configs/capabilities.json`, hard-fail registry tests | Five non-aliased production entrypoints registered; development comparison not yet run |
| H0-H4 are distinct | `configs/capabilities.json`, hard-fail registry tests | Five non-aliased entrypoints registered; no held-out risk validation |
| Controller isolation | `controller_isolation_replay` mode and replay scorer | Outcome scoring prohibited by test |
| Full pipeline comparison | `full_pipeline_closed_loop` mode and integrated A03/A05/A04 adapter | A1-A5 integration tests pass; bounded development comparison pending |
| Paired stochastic runs | `EpisodeKey`, job expansion, pairing test | Pair includes scenario/seed/tapes/policy |
| Development/calibration/held-out separation | `manifests/splits.json`, manifest validation | Disjoint seeds and provenance groups; held-out frozen |
| >=1,000 held-out episodes | held-out template with 1,200 distinct scenario/seed episodes | Blocked until versions and calibration are frozen; not executed |
| >=30 stochastic headline pairs | manifest guard | Enforced before job expansion |
| Independent truth scoring | `evaluation/scoring.py`, command-ID authority audit | Shared schema plus exact receiver command lineage; recovery feasibility remains unknown |
| Calibration without test leakage | `evaluation/calibration.py` | Only calibration split accepted |
| Safety-first selection | `configs/selection-rule.json` | Frozen rule; no recommendation issued |
| Explicit uncertainty semantics | shared contracts and A1-A5 requirements | Covariance coverage and bounded assumptions remain distinct |
| Model-appropriate A4 | `protocol/barrier-and-recoverability.md`, A4 candidate | Provisional kinematic QP plus final 3-DOF rollout; no formal CBF claim |
| No fabricated results | fixture labels and claim ledger | Recommendation is withheld; smoke excluded |
| Compute cap | `jobs/compute-budget.json` | USD 80 usable, USD 20 reserve, zero spend |
