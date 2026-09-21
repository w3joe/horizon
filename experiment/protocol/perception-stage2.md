# Perception stage 2: runtime alignment and calibration

## Frozen development comparison

The next extraction uses the 296 left-camera frames from
`kope81-00-00006800-00007095`, already assigned to development. The paired arms are the existing
MPS/float32 artifact and one CUDA/L4/float16 artifact produced from identical ordered frame hashes,
model source, checkpoint, and preprocessing. `configs/perception-stage2.json` freezes those
identities, analysis seed, compared layers, and health fields before the CUDA artifact is scored.

`perception-drift` rejects a changed frame set, split, checkpoint, preprocessing function, hook
fidelity check, temporal reset replay, feature dimension, or nonfinite value. It reports paired
activation, output-health, and optional class-mask differences. It does not fit a numeric
equivalence tolerance from one serially dependent sequence. Any nonzero representation drift means
H2-H4 references must be rebuilt on the target runtime before calibration; it is not rounded away.
CUDA and MPS latency remain separate descriptive measurements because device and precision differ.

The finite extraction request is one L4, four physical CPU cores, 16 GiB RAM, one container, one
attempt, no retries, 900 seconds startup, 1,200 seconds execution, 1,800 seconds controller time,
296 input frames, and 100 MiB output. At the published project rates this resource request is
USD 1.1172 per running hour; the 30-minute ceiling is USD 0.5586 before transfer/storage, with a
conservative all-in reservation cap of USD 1.50. Only the coordinator may reserve or launch it.

The GPU smoke completed 85 integration frames on an NVIDIA L4. Its instrumented forward median was
32.147 ms and maximum was 34.601 ms; CPU health-feature postprocessing had a 62.349 ms median. This
is useful for bounding the next job, but it is neither a 20 Hz end-to-end claim nor evaluation data.

## Calibration record

Calibration remains sequence-disjoint: `kope67` and `kope75` contain 13 declared sequences and
6,568 left-camera frames. The GPU extraction must not read annotations. A later CPU label pass may
join frozen masks to annotations only after the cross-split near-duplicate audit, target-runtime
reference artifacts, label policy, and controlled-perturbation hashes are fixed.

Every calibration arm records the dataset, collection group, sequence, frame and source-frame hash;
analysis seed; condition and perturbation-parameter hash; annotation and label-policy hash; method;
score; and H2-H4 reference hash. All eligible methods must receive the same nominal and controlled
arms. A controlled arm without its nominal parent is rejected. Unevaluable samples fail the run
instead of disappearing from a denominator.

Thresholds detect the five declared controlled stresses at the same empirical 5% nominal false
alarm target. This measures perturbation detection. It does not turn a reconstruction score into
distance or safety. The separate missed-obstacle label is a Horizon proxy: a frame is marked missed
when at least one finite MODD2 bounding box contains zero predicted class-0 pixels after coordinate
projection. This permissive proxy can undercount misses and is not an official MODD2 metric.
Calibration associations remain `unknown` risk until frozen heldout validation.

H0 may calibrate once its score rows are available. H1 remains blocked while its independent
horizon and occlusion capabilities lack a predeclared complete score. H2 and H3 require target
CUDA/float16 development references. H4 additionally requires a converged reference and at least
12 state-reset intervention pairs spanning three development sequences and three seeds. Its
selected direction must beat both random-direction and equal-norm controls in at least 75% of
pairs, with a one-sided exact sign-test p-value at most 0.05. The current H4 reference reached the
3,000-epoch cap without convergence, so it fails before causal evidence is considered.

Heldout `kope71` and `kope82` remain sealed until the overlap audit, target-runtime references,
calibration artifacts, operating scope, method versions, and selection rule hashes are frozen.
Failures discovered later stay in the heldout denominator and cannot trigger threshold changes.

## Controller study gate

`controller-evidence` reads the external closed-loop run index and reports readiness before Pareto
analysis. It requires identical episode sets across candidates, independent recovery boundaries
for declared-recoverable cases, complete gate and trace evidence, actual configured engineering
assumption audits, and observed candidate compute opportunities. Bound violations remain in the
outcome denominator and are also reported as out-of-domain strata. Initially-unrecoverable cases
remain visible in a separate stratum. Zero deadline misses with zero decisions is `unknown`, and
candidate compute time is never relabeled end-to-end latency. The command returns no architecture
ranking; a predeclared Pareto analysis can run only after all candidate evidence gates pass.
