# Remaining implementation — active parallel work

The user authorized the remaining work on 2026-09-21, with Sol/high agents,
isolated worktrees, automatic integration after checks, and a USD 100 total
Horizon compute cap. Three workers can run alongside the coordinator.

| Workstream | Owner / worktree | Current deliverable |
| --- | --- | --- |
| Marine physics | A10, `a10-marine-physics` | Versioned authoritative wave/current/wind response and heave/roll/pitch; physical characterization and an operating-mode assurance barrier; preserve the original plant. |
| Live neural evidence | A07/A05, `a07-live-perception` | Actual recorded-camera inference streamed through collector/fusion with frame and health lineage, bounded queues/expiry, explicit geometry/risk unknowns, and a finite CUDA development extractor. |
| Research | A02, `a02-research` | Frozen device/precision drift study, H0–H4 development and calibration procedures, convergence/causal gates, then eligible held-out comparisons. |
| Integration/platform | Coordinator, integrated `main` | Shared contracts, launch modes, Modal reservations and artifact recovery, UI integration, Linux timing diagnosis, merges and private remote. |

Follow-on worker packets are the remaining licensed civilian/naval/harbor assets,
renderer integration of physical marine motion, and independent S01–S22 acceptance
coverage. Rotate workers onto these when an active packet finishes or has an
external dependency; do not create concurrent owners for the same paths.

## Completion evidence

- New physical modes may not inherit the baseline safety claim. Physical model
  characterization and qualification for runtime assurance are separate.
- A camera stream may influence source-health policy without pretending its
  recorded imagery depicts the simulator. Metric contacts require validated pose,
  calibration, and a projection error model. Unknown risk stays unknown.
- Research must preserve development/calibration/held-out partitions. Do not run
  the planned 1,000-episode final study until the relevant methods, assumptions,
  and thresholds are frozen and the comparison is scientifically meaningful.
- Real ship hardware and production AI connections require actual endpoint and
  interface information. Tested adapters, local replay, and declared unavailable
  capabilities are the current implementable boundary.
- Every cloud job needs a unique bounded reservation. The working budget remains
  USD 80 with USD 20 protected; do not change the workspace-wide billing limit.

## Modal retry result

L4 access probe 006 succeeded. WaSR-T job `a07-wasrt-sequence-003` then completed
85 CUDA/fp16 frames on an NVIDIA L4. The initial directory download failed; its
14,924,112 bytes of saved output were recovered without rerunning inference,
validated and hashed locally, and the temporary Volume was deleted. The exact app
`ap-lIRjKmydewekqCsyxnFhZE` is stopped with zero tasks.

Median instrumented network forward time was 32.147 ms, maximum 34.601 ms.
CPU health-feature processing had a 62.349 ms median. The three measured prefix
frames had identical instrumented/uninstrumented outputs and repeatable reset.
These are integration measurements, not an end-to-end 20 Hz or held-out claim.
The platform run metadata supplies clean launch provenance because the inference
container does not contain the repository's Git metadata.

Artifacts remain under `horizon-runs/compute/a07-wasrt-sequence-003/`. The USD 1.50
job reservation and USD 0.10 successful-probe reservation remain charged against
the budget until provider billing is reconciled. Reconciled earlier spend is
USD 0.00515378; prior rejected probes 003–005 reported USD 0 through the completed
19:00 UTC billing interval. Reservations are ceilings, not measured charges.
