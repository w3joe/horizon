# H0-H5 missed-obstacle proxy accuracy experiment

## Result (2026-09-24)

H5 was the strongest ranking method in this collection-group-disjoint
calibration experiment, but it did not achieve deployment-ready fault
detection. Its raw accuracy was 97.83%, balanced accuracy was 59.65%, recall
was 21.21%, precision was 3.50%, false-positive rate was 1.92%, AUROC was
0.712, and average precision was 0.0515.

Raw accuracy is misleading here. Only 33 of 10,110 evaluation records (0.33%)
were positive under the frozen missed-obstacle proxy, so an always-negative
classifier would achieve 99.67% accuracy while detecting no misses. Balanced
accuracy, recall, AUROC, and average precision are therefore the more useful
comparison metrics.

| Method | Accuracy | Balanced accuracy | Precision | Recall | F1 | FPR | AUROC | Avg. precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H0 | 97.95% | 53.67% | 1.67% | 9.09% | 2.82% | 1.76% | 0.664 | 0.0103 |
| H1 | Not evaluated | Not evaluated | - | - | - | - | - | - |
| H2 | 91.60% | 45.95% | 0.00% | 0.00% | 0.00% | 8.10% | 0.639 | 0.0042 |
| H3 | 92.17% | 47.74% | 0.13% | 3.03% | 0.25% | 7.54% | 0.521 | 0.0036 |
| H4 | 98.94% | 49.63% | 0.00% | 0.00% | 0.00% | 0.73% | 0.641 | 0.0066 |
| **H5** | **97.83%** | **59.65%** | **3.50%** | **21.21%** | **6.01%** | **1.92%** | **0.712** | **0.0515** |

H5 detected 7 of the 33 proxy misses. All seven detections were in the
occlusion arm; it detected none of the misses in the nominal, blur, JPEG,
underexposure, or temporal-drop arms at the frozen threshold. This means the
aggregate lead does not yet demonstrate broad fault coverage.

## Frozen design

- H2-H5 references were fitted on the `kope81` development collection.
- `kope67` calibration sequences selected one threshold per method, maximizing
  recall subject to a 5% empirical false-positive-rate ceiling.
- Thresholds were then applied once to the disjoint `kope75` collection.
- Every method used the same 10,110 evaluation records and labels.
- The first frame of each sequence/arm was excluded for every method because
  H5 requires a prior temporal-fusion embedding.
- Nominal data and five frozen controlled arms were included: blur,
  underexposure, occlusion, JPEG degradation, and temporal drop.
- The sealed `kope71`/`kope82` held-out partition was not opened.

The positive label is the Horizon MODD2 bounding-box proxy: a frame is positive
when at least one annotated obstacle box contains zero WaSR-T obstacle pixels.
It is not the official MODD2 metric. Adjacent frames are serially dependent, so
the frame-level Wilson intervals in the machine-readable report are
descriptive rather than independent-sample coverage guarantees.

H1 was excluded rather than silently approximated because the acquisition does
not contain its required independent horizon and occlusion signals.

## Reproduction

The executable evaluator is
`experiment/evaluation/h_stack_accuracy.py`. The machine-readable output is
external at `horizon-runs/analysis/h-stack-accuracy-mps-v1.json`; it binds all
feature, label, and reference hashes and records artifact hash
`bf3e4a70c81ec4f8ae626f375f43cce13c898f1941780828f22783bbb1981ed2`.

```sh
PYTHONPATH=. .venv/bin/python -m experiment.evaluation.h_stack_accuracy \
  --acquisition-root ../horizon-runs/calibration-acquisition-mps-v1 \
  --split configs/perception/modd2-splits.json \
  --h2-reference ../horizon-runs/compute/a07-modd2-dev-completion-20260921/development-cross-sequence-analysis/references/h2.json \
  --h3-reference ../horizon-runs/compute/a07-modd2-dev-completion-20260921/development-cross-sequence-analysis/references/h3.json \
  --h4-reference ../horizon-runs/h4-validation-20260922-v2/causal-v3/h4-reference-validated.json \
  --h5-reference ../horizon-runs/h5-tuning-20260924-v3/causal-v1/h5-reference-validated.json \
  --output ../horizon-runs/analysis/h-stack-accuracy-mps-v1.json
```

## Claim boundary and next gate

This experiment supports the claim that H5 ranked proxy misses better than the
other executable H methods under this particular collection shift. It does not
show that H5 is accurate enough for runtime authority, that its score is a
calibrated missed-obstacle probability, or that it generalizes to the sealed
held-out collections.

Before any runtime accuracy claim, freeze the present references, thresholds,
label policy, overlap audit, and evaluation code; then run the sealed
`kope71`/`kope82` comparison once. Report sequence-clustered uncertainty and
fault-stratified recall, not frame-level accuracy alone.
