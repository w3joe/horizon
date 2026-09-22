# Remaining implementation — active parallel work

The user authorized the remaining work on 2026-09-21, with Sol/high agents,
isolated worktrees, automatic integration after checks, and a USD 100 total
Horizon compute cap. Three workers can run alongside the coordinator.

| Workstream | Owner / worktree | Current deliverable |
| --- | --- | --- |
| Marine physics | A10, `a10-marine-physics` | First implementation merged: versioned wave/current/wind response, heave/roll/pitch, effective draft, characterization, and public operating-mode qualification. Fusion enforcement is in the live-evidence packet. |
| Maritime assets | A11, `a11-asset-renderer` | Merged licensed RIB, cargo vessel, cargo stacks and buoys with normalized GLBs, provenance, browser budgets, loading fallbacks and role-appropriate placement in both 3D views. |
| Live neural evidence | A13, `a13-live-launch` | Core recorded-camera inference, health lineage, bounded queues and fail-closed fusion policy are merged. Active packet makes it an explicit coordinated launch mode. |
| Research and verification | A12, `a12-acceptance` | Frozen 296-frame CUDA/MPS drift comparison complete. S09/S12 restart lineage tests and the S01–S22 gap audit are merged; S02/S22 evidence strengthening remains active. |
| Deadline performance | A14, `a14-deadline` | Preserve the exact 40 ms control deadline while removing predictive-rollout allocation overhead; prove numerical equivalence and rerun Linux end-to-end tests. |
| Integration/platform | Coordinator, integrated `main` | Shared contracts, launch modes, Modal reservations and artifact recovery, UI integration, Linux timing diagnosis, merges and private remote. |

Three Sol/high workers run concurrently in isolated worktrees, with the
coordinator as the fourth active slot. Completed workers rotate onto the next
bounded packet without overlapping file ownership. The coordinator integrates
and automatically merges reviewed changes into the private repository.

The replay renderer now accepts authoritative wave components and physical
heave/roll/pitch, and the launcher accepts `--marine-config`. The existing audited
guided replay remains the baseline encounter. A new marine demonstration still
needs its own recorded run and visual verification; it must not reuse the
baseline safety outcome. Full S01–S22 acceptance and Linux timing remain open.

The integrated local check passes 377 Python tests, contract checks, the maritime
asset registry, TypeScript checks and a production console build. The guided
collision comparison and live harbor were checked visually after asset
integration. Linux run 35677708977 still failed six live-chain tests because
recovery requests took about 48–59 ms under shared-runner load. The 40 ms
deadline has not been relaxed; A14 is optimizing the same fixed-step equations.

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

Artifacts remain under `horizon-runs/compute/a07-wasrt-sequence-003/`. Provider
billing is reconciled for the probe, this job, and both failed pre-inference
alignment attempts.

The successful development alignment is
`a07-modd2-dev-runtime-alignment-003`: 296 MODD2 left-camera frames, 296 masks,
296 previews, all feature records and five fixed spatial probes. Exact app
`ap-vGQLUF8L91XBN77IWg3xb5` is stopped and its bounded Volume is deleted. Median
CUDA/fp16 forward time was 34.907 ms; CPU health features took 108.308 ms, so no
20 Hz end-to-end claim is made. Relative feature drift against the frozen MPS
reference was nonzero (p95: encoder 0.003637, temporal 0.009944, decoder 0.002857)
and mask-disagreement p95 was 0.000174. This remains descriptive development
evidence: equivalence and calibration thresholds are not established.

All completed provider charges total USD 0.04250377. There are no active compute
reservations, leaving USD 79.957496 of the USD 80 working budget and the full
USD 20 protected reserve. No calibration or held-out partition was opened. The
external ledger remains authoritative for current spending.
