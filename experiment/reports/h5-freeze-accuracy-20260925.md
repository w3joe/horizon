# H5 with freeze guard: broader missed-obstacle rerun

The freeze guard produced **no change** in the original 10,110-frame
missed-obstacle proxy evaluation. H5 still detected **7 of 33 misses (21.21%)**,
missed 26, and generated 193 false warnings against that proxy.

## Paired results

| Metric | Original neural H5 | H5 plus freeze guard |
|---|---:|---:|
| Evaluation frames | 10,110 | 10,110 |
| Misses detected | 7/33 | 7/33 |
| Misses not detected | 26/33 | 26/33 |
| False warnings | 193 | 193 |
| True negatives | 9,884 | 9,884 |
| Recall | 21.21% | 21.21% |
| Precision (warnings matching the proxy) | 3.50% | 3.50% |
| False-positive rate | 1.92% | 1.92% |
| Balanced accuracy | 59.65% | 59.65% |
| Raw accuracy | 97.83% | 97.83% |

Raw accuracy is misleading because misses are rare: always reporting no issue
would achieve 99.67% accuracy while detecting none. Recall and false warnings
are more informative here. The target is an annotated obstacle box with zero
predicted obstacle pixels, not a general camera-failure label or the official
MODD2 metric.

## Why the result did not improve

There were **337 decoded duplicate frames**, all in the temporal-drop arm.
Every duplicate was isolated: the maximum consecutive-duplicate count was
**one**. That arm repeats a single frame every five frames. The guard needs
three consecutive duplicates, so it added zero warnings, zero detections and
zero false positives. Its setting was not changed during this evaluation.

| Condition | Misses detected / present | False warnings | Decoded duplicates | Guard warnings |
|---|---:|---:|---:|---:|
| Original video | 0/1 | 0 | 0 | 0 |
| Blur | 0/1 | 0 | 0 | 0 |
| Underexposure | 0/4 | 0 | 0 | 0 |
| Occlusion | 7/16 | 193 | 0 | 0 |
| JPEG degradation | 0/1 | 0 | 0 | 0 |
| Isolated temporal drops | 0/10 | 0 | 337 | 0 |

The [separate sustained-freeze exercise](h5-frozen-feed-20260925.md) still shows
the intended improvement: warning from the third repeated frame and clearing
on changed pixels. This broader dataset contains no such sustained repeat run.
The new guard therefore adds coverage of sustained freezes, but this rerun
demonstrates no improvement in missed-obstacle detection.

## Method and limitations

- Same four `kope75` calibration sequences and six conditions as the original
  evaluation; no new data, injected faults, training or threshold fitting.
- Original WaSR-T activation caches and labels verified against their hashes in
  the prior report. Each image verified against the digest in its cached record.
- Actual images decoded for current pixel-repeat evidence. Every frame rescored
  through the current `RecordedCameraObservationBuilder`, including its actual
  freeze counter, reason codes and warning combination.
- Neural scores checked against the original batch evaluator with tight numeric
  tolerance and identical threshold decisions. The original confusion matrix
  was reproduced exactly.
- 10,134 frames processed; the first frame of each of 24 jobs was unknown and
  excluded, preserving the original paired denominator of 10,110.
- Predictions written before joining labels; no fault or missed-obstacle labels
  supplied to the detector. Predictions retain both component triggers.
- Model forward passes were reused, not repeated. This is offline rescoring
  with synthetic timestamps, not a live-latency or navigation-benefit test.
- Same previously inspected calibration evaluation group, not independent
  held-out validation. Held-out sequences remain unopened. Adjacent frames are
  dependent; no independent-frame confidence intervals are claimed. The binary
  combined rule has no manufactured joint AUROC or average-precision score.

## Reproduction and evidence

```sh
PYTHONPATH=.:services/perception:services/neural-health \
OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv/bin/python \
  -m experiment.evaluation.h5_freeze_accuracy \
  --output ../horizon-runs/analysis/h5-freeze-accuracy-repeat
```

Completed artifacts: `../horizon-runs/analysis/h5-freeze-accuracy-20260925-v1/`.
The directory contains the predeclared `protocol.json`, frozen source files,
24 label-free prediction files, `summary.json` with per-condition/per-sequence
results, `output-hashes.json`, and `repository-check.log`.

Verification: all input and source checks passed; all 24 prediction files and
frame counts were audited. `./scripts/check.sh` passed with **629 passed,
1 skipped**, plus contract checks and console checks/build. Targeted metric
and pixel-digest parity tests, Ruff and whitespace checks passed.
