# Remaining implementation and claim closure

The bounded implementation packets authorized on 2026-09-21 are integrated into
the main branch. The USD 100 total Horizon compute cap remains in force. This
page records the resulting capabilities and the evidence still required;
worktree and worker status are omitted because they are temporary coordination
state.

| Workstream | Integrated capability | Remaining boundary |
| --- | --- | --- |
| Marine physics | Versioned wave/current/wind response, heave/roll/pitch, effective draft, characterization, and public operating-mode qualification are merged. | The higher-fidelity marine mode remains unqualified for assurance and needs its own evidence. |
| Maritime assets | The console renders the licensed RIB, cargo vessel, cargo stack, and buoy in both 3D views, with normalized GLBs, provenance, browser budgets, loading fallbacks, and validated served copies. | These are display assets only; collision hulls, hydrodynamics, sensing, and safety evidence remain separate. |
| Live neural evidence | Recorded-camera inference, exact health lineage, bounded queues and expiry, fail-closed policy, and the optional eighth coordinated process are merged. | The finite MODD2 footage is not pose-reactive and cannot provide metric contacts, free-space authority, or calibrated risk. |
| Research and verification | The frozen 296-frame CUDA/MPS descriptive drift comparison, S09/S12 restart-lineage tests, and S01–S22 gap audit are merged. | Full mission-level S01–S22 acceptance, calibration, and held-out evaluation remain open. |
| Deadline performance | Fixed-step recovery rollout allocation was reduced without relaxing the production 40 ms deadline. | Linux shared-runner deadline evidence and the remaining live-chain acceptance cases must still pass. |
| Integration/platform | Shared contracts, the default seven-service launch, opt-in perception launch, UI integration, bounded compute records, and private repository integration are present. | Real ship endpoints, production AI integration, and deployment qualification require external systems and evidence. |

The replay renderer now accepts authoritative wave components and physical
heave/roll/pitch, and the launcher accepts `--marine-config`. The existing audited
guided replay remains the baseline encounter. A new marine demonstration still
needs its own recorded run and visual verification; it must not reuse the
baseline safety outcome. Full S01–S22 acceptance and Linux timing remain open.

The integrated checks cover Python, contracts, the maritime asset registry,
TypeScript, and a production console build. The guided collision comparison and
live harbor were checked visually after asset integration. Linux run 35677708977
failed six live-chain tests because recovery requests took about 48–59 ms under
shared-runner load. The later fixed-step optimization preserves the 40 ms
deadline, but a passing Linux end-to-end rerun is still required.

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
- Every cloud job needs a unique bounded reservation. The project cap remains
  USD 100: USD 80 working and USD 20 protected. Do not change the workspace-wide
  billing limit.

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
evidence: numerical, operational, and safety equivalence and calibration
thresholds are not established.

All completed provider charges total USD 0.04250377. Every reservation is
reconciled and none is active, leaving USD 79.95749623 of the USD 80 working
budget, the full USD 20 protected reserve, and USD 99.95749623 unspent under the
USD 100 project cap. No calibration or held-out partition was opened. The
external ledger remains authoritative for current spending.
