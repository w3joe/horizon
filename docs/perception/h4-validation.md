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
   obstacle fraction across at least three selection sequences;
3. rank highest by median absolute per-sequence association before any
   intervention outcome is observed; and
4. provide at least two positive, context-valid, separated obstacle probes in
   every predeclared causal-probe sequence.

The coverage check may move to the next stability-ranked feature, but it runs
before any intervention outcome is observed. It cannot change the probe
sequences or select a feature based on causal results.

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

This opens the **development causal gate only**. H4 may emit shadow diagnostics,
but remains unavailable for live RTA health authority until calibration
extraction produces matched H0-H4 scores and thresholds, and a later sealed
held-out comparison shows that H4 improves fault detection over H1, H2, and H3
at the same false-positive target.

### Development-only tuning result (2026-09-24)

A fixed 11-candidate grid was evaluated with leave-one-development-sequence-out
validation over the same eight `kope81` sequences. The selection rule minimized
mean reconstruction MSE, preferring a smaller representation and then stronger
sparsity within one percent of the best result. It selected 96 features,
learning rate 0.003, and L1 weight 0.003. The full fit converged in 415 epochs
with no dead features and reduced mean cross-sequence reconstruction MSE by
about 6.1% relative to the prior 64-feature configuration. Its worst-sequence
MSE was about 2.1% higher, so reconstruction evidence was already mixed.

The top stability-ranked feature lacked enough positive probes in one of the
three frozen probe sequences. Under the pre-intervention coverage rule above,
the next stability-ranked eligible feature was frozen. It beat both controls in
only 11 of 18 trials (61.1%; one-sided sign-test p = 0.240) and therefore failed
the development causal gate. This negative result is retained. It demonstrates
that improved SAE reconstruction is not evidence of a stronger causal monitor.
The validated 64-feature reference remains the best-supported development H4
candidate; the tuned 96-feature reference is ineligible.
