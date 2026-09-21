# Maritime RTA Research

**Hackathon alignment:** SDTH 2026 Challenge 4, *One Picture, Many Eyes*  
**Working demonstrator:** HARBOR - runtime assurance for an uncrewed surface vessel  
**Compute allowance:** up to 10 NVIDIA L4 GPUs, with a total spend cap of USD 100  
**Primary research question:** Which runtime-assurance architecture turns disagreeing maritime observations into the earliest useful safety intervention, with acceptable false interventions, latency, and mission cost?

This plan complements [`runtime_assurance_uav_concept.md`](./runtime_assurance_uav_concept.md). That document defines the broader vessel, simulator, assurance boundary, and judge-facing experience. This plan defines the controlled architecture comparison and the GPU-limited perception-health experiment.

## 1. Challenge fit

Challenge 4 asks for a software demonstration that fuses sensors, open data, and sources not normally treated as sensors into a warning that somebody can act on. It highlights seven specific difficulties. HARBOR addresses each directly:

| Challenge requirement | HARBOR response | Demonstration evidence |
|---|---|---|
| Strategic and tactical warning | The research focuses on tactical vessel encounters; the interface also accepts slower-changing context such as route closures or abnormal traffic patterns | One timeline contains both slow context and second-by-second approach risk |
| Association without shared identifiers | Camera, radar, AIS, and peer messages are associated from geometry, motion, time, and uncertainty rather than identifier equality | An AIS identity mismatch does not remove a radar-supported contact |
| Temporal alignment | Every observation retains source time, receipt time, age, and validity interval | Inject camera delay, radar sweep latency, and reordered frames |
| Per-source uncertainty | Each source contributes an uncertainty set or calibrated empirical interval | The display shows which source supports each part of the fused track |
| Manipulable sources | AIS, peer intention, and the external autonomy proposal are treated as untrusted claims | A false intention message is contradicted by observed motion and contained |
| Labelled-data scarcity | Use pretrained WaSR/WaSR-T representations; train only small health models and fusion/calibration layers | No maritime foundation model is trained from scratch |
| Picture to tasking | The fused picture drives a deterministic pass, modify, or recover decision | The vessel executes the correction and the operator sees the reason |

The distinctive "sensor nobody normally treats as a sensor" is the **neural network's own internal representation**. It is used as a perception-health observation, not as proof that the scene is safe.

## 2. Scope and claim

### In scope

- One synthetic 12 m uncrewed surface vessel and several civilian contacts.
- Camera segmentation from WaSR-T, radar-like tracks, AIS-like reports, GNSS/IMU, network timing, and peer-intention messages.
- Camera and neural-representation health under glare, blur, occlusion, stale/reordered frames, preprocessing errors, and unfamiliar scenes.
- Collision, corridor, and shallow-water constraints in simulation.
- Five RTA architectures evaluated on the same plant, observations, faults, and autonomy proposals.
- A live or replayable browser demonstration with provenance from source observation to intervention.

### Out of scope

- Weapons, targeting, interception tactics, or engagement decisions.
- Claims of complete COLREG compliance, certification, or unrestricted collision avoidance.
- Training a large vision or multimodal foundation model.
- Treating AIS, a saliency heatmap, or an interpretability score as independent ground truth.
- Claiming that a recorded perception sequence is a closed-loop sea trial.

### Candidate research claim

> Under the declared maritime operating conditions and fault set, an assurance architecture that conditions deterministic safety margins on fused source quality and internal neural-representation health improves intervention lead time or reduces unsafe outcomes at a matched false-intervention and runtime budget.

If the representation signal adds no value beyond conventional quality and temporal checks, report that result. The hackathon system remains useful as a source-aware fusion and runtime-assurance demonstrator.

## 3. Common experimental platform

All five architectures use the same interfaces so differences can be attributed to the assurance method.

```text
ship sensors and actuator feedback -----------------------+
obstacle perception --------------------------------------+ 
onboard network and internal communications --------------+--> collectors
inter-ship communications --------------------------------+      + diagnostics
separate decision-AI telemetry ---------------------------+      + time alignment
neural-sensor internals ----------------------------------+      + association
                                                               + uncertainty/provenance
                                                                         |
                                                           compact synchronized record
                                                                         |
                                                                  fast RTA governor
                                                                         |
                                                           exclusive actuator gate
                                                                         |
                                                                 vessel simulation
```

### Fixed plant and interface

- A documented 3-DOF surface-vessel model with actuator saturation, lag, wind/current disturbance, and a swept hull.
- Fixed-step authoritative simulation; rendering never owns vessel state.
- Common `Observation`, `FusedTrack`, `ActuatorCapability`, `SourceHealth`, `PerceptionHealth`, `ProposedCommand`, `GovernorInput`, and `AssuranceDecision` schemas.
- Separate planner/autonomy and safety-supervisor processes.
- Evaluation truth inaccessible to all five supervisors.
- One final command owner; every modified command is revalidated after all constraints are applied.

### Authoritative input model

The RTA takes evidence from **the ship, its communications, and the separate decision-making AI**. Collection and diagnostic services organize that evidence into eight groups:

| Input group | What is collected | What the RTA checks |
|---|---|---|
| **1. Navigation and environmental sensors** | GNSS position, IMU, heading, speed, depth, and wind/current estimates | Where the ship is, how it is moving, and whether measurements agree |
| **2. Obstacle perception** | Radar tracks, camera frames, neural segmentation/detections, and AIS reports | Nearby obstacles, relative motion, collision risk, and uncertainty |
| **3. Ship and actuator feedback** | Actual rudder angle, propulsion/RPM, power, and equipment faults | Whether the ship can execute a correction and whether it actually does |
| **4. Onboard network traffic** | Available packet/message content, source/destination, sequence number, timestamps, delay, loss, and duplication | Whether information is stale, missing, malformed, duplicated, or inconsistent |
| **5. Internal ship communications** | Sensor-to-fusion messages, fusion-to-AI inputs, AI-to-controller commands, operator requests, acknowledgements, and heartbeats | What each onboard component actually received, consumed, and used |
| **6. Inter-ship communications** | Reported position, intended course/manoeuvre, coordination messages, sender information, and message age | Whether another vessel's claims agree with independently observed motion |
| **7. Separate decision-AI telemetry** | Proposed heading/speed/trajectory, input snapshot IDs, proposal expiry, inference timing, model version, and candidate scores when exposed | Whether the proposed action is safe, timely, and based on usable information |
| **8. Neural-sensor internals** | Selected-layer activations, temporal-buffer state, logits, feature-distribution changes, preprocessing version, and runtime errors | Whether the perception model appears degraded or outside calibrated conditions |

Inputs 4 and 5 are deliberately separate. Network capture can establish that a message traversed an observable interface, while application telemetry can establish that a component received or consumed it. Neither fact implies the other.

### Reference inputs

The safety analysis also uses versioned reference information:

- permitted operating area and geofences;
- chart/depth information and uncertainty;
- vessel dynamics and validated recovery envelopes;
- actuator limits, latency, and degraded capabilities;
- configured collision, under-keel, and corridor margins;
- the explicitly supported navigation-rule subset; and
- fallback policies, mode-transition guards, and human-handover conditions.

Reference configuration is identified in every decision record. A changed chart, model, limit, or policy creates a new configuration version rather than silently altering the meaning of previous evidence.

### Compact fast-governor record

Raw packets, frames, full activation tensors, and diagnostic traces do not enter the control-critical loop directly. Collectors and asynchronous diagnostic modules reduce them to a bounded, synchronized `GovernorInput`:

```text
Estimated ship state + uncertainty
Nearby contacts + uncertainty
Current actuator capability
Proposed action from the separate AI
Sensor / communication / perception-health flags
Operating constraints and recovery options
```

Every field carries source lineage, source/receipt time, age, validity interval, units/frame, configuration version, and an explicit unknown state. The governor rejects or degrades records that are incomplete, stale, internally inconsistent, or outside calibrated scope.

Expensive neural interpretation, raw-video processing beyond the deployed perception deadline, historical packet analysis, and offline attribution run outside the fast loop. Missing or late diagnostics invoke the declared `unknown` policy; they must never block command expiry or independent recovery.

Internal neural inspection is capability-dependent. If model/runtime access or a supported diagnostic interface is available, the collector may publish the bounded Group 8 summary. For an output-only appliance, hidden activations remain unavailable and `capability = output_only`; monitoring is limited to predictions, timing, temporal behavior, and cross-sensor consistency. The system must not infer or fabricate unavailable internal evidence.

## 4. Five candidate RTA architectures

These architectures form a progression rather than five unrelated products. Each one must consume the same proposed command and evidence snapshot.

### A1 - Rule-based late fusion with Simplex fallback

**Mechanism:** Associate sources, select the freshest/highest-priority track, apply fixed CPA/TCPA/geofence/depth thresholds, and switch to a fixed recovery controller when a threshold is crossed.

**Purpose:** Minimum credible baseline and easiest architecture to explain.

**Expected strength:** Deterministic, fast, inspectable.

**Expected weakness:** Brittle thresholds; little use of uncertainty; may intervene late or unnecessarily.

### A2 - Probabilistic track fusion with risk-triggered Simplex

**Mechanism:** Maintain probabilistic contact tracks, propagate covariance through a short prediction horizon, and trigger fallback when collision probability or constraint-risk exceeds a calibrated threshold.

**Purpose:** Test whether ordinary uncertainty-aware fusion is sufficient.

**Expected strength:** Natural treatment of noisy, asynchronous observations.

**Expected weakness:** Sensitive to model calibration and independence assumptions; a precise but wrong source may dominate.

### A3 - Bounded set fusion with predictive reachability

**Mechanism:** Represent ownship and contact uncertainty as conservative bounded sets. Intersect consistent evidence, retain unions or conflict flags when sources disagree, and reject a command when forward reachable tubes intersect an unsafe set or lose a checked recovery continuation.

**Purpose:** Provide the closest candidate to deterministic, assumption-scoped assurance.

**Expected strength:** Explicit guarantees under declared bounds and clear behavior under disagreement.

**Expected weakness:** Conservatism and computation grow with contacts and uncertainty.

### A4 - Control-barrier-function safety filter

**Mechanism:** Convert fused track uncertainty and vessel constraints into robust control-barrier constraints. Solve a small quadratic program that minimally changes requested heading/thrust; use a checked backup maneuver if the optimization is infeasible, late, or numerically invalid.

**Purpose:** Compare continuous command correction against hard Simplex switching.

**Expected strength:** Smooth, minimally invasive intervention with an established safety-filter structure.

**Expected weakness:** Guarantees depend on the dynamics and uncertainty model; multi-contact feasibility can fail.

### A5 - Evidence-conditioned hybrid RTA

**Mechanism:** Combine A3's predictive recoverability check with A4's minimally invasive filter. A dynamic evidence layer changes permissible camera use, uncertainty inflation, speed, and operating mode from source age, cross-sensor disagreement, network lineage, and neural representation health. An independent recovery controller remains available.

**Purpose:** Main research candidate and strongest Challenge 4 alignment.

**Expected strength:** Turns many disagreeing inputs into both a fused picture and an actionable, traceable intervention.

**Expected weakness:** Highest integration burden; must demonstrate that additional evidence improves decisions rather than merely decorating the interface.

## 5. Fair comparison strategy

Do not choose an architecture from one impressive scenario. Use a two-stage tournament.

### Stage 1 - Safety-controller comparison

Give A1-A5 identical pre-recorded fused evidence, vessel states, autonomy proposals, uncertainty inputs, and deadlines. Initially disable the experimental neural representation signal so the comparison isolates the RTA architecture.

Primary questions:

- Does it prevent a violation from recoverable starting states?
- How early does it act?
- How often does it intervene in benign scenes?
- What mission delay, excess path, and helm activity does it cause?
- Does it meet the declared real-time deadline under multi-contact load?
- Does it fail safe under infeasibility, timeout, NaN, or process loss?

### Stage 2 - Perception-health ablation

Take the best one or two Stage 1 architectures and compare five camera-health configurations:

| Health configuration | Evidence used |
|---|---|
| H0 | Output confidence/entropy only |
| H1 | H0 plus exposure, blur, occlusion, frozen-frame, timestamp, horizon, and temporal-output checks |
| H2 | H1 plus fixed-layer embedding distance or Mahalanobis score |
| H3 | H1 plus PCA/dictionary reconstruction and activation novelty |
| H4 | H1 plus sparse-autoencoder features and validated offline feature interventions |

H2-H4 may change uncertainty/mode only through a frozen, calibrated contract. They may not directly steer the vessel.

This staged design avoids changing the fusion method, safety controller, and interpretability method simultaneously.

## 6. Vision and representation experiment

### Models and data

- **Primary:** WaSR-T with an official pretrained checkpoint for temporal maritime obstacle segmentation.
- **Model baseline:** single-frame WaSR using a documented comparable variant.
- **Nominal/reference data:** MaSTr1325, with sequence-aware separation and no claim that training images are held-out evidence.
- **Main evaluation:** MODS sequences and official obstacle/water-edge/danger-zone metrics.
- **Additional stress data:** MODD2 sequences for glare, reflections, fog, abrupt motion, and small obstacles, after auditing overlap/provenance.
- **Optional display-only comparator:** a YOLO maritime detector; it must not replace the onboard-USV segmentation study.

### Layer instrumentation

Capture only a small fixed set of tensors:

1. one encoder feature map;
2. the temporal-fusion output;
3. decoder logits;
4. final segmentation and extracted obstacle regions.

For every inference, store model/weights/preprocessing hashes, input frame IDs, temporal-buffer age, tensor summaries, output, and latency. Verify that hooks do not change model output and measure their overhead.

### Mechanistic-interpretability boundary

- Embedding distance and reconstruction error are representation-health methods, not mechanistic explanations.
- A feature receives a semantic name only as a hypothesis derived from top-activating patches.
- A stronger causal statement requires activation masking or patching across matched examples, with random-feature and equal-magnitude controls.
- All activation intervention occurs offline on a diagnostic model copy.
- Runtime uses frozen summaries and thresholds only; the safety path does not depend on a visualization worker.

### Health-to-RTA contract

```text
healthy  -> camera may contribute inside its calibrated scope
degraded -> camera cannot clear conflicting occupancy; inflate bounds and reduce speed
invalid  -> reject camera free-space claims; use supported degraded mode or recovery
unknown  -> apply missing-health policy; never silently treat as healthy
```

The health state must include reasons, validity time, calibration version, evaluated scope, and whether obstacle-miss risk is calibrated or unknown.

## 7. Scenario matrix

Use paired seeds and identical initial conditions for every architecture.

| Family | Parameters to sweep | Key Challenge 4 issue |
|---|---|---|
| Crossing/head-on/overtaking | angle, range, relative speed, contact response | Association and tactical warning |
| Dense traffic | 2-12 contacts, crossing tracks, identity swaps | Association without shared identifiers |
| Sensor timing | frame delay/reorder/drop, radar sweep phase, clock offset | Temporal alignment |
| Source conflict | camera/radar/AIS disagreement, false precision, stale peer intent | Per-source uncertainty |
| Manipulation/fault | AIS spoof-like claim, replayed message, stale AI input, preprocessing mismatch | Adversarial/manipulable sources |
| Visual degradation | glare, wake, fog, blur, occlusion, lens contamination | Neural perception health |
| Vessel degradation | current, slow/stuck rudder, thrust reduction | Recovery feasibility |
| Boundary/depth | corridor exit, shallow route, chart uncertainty | Multiple simultaneous constraints |
| Initially unrecoverable | hazard already inside viable stopping/turning region | Honest limitation reporting |

Minimum evaluation set:

- 100 cases per major encounter family during development.
- At least 1,000 frozen held-out episodes across the declared operating domain for final comparison.
- At least 30 paired runs for every headline scenario/architecture cell where stochastic sensing or traffic is present.
- Separate preventable, initially unrecoverable, and out-of-domain cases.

## 8. Metrics and selection rule

### Primary safety metrics

- Collision, grounding, or boundary violation count from independent simulator truth.
- Minimum signed hull/obstacle and under-keel margins.
- Fraction of declared recoverable episodes that remain inside the safe set.
- Intervention lead time relative to the last validated recovery opportunity.
- Unsafe or stale commands accepted by the actuator gate.

### Operational metrics

- False interventions on benign episodes.
- Mission completion, route delay, extra distance, and recovery duration.
- Authority switches, helm reversals, and control discontinuity.
- Time spent in healthy, degraded, invalid, and unknown modes.

### Perception-health metrics

- Missed-obstacle episode detection rate and false-alarm rate.
- Alarm lead time before the associated unsafe autonomy proposal.
- Calibration error and performance by weather/fault/obstacle-size stratum.
- Incremental value of H2-H4 over H1 at matched false-alarm rates.
- Instrumentation latency, GPU memory, logging bandwidth, and dropped telemetry.

### Architecture choice

Apply a safety gate before ranking:

1. Reject an architecture that permits a preventable violation in the frozen acceptance suite, accepts stale/unauthorized commands, or lacks defined timeout/infeasibility behavior.
2. Among survivors, reject those that miss the control-cycle deadline at the declared load.
3. Plot mission cost versus false interventions and safety margin; do not collapse everything into one opaque score.
4. Select the simplest architecture on the Pareto frontier unless a more complex candidate shows a material, repeatable improvement in lead time, safety coverage, or mission usefulness.
5. Adopt H4 only if it outperforms H1-H3 on held-out faults after its compute and false-alarm costs are included.

The expected implementation choice is A5 for the showcase, with A1 and the strongest of A3/A4 retained as visible baselines. The experiment, not the plan, decides whether A5 remains the research recommendation.

## 9. USD 100 / 10-L4 compute plan

### Budget rule

The USD 100 limit is authoritative. Provider prices vary, so calculate available device-hours immediately before launch:

```text
usable_budget = 0.80 * 100 USD
reserved_budget = 0.20 * 100 USD
available_L4_hours = usable_budget / actual_all_in_L4_hourly_price
wall_clock_hours_at_10_GPUs = available_L4_hours / 10
```

The 20% reserve covers failed jobs, storage/egress surprises, and the final demo rehearsal. Configure a provider-side hard spend limit and automatic instance shutdown. Never leave ten instances allocated while downloading data or debugging dependencies.

### Allocation by work package

| Share | Work package | Parallel use |
|---:|---|---|
| 10% | Environment/checkpoint reproduction and small benchmark smoke test | 1-2 GPUs; stop after correctness is established |
| 25% | WaSR/WaSR-T inference and corruption/fault sweeps | Shard sequences over up to 10 GPUs |
| 20% | Fixed-layer feature extraction and cached summaries | Shard by sequence; never repeat extraction for every health model |
| 20% | PCA/dictionary/SAE capacity and seed sweep | Multiple small independent jobs |
| 10% | Offline activation masking/patching controls | Shard target feature/scene pairs |
| 10% | Frozen held-out evaluation and calibration reports | One immutable evaluation manifest |
| 5% | Contingency inside the usable 80%; the separate 20% budget reserve remains untouched | Failed/preempted jobs only |

Control simulation, reachability sweeps, CBF/RMPC comparisons, report generation, and most calibration should run on CPU unless profiling proves a GPU benefit.

### Ten-GPU launch layout

When all ten L4s are useful simultaneously:

| GPU | Job |
|---:|---|
| 0 | WaSR single-frame baseline inference |
| 1-3 | WaSR-T nominal and visual-corruption inference shards |
| 4-5 | Timing faults: dropped/repeated/reordered/delayed-frame shards |
| 6 | Encoder/temporal/decoder feature extraction |
| 7 | PCA/dictionary/Mahalanobis sweeps |
| 8 | Sparse-autoencoder seed/capacity sweeps |
| 9 | Activation-intervention controls or held-out evaluation |

Use immutable input manifests, per-job cost/time limits, periodic checkpoints, and immediate shutdown on completion. Cache extracted features in reduced precision where validated; do not rerun WaSR-T merely because a health-model hyperparameter changes.

### Compute stop gates

- Do not launch the full sweep until one sequence runs end-to-end and reproduces expected output conventions.
- Stop an SAE configuration early if it is dominated by PCA/distance baselines on development data.
- Stop interpretability scaling if causal controls are unstable across seeds/scenes.
- Preserve at least USD 20 until the complete demonstration pipeline has run once.
- Final held-out data may be evaluated only after architectures, thresholds, and selection criteria are frozen.

## 10. Build sequence

### Phase 0 - Freeze the experiment

- Create source/dataset/checkpoint provenance records and verify terms.
- Define operating domain, recoverability assumptions, common message schemas, metrics, split policy, and the frozen final test manifest.
- Implement budget logging and deterministic seed/config capture.

**Exit:** one documented scenario replays identically and one WaSR-T example runs with recorded provenance.

### Phase 1 - Build the common evidence pipeline

- Implement adapters for all eight input groups, with explicit `unavailable` capability states where an interface is not exposed.
- Implement timestamp alignment, association, uncertainty, conflict retention, and source-to-consumer provenance.
- Build navigation, radar/AIS/camera, actuator, network, internal-message, peer-message, decision-AI, and neural-diagnostic fault injection.
- Produce the compact `GovernorInput` independently of the raw-data archive and diagnostic UI.
- Expose the fused picture without yet claiming that it is safe.

**Exit:** every displayed track can identify supporting and contradicting observations; every governor field has lineage and freshness; failure or overload of a raw collector/diagnostic worker follows a tested degraded or unknown policy.

### Phase 2 - Implement A1-A4

- A1 threshold/Simplex first.
- A2 probabilistic track-risk trigger.
- A3 bounded reachable-set/predictive recovery check.
- A4 robust CBF-QP filter with explicit infeasibility and timeout fallback.

**Exit:** all candidates use the same command interface and pass authority/expiry tests.

### Phase 3 - Implement A5

- Combine predictive recoverability, minimally invasive correction, health-conditioned bounds, and independent recovery.
- Add an auditable reason record for every pass/modify/recover decision.

**Exit:** failure of the neural diagnostic worker cannot stop or delay deterministic recovery.

### Phase 4 - Run Stage 1

- Execute paired architecture comparisons on development scenarios.
- Fix defects, then freeze the acceptance suite and selection rule.
- Select the one or two architectures used in Stage 2.

**Exit:** architecture choice is supported by safety, runtime, and mission-cost evidence.

### Phase 5 - Run the GPU perception study

- Reproduce WaSR/WaSR-T.
- Extract and cache features.
- Train/evaluate H0-H4 on separated development/calibration/test partitions.
- Perform offline causal controls for any named internal feature.
- Freeze the health-to-RTA contract.

**Exit:** incremental value over conventional health monitoring is measured, not assumed.

### Phase 6 - Integrate and rehearse

- Connect recorded perception faults to the selected RTA without presenting replay as live closed-loop perception.
- Produce deterministic replay, counterfactual branch, and evidence report.
- Run one final cost-capped held-out evaluation.

**Exit:** the complete judge demo runs locally without cloud access; all headline numbers regenerate from saved manifests and logs.

## 11. Judge-facing demonstration

Target a concise five-part story:

1. **Many eyes:** Show camera segmentation, radar, AIS, peer intent, network timing, and neural health disagreeing about one approaching contact.
2. **One picture:** Time-align and associate them while preserving uncertainty and provenance. Do not hide the disagreement.
3. **Unsafe proposal:** The separate autonomy controller proposes a path that is validly formatted but unsafe under the fused evidence.
4. **Action:** The selected RTA modifies the command or invokes recovery before the last validated escape opportunity.
5. **Why:** Replay the evidence chain and compare the protected branch with A1 or no-RTA under identical initial conditions.

The key screen should show:

- top-down maritime scene with requested, accepted, and counterfactual trajectories;
- source cards with age, uncertainty, agreement, and provenance;
- camera frame, WaSR-T mask, and bounded neural-health state;
- current authority and exact binding constraint;
- measurable outcome: clearance, lead time, mission delay, and runtime.

Include one honest limitation case where the vessel begins outside the recoverable set. The system should report that it cannot validate recovery and take a declared minimum-risk action rather than displaying a false green status.

## 12. Deliverables

- Five interchangeable RTA implementations behind one interface.
- Eight input-group adapters and one versioned, bounded `GovernorInput` contract.
- Reproducible scenario/fault manifests and paired-seed evaluation runner.
- WaSR/WaSR-T reproduction manifest, cached feature dataset, and H0-H4 monitor implementations.
- Frozen calibration and held-out evaluation reports.
- Source-to-decision provenance log and dynamic assurance record.
- Interactive HARBOR console plus offline recorded fallback.
- One-page architecture-selection summary with Pareto plots.
- Claim ledger distinguishing simulation evidence, empirical perception evidence, model-scoped proofs, assumptions, and future validation.

## 13. Minimum viable path if time becomes tight

Preserve the research logic by reducing breadth in this order:

1. Build A1, A3, and A5 fully; implement A2/A4 as headless comparators.
2. Demonstrate one crossing scenario plus timing, AIS-conflict, and camera-fault variants.
3. Complete H0, H1, H2, and one small H4 configuration; treat H3 as optional.
4. Use recorded WaSR-T perception faults coupled to a closed-loop vessel simulation and label the boundary clearly.
5. Keep the source provenance, actuator gate, paired counterfactual, and held-out comparison. These are central to Challenge 4 and must not be traded for visual polish.

## 14. Immediate next actions

1. Freeze the common schemas and one crossing-encounter scenario.
2. Reproduce a short WaSR-T sequence and measure inference plus instrumentation cost on one L4.
3. Implement A1 and A3 against the same replay log.
4. Estimate actual L4 device-hours from the chosen provider and generate job-level dollar limits.
5. Complete one vertical slice: frame and radar observation -> fused evidence -> unsafe proposal -> RTA correction -> executed vessel motion -> replayable explanation.
