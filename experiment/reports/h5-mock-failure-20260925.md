# H5 mock camera failure: real inference results

This records the original monitor. See the [subsequent frozen-feed guard
implementation and rerun](h5-frozen-feed-20260925.md) for the updated behavior.

H5 detected a fully black camera feed immediately, but missed a frozen feed.
This demonstrates a response to one input fault, not reliable detection of all
camera failures or improved obstacle avoidance.

## Exercise

Run the same first 60 left-camera frames from development sequence
`kope81-00-00006800-00007095` through the pinned WaSR-T model and the existing
`RecordedCameraObservationBuilder` H5 warning path. Reset model, conventional
checks and H5 temporal state between arms. Use FP32 on local MPS.

- Control: all 60 frames unchanged.
- Blackout: 20 normal frames, 20 completely black frames, 20 normal frames.
- Frozen feed: 20 normal frames, repeat the last normal frame for 20 frames,
  then resume the original sequence for 20 frames.

The fault design, source hashes and threshold were saved before inference.
Only input pixels changed; warning scores were not injected. The H5 threshold
remained **2.731332008015018**, using the existing reference artifact. Fault
labels remained in the offline input manifest and evaluation; they were not
supplied as detector evidence. No observations were published to the running
application and no controller was actuated.

## Results

| Input during frames 20–39 (zero-based) | H5 warnings | H5 score range | Simple camera check |
|---|---:|---:|---|
| Original video | 0/20 | 0.031–0.098 | No darkness or duplicate-frame detections |
| Blackout | **20/20** | 10.581–21.762 | Underexposure detected on 20/20 frames |
| Frozen feed | **0/20** | 0.047–0.060 | Duplicate-frame check detected 20/20 frames |

Across the full 60-frame original clip, H5 generated no warnings: 59 frames
were below threshold and the first was unknown because no previous frame
existed. The 20-frame normal prefix was identical across all three arms.

Blackout detection occurred on the **first corrupted frame**, with score
21.762. After normal video resumed, scores were 22.419, 6.094, 4.107 and 2.565
on the first four recovery frames. H5 therefore retained its warning for
three recovery frames, then remained below threshold for the rest of the run.
Temporal context can retain evidence of the preceding fault.

WaSR-T's mean pixel confidence during the blackout fell to 59.8%, compared
with 99.1% for the matching original window. Frozen video retained about
99.0% confidence. These are output diagnostics, not probabilities of correct
navigation or a calibrated H0 comparison.

H5's missed freeze is consistent with its design: an unchanged, ordinary-looking
internal representation need not have high reconstruction error or temporal
change. The separate duplicate-frame check is useful here. Both injected
faults were detectable with simpler camera checks, so this exercise does not
establish an incremental benefit from H5 over those checks.

## Timing and limits

This was an offline exercise with synthetic 10 Hz timestamps. Detection and
recovery delays above are **frame offsets**, not measured live response times.
Median model inference was approximately 813–816 ms per frame on this local
MPS run. Publication timestamps were deliberately synthetic; no claim of
10 Hz live throughput or end-to-end deadline compliance follows.

This is one development clip, whose collection may have been seen during H5
training. Held-out data were not opened. Camera input corruption is known by
construction, but segmentation accuracy, missed obstacles and collision
outcomes were not evaluated. Twenty consecutive fault frames are one fault
episode, not twenty independent trials. Safety-facing health remains unknown.

## Reproduction and evidence

From the repository root, choose a new output directory:

```sh
PYTHONPATH=.:services/perception:services/neural-health .venv/bin/python \
  -m experiment.evaluation.h5_mock_failure \
  --output ../horizon-runs/development/h5-mock-failure-repeat
```

The completed run is at
`../horizon-runs/development/h5-mock-failure-20260925-v1/`.
It retains `protocol.json`, `input-manifest.json`, transformed images,
per-frame scores/checks/output summaries, `summary.json`, exact frozen sources
and `repository-check.log`. All 180 output rows, fixed thresholds and frozen
source hashes were verified.

Validation: `./scripts/check.sh` passed, including **605 tests passed,
1 skipped**, contract validation and console checks/build. Targeted fault-window
and recovery-accounting tests passed, as did Ruff and whitespace checks.
