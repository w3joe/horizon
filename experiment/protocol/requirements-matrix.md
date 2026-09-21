# Research requirement matrix

| Research requirement | Executable artifact | Evidence/status |
|---|---|---|
| A1-A5 are distinct | `configs/capabilities.json`, hard-fail registry tests | Entries explicit; implementations pending A04 |
| H0-H4 are distinct | `configs/capabilities.json`, hard-fail registry tests | Entries explicit; implementations pending A07 |
| Controller isolation | `controller_isolation_replay` mode and replay scorer | Outcome scoring prohibited by test |
| Full pipeline comparison | `full_pipeline_closed_loop` mode and A03 adapter request | Analytic fixture passes; A03 integration pending |
| Paired stochastic runs | `EpisodeKey`, job expansion, pairing test | Pair includes scenario/seed/tapes/policy |
| Development/calibration/held-out separation | `manifests/splits.json`, manifest validation | Disjoint seeds and provenance groups; held-out frozen |
| >=1,000 held-out episodes | held-out template with 1,200 distinct scenario/seed episodes | Blocked until versions and calibration are frozen; not executed |
| >=30 stochastic headline pairs | manifest guard | Enforced before job expansion |
| Independent truth scoring | `evaluation/scoring.py` | Shared EvaluationRecord schema test passes on fixture |
| Calibration without test leakage | `evaluation/calibration.py` | Only calibration split accepted |
| Safety-first selection | `configs/selection-rule.json` | Frozen rule; no recommendation issued |
| Explicit uncertainty semantics | shared contracts and A1-A5 requirements | Covariance coverage and bounded assumptions remain distinct |
| Model-appropriate A4 | `protocol/barrier-and-recoverability.md` | Required disclosures defined; implementation pending |
| No fabricated results | fixture labels and claim ledger | Recommendation is withheld; smoke excluded |
| Compute cap | `jobs/compute-budget.json` | USD 80 usable, USD 20 reserve, zero spend |
