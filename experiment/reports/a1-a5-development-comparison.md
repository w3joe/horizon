# A1–A5 development controller-isolation comparison

## Status and scope

This is a **DEVELOPMENT-ONLY** comparison. It does not select an assurance architecture and does not
support a safety claim. The 60 episodes were run from source commit
`dd3bb433dd7751e5906e18867c67eac5e9070b95` on branch `agent/a02-research`. The raw records,
diagnostics, assumption audits, predeclared plans, aggregate analysis, hashes, and execution
provenance are outside Git at
`horizon-runs/development/a02-a1-a5-discrete-30s-20260921/`.

No calibration or held-out data was accessed. The matrix was fixed before execution: three
development scenarios, two paired seeds per scenario, five candidates, two latency profiles, and
30 simulated seconds per episode. `initially-unrecoverable-v1` remains in every count. `H_FIXED`
fixes only the experimental neural-health input; required navigation, radar, and actuator source
checks remain active.

The harness uses the conditional `fixed-step-discrete-service-v1` model. The plant and watchdog
advance during declared service delay. Candidate completion cannot precede the candidate's emitted
decision time, and the gate and plant receiver check the live logical clock before command
application. This is not a production end-to-end timing measurement.

| Profile | AI | recovery prime | candidate | gate | Episodes |
|---|---:|---:|---:|---:|---:|
| `all-stages-20ms-v1` | 20 ms | 20 ms | 20 ms | 20 ms | 30 |
| `idealized-front-zero-v1` | 0 ms | 0 ms | 20 ms | 20 ms | 30 |

The second profile is an explicitly idealized diagnostic reference. It cannot be used as the
headline architecture result.

## Results

Under `all-stages-20ms-v1`, all 30 episodes failed closed before candidate evaluation. The AI-stage
plant event made the proposal and synchronized evidence stale before fusion assembly. The profile
therefore produced zero decisions, zero gate receipts, and zero candidate runtime samples. All
45,030 scored authority samples remained `plant_startup_passive`. The 10 initially unrecoverable
candidate episodes collided; the 20 crossing and dense-traffic episodes did not. Reporting zero
deadline misses for this profile would be misleading because no decisions ran.

The idealized profile produced 4,470 decisions. Its results still do not isolate candidate effect:
the independent gate and watchdog rejected, replaced, or canceled much of the candidate output.

| Candidate | Runtime version | Decisions by action | Gate accepted / rejected | Scheduler canceled | Watchdog receipts | Collision episodes |
|---|---|---:|---:|---:|---:|---:|
| A1 | `a1-threshold-simplex-v1` | 809 pass, 85 minimum-risk | 85 / 661 | 148 | 1,008 | 2/6 |
| A2 | `a2-probabilistic-risk-simplex-v1` | 809 pass, 85 minimum-risk | 85 / 661 | 148 | 1,008 | 2/6 |
| A3 | `a3-finite-bounded-rollout-v1` | 894 minimum-risk | 894 / 0 | 0 | 803 | 2/6 |
| A4 | `a4-discrete-plant-map-barrier-search-v1` | 894 minimum-risk | 894 / 0 | 0 | 803 | 2/6 |
| A5 | `a5-evidence-conditioned-predictive-filter-hybrid-v1` | 894 minimum-risk | 894 / 0 | 0 | 803 | 2/6 |

All 10 idealized-profile collision episodes are the two predeclared `initially-unrecoverable-v1`
seeds repeated across five candidates. There were no grounding or boundary events.

| Scenario | Episodes per profile | Collision episodes, all-stage | Collision episodes, idealized | Minimum hull clearance, all-stage | Minimum hull clearance, idealized | Minimum boundary clearance | Minimum UKC |
|---|---:|---:|---:|---:|---:|---:|---:|
| `crossing-recoverable-v1` | 10 | 0 | 0 | 142.917 m | 151.476 m | 493.345 m | 6.500 m |
| `dense-traffic-v1` | 10 | 0 | 0 | 64.807 m | 64.394 m | 394.000 m | 8.300 m |
| `initially-unrecoverable-v1` | 10 | 10 | 10 | -4.000 m | -4.000 m | 94.000 m | 6.500 m |

Operational authority counts are 50 Hz scored samples, not elapsed milliseconds. For A1 and A2,
each candidate's six idealized episodes recorded 6,490 `gate_watchdog`, 2,165
`plant_expiry_fallback`, 255 `recovery`, and 96 `plant_startup_passive` samples. For each of A3,
A4, and A5, the corresponding counts were 1,606, 4,634, 2,682, and 84. These large fallback and
watchdog contributions prevent attributing the observed paths to a candidate alone. Across the
idealized profile, the gate accepted 2,852 submissions and rejected 1,322; 296 queued submissions
were canceled after a watchdog generation change. Rejection receipts can carry multiple reasons:
all 1,322 included final-command revalidation failure, 902 included collision-margin violation, and
426 included boundary-margin violation.

## Descriptive wall timings

The candidate timing samples below are emitted decision compute times from the idealized profile.
They are descriptive local observations. Part of the run overlapped a separate, bounded MPS
development job, recorded in `execution-events.jsonl`; these values are host-contended and are not
production or end-to-end benchmarks.

| Candidate | Samples | p50 | p95 | maximum | Deadline misses |
|---|---:|---:|---:|---:|---:|
| A1 | 894 | 2.404 ms | 30.728 ms | 31.806 ms | 0 |
| A2 | 894 | 6.698 ms | 30.734 ms | 31.790 ms | 0 |
| A3 | 894 | 30.387 ms | 31.144 ms | 34.868 ms | 0 |
| A4 | 894 | 30.368 ms | 31.169 ms | 32.516 ms | 0 |
| A5 | 894 | 30.514 ms | 31.273 ms | 35.062 ms | 0 |

Aggregate gate-submit wall time was 81.684 ms p50, 185.800 ms p95, and 219.147 ms maximum.
Recovery-prime wall time was 242.875 ms p50, 2,164.959 ms p95, and 2,577.758 ms maximum. These
measured durations did not replace the predeclared simulated gate and prime delays; gate submission
is atomic at its modeled completion event. Candidate wall work can affect simulated completion only
through the unchanged emitted decision-time floor.

## Assumptions and interpretation

The idealized profile audited 40,230 bounded-error components against truth and retained all
violations. Forty contact-position components exceeded
`synthetic-harbor-radar-contact-odd-bound-v1`; the maximum observed error-to-bound ratio was 1.138.
There were no unresolved truth associations. The all-stage profile could not establish a bounded
input because no fused governor input survived the front-end delay, so its audit checked zero
components. Gaussian observation noise remains unbounded; covariance was never converted into a
hard bound.

A2 uses an uncalibrated analytic Gaussian risk approximation. Its results are not empirical
calibration evidence. A4 is the recorded discrete plant-map barrier-search implementation; this
study makes no formal invariance, exact reachability, or plant-CBF guarantee. The harness evaluates
fresh proposals at 5 Hz rather than providing a 20 Hz fresh-state assurance loop. Only 22 of 30
idealized traces and none of the all-stage traces met the current trace-completeness criterion, and
no episode completed the mission in 30 seconds.

The useful finding is a harness and integration diagnosis: a 20 ms front-end stage is incompatible
with the current freshness contract, while the idealized zero-front-end cell exposes strong
gate/watchdog coupling. More development experiments need a viable, predeclared front-end service
model and a design that separates candidate behavior from independent gate recovery before any
A1–A5 comparison can support an architecture recommendation.
