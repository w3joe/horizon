# Live recorded-camera perception boundary

Horizon can run the pinned WaSR-T checkpoint against a finite recorded MODD2
sequence and publish the results through the live collector and fusion APIs.
This is a source-integration mode. The images do not depict, synchronize with,
or respond to the simulator vessel. Radar remains the source of metric contact
geometry.

Each frame produces an atomic pair of `Observation` records:

1. `camera-recorded-wasrt` carries image-space segmentation statistics, the
   source-frame digest, intrinsics provenance, and an empty `contacts` array.
2. `neural-health-recorded-wasrt` carries bounded layer statistics and a nested
   schema-valid `PerceptionHealth` record. It names the exact camera
   observation, frame, and inference.

The source capture timestamp and expiry are immutable. Inference completion or
collector receipt cannot renew them. The service uses a bounded drop-oldest
queue and does not retry publication. Queue loss, frames stale before
inference, inference failure, publication failure, and results that completed
after the source expiry are separate counters.

Fusion accepts a neural health record only while both it and its exact linked
camera observation are fresh. It binds the nested `health_id` into
`GovernorInput.health.perception_health_id` and the corresponding evidence
bundle. Camera observations never enter contact association. Before requesting
an external AI proposal, fusion can supply this bounded context:

```text
[fusion snapshot_id, nested perception health_id]
```

The external AI must return that exact ordered list in
`AIInferenceTrace.consumed_input_ids`. A changed, expired, missing, reversed, or
extra identity fails closed. The context always says that metric contacts and
camera free-space permission are unusable and that calibrated risk is unknown.

`AssuranceConfig.camera_reliance_mode` is deployment configuration, not an AI
claim. The default `radar_only` mode does not require camera evidence. The
`recorded_camera_supporting` mode requires current neural health for normal AI
authority. Unknown or output-only health moves A1 into its independently
validated radar/navigation/actuator recovery path. Camera health is not
required for that recovery, so a camera failure cannot disable the independent
fallback. Radar and other normal required sources remain required in both
modes.

The simulator reference also maps to an
`operating_mode_qualification` health leaf. It is available only when the
reference identifies the same plant mode as physically characterized and
assurance-qualified. Missing qualification is compatible only with the exact
legacy `synthetic-12m-3dof-v1` baseline. New marine modes remain unavailable to
assurance until separately qualified; perception cannot qualify them.

## Camera geometry

[`recorded-camera-live.json`](../../configs/perception/recorded-camera-live.json)
binds the source calibration file digest and its left-camera intrinsics and
distortion coefficients. Metric projection is explicitly unavailable because
the integration lacks validated camera-to-vessel extrinsics, camera height and
water level, synchronized attitude, a water-contact pixel extractor, a
projection error model, and range validation. Intrinsics and stereo baseline
alone do not establish vessel-frame contacts or safe water.

## Finite operation

### Coordinated local launch

The default launcher still starts the original seven services and does not load
a neural model. Recorded-camera processing is an explicit opt-in:

```bash
./scripts/launch_cpu.sh \
  --perception-config configs/perception/recorded-camera-live.json
```

Before creating the run directory or starting any process, the launcher checks
the config schema and mode, the frozen development split digest and membership,
the source sequence path, the calibration digest, the left-frame count, the
bounded cadence/TTL/queue, drop-oldest backpressure, zero publication retries,
and the absence of metric-contact authority. Missing external data or any
invalid field stops startup.

The optional supervisor starts after collector and before fusion. It binds an
ephemeral loopback port, recorded under `ports.perception` in `run.json`, so the
stable seven-service port map is unchanged. It becomes ready only after
collector reports fresh observations from both `camera-recorded-wasrt` and
`neural-health-recorded-wasrt`. The loopback endpoints are:

- `GET /health` for current readiness and source-exhaustion state. It returns
  503 after finite source exhaustion because no fresh camera input remains.
- `GET /v1/diagnostics` for allowlisted source identity, bounded processing
  counts, queue/expiry loss, and the explicit authority limits.

Diagnostics contain no external filesystem paths, images, activations, labels,
tokens, metric contacts, or free-space claims. `run.json` records the config
digest, source partition and sequence, frame count, diagnostic URL, and sources
observed at readiness. SIGINT/SIGTERM stops the supervisor and its perception
child; the central launcher retains its existing five-second bounded shutdown
and forced-kill fallback.

This configuration selects local MPS float32 WaSR-T processing with H5 simulation
warnings. It is a finite
296-frame recorded source, not a sustained sensor daemon. When the sequence is
exhausted, diagnostics report completion and fresh health naturally expires;
records are never replayed or renewed. The optional process does not establish
20 Hz end-to-end operation.

### Standalone component

Run the service as a supervised process, using external source, weight, data,
and calibration paths:

```bash
PYTHONPATH=services/perception:services/neural-health \
python -m horizon_perception.live_service \
  --source "$WASRT_SOURCE" \
  --weights "$WASRT_WEIGHTS" \
  --sequence "$MODD2_SEQUENCE/frames" \
  --calibration "$MODD2_SEQUENCE/calibration.yaml" \
  --geometry-config configs/perception/recorded-camera-live.json \
  --collector-url http://127.0.0.1:8105 \
  --run-id recorded-camera-demo --maximum-frames 296
```

The finite 296-frame L4 extraction wrapper is
`services/perception/modal_wasrt_modd2_dev.py`. The central compute controller,
not this entrypoint, owns budget reservation, launch, wall timeout, artifact
retrieval, reconciliation, and volume deletion. The wrapper requires clean
host launch commit and source/entrypoint/job-spec hashes because its container
is not a Git checkout. It verifies the exact 296-frame count, 105,562,636 input
bytes, development split digest, 296 feature/mask/preview records, five probe
frames, and a 100 MiB output limit.

An earlier finite 85-frame L4 development run recorded median synchronized
forward time 32.147 ms and maximum 34.601 ms. CPU health computation had a
62.349 ms median, while hook capture had an 18.652 ms median. These component
measurements do not establish end-to-end 20 Hz operation. Its external record
is `horizon-runs/compute/a07-wasrt-sequence-003`; the authoritative clean launch
metadata is `platform-run.json`, because the container correctly had no Git
identity.

H4 can also be exercised explicitly as a shadow monitor by supplying its
validated development reference:

```bash
PYTHONPATH=services/perception:services/neural-health \
python -m horizon_perception.live_service \
  --source "$WASRT_SOURCE" \
  --weights "$WASRT_WEIGHTS" \
  --sequence "$MODD2_SEQUENCE/frames" \
  --calibration "$MODD2_SEQUENCE/calibration.yaml" \
  --geometry-config configs/perception/recorded-camera-live.json \
  --collector-url http://127.0.0.1:8105 \
  --run-id recorded-camera-h4-shadow --method H4 \
  --reference-artifact "$H4_VALIDATED_REFERENCE"
```

Without a matching frozen health-calibration artifact, the service records the
H4 representation score and artifact identity but publishes health as unknown.
It does not grant camera authority, metric geometry, or free-space permission.
The failed tuned 96-feature reference is not suitable for this command; use of
any reference still remains a development/shadow exercise until calibration and
held-out gates pass.

H5 uses the same command shape with `--method H5` and the validated H5
reference. Its first frame remains `unknown` because a previous temporal-fusion
embedding is required; later frames record reconstruction and temporal-code
distance scores. Camera health and missed-obstacle risk remain unknown without
the required calibration and validation.

### Active H5 warnings in the simulation

The checked-in `configs/perception/recorded-camera-live.json` now selects
`health_monitor.mode: simulation_warning`. Start the simulator stack with:

```bash
.venv/bin/python scripts/launch.py \
  --perception-config configs/perception/recorded-camera-live.json
```

The experimental neural trigger is the H5 representation score at or above
`2.731332008015018`, taken from the `kope67` threshold fit in the
[proxy accuracy experiment](h-stack-accuracy.md). The config binds it to that
experiment's H5 reference hash and records the source report hash. It is a demo
threshold, not a calibrated missed-obstacle probability.

H5's simulation warning also includes a separate frozen-feed guard. The pinned
WaSR-T model aggregates temporal features but does not detect camera freezes;
the guard belongs to Horizon's monitoring wrapper, not the neural network.
`health_monitor.simulation_warning.frozen_feed.minimum_consecutive_duplicates`
is set to **3**. It raises a warning on the third consecutive duplicate of the
last changing image (four identical observations including that original).
The conventional image check compares exact decoded RGB pixels and dimensions,
so changes to file metadata alone do not defeat it. One or two repeated frames
do not trigger this guard. Re-encoding that changes pixels, near-static images,
or a camera that stops delivering frames entirely require other checks; missing
frames still expire through the existing freshness path.

The counter resets on changed pixels, missing duplicate evidence, stale
publication, a source-sequence change, reversed ordering, or an expired previous
frame. It counts consecutive processed observations, so dropped input frames
and inference speed affect elapsed detection time. Three repeats are a demo
debounce setting, not a validated vessel safety threshold.

`simulation_h5_warning.reason_codes` distinguishes `frozen_feed` from
`spatiotemporal_feature_shift`. Its `frozen_feed` object includes the method,
count, threshold and status. The representation score and its calibration
threshold are unchanged; a frozen-feed warning can coexist with a low or missing
neural score. This is an additional camera-integrity check, not evidence that
the trained H5 representation now recognizes freezes.
The [mock-failure rerun](../../experiment/reports/h5-frozen-feed-20260925.md)
records detection, recovery and unchanged neural scores on identical inputs.

For each fresh temporal pair, the producer publishes a separate
`simulation_h5_warning` with status `warning`, `below_threshold`, or `unknown`.
Fusion forwards it with the exact frame and inference lineage only in simulator
context. The decision-AI fixture caps its proposed speed at **1 m/s** when the
status is `warning`; A5 and the actuator gate still validate the proposed
command. The inference trace records `candidate_scores.h5_simulation_warning`
and the consumed health ID so the response is auditable.

A below-threshold score without a freeze trigger leaves the fixture's ordinary
navigation proposal in place. Missing or first-frame evidence remains unknown
unless an independent freeze trigger is available; expired evidence is unusable. It
does not assert healthy perception. Temporal pairs cannot cross source sequences
or reuse an expired previous frame. Recorded video still does not respond to
the simulated boat, so this demonstrates a control response to recorded H5
evidence, not closed-loop camera validation.

Set `health_monitor.mode` back to `shadow_only` to collect scores without the
simulation warning response. Other decision-AI implementations do not acquire
this behavior automatically. Camera geometry, free-space permission, risk bands,
and real-vessel authority are unchanged.

No calibration or held-out labels are consumed by live mode. H0/H1 may expose
mechanistic conditions, but missed-obstacle risk remains unknown. H2-H5 may be
run for development diagnostics, including H4/H5 shadow scoring and H5's
simulation-only warning response, but cannot gain runtime health authority until
their frozen calibration, matched controls, and held-out claim gates pass.
