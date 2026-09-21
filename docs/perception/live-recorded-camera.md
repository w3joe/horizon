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

No calibration or held-out labels are consumed by live mode. H0/H1 may expose
mechanistic conditions, but missed-obstacle risk remains unknown. H2-H4 remain
offline development methods until their frozen calibration, matched controls,
and held-out claim gates pass.
