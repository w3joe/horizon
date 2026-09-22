# Horizon — Multi-agent implementation plan

> **Historical execution plan.** The repository, demo, algorithms, experiment harness,
> maritime assets, marine model, and recorded-perception packets now exist. Current
> status and remaining evidence are tracked in the repository README,
> `docs/architecture/remaining-work.md`, and `experiment/reports/claim-ledger.md`.

**Status:** Approved historical plan from 2026-09-21; automatic integration of accepted agent work was authorized.
**Repository:** `horizon`, private on GitHub.  
**Local clone:** `/Users/w3joe/Desktop/2026_sdth/horizon`  
**All subagents:** **Sol / high** — `model: gpt-5.6-sol`, `reasoning_effort: high`.  
**Team:** One coordinating parent, eight core subagent assignments, and two later 3D-realism assignments, scheduled within available concurrency.  
**Outcome:** A working maritime runtime-assurance demo and reproducible implementation of the current research plan.

This document is the original review artifact and preserves its planned sequencing and targets. It is not current implementation evidence.

## 1. Source of truth and intended result

Read and implement these existing documents:

1. [Maritime RTA research plan](maritime_rta_research.md): the experimental specification, A1–A5 architecture comparison, H0–H4 perception-health comparison, metrics, and compute limits.
2. [Runtime assurance concept](runtime_assurance_uav_concept.md): operational scope, independence, input contracts, intervention behavior, scenarios, visual design, and assurance limitations.
3. [Dataset and repository survey](maritime_data_survey.md): acquisition options, licensing limitations, and data gaps.
4. [Existing sample provenance](starter_sample_provenance.json): two small inspected starter files.

Use **Horizon** as the new repository and product name. Preserve HARBOR-labelled source documents as historical design inputs; do not silently rewrite their research assumptions when copying them into the repository.

Implement the full agreed scope without an artificial time deadline. Deliver incrementally, with each milestone runnable. Do not quietly drop A2/A4 or H3 because an earlier demo already looks good.

The final system must demonstrate:

- All eight input groups, with explicit recorded/synthetic/unavailable provenance.
- A separate decision-making AI interface and replaceable test-double service.
- Five interchangeable RTA implementations, A1–A5, with one exclusive actuator gate.
- An instrumented WaSR-T perception service, WaSR baseline, and H0–H4 health methods.
- Independent recovery when the AI, supervisor, diagnostics, or communications fail.
- A compelling browser console with navigation, data flow, neural inspection, and replay/comparison.
- Research comparisons, calibrated thresholds, reproducible reports, and an honest claim ledger.

No weapons, targeting, or engagement functionality is part of this implementation. Sensor perception and the external decision AI remain distinct systems.

## 2. Execution model and concurrency

Each subagent is launched with the explicit configuration below. Do not inherit another model through a full-history fork:

```json
{
  "model": "gpt-5.6-sol",
  "reasoning_effort": "high",
  "fork_turns": "none"
}
```

The coordinator supplies the task brief, worktree path, base commit, owned paths, contracts, input documents, dependencies, and acceptance criteria in the launch message. If Sol/high is unavailable, report that limitation instead of substituting another model silently.

The present session supports **four active agents total: coordinator plus three subagents**. The ten assignments therefore run in overlapping waves, not ten simultaneous processes. A09/A10 begin in the later realism phase, after the working RTA demo. Keep research active alongside two demo workstreams when it has useful independent work. When an agent is waiting on a dependency, finish its current handoff and release its active slot; resume the assignment when the dependency is available.

The coordinator schedules tasks, resolves design disagreements, integrates branches, and communicates progress. The coordinator is not counted as an implementation subagent.

## 3. Team and exclusive ownership

Every row uses **Sol / high**. Ownership includes component-local tests and documentation unless another path is listed explicitly.

| Agent | Assignment | Primary owned paths | Main deliverable |
|---|---|---|---|
| **A01** | Repository, contracts, platform | Root manifests/lockfiles, `AGENTS.md`, `.github/`, `packages/contracts/`, `scripts/`, `infra/`, `docs/architecture/`, source-document copies | Private repo, cloned scaffold, shared schemas, launch/CI tooling |
| **A02** | Research implementation and experiments | `experiment/` | Executable research protocol, paired evaluations, calibration/selection reports, claim ledger, all inside the same repository |
| **A03** | Vessel simulation and external-AI interface | `services/simulator/`, `adapters/decision-ai/`, `fixtures/decision-ai/`, `scenarios/` | Authoritative dynamics, traffic/sensors/faults, separate AI test doubles |
| **A04** | RTA controllers and actuator authority | `services/assurance/`, `services/gate/`, `tests/assurance/` | A1–A5, recovery, command validation/expiry, watchdog and handover |
| **A05** | Data ingestion, communications and fusion | `services/collector/`, `services/fusion/`, `adapters/maritime/`, `data/manifests/`, `tests/ingestion/` | Eight-group ingestion, time alignment, association, source health/provenance |
| **A06** | Demo console and initial visual simulation | `apps/console/`, `assets/core/`, `docs/demo/` | Functional interactive 3D console, evidence views, synchronized comparison; later integration of A09/A10 outputs |
| **A07** | Neural perception and model inspection | `services/perception/`, `services/neural-health/`, `configs/perception/`, `tests/perception/`, `docs/perception/` | WaSR/WaSR-T, layer telemetry, H0–H4, offline causal diagnostics |
| **A08** | Independent integration verification | `tests/e2e/`, `tests/system/`, `docs/verification/` | Adversarial failure tests, end-to-end verification, release evidence |
| **A09 — later** | Realistic naval/maritime assets and environment art | `assets/maritime/`, `tools/assets/`, `docs/realism/assets/` | Licensed online vessel/warship models, realistic harbor assets, textures, optimized scene assets |
| **A10 — later** | Ocean physics and vessel-motion realism | `packages/marine-environment/`, `configs/sea-state/`, `tests/marine-environment/`, `docs/realism/physics/` | Ocean rendering package, physically driven marine motion/forces, integration contract and fidelity checks |

Only A01 edits shared schemas, root dependency manifests, lockfiles, and CI definitions. Other agents request dependency/interface changes with a concrete patch or specification; A01 applies them serially. A03 owns scenario files; A02/A08 submit scenario requirements rather than independently editing them. A06 may add UI-local presentation fixtures, but they must use the shared schema and remain visibly separate from live data.

The coordinator owns integration operations on `main`. A01 owns platform code, not unilateral merges of another agent's work. Do not spawn nested agents unless the coordinator explicitly allocates a slot and gives them the same Sol/high requirement.

## 4. A01 must run first: private repository and bootstrap

### Scope

1. Read applicable `AGENTS.md` instructions and inspect Git/GitHub CLI authentication without printing credentials.
2. Resolve the currently authenticated personal GitHub account as the default owner. Do not select an organization without user direction.
3. Check whether `OWNER/horizon` or the local target directory already exists. Reuse only when confirmed to be the intended project; do not overwrite, delete, or repurpose unrelated work.
4. **Create `horizon` online as private, then clone it into the requested directory.**
5. Verify the remote visibility, repository URL, clone path, `origin`, and default branch. Normalize the new project to `main` if required and record the result.
6. Create the minimal scaffold, root `AGENTS.md`, schemas, examples, process boundaries, dependency setup, CI smoke checks, and source-document copies.
7. Commit and push the bootstrap baseline. Return its commit ID to the coordinator before other implementation branches begin.

Illustrative commands, executed only after the preflight checks during the approved implementation:

```sh
gh auth status
horizon_owner=$(gh api user --jq .login)
gh repo view "$horizon_owner/horizon" --json nameWithOwner,visibility,url
```

A failed lookup is not automatically proof that the repository is absent; distinguish absence from authentication, permission, and network errors. If creation is appropriate:

```sh
gh repo create "$horizon_owner/horizon" --private --add-readme --description "Maritime runtime assurance research and simulation"
gh repo clone "$horizon_owner/horizon" /Users/w3joe/Desktop/2026_sdth/horizon
gh repo view "$horizon_owner/horizon" --json nameWithOwner,visibility,url
```

These workflows follow the official [GitHub repository creation](https://cli.github.com/manual/gh_repo_create) and [clone](https://cli.github.com/manual/gh_repo_clone) interfaces. Reconfirm installed command options at execution time if needed.

### Bootstrap contents

- Python service workspace and React/TypeScript console workspace, with pinned compatible dependencies.
- JSON Schema as the language-neutral contract source; generated/validated Python and TypeScript interfaces and sample messages.
- A CPU-only fixture path that runs without cloud credentials, large datasets, or GPU weights.
- A process launcher with explicit ports, health checks, shutdown behavior, and run IDs.
- `.gitignore` covering secrets, local environments, datasets, weights, large feature caches, and generated run output.
- A dataset/artifact manifest convention with URL, hash, rights, version, and recorded/synthetic provenance.
- Copies of only the relevant plan/survey files under `docs/source/`, preserving hashes and fixing relative links. Do not add the entire parent Desktop folder.
- `AGENTS.md` containing model requirements, ownership, explicit worktree paths, shared-file protocol, test commands, and safety/data boundaries.

Do not automatically assign a permissive project license or republish third-party datasets. Keep the repository private; no public deployment, GitHub Pages, or public model/data release is included.

### Acceptance and handoff

Remote visibility is verified **PRIVATE**; clone exists at the exact requested path; `origin` points to the intended repository; bootstrap smoke checks pass; the initial commit is pushed; contract fixtures validate in both languages. No large research download or GPU launch is needed to unblock the team.

## 5. A02: research agent with executable ownership

**Mission:** Implement [the current maritime research plan](maritime_rta_research.md), not merely write another literature review. Translate its hypotheses into runnable experiments and make the architecture recommendation depend on evidence.

**Location is fixed:** the harness lives in **`horizon/experiment/`**, in the same private GitHub repository as the demo. Do not create a separate experiment repository or competing top-level `research/`, `experiments/`, and `evaluation/` trees. Production controllers/perception services remain reusable modules outside this folder; the harness imports their public interfaces rather than duplicating implementations.

```text
horizon/
  experiment/
    README.md                 # reproduce the research study
    protocol/                 # hypotheses, comparisons, frozen selection rules
    configs/                  # architecture/health matrices and sweep configuration
    manifests/                # seeds, splits, data/model/configuration hashes
    harness/                  # orchestration, paired branches, resume/checkpoints
    evaluation/               # independent scoring, calibration, statistics
    jobs/                     # CPU/GPU experiment job specifications
    tests/                    # harness and metric correctness
    reports/                  # compact reviewed results and claim ledger
    notebooks/                # optional exploration; not the sole reproduction path
```

The repository tracks harness code and compact reproducibility artifacts. Large datasets, activation caches, and raw runs stay in the external data/run locations below and are referenced by manifests. One documented repository-root command must run a small `experiment/` smoke comparison.

### Work packages

1. Create a requirements/claim matrix mapping research-plan sections to code, scenarios, metrics, and evidence.
2. Implement the experiment runner, seed manifests, development/calibration/held-out partitions, artifact hashes, results schema, CPU execution, resume/checkpoint handling, and report generation.
3. Specify A1–A5 behavior and A04's comparison API; specify H0–H4 and A07's health API. Record assumptions and numerical/uncertainty distinctions in decision records.
4. Implement independent outcome scoring from simulator truth, intervention timing, false interventions, mission cost, deadline statistics, and architecture-selection/Pareto reporting.
5. Build and run Stage 1 architecture comparisons when A04's candidates arrive, and Stage 2 health ablations when A07's methods arrive.
6. Implement calibration procedures and sensitivity/false-alarm comparisons without allowing held-out results to tune thresholds.
7. Maintain dataset overlap checks, literature-to-method traceability, and the claim ledger distinguishing empirical results, model assumptions, formal arguments, and unimplemented work.
8. Produce a concise architecture recommendation, including negative results when A5 or H4 adds no value.

### Experimental design corrections to resolve explicitly

The source plan compares architectures with different fusion behavior but also requests identical fused inputs. Implement two separately labeled experiments:

- **Controller isolation:** provide one frozen evidence snapshot containing the declared point, covariance, and bounded-set representations to each governor. Hold fusion and health policy fixed; compare controller behavior and runtime.
- **Full pipeline comparison:** provide identical raw observation/fault tapes to each fusion/controller configuration, then measure the resulting differences. Do not attribute a fusion change solely to the controller.

Also distinguish replay from closed loop. Identical recorded states/proposals support response/runtime comparisons. Collision prevention and mission cost require independent closed-loop branches with the same initial state, external-AI policy, seeds, and exogenous fault schedule. Once commands differ, vessel states and subsequent observations must be allowed to diverge.

Do not silently turn probabilistic covariance into a guaranteed hard bound. Each representation needs its stated confidence/coverage or bounded-error assumptions.

### Acceptance

- A small fixture experiment runs immediately against a stub governor, then unchanged against real A1–A5 implementations.
- All five candidates and H0–H4 have explicit manifest entries; missing implementations fail visibly rather than being aliased to another method.
- Research-plan coverage includes development encounter sweeps, at least 1,000 final held-out episodes, and 30 paired runs per stochastic headline comparison cell as specified.
- Frozen data/configuration/model manifests reproduce the headline metrics.
- Research results do not depend on UI-generated status flags.

**Dependencies:** Bootstrap contracts first. Protocol, harness, literature checks, and scoring can proceed while the simulator, controllers, and UI are being built. Research implementation remains active through integration.

## 6. A03: authoritative vessel simulation and separate AI

### Scope

- Implement the documented horizontal vessel dynamics, hull geometry, actuator lag/saturation, disturbance bounds, and numerical integration.
- Characterize stopping, acceleration, turning, and degraded actuator behavior; export versioned capability assumptions.
- Implement synthetic harbor/traffic, depth/corridor geometry, sensor noise/rates/delays, and scenario/fault scheduling.
- Maintain separate ports/APIs for noisy observations and evaluation-only truth. Safety/AI services cannot subscribe to truth or hidden fault labels.
- Implement a separate nominal/faulty decision-AI test-double process and adapter contract. Its proposal includes input lineage, issue/expiry times, and the requested heading/speed/trajectory.
- Implement cloned protected/unprotected branches, replay snapshots, and deterministic restart/reset including all random/model state.
- Implement the plant-facing actuator endpoint so only the designated gate writes protected-run commands.

### Deliverables and acceptance

A headless run and a browser snapshot stream share one authoritative simulation state. Commands visibly change simulated actuators and motion. Coordinate/rudder-sign/wraparound tests pass; stopping/turning envelopes are recorded; collision checks include swept hulls between integration steps. Two AI test doubles can be swapped without editing RTA code. Kill/restart/reset tests preserve correct authority and scenario state.

**Boundary:** A03 does not write governor decisions, fusion logic, frontend scenes, or outcome-selection rules. Its truth scoring interfaces support A02/A08, but truth never enters the protected control path.

## 7. A04: A1–A5, recovery, and the actuator gate

### Scope

Implement the common `evaluate(GovernorInput) -> AssuranceDecision` boundary and five distinct candidates:

| Candidate | Required implementation |
|---|---|
| A1 | Deterministic threshold monitor with Simplex-style recovery switching |
| A2 | Calibrated probabilistic-risk trigger using uncertainty-aware tracks |
| A3 | Bounded prediction/reachable-envelope checks with a validated recovery continuation |
| A4 | A model-appropriate robust CBF filter, with numerical checks and explicit infeasibility/timeout behavior |
| A5 | Evidence-conditioned hybrid integrating predictive recoverability and minimally disruptive correction, with final-command revalidation |

A sampled rollout may establish a useful engineering check, but must not be described as exact reachability or a formal guarantee. For A4 derive the appropriate relative-degree/tracking formulation and document whether the optimization is actually a convex QP. Do not apply a point-robot formula to rudder dynamics.

Implement command validation, exclusive authority, sequence/expiry handling, independent recovery, supervisor watchdog, quarantines, hysteresis, operator handshake, and degraded-capability response. Keep gate/recovery independently runnable when the main supervisor exits. Stop/loiter is a candidate maneuver only when its continuation is checked.

### Acceptance

All candidates consume the frozen contracts and record their method/version. Malformed, stale, unauthorized, NaN, infeasible, or late output cannot silently reach actuation. Every correction has a gate receipt and actual response trace. Planner/supervisor kill tests invoke the expected recovery or explicit loss-of-assurance state. Multi-constraint validation applies to the final command after all modifications.

**Dependencies:** Begin from A01 fixtures and A02 requirements; use A03 dynamics/capability interface when available. A05 supplies fused evidence, but its full implementation is not needed to start.

## 8. A05: sensors, network, internal and inter-ship communications

### Scope

- Implement adapters for the eight input groups from the research plan, with capability-declared unavailable inputs rather than fabricated telemetry.
- Start with the inspected NMEA log and synthetic application/inter-ship messages; add MARSIM PCAP replay and selected real multisensor data through manifests.
- Preserve raw references while normalizing units, coordinate frames, source/receipt/consumption times, sequence numbers, and validity.
- Separate packet observation, application receipt, and actual AI consumption; track capture loss separately from source loss.
- Implement ownship estimation, contact association without shared IDs, common-ancestry tracking, and disagreement retention.
- Export point/covariance/bounded evidence through common contracts for A04 and A02; make unsupported bounds explicit.
- Maintain claimed peer intent separately from independently observed contact motion.
- Bound queues and parsing costs; report collector failure and diagnostic incompleteness without blocking the gate.

### Acceptance

All eight groups have adapters or an explicit capability state. Parsed NMEA checksums and units are tested. Delay/reorder/replay/conflict fixtures preserve end-to-end lineage. False AIS/peer identity cannot delete a radar-supported contact. Fusion does not double-count a sensor and its derived gateway message as independent evidence. Network delay claims include clock uncertainty. No passive capture claims access to encrypted contents or hidden activations.

**Boundary:** Own fusion and data health, not neural internals, governor thresholds, or packet-level operations on an actual vessel. All initial network fault injection stays in local replay/simulation.

## 9. A06: visually compelling demo

### Scope

Build a browser console using the source concept as a visual reference, with an actual telemetry-backed scene:

- An initial 3D harbor, distinct vessel silhouettes, readable ocean/wakes, tactical and oblique cameras. This establishes the operational interface; A09/A10 own the later realistic vessel assets and water-physics upgrade in Section 18.
- Proposed, accepted, and counterfactual trajectories; selectable hull/uncertainty/corridor/depth overlays.
- Navigation, data-flow, and neural-inspection workspaces sharing an event/inference selection.
- Source age, uncertainty, provenance, disagreement, control authority, and the actual binding constraint.
- Camera frame, segmentation, selected layer summaries and health status where available.
- Fault injection, pause/reset/playback, paired branch comparison, and an event timeline.
- A labeled recorded fallback for judging, with assets available offline.

Begin against schema-valid fixture streams immediately after bootstrap. Label fixtures as illustrative. Replace them with real service streams at integration; never hardcode a successful intervention, activation pattern, or safety metric into live mode.

### Acceptance

The main scene uses backend state; frame rate does not affect physics. Viewers can identify the hazard, issued correction, and authority without technical narration. At least 30 fps is sustained on declared demo hardware while control timing remains independent. Responsive layout, keyboard controls, stale/disconnected state, and reset/replay behavior are checked in a browser. Claimed/predicted/observed outcomes are visually distinct.

**Boundary:** Own presentation and licensed assets only. Do not recreate physics, a governor, or a second source of truth inside the frontend. Follow applicable frontend/site skills when implementation begins, without adding a public deployment requirement.

## 10. A07: WaSR-T and perception-health research implementation

### Scope

1. Reproduce one pinned WaSR-T example and single-frame WaSR baseline; record preprocessing, weights, runtime, sensor capability, and latency.
2. Instrument selected encoder, temporal-fusion, and decoder layers with bounded summaries. Preserve frame/context lineage and reset temporal state correctly.
3. Implement H0 confidence/entropy; H1 conventional quality/temporal checks; H2 embedding-distance/Mahalanobis; H3 PCA/dictionary reconstruction; H4 a small SAE plus validated offline feature experiments.
4. Provide cached feature extraction and training jobs compatible with A02's experiment manifests. Reuse features rather than repeating costly inference for each small health model.
5. Implement calibrated `PerceptionHealth` with healthy/degraded/invalid/unknown, validity, reference/calibration version, supported scope, and unknown-risk handling.
6. Perform feature masking/patching only on offline diagnostic copies, with matched/random/equal-magnitude controls.
7. Supply raw-frame/segmentation/diagnostic references to A06 and bounded health records to A05/A04.

### Acceptance

Instrumented and uninstrumented inference agree within declared tolerance; overhead is measured. Frame delay/reorder/buffer-reset tests pass. Black-box appliances expose no invented internals. Health thresholds are fitted and frozen on appropriate partitions through A02's calibration procedure. H4's claimed improvement is measured against H1–H3, not presumed.

Metric obstacle uncertainty must come from calibration/geometry/range evidence, not a direct conversion of feature novelty into metres. Recorded-video replay and pose-reactive rendered-camera runs are labeled separately. Model/data terms and training/test overlap are checked before making benchmark claims.

**Dependencies:** Bootstrap contracts and experiment specification. Model reproduction can proceed independently of the vessel UI; full ablation results depend on A02's runner and approved compute allocation.

## 11. A08: independent verification and demonstration readiness

### Scope

- Exercise the integrated system from launch to actual actuation and replay using system-level tests.
- Test every source-plan scenario S01–S22, with coverage mapped to the research scenario families.
- Kill/restart planner, supervisor, diagnostics, and collectors; inject expiry, invalid values, duplicate sequences, queue overload, and lost communication.
- Verify that evaluation truth and fault labels cannot leak into online AI/safety inputs.
- Validate pairing/reset/provenance and independently spot-check A02's metrics against known analytic or synthetic cases.
- Verify the local offline launch, dataset/model missing-state behavior, UI end-to-end controls, and recorded-fallback labeling.
- Produce a defect report and release checklist tied to the tested commit and artifacts.

### Acceptance

Safety-critical integration defects are resolved and rechecked. A clean environment can reproduce the documented CPU fixture demo. The neural path runs when its declared artifacts are present and reports unavailable when absent. The release evidence contains exact commands, environment/hardware, passing/failing tests, limitations, and reproduction manifests.

A08 reports defects to the owning agent and adds reproductions in its test paths. It does not rewrite another agent's component or fix a mismatch by weakening expected outcomes. Visual failures are fixed by A06; safety/authority failures by A04; simulator faults by A03.

## 12. Shared interfaces: the prerequisite for parallel work

A01 creates contract version 0 before the demo streams branch. Domain agents can propose revisions, but only the contract owner changes schemas/generated types.

| Contract | Producer → consumer | Essential properties |
|---|---|---|
| `Observation` / `NetworkObservation` | Simulator/collectors → fusion/diagnostics | Provenance, event/receipt time, clock uncertainty, units/frame, age/validity, capability |
| `FusedTrack` / `EvidenceBundle` | Fusion → governors/research/UI | Supporting/contradicting observations, point/covariance/set semantics, assumptions |
| `ActuatorCapability` | Plant characterization/feedback → governor/gate | Limits, lag, degraded availability, model version |
| `AIInferenceTrace` / `ProposedCommand` | Separate AI → governor/diagnostics | Consumed inputs, action/trajectory, source/sequence, issue/expiry |
| `SourceHealth` / `PerceptionHealth` | Diagnostics → fusion/governor/UI | Bounded states, reasons, calibration/scope, unknown handling |
| `GovernorInput` | Evidence assembler → A1–A5 | Compact synchronized state/contact/capability/proposal/constraint record |
| `AssuranceDecision` / `GateReceipt` | Governor/gate → plant/evidence/UI | Proposed/issued actions, method, authority, reasons, validation, actual actuation |
| `SimulationSnapshot` / `EvaluationRecord` | Simulator → UI / evaluation-only scorer | Public display state separated from privileged truth |
| `RunManifest` / `ArtifactManifest` | Platform/research → all | Versions, seeds, branches, data hashes, split, costs, provenance |

Each service must have a schema-valid fixture before another agent depends on its running process. Use monotonically ordered local deadlines and explicitly mapped simulation time; display UTC only where useful for audit.

## 13. Worktrees, branches, and integration rules

Keep the reviewed repository in:

```text
/Users/w3joe/Desktop/2026_sdth/
  horizon/                      # main; coordinator integrates here
  horizon-worktrees/
    a01-platform/
    a02-research/
    a03-simulator/
    a04-assurance/
    a05-ingestion/
    a06-console/
    a07-perception/
    a08-verification/
    a09-maritime-assets/         # later realism phase
    a10-marine-environment/      # later realism phase
  horizon-data/                 # ignored external datasets/weights/caches
  horizon-runs/                 # generated run artifacts, outside Git
```

Use a branch per assignment: `agent/a01-platform`, `agent/a02-research`, and so on. Create worktrees from the coordinator's named integration commit; create them lazily when work starts. Example after bootstrap:

```sh
git -C /Users/w3joe/Desktop/2026_sdth/horizon worktree add -b agent/a03-simulator /Users/w3joe/Desktop/2026_sdth/horizon-worktrees/a03-simulator main
```

Separate worktrees provide distinct working directories on one repository, as described by [Git's worktree documentation](https://git-scm.com/docs/git-worktree). They do not isolate ports, datasets, virtual environments, cloud resources, or the shared Git object store; coordinate those explicitly.

Rules:

1. Every file edit and command uses the agent's explicit absolute worktree path. Do not rely on a shared default current directory.
2. Never switch another worktree's branch or edit another agent's directory. The main checkout is coordinator-owned after bootstrap.
3. Keep commits small and component-scoped; report base/head commit IDs, tests, contract version, and remaining limitations.
4. Before consuming a dependency, the coordinator merges its accepted commit to `main`; the dependent agent updates its own branch from that integration commit. Avoid cross-agent cherry-pick chains.
5. Do not rewrite published history, force-push shared branches, or delete unfinished worktrees. Preserve a reproducible commit for every reported result.
6. Assign isolated ports/run IDs to parallel local processes. Do not launch multiple integrations against the same actuator endpoint or write to one artifact directory.
7. Share datasets/caches through content-addressed, read-only completed artifacts; write into job-specific temporary directories and publish atomically. Use cache locks for downloads.
8. Conflicts in owned code go to that owner; shared-schema/lockfile conflicts go to A01. The coordinator sequences resolution and rechecks the affected integration.

No continuous PR commentary or other external messaging is required. Use agent messages and checked-in handoff records for coordination. Push accepted code to the private repository as implementation progresses.

## 14. Concurrency schedule and integration gates

The sequence below respects three active subagent slots. Assignments can be resumed for later packets; the research agent releases its slot when a dependency would leave it idle.

| Wave | Slot 1 | Slot 2 | Slot 3 | Gate |
|---|---|---|---|---|
| **0: Bootstrap** | A01 | — | — | Private repo, clone, contract v0, fixtures, initial commit |
| **1: Start parallel work** | A02: protocol, harness, scorer | A03: plant + AI fixture | A06: console against fixture stream | Numerical motion, visible scene, runnable research harness |
| **2: Safety and data** | A02: comparison specification and development fixtures | A04: A1/A3, gate, recovery | A05: ingestion, fusion, communication faults | First actual protected crossing with input lineage |
| **3: Complete algorithms and perception** | A02: initial architecture experiments | A04: A2/A4/A5 and failure cases | A07: model reproduction, hooks, H0/H1 | Five distinct governor implementations; real perception output |
| **4: Connect the full product** | A02: Stage 1 runs and calibration protocol | A06: live integration, comparison, inspection | A07: H2/H3/H4 and cached experiment jobs | Coherent console and health-method implementations |
| **5: Package and challenge** | A02: Stage 2 and report preparation | A01: launcher/CI/contracts reconciliation | A08: system/failure testing | Runnable offline demo; defects recorded and routed |
| **6: Freeze and verify** | A02: frozen final evaluation | A08: independent verification | Rotating component owner for defects | Final reports, clean launch, verified private remote |
| **7: Later realistic 3D scope** | A09: vessel/warship assets and harbor art | A10: ocean/ship-motion physics | A06: renderer integration | Realistic 3D scene uses licensed assets and the authoritative simulation |
| **8: Realism validation** | A03: plant integration and characterization | A08: performance/system regression | A02: reassess affected research claims | New physical effects have separate evidence; visuals do not conceal altered dynamics |

The coordinator integrates between gates while other independent work continues. Slot assignments can rotate to unblock a concrete dependency; maximum concurrency and ownership remain unchanged. If runtime capacity later permits more concurrent agents, preserve the bootstrap/interface dependency and ownership boundaries, then overlap the ready assignments further.

The first end-to-end slice is:

```text
sensor observation + separate AI proposal
  → normalized evidence and GovernorInput
  → A1/A3 intervention
  → exclusive gate
  → changed actuator feedback and ship motion
  → visible trajectory and replayable reason
```

Do not wait for full SAE research or all datasets before completing this slice. Conversely, completing it does not waive the full research deliverables.

## 15. Compute and data coordination

Carry forward the research plan's **maximum ten NVIDIA L4 GPUs and USD 100 total spend**, including associated storage/egress/host charges within the defined accounting boundary. Ten GPUs are a ceiling, not a default allocation.

- A02 specifies experiment jobs and estimated costs. A07 supplies inference/extraction/training implementations. A01 owns provisioning scripts and the single budget ledger; the coordinator authorizes jobs against that ledger during approved execution.
- No subagent independently provisions a GPU fleet, repeats another extraction, or launches an unbudgeted sweep.
- Resolve the provider/account and actual all-in price at execution. Preserve the research plan's USD 20 reserve and its allocation within the remaining USD 80.
- Prefer provider-enforced limits where supported. Billing alerts alone are not a hard cap; add conservative job-runtime limits, automatic termination, and cost reconciliation. If a bounded run cannot be justified, do not start it.
- Establish one-sequence reproduction before scaling. Download/configure on CPU where practical; shut down accelerators as soon as jobs finish.
- Keep large datasets, weights, feature caches, packet archives, and recordings outside Git. Commit manifests, scripts, small clearly licensed fixtures, and compact reports.
- Do not treat inaccessible/unclearly licensed data as already available. Use explicit synthetic fixtures to keep independent integration moving while reporting the missing research dependency.

For final held-out evaluation, freeze code/model/calibration/split hashes before launch. If a defect requires changing them, invalidate the affected comparison and rerun under a new manifest; preserve the previous result history.

## 16. Agent launch brief and handoff format

Every launch uses this template, with the assignment details above appended:

```text
You are Horizon agent Axx, running gpt-5.6-sol with high reasoning.
Assignment: [role and concrete work packet]
Worktree: [absolute path]
Branch/base: [branch and commit]
Owned paths: [exact list]
Read: AGENTS.md, docs/source research/concept, contract version [v].
Inputs available: [fixtures, accepted dependency commits, artifact manifests]
Deliver: [code, tests, evidence, handoff]
Acceptance: [observable pass conditions]
Do not edit other agents' paths or shared contracts/lockfiles.
Send a concrete interface request when blocked; continue independent work.
Keep external decision AI separate and never use evaluation truth online.
Do not provision compute outside the central budget process.
No further subagent spawning without coordinator allocation.
```

Handoff record:

```text
Assignment / completed work packet:
Branch / base commit / head commit:
Files and contracts changed:
Run and test commands:
Observed results and artifact hashes:
Known failures / unsupported cases:
Dependency requests:
Next independently executable work packet:
```

Store handoffs inside each agent's owned documentation area. Ask other agents for required schema/data changes through the coordinator; do not modify another implementation to match an undocumented assumption.

## 17. Core-demo completion and full-scope completion

The core research/demo milestone is complete when:

1. `horizon` exists on GitHub, remains private, and the requested clone contains the integrated, pushed code.
2. A clean documented command launches the local CPU fixture demo; optional neural artifacts have explicit acquisition and launch instructions.
3. Eight input groups and reference inputs flow through versioned contracts with provenance and unknown states.
4. The external AI can be exchanged without changing the safety kernel; protected actuators remain gate-owned.
5. A1–A5 and H0–H4 are implemented and tested as distinct candidates, with unsupported assumptions and negative results recorded.
6. Correction changes real simulated motion; planner/supervisor/diagnostic/communication faults produce tested behavior.
7. The console shows the same source, inference, decision, actuation, and outcome across its linked views. Offline replay cannot masquerade as live inference.
8. Paired evaluation, calibration and held-out reports reproduce from saved manifests, stay within the compute cap, and support the chosen architecture.
9. A08's critical defects are resolved; the claim ledger and limitations are current.

The full planned scope additionally includes the later A09/A10 realistic 3D simulation phase below. Completing the core milestone does not label placeholder vessels or visual-only water as the finished realism deliverable.

## 18. Later scope: realistic 3D maritime simulation

**User-requested sequencing:** schedule this after the core RTA/data/research demonstration works. It is a real subsequent work package for additional agents, not an implication that the current illustrative preview already provides realistic ship or water physics. Both new subagents run **Sol / high** with their own worktrees.

### A09: existing ship models and realistic environment assets

Search online for usable existing **warship, naval patrol vessel, and civilian ship 3D models**, along with harbor structures, ocean materials, and environmental textures. Prefer authentic proportions, useful PBR materials, and assets whose license permits this demo and the intended distribution. Original creators, official model repositories, and reputable asset libraries are candidates; verify each asset's actual terms instead of assuming a download is reusable.

Deliver:

- A shortlist with model/source URL, creator, license, attribution, permitted use/redistribution, format, size, geometry/material quality, scale, and rigging/collision suitability.
- A selected ownship and representative civilian/background vessels, with source files/conversion scripts and an asset manifest.
- Browser-ready glTF/GLB conversions where appropriate, sensible polygon/texture budgets, levels of detail, collision proxies, correct origins, and consistent metre scale.
- A realistic harbor/environment asset package and lighting/material guidance for A06.

Choose an ownship model consistent with the simulated hull. A full-size warship cannot be substituted for a 12 m patrol craft by changing the mesh alone. Larger warships may appear as appropriately modeled background traffic; making one the controlled vessel requires new mass/draft/propulsion/turning parameters and a new characterized operating domain from A03/A04.

Prefer free appropriately licensed assets first. A paid asset is not charged silently to the GPU budget. If purchase is necessary, present the exact model, price, license, and purpose before any purchase. Keep restricted source assets out of Git; use an acquisition manifest when redistribution is prohibited. Detailed ship models are scenery/navigation assets only and add no weapon functionality.

**Acceptance:** asset provenance is complete; licenses support the intended use; scale, materials, loading performance, collision proxies, and attribution are checked. A06 integrates the assets without A09 concurrently modifying the console's source files.

### A10: water physics and vessel motion

Evaluate existing documented marine/ocean implementations before writing a fluid model from scratch. Reuse compatible open-source components where they satisfy fidelity, performance, licensing, and integration requirements.

Implement two explicitly separated layers:

1. **Visual ocean:** wave surface, reflections, foam, shoreline behavior, hull interaction, and speed/turn-dependent wakes. Connect effects to the actual vessel/environment state rather than a looping animation disconnected from motion.
2. **Physical marine response:** documented buoyancy, hydrodynamic damping, current/wind and wave forcing, and physically driven heave/roll/pitch where supported. Introduce a versioned higher-fidelity plant mode, potentially 6-DOF, through A03's authoritative simulation interface.

The rendering ocean must not become an independent owner of vessel position. One plant remains authoritative. If a separate physics engine is adopted, A03 must designate it as the plant for that run; do not simultaneously integrate two competing engines or apply disturbance forces twice. A10 provides the environment/force model and A03 owns final plant coupling.

Use versioned sea-state parameters, deterministic seeds, camera/motion synchronization, and a documented coordinate contract. Check equilibrium/flotation, draft, damping response, frame/sign conventions, and motion under simple forcing. Benchmark render/physics load independently from governor latency.

A high-fidelity mode changes the research assumptions. Derive and validate how the 3D motion projects into the governor's horizontal state and uncertainty bounds, including attitude-dependent camera geometry. If the conditions exceed the characterized RTA envelope, show degraded/unknown assurance rather than carrying over the original 3-DOF claim. Keep the original simple plant available as a regression baseline.

**Acceptance:** the resulting scene is visibly realistic, includes reusable licensed vessel assets and physically driven marine motion, and remains interactive on declared demo hardware. A08 verifies that wave/camera/render workload cannot starve command expiry or recovery. A02 reruns affected scenarios and versions the evidence whenever physical behavior changes.

### Integration and completion for the later phase

- A09 and A10 work concurrently after the core milestone, with separate worktrees and owned directories.
- A06 integrates their public modules/assets in `apps/console/`; A03 integrates force/plant changes in `services/simulator/`. Cross-owner edits are requested and serialized.
- Run a daylight and a degraded-visibility/rougher-water scenario, with visible proposed/accepted paths and provenance intact.
- Publish an asset/license manifest, physical-model assumptions, performance results, updated scenario manifests, and before/after evidence.
- Keep the repository private. This phase does not imply public hosting or operational deployment.

**Review checkpoint:** Accepted on 2026-09-21. Launch **A01 first**, then proceed through the concurrent work packets, including the scheduled later realism phase, without requesting repetitive permission for ordinary implementation steps already covered by that authorization.
