# A32 A1-A5 30-second development analysis

**Source revision:** `28eb923`  
**Run:** external `horizon-runs/development/a32-a1-a5-30s-20260922/`  
**Result index SHA-256:** `a63a511a619c016c8d9a0676c28eaac7d317c76caafa1f2c7e009f82501dbbd0`  
**Reproducible analysis hash:** `8004358ed9ac50c824750a6906e344b94e438ec04b0448bca421e7768072e909`

## Scope

This analysis covers 60 development episodes from the `idealized-front-zero-v1` modeled service profile. It used no calibration or held-out data. All results are finite-run engineering evidence.

The analyzer verified 12 paired episode identities, five candidates per pair, and exact one-to-one joins among all 60 evaluation records, diagnostics, and assumption audits.

## Candidate results

| Candidate | Episodes | Actions | Gate accepted/rejected | Deadline misses | Collision episodes | Min hull clearance | Trace complete | Censored | Compute p50/p95/max |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | 12 | minimum_risk 128, pass 1659, recover 1 | 129 / 1326 | 0 | 3 | -4.000 m | 12 | 12 | 1.657 / 31.695 / 36.706 ms |
| A2 | 12 | invalid 1, minimum_risk 127, pass 1660 | 127 / 1328 | 1 | 3 | -4.000 m | 12 | 12 | 3.230 / 32.587 / 42.108 ms |
| A3 | 12 | invalid 6, minimum_risk 948, recover 834 | 1782 / 6 | 6 | 3 | -4.000 m | 12 | 12 | 29.480 / 33.629 / 56.637 ms |
| A4 | 12 | invalid 10, minimum_risk 961, recover 817 | 1778 / 10 | 10 | 3 | -4.000 m | 12 | 12 | 29.635 / 33.479 / 61.449 ms |
| A5 | 12 | invalid 3, minimum_risk 898, recover 887 | 1785 / 3 | 3 | 3 | -4.000 m | 12 | 12 | 29.056 / 33.536 / 50.705 ms |

## Gate and scheduler evidence

| Candidate | Gate acceptance rate | Scheduler rejected | Unsafe/stale accepted | Watchdog receipts |
|---|---:|---:|---:|---:|
| A1 | 8.87% | 333 | 0 | 186 |
| A2 | 8.73% | 333 | 0 | 0 |
| A3 | 99.66% | 0 | 0 | 888 |
| A4 | 99.44% | 0 | 0 | 888 |
| A5 | 99.83% | 0 | 0 | 894 |

## Scenario results across candidates

| Scenario | Episodes | Collision episodes | Min hull clearance | Trace complete | Censored | Assumption-violation episodes |
|---|---:|---:|---:|---:|---:|---:|
| crossing-recoverable-v1 | 15 | 0 | 140.821 m | 15 | 15 | 15 |
| dense-traffic-v1 | 15 | 0 | 64.807 m | 15 | 15 | 10 |
| vessel-degradation-v1 | 15 | 0 | 203.838 m | 15 | 15 | 0 |
| initially-unrecoverable-v1 | 15 | 15 | -4.000 m | 15 | 15 | 15 |

## Timing and assumption evidence

The modeled service intervals were AI 0 ms, recovery prime 0 ms, candidate 20 ms, and gate 20 ms. The host measurements below are descriptive and do not directly advance simulation time.

| Measured host stage | Samples | p50 | p95 | Maximum |
|---|---:|---:|---:|---:|
| candidate | 8940 | 25.645 ms | 33.029 ms | 61.458 ms |
| gate_submit | 8274 | 11.080 ms | 40.170 ms | 64.846 ms |
| recovery_prime | 8940 | 1.529 ms | 2.130 ms | 6.680 ms |
| watchdog | 90000 | 0.001 ms | 0.010 ms | 0.578 ms |

Engineering-bound violations occurred in 40 of 60 candidate branches and 65 of 73755 checked components. The maximum contact-position error-to-bound ratio was 1.137836. There were 0 unresolved truth associations. All affected branches remain in the outcome counts.

## Observed development patterns

- All 15 initially-unrecoverable branches collided. No declared-recoverable branch collided.
- A1 and A2 emitted mostly pass decisions, while their gate acceptance rates were 8.87% and 8.73%. Gate revalidation and scheduler cancellation therefore materially changed their closed-loop behavior.
- A3, A4, and A5 emitted only recovery, minimum-risk, or invalid decisions. They recorded 6, 10, and 3 candidate deadline misses, respectively.
- All 60 branches were mission-censored at 30 seconds; route delay and extra distance remain unknown. No independent recovery boundary was supplied.

## Interpretation limits

- Development split only; the protocol was not frozen and calibration_hash is null.
- Three paired seeds per scenario are insufficient for statistical safety or architecture-selection claims.
- The idealized-front-zero-v1 profile assigns zero modeled AI and recovery-prime service delay; it is not production end-to-end timing evidence.
- Observed host wall times are descriptive and do not directly advance simulated time; candidate compute can affect the plant-grid completion floor.
- Gate and watchdog behavior contributes materially to trajectories, so closed-loop outcomes cannot be attributed to the candidate alone.
- Censored missions have unknown route delay and extra distance; early termination is never counted as mission benefit.
- No independent sampled recovery boundary was present, so intervention lead time is unknown.
- Gaussian simulator noise is unbounded; engineering-bound violations remain in all outcome denominators.
- A2 probability is uncalibrated; A3/A5 recoverability and A4 barrier behavior are finite sampled engineering checks, not formal guarantees.
- Initially unrecoverable episodes are retained and stratified; they do not establish preventable-collision performance.
- No held-out or calibration data was read, and this report makes no architecture recommendation.

The accompanying JSON retains candidate-scenario cells, action and reason counts, gate and watchdog evidence, timing distributions, authority samples, pairing checks, and assumption audits.
