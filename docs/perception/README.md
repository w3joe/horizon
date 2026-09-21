# Perception and neural-health pipeline

Horizon loads the official pinned WaSR-T and WaSR source trees from the external `horizon-data` directory. It does not vendor source, checkpoints, frames, or activations. `horizon_perception.model` verifies the source commit and checkpoint SHA-256 before constructing a model. Its compatibility wrapper prevents both upstream ResNet101 factories from downloading an unpinned backbone when a complete checkpoint is already present.

The WaSR-T runner requires `reset(sequence_id)` before inference. A repeated or non-increasing timestamp invalidates the sequence and clears temporal state; processing can resume only after another explicit reset. The reproduction output retains source dimensions, the 512 by 384 model geometry, synthetic order-only timestamps for the upstream example, raw class-ID masks, color previews, frame hashes, source/checkpoint pins, and actual pooled encoder, temporal-fusion, and decoder summaries.

The hook comparison processes the same prefix three ways: vanilla after one warm-up frame, vanilla again after clearing sequential state, and instrumented after the same warm-up. The job fails if either reset replay or hooked logits differ exactly. Timings are a small three-frame device check and are labeled as such in the manifest.

## Reproduce the official example

The centrally controlled Modal job is `services/perception/modal_wasrt_smoke.py`. A01 creates the temporary artifact Volume, reserves the job in the central ledger, runs it with a 2,250-second controller wall-clock limit, retrieves and verifies artifacts, and deletes the Volume. The remote function itself enforces one L4, one container, no retries, fixed CPU/RAM bounds, a 900-second startup timeout, a 1,200-second execution timeout, no network, exactly 85 frames, and a 150 MiB output cap. Partial or oversized results are deleted before the Volume commit.

No local or cloud result is committed to Git. The expected external run directory contains `manifest.json`, `features.jsonl`, 85 single-channel `class_masks/*.png`, and 85 `mask_previews/*.png` files.

The upstream sequence is a reproduction fixture, not held-out evidence. Its timestamps are synthesized at 10 Hz from file order because source timestamps are absent. WaSR-T labels are obstacle 0, water 1, and sky 2; MaSTr ground truth may also contain ignore label 4. Image-space water does not establish depth or safe free space.

## Health methods

All methods return the bounded `PerceptionHealth` record described in `configs/perception/health-methods.json`.

- H0 uses output confidence/normalized entropy, preferring declared obstacle/danger-relevant regions over a whole-frame aggregate.
- H1 adds exposure, blur, occlusion, frozen-frame, timestamp, horizon, and temporal-output checks.
- H2 adds a regularized diagonal-covariance Mahalanobis distance on a fixed pooled embedding.
- H3 adds PCA reconstruction error and activation novelty.
- H4 adds a small sparse autoencoder. Its reference cannot be built unless matched, random-feature, and equal-norm intervention controls are recorded as completed.

H2-H4 use cached real hook outputs. The cache reader applies a deterministic fixed grouped projection, normally to 64 dimensions, before fitting so covariance/PCA work stays bounded. The projection dimension, selected layer, source groups, fit split, model pin, and reference hash belong in the immutable artifact.

References may be fitted only on `development` or `nominal_reference` partitions. Threshold artifacts require the calibration split and a frozen hash. A health status may be calibrated for alerting while `missed_obstacle_risk` remains `unknown`: the current builder deliberately marks empirical calibration risk bins as unvalidated until a frozen held-out evaluation validates their coverage. Missing artifacts, mismatched hashes, features of the wrong dimension, and observations outside scope return `unknown`; they never silently become healthy.

Runtime health never outputs steering, obstacle-free truth, or metre-valued uncertainty. It can only select a separately defined, frozen uncertainty/mode policy. Representation novelty is not converted directly to distance. H4 intervention code is offline-only, requires `offline=True`, and is absent from runtime entrypoints.

## Dataset boundary

MaSTr1325 is nominal/reference material with training overlap for the official checkpoint. It cannot supply held-out evidence. A local exact SHA-256 audit on 21 September 2026 compared 1,325 MaSTr image files with 23,479 MODD2 JPG/PNG camera files and found zero byte-identical files. This excludes exact duplicates only; it does not establish session, location, or near-duplicate independence.

MODD2 remains additional raw stereo/IMU material with sequence calibration and annotations. It is not the official MODS benchmark. The full official MODS dataset is unavailable through the current route because the old URL returns 404 and the current SharePoint requires institutional login. Until that access boundary changes, Horizon cannot claim official MODS reproduction or a final held-out perception result.
# Local integration verification

On 2026-09-21 the pinned WaSR-T checkpoint was loaded and evaluated on the first
four provided example frames using local CPU, PyTorch 2.5.1, torchvision 0.20.1,
Pillow 10.4.0, and four CPU threads. Instrumented versus uninstrumented outputs
and temporal reset replay were exactly equal (maximum absolute difference 0).
Encoder, temporal-fusion, and decoder-logit summaries were captured. The
training-only Lightning compatibility shim includes the logger type used by an
eagerly evaluated upstream annotation; importing the real model exposed this
requirement.

The local artifacts are outside Git under
`horizon-runs/compute/local-wasrt-preflight/`. This is a four-frame integration
check, not held-out evidence or a GPU performance measurement. The first Modal
attempt was stopped during image construction after this import issue was
found locally; no successful GPU inference is claimed for that attempt.
