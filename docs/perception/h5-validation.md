# H5 temporal-feature validation

H5 is a hard-TopK sparse autoencoder over the spatial mean of all 2,048 channels
in WaSR-T's temporal-fusion module. It adds a deterministic temporal triplet
loss: adjacent observations from one sequence are positive pairs, while the
same sampled position in the next development sequence is the negative. H5 is
a perception-health diagnostic; it does not identify safe water, provide metric
geometry, or issue vessel commands.

## Frozen development design

Eight `kope81` development sequences supply 40 uniformly sampled frames each.
Five sequences fit candidate configurations and three disjoint sequences score
validation reconstruction and temporal separation. The fixed grid varies SAE
width, TopK sparsity, and temporal weight. Eligible temporal candidates must
converge, have no more than 10% dead features, and lie within 10% of the best
validation reconstruction error; selection then maximizes the ratio of
cross-sequence to adjacent-frame code distance.

The selected configuration is refitted with seeds 0, 1, and 2 before causal
outcomes are observed. The later seed-zero feature selection is reproducible
only if its nearest decoder direction in both replicas has cosine at least 0.5
and its activation trace has Pearson correlation at least 0.6. This is a
feature-specific claim: the full dictionaries remain substantially unstable.

Feature selection then uses five development sequences and requires stable
association with WaSR-T's predicted obstacle fraction. The selected feature
must also provide two positive, separated, context-valid obstacle probes in
each of three disjoint sequences. Causal controls replay identical six-frame
contexts and compare the selected temporal-fusion decoder direction against a
random direction and equal-norm noise for three seeds.

## Result (2026-09-24)

Two earlier development runs exposed weak whole-dictionary stability and led to
the feature-specific cross-seed gate above. The result below is the subsequent
development rerun under that revised protocol. It is not a sealed confirmatory
evaluation; the thresholds and procedure must remain frozen for independent
held-out work.

The selected candidate has 128 latents, TopK 16, and temporal weight 0.1. It
converged in 622 epochs on 320 frames, with 3/128 dead features and final
reconstruction MSE 0.01667. Against the capacity-matched zero-temporal-weight
baseline, validation reconstruction MSE improved from 0.9533 to 0.8295 and the
temporal separation ratio improved from 6.12 to 6.89.

The complete dictionary was not seed-stable (mean nearest decoder cosine 0.425),
but five seed-zero features passed the frozen feature-specific rule. The
independently selected causal feature, index 55, was among them: its minimum
decoder cosine was 0.528 and minimum activation correlation was 0.667 across
the two alternate seeds. It beat both causal controls in 18/18 trials across
three probe sequences and three seeds, giving a one-sided sign-test
`p=3.81e-6`. H5 therefore passes its **development feature-reproducibility and
causal gate**.

This does not make H5 deployment-ready. All model selection and causal evidence
come from the `kope81` development collection; inputs are spatially averaged;
there is no calibrated fault threshold, sealed held-out H1–H5 comparison,
spatial causal localization, collateral-effect study, or target-runtime latency
qualification. Runtime use remains fail-closed and shadow-only until those
independent gates pass.
