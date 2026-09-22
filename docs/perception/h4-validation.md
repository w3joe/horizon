# H4 validation protocol

H4 is a sparse-autoencoder (SAE) monitor over the spatial mean of all 2,048
channels in WaSR-T's final encoder block. It is a perception-health input. It
does not identify metric obstacles, certify free water, or issue vessel
commands.

## Development protocol

The reference fit uses 40 uniformly spaced frames from each of the eight frozen
MODD2 `kope81` development sequences (320 frames total). The deterministic
full-batch Adam fit stops only when relative objective improvement remains below
the declared tolerance over the full patience window. A reference that reaches
its epoch cap, has too many dead features, or lacks causal controls remains
unusable.

Feature selection and causal probing use disjoint development sequences. A
candidate feature must:

1. be non-degenerate in the selection data;
2. show at least 80% correlation-sign agreement with WaSR-T's predicted
   obstacle fraction across at least three selection sequences; and
3. rank highest by median absolute per-sequence association before any
   intervention outcome is observed.

The causal arm then ablates that frozen SAE decoder direction on two separated,
high-activation, obstacle-annotated frames from each of three other development
sequences. Every target uses three seeds and identical six-frame temporal
context. Each replay resets WaSR-T state and compares the selected direction
against a random direction and equal-norm noise.

The predeclared claim gate requires all of the following:

- converged reference with at least 296 fit samples;
- no more than 10% dead SAE features;
- at least 12 controls, three sequences, and three seeds;
- the selected direction beats both controls in at least 75% of trials; and
- a one-sided sign-test p-value no greater than 0.05.

Passing this gate supports only the bounded claim that the selected internal
direction has a repeatable effect on obstacle logits. Calibration must still
show matched-false-positive-rate fault detection, and held-out evaluation must
show benefit over H1-H3 before H4 can be selected or enabled at runtime.

## Recorded development evidence

The initial 16-feature SAE converged, but variance-selected directions beat both
controls in only 9 of 18 trials (50%, one-sided sign-test p = 0.593). That
reference is retained as a failed development attempt and is not eligible for
runtime use. The result motivated disjoint feature selection based on stable
association with the model's obstacle output; this revision remains development
iteration and therefore cannot be reported as held-out confirmation.

The revised 64-feature reference used the disjoint protocol above. It converged
in 455 epochs, reduced the objective from 0.9815 to 0.0398, and produced no dead
features. The frozen feature direction beat both controls in 15 of 18 trials
(83.3%) across three probe sequences and three seeds. The predeclared one-sided
sign test was 0.00377. The repository's independent experiment evaluator
accepted every development causal-claim check.

This opens the **development causal gate only**. H4 remains unavailable to the
live RTA path until the ongoing calibration extraction produces matched H0-H4
scores and thresholds, and a later sealed held-out comparison shows that H4
improves fault detection over H1, H2, and H3 at the same false-positive target.
