# H5 frozen-feed guard: implementation and verification

The H5 simulation monitor now detects repeated camera images with a separate
camera-integrity guard. The pinned WaSR-T model has temporal feature aggregation
but no frozen-feed alarm. Its neural architecture and weights were not changed.
Neither were the H5 reference, neural score, or 2.731332008015018 threshold.

## Behavior

The guard compares decoded RGB pixels and dimensions. Three consecutive
duplicates trigger `frozen_feed`; one or two do not. This is four identical
observations including the original image. Metadata-only file changes do not
hide duplicate pixels. The trigger resets on changed pixels, missing duplicate
evidence, stale publication, sequence changes, reversed ordering or an expired
previous frame.

The `simulation_h5_warning` payload reports the repeat count, configured minimum
and separate reasons for freezes and neural anomalies. A freeze can warn even
when the representation score is low or unavailable. Fusion carries this
evidence to the simulation fixture, which requests its existing 1 m/s speed
cap; A5 and the gate still validate the command. Safety-facing camera health
and metric geometry remain unknown/unavailable.

## Real-model rerun

The [original mock exercise](h5-mock-failure-20260925.md) was repeated on exactly
the same 180 input images: 60 original, 60 with a 20-frame blackout, and 60 with
a 20-frame freeze. Input hashes matched the original experiment. All 180 neural
scores also matched exactly, isolating the new camera guard's effect.

| Test | Before | With guard | Detection/recovery |
|---|---:|---:|---|
| Frozen window | 0/20 warnings | **18/20 warnings** | Starts on third repeated frame; clears on first changed frame |
| Blackout window | 20/20 warnings | **20/20 warnings** | Neural warning starts on first black frame; clears after three recovery frames |
| Original 60-frame clip | 0 warnings | **0 warnings** | First frame unknown, remaining 59 below threshold |

The freeze begins at zero-based frame 20. The guard warns from frame 22 through
39; the first two repeats are the intentional debounce interval. On frame 40,
normal video resumes and the freeze warning clears. The neural score during
the freeze remains 0.047–0.060, so all 18 warnings come from the camera guard.

These are offline frame-order measurements with synthetic timestamps, not
measured live response times. Inference throughput and dropped input frames
affect real elapsed detection time. This is one development clip and one
episode per fault, not held-out validation. Exact repeated pixels are covered;
re-encoding that changes pixels, near-frozen images and total absence of new
frames are outside this guard. Existing freshness checks handle absent evidence.

## Evidence and checks

Run artifacts: `../horizon-runs/development/h5-mock-failure-20260925-v2/`.
The directory contains the predeclared protocol, input hashes, frozen runtime
sources, per-frame results, summaries and repository-check log. Reproduce with
the command in the original mock report and a new output directory.

`./scripts/check.sh` passed: **627 passed, 1 skipped**, plus contracts and console
checks/build. Coverage includes debounce and reset conditions, duplicate pixels
with changed file metadata, low/missing neural scores, malformed guard evidence,
and freeze warnings passing through fusion, the fixture, A5 and a gate with a
test plant. Expired evidence and non-simulation snapshots do not activate the
simulation speed response. Ruff and whitespace checks also passed.
