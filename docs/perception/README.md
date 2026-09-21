# Perception and neural-health pipeline

Horizon loads the official pinned WaSR-T and WaSR source trees from the external `horizon-data` directory. It does not vendor source, checkpoints, frames, or activations. `horizon_perception.model` verifies the source commit and checkpoint SHA-256 before constructing a model. Its compatibility wrapper prevents both upstream ResNet101 factories from downloading an unpinned backbone when a complete checkpoint is already present.

The WaSR-T runner requires `reset(sequence_id)` before inference. A repeated or non-increasing timestamp invalidates the sequence and clears temporal state; processing can resume only after another explicit reset. The reproduction output retains source dimensions, the 512 by 384 model geometry, synthetic order-only timestamps for the upstream example, raw class-ID masks, color previews, frame hashes, source/checkpoint pins, and actual pooled encoder, temporal-fusion, and decoder summaries.

The hook comparison processes the same prefix three ways: vanilla after one warm-up frame, vanilla again after clearing sequential state, and instrumented after the same warm-up. The job fails if either reset replay or hooked logits differ exactly. Timings are a small three-frame device check and are labeled as such in the manifest.

## Reproduce the official example

The centrally controlled Modal job is `services/perception/modal_wasrt_smoke.py`. A01 creates the temporary artifact Volume, reserves the job in the central ledger, applies the selected job's controller wall-clock limit (600 seconds for attempt 002), retrieves and verifies artifacts, and deletes the Volume. The remote function itself enforces one L4, one container, no retries, fixed CPU/RAM bounds, a 900-second startup timeout, a 1,200-second execution timeout, no network, exactly 85 frames, and a 150 MiB output cap. Partial or oversized results are deleted before the Volume commit.

No local or cloud result is committed to Git. The expected external run directory contains `manifest.json`, `features.jsonl`, 85 single-channel `class_masks/*.png`, and 85 `mask_previews/*.png` files.

The upstream sequence is a reproduction fixture, not held-out evidence. Its timestamps are synthesized at 10 Hz from file order because source timestamps are absent. WaSR-T labels are obstacle 0, water 1, and sky 2; MaSTr ground truth may also contain ignore label 4. Image-space water does not establish depth or safe free space.

## Health methods

All methods return the bounded `PerceptionHealth` record described in `configs/perception/health-methods.json`.

- H0 uses output confidence/normalized entropy, preferring declared obstacle/danger-relevant regions over a whole-frame aggregate.
- H1 adds exposure, blur, occlusion, frozen-frame, timestamp, horizon, and temporal-output checks.
- H2 adds a regularized diagonal-covariance Mahalanobis distance on a fixed pooled embedding.
- H3 adds PCA reconstruction error and activation novelty.
- H4 adds a small sparse autoencoder. Fitting may precede causal controls, but runtime H4 remains `unknown` until matched, random-direction, and equal-norm controls pass a separate claim gate.

H2-H4 use cached real hook outputs. The cache reader applies a deterministic fixed grouped projection, normally to 64 dimensions, before fitting so covariance/PCA work stays bounded. The projection dimension, selected layer, source groups, fit split, model pin, and reference hash belong in the immutable artifact.

References may be fitted only on `development` or `nominal_reference` partitions. Threshold artifacts require the calibration split and a frozen hash. A health status may be calibrated for alerting while `missed_obstacle_risk` remains `unknown`: the current builder deliberately marks empirical calibration risk bins as unvalidated until a frozen held-out evaluation validates their coverage. Missing artifacts, mismatched hashes, features of the wrong dimension, and observations outside scope return `unknown`; they never silently become healthy.

Runtime health never outputs steering, obstacle-free truth, or metre-valued uncertainty. It can only select a separately defined, frozen uncertainty/mode policy. Representation novelty is not converted directly to distance. H4 intervention code is offline-only, requires `offline=True`, and is absent from runtime entrypoints.

## Frozen MODD2 development protocol

`configs/perception/modd2-splits.json` freezes whole `kopeNN` collection groups before evaluation. `kope81` is development because root had already inspected one schema-only label there; `kope67` and `kope75` are calibration; `kope71` and `kope82` are held out. Neither calibration nor held-out labels have been evaluated. The upstream WaSR-T example stays integration-only, and MaSTr1325 stays nominal/reference-only because of checkpoint training-family overlap.

The first bounded development extraction uses all 296 left-camera frames from `kope81-00-00006800-00007095`. It records full-frame pixelwise softmax entropy before logits are discarded, conventional checks with missing capabilities exposed, all-channel pooled encoder features, raw class masks, and unlabeled spatial maps at five evenly spaced frames. The fixed channel indices and frame rule are selected before inference. Display heatmaps use per-map min/max normalization and carry no semantic label.

H2 and H3 fit a frozen 64-dimensional contiguous-group projection of all 4,096 encoder mean/standard-deviation summary values. H4 fits all 2,048 encoder spatial means without truncation. Standardization is fitted on the development reference only. The H4 fitting record includes loss, epoch-cap status, constant inputs, and dead features.

The MODD2 parser follows RAW annotation conventions: MATLAB 1-based coordinates and inclusive `x:x+w`, `y:y+h` extents, finite sea-edge filtering, and singleton obstacle handling. The development report's fraction of class-0 mask pixels inside an obstacle bounding box is explicitly a Horizon proxy. It does not reproduce official MODD2 water-edge, own-vessel-mask, shoreline-dent, small/large obstacle, or detection metrics.

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

The full 85-frame example subsequently completed on the same local CPU setup.
`horizon-runs/compute/local-wasrt-sequence-085/` contains all 85 class masks,
previews, and three-layer feature records. The three measured comparison
frames again had exact vanilla/hooked and reset-replay agreement. Across the
85 instrumented frames, inference median was 1,413.744 ms and maximum was
1,583.511 ms. This is offline reproduction, not a 20 Hz perception result.
The recorded repository commit in its environment file was captured at run
completion; perception source did not change during that run.

Local Apple M1 Max MPS also passed a four-frame WaSR-T compatibility check
with exact hooked/unhooked output agreement. The generic runner now explicitly
synchronizes both MPS and CUDA before reading forward timing.
`horizon-runs/compute/local-mps-compatibility.json` records this small check;
it does not establish CPU/MPS numerical equivalence or sustained throughput.

The single-frame WaSR checkpoint passed a separate one-frame MPS functional
check, with exact hooked/unhooked agreement and real encoder/decoder summaries.
Its importer additionally needs a training-only Lightning `Callback` base stub.
Artifacts are in `horizon-runs/compute/local-wasr-baseline-preflight/`. WaSR has
no temporal module, and no temporal evidence is claimed for this baseline.

Modal attempt 002 built its image but was rejected before GPU execution because
the workspace lacked a payment method. Its app was stopped and empty temporary
volume deleted. Neither cloud attempt supplies GPU inference evidence.
