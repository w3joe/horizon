# H5 warning response: paired simulation results

This study measures the effect of reacting to recorded H5 warnings. Both branches use the nominal fixture planner, A5, radar geometry and the actuator gate. It does not compare standalone WaSR-T navigation with H5 navigation.

## Results

Means are over matched seeds. Normal transit lasts 100 s; crossing lasts 120 s. No-contact transit has no inter-vessel clearance metric.

| Scenario | Video arm | H5 response | Collisions | Mean min. hull clearance (m) | Mean travel (m) | Arrivals | Mean warning requests | Proxy-negative warning requests (s) | Deadline misses |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| normal-transit-v1 | nominal | off | 0 | — | 46.25 | 0/3 | 0.0 | 0.0 | 23 |
| normal-transit-v1 | nominal | on | 0 | — | 46.23 | 0/3 | 0.0 | 0.0 | 14 |
| normal-transit-v1 | occlusion-v1 | off | 0 | — | 46.28 | 0/3 | 0.0 | 0.0 | 32 |
| normal-transit-v1 | occlusion-v1 | on | 0 | — | 45.31 | 0/3 | 39.0 | 7.8 | 26 |
| crossing-recoverable-v1 | nominal | off | 0 | 146.06 | 58.72 | 0/3 | 0.0 | 0.0 | 16 |
| crossing-recoverable-v1 | nominal | on | 0 | 146.00 | 58.78 | 0/3 | 0.0 | 0.0 | 15 |
| crossing-recoverable-v1 | occlusion-v1 | off | 0 | 146.09 | 58.71 | 0/3 | 0.0 | 0.0 | 13 |
| crossing-recoverable-v1 | occlusion-v1 | on | 0 | 145.98 | 58.81 | 0/3 | 59.0 | 11.8 | 15 |

## Interpretation

There were 0 collisions across 24 episodes. The study therefore demonstrates no observed reduction in collisions.

Only 0/24 episodes reached the arrival region. Arrival-delay comparisons are undefined wherever either paired branch did not arrive; the run horizon must not be substituted for a journey time.

Trace completeness passed in 24/24 episodes, with 0 authority-trace mismatches. The generic scorer reports 4978 acceptance flags; all 4978 identified recovery substitutions must be separated from expired or invalid proposals. The gate can retain a validated recovery command while an autonomy-release handshake is pending. Raw scorer results are preserved.

The independent receipt audit found: unapproved_source=0, expired_proposal=0, expired_decision=0, invalid_decision=0, unexplained_authority_difference=0. This is an input/receipt audit, not a fresh certification of every recovery trajectory.

Mean accepted autonomy commands at or below 1 m/s in the occlusion warning branches: normal-transit-v1=39.0; crossing-recoverable-v1=0.0. A warning request is not an executed autonomy command when the gate retains recovery authority.

The nominal video arm produces no H5 warnings and acts as a no-response control. Its branch differences and deadline misses expose variation due to wall timing. Watchdog actions also influence movement. These effects limit attribution of small trajectory changes to H5.

The recorded video is unrelated to the simulated encounters. A changed clearance does not show that H5 detected a simulated hazard. Proxy-negative warning request time is a cost indicator based on recorded-image labels, not measured time spent unnecessarily slowing the physical boat. H5 scores were precomputed, so video/monitor compute overhead is not assessed.

## Audit and reproduction

- Output directory: `/Users/w3joe/Desktop/2026_sdth/horizon-runs/development/h5-control-paired-20260925-v1`.
- Source commit before experiment edits: `7e1370454f1cb7b8d78b7bdd53f5c8b94442f2f7`; the frozen protocol records the exact experimental source hashes.
- `protocol.json`, `tape-index.json`, compressed branch bundles and `summary.json` retain the inputs and results.
- `frozen-source/` retains the four experimental source files, verified against the protocol hashes.
- `analysis.json` adds per-branch authority durations and distance travelled without changing the source results.
- See [the protocol](../protocol/h5-control-response.md) for design, provenance and reproduction.
- Keep H5 optional: a pose-reactive visual experiment with stable control timing is needed to establish navigation benefit.
