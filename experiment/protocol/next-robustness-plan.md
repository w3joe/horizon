# Next research plan: evidence before more architectures

**Decision:** do not expand A1-A5 yet. The next research milestone is to make the comparison valid, calibrated, and hard to game. Keep **A5 as the provisional demo controller with A1 as the independently simple fallback**, but do not call A5 the research winner until the frozen held-out gates pass.

The current 60-episode development run is useful integration evidence, but it cannot rank the candidates: every mission was censored; 40 branches exceeded at least one declared engineering bound; the modeled front-end delay was zero; no independent recovery boundary was supplied; A2 was uncalibrated; and no calibration or held-out data was read. Adding another architecture would add breadth without resolving those limits.

## Ordered work

### R1 — Make one episode scientifically complete

Fix the experiment before scaling it:

- extend the horizon until mission completion or a predeclared censoring event;
- supply an independent sampled recovery boundary and record intervention lead time;
- replace the zero-delay front end with measured or deliberately conservative service-delay distributions for sensing, fusion, candidate, gate, and actuator stages;
- remove the current error-to-bound overruns or classify the affected episode as outside the declared operating domain;
- separate preventable, initially unrecoverable, and out-of-domain cases in the scorer; and
- prove that the protected and counterfactual branches share the same initial state, exogenous fault tape, and autonomy proposal until commands cause legitimate divergence.

**Gate R1:** one crossing case and one dense-traffic case finish with complete lineage, mission outcome, recovery-boundary lead time, end-to-end timing, and no unexplained engineering-bound violation.

### R2 — Freeze the operating domain and data partitions

Freeze candidate versions, decision budgets, vessel model, safety margins, scenario families, metrics, and the development/calibration/held-out split before final evaluation. Include:

- crossing, head-on, overtaking, multi-contact and narrow-channel geometries;
- glare, fog, camera dropout, radar clutter, GNSS/IMU error, network delay/reorder and decision-AI anomalies;
- AIS dropout, false identity, stale course/speed, a radar-only contact, and an AIS-derived Singapore traffic snapshot;
- parameter sweeps around speed, mass/loading, current, wind/wave disturbance, sensor latency, detection range, contact maneuver, and actuator degradation; and
- explicit in-domain and out-of-domain labels.

Use scenario/session/source-provenance groups as split units. Adjacent video frames or samples from the same AIS capture cannot cross partitions.

**Gate R2:** immutable manifests and hashes exist for code, configs, datasets, traffic/geography bundles, calibration split, held-out split, and all thresholds that will be frozen before held-out access.

### R3 — Calibrate uncertainty before architecture selection

Calibrate the quantities the architectures consume:

- track covariance or bounded position/velocity sets against simulator truth;
- camera obstacle-miss risk and perception-health states on a calibration split;
- A2 collision-risk threshold and coverage behavior;
- age-dependent AIS uncertainty without allowing AIS to shrink radar-supported uncertainty; and
- false-intervention targets shared by H0-H4 so health methods are compared at equal operating points.

Report reliability curves, expected calibration error or an appropriate risk calibration score, empirical set coverage, and performance stratified by condition. Do not convert representation novelty directly into metres of obstacle uncertainty without a calibrated geometric model.

**Gate R3:** each controller input has declared semantics, observed calibration/coverage on calibration data, and a conservative unknown/degraded behavior outside that scope.

### R4 — Run the existing A1-A5 tournament

Run two separately labelled studies:

1. **Controller isolation:** identical frozen evidence bundles and proposals enter A1-A5.
2. **Full pipeline:** identical raw observations and faults enter the common collector/fusion path, after which closed-loop state may diverge because commands differ.

Use at least 1,000 frozen held-out episodes and at least 30 paired seeds per stochastic headline scenario/candidate cell, as already specified. Report preventable safety violations, unsafe/stale gate acceptances, collision/grounding margin, intervention lead time, mission completion/cost, false interventions, p50/p95/p99/max end-to-end latency, and every missed deadline. Apply the frozen safety-first Pareto rule.

**Gate R4:** select only among candidates with zero observed preventable violations, zero unsafe/stale gate acceptances, defined timeout/infeasibility behavior, and no deadline miss inside the declared acceptance load. Wilson intervals accompany finite-suite event rates; zero observed events is not a proof of zero risk.

### R5 — Test whether neural internals add value

On the best one or two RTA candidates from R4, compare the frozen H0-H4 perception-health methods at a matched false-alarm rate:

- H0: confidence only;
- H1: image quality, entropy and temporal consistency;
- H2: embedding-distance monitor;
- H3: sparse-autoencoder or dictionary reconstruction/novelty monitor; and
- H4: H1 plus the best representation monitor.

Use causal controls before making a mechanistic claim: patch or ablate selected features and measure the obstacle/water segmentation change; compare against random features; rerun saliency sanity checks; and measure instrumentation overhead and dropped diagnostic frames. H4 is adopted only if it improves held-out fault detection or downstream safety/mission performance over H1-H3 after compute and false-alarm costs.

**Gate R5:** the health method has a frozen threshold, held-out detection/false-alarm results by condition, causal-control evidence for any mechanistic statement, and a bounded effect on RTA uncertainty/mode rather than direct steering authority.

### R6 — Singapore traffic robustness study

Use the AIS traffic mirror as domain-randomization input, not evaluation truth. Compile several separately hash-pinned traffic snapshots by time of day and area, then vary contact motion and sensing faults synthetically while retaining the same initial traffic geometry across candidate branches.

Measure:

- closest-point-of-approach and time-to-CPA errors under stale or missing AIS;
- association failures when radar and AIS disagree;
- collision avoidance for an AIS-derived contact and a simultaneous radar-only contact;
- scale behavior as visible and tracked contact counts increase; and
- whether map/geography rendering changes timing on the safety path.

The scored truth remains the deterministic simulator state. Live AIS remains an unscored shadow display.

**Gate R6:** the selected stack preserves the safety gate with AIS absent, stale, contradictory, or overloaded and the demo reproduces offline from hashes without a network connection.

## When to add another architecture

Add a sixth candidate only after R4 identifies a repeatable gap that cannot be repaired by calibration or implementation work. Predeclare the hypothesis and keep the same contracts and selection rule.

| Observed gap | Next experiment | Architecture change justified? |
|---|---|---|
| A3/A4/A5 miss deadlines but find useful recoveries | Profile, reduce allocations, precompute reachable controls, add an anytime cutoff, and retest identical cases. | Usually no; first fix execution. |
| A1/A2 intervene too often or too late | Recalibrate risk/margins and audit uncertainty semantics. | No, unless calibrated evidence still exposes a structural limit. |
| A4 fails because the barrier/plant model is mismatched | Identify dynamics, add robust disturbance sets, and compare model-matched barrier filtering against its current finite search. | Yes, as a replacement A4 version with new evidence, not an extra name for the old result. |
| A5 beats simpler candidates only when evidence quality changes | Confirm on held-out source-conflict and health-fault cases with matched runtime. | No; this is the A5 hypothesis. |
| Existing candidates cannot balance COLREG-style encounter intent, finite-horizon feasibility, and mission progress | Add one rule-aware robust MPC/reachability candidate with an explicit deterministic fallback and timeout behavior. | Yes; this is the strongest case for A6. |
| Neural health adds no held-out benefit | Retain conventional H1 monitoring and remove representation complexity from the safety claim. | No controller change. |

## Provisional architecture position

For the polished demo, use A5 because it can condition a predictive safety response on source health and conflict while still producing an explicit bounded action, and retain A1 as the simple recovery path if A5 is late, invalid, or outside its assumptions. This is a product-integration choice supported by current working-interface behavior, not a research selection. The current evidence records fewer candidate deadline misses for A5 than A3/A4 in the 60-episode development run, but every branch was censored and the suite was not held out.

The scientific winner remains **unselected** until R1-R4 pass. The near-term research value is in calibration, timing realism, domain coverage, adversarial source disagreement, and reproducible Singapore traffic scenes. Trying more architectures before those steps would make the study larger without making its conclusion stronger.
