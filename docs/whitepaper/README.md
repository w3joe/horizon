# Horizon white paper

The consolidated paper is **six pages in ACM `acmart` two-column (`sigconf,nonacm`) format**. It introduces gate-owned policy authorization (A6) and the temporal sparse perception monitor (H5) before using their code names. It combines the A-series architecture comparison, H-series detection results, ROC/precision–recall curves, controlled camera failures, GPU methodology, and the A6/H5 implementation rationale. The Horizon GitHub link appears in the abstract. The bibliography contains eight external research papers and two official IMO instruments, with no project self-citations. The subtitle has been removed.

- Editable source: [horizon-runtime-assurance-white-paper.tex](../../whitepaper/horizon-runtime-assurance-white-paper.tex)
- Canonical PDF: [horizon-runtime-assurance-white-paper.pdf](../../whitepaper/horizon-runtime-assurance-white-paper.pdf)
- Delivery copy: [output/pdf/horizon-runtime-assurance-white-paper.pdf](../../output/pdf/horizon-runtime-assurance-white-paper.pdf)
- Curve reconstruction: [build_curves.py](build_curves.py) and [curve-summary.json](../../whitepaper/figures/curve-summary.json)
- Archived-record timing analysis: [whitepaper-timing-summary.json](whitepaper-timing-summary.json)

The shorter A5/A6 and H1–H5 papers in this directory are historical companion documents. The consolidated paper reflects the 25 September enforcement and freeze-guard implementation.

## Evidence index

These evidence IDs identify the project measurements; they are separate from the external-paper bibliography in the PDF. Raw data and model artifacts remain in the external sibling `horizon-runs` and `horizon-data` directories.

| Evidence ID | Repository report / implementation | Recorded experiment |
|---|---|---|
| 1 | [A1–A5 analysis](../../experiment/reports/a1-a5-development-20260922.md) | `development/a32-a1-a5-30s-20260922`; revision `28eb923`; 60 branches |
| 2 | [Nonzero-delay comparison](../../experiment/reports/r1-r2-local-acceptance-20260922.md) | `development/r1-r2-local-acceptance-v3-20260922`; ten branches |
| 3 | [A6 enforcement results](../../experiment/reports/a6-enforcement-development-20260925.md), [policy implementation](../../services/assurance/docs/a6-policy-enforcement.md) | `development/a6-enforcement-counterfactual-20260925-v5`; nine branches; `development/a6-a32-enforcement-30s-20260925-v2`; 12 episodes |
| 4 | [H-stack accuracy](../perception/h-stack-accuracy.md), [evaluator](../../experiment/evaluation/h_stack_accuracy.py) | `analysis/h-stack-accuracy-mps-v1.json`; MPS FP32; 10,110 paired records |
| 5 | [H5 validation](../perception/h5-validation.md), [H4 validation](../perception/h4-validation.md) | `kope81` development training, feature selection, and causal controls |
| 6 | [Mock failure](../../experiment/reports/h5-mock-failure-20260925.md), [freeze guard](../../experiment/reports/h5-frozen-feed-20260925.md), [broader rerun](../../experiment/reports/h5-freeze-accuracy-20260925.md) | `development/h5-mock-failure-20260925-v2`; 180 actual-model frames; `analysis/h5-freeze-accuracy-20260925-v1`; cached activations and decoded images |
| 7 | [CUDA/MPS drift](../../experiment/reports/modd2-runtime-drift-003.md), [L4 entrypoint](../../services/perception/modal_wasrt_modd2_dev.py), [job specification](../../infra/modal/jobs/a07-modd2-dev-runtime-alignment-003.json) | `compute/a07-modd2-dev-runtime-alignment-003` and `compute/a07-modd2-dev-kope81-00006800`; 296 identical inputs |
| 8 | [L4 fixture specification](../../infra/modal/jobs/a07-wasrt-sequence-003.json) | `compute/a07-wasrt-sequence-003`; 85 integration frames |
| 9 | [A6 shadow evaluation](../../experiment/reports/a6-a32-shadow-development-20260924.md) | `development/a6-a32-shadow-30s-20260924-v2`; 1,788 assessments |
| 10 | [Paired response results](../../experiment/reports/h5-control-response-20260925.md), [protocol](../../experiment/protocol/h5-control-response.md) | `development/h5-control-paired-20260925-v1`; 24 episodes, cached H5 tapes, A6 disabled in both arms |

## How the L4 experiments were run

The retained 296-frame job was launched from clean revision `be894dda1dfbff2dbe299b94331adc179667eece` through the central compute controller. Its entrypoint hydrated the pinned source, checkpoint, frozen split, and exactly 296 left-camera JPEGs into one bounded Modal container. The allocation was one NVIDIA L4, four CPU cores, 16 GiB host memory, one attempt, and no automatic retries.

PyTorch 2.5.1+cu124 and torchvision 0.20.1+cu124 used CUDA 12.4/cuDNN 90100, FP16, and batch size one. The sequential runner reset temporal state, checked vanilla/instrumented output equality and reset reproducibility, then retained every frame's model output and encoder/temporal/decoder summaries. The three-frame instrumentation comparison used one identical warm-up frame per arm. The sequence statistics retain all 296 extraction frames, including the first temporal cold-start frame.

Input preprocessing decoded RGB, resized to 512 × 384, and applied ImageNet normalization. The retained model pins are:

- WaSR-T source commit: `1b5360af20408e09bbf0116a0029f7e0c0800e7c`
- Checkpoint SHA-256: `6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef`
- Preprocessing SHA-256: `2056a83b36de33f4a8a123150cfe067238029934890bd625e4073333a90a2886`
- L4 manifest SHA-256: `474ab75388a8c5d1af224e23c28a9bef68ab6474f5b8a19fbb2bd4f160b78cd9`
- L4 features SHA-256: `f924129d05e2c0ff61a4dcbd827e66f2f5319f856ee6be4877b05c25bc89f56e`
- L4 platform record SHA-256: `0fc9efc321ba9bf101d6deb0f03e3fddf99c76450ad44e414507c08c806db26d`

The frozen platform record contains the source-tree, entrypoint, job-specification, and output hashes. Outputs were 296 raw class masks, 296 previews, 296 feature records, and spatial probes at five fixed frame positions. The L4 job measured extraction and backend drift. H-series proxy accuracy and actual-model injected-camera experiments used the separately recorded MPS execution; control experiments used the simulator and local host. No new GPU experiment was run to produce this revision.

## Timing derivation

`whitepaper-timing-summary.json` records the source manifest/cache hashes and all derived values. The 296-frame CUDA and MPS feature digests were checked against the archived drift report before analysis.

For each JSONL frame record:

```python
forward = row["inference_ms"]
post = row["postprocessing_ms"]
subtotal = forward + post["device_to_cpu"] + post["cpu_resize"] + post["health_features"]
```

The subtotal includes the forward pass and the recorded CPU stages. Activation-hook capture is already inside `inference_ms`; adding it again would double-count. Decode, preprocessing, model load, output writing, publication, H5 SAE scoring, fusion, A6, and actuator dispatch are outside this subtotal.

For sorted values `a` and `position = (len(a) - 1) * 0.95`, p95 linearly interpolates between the adjacent array entries. p50 is the ordinary median and maximum is the observed maximum. Subtotal quantiles are taken after per-frame summation. They are not sums of marginal quantiles. No stage measurement is replaced with a modeled service interval.

The H5 confusion matrix is TP 7, FN 26, FP 193, TN 9,884. The corresponding positive counts are H0 3, H2 0, H3 1, H4 0, and H5 7, each out of 33. Fault-window coverage (18/20 frozen frames) is separate from this missed-obstacle denominator.

## ROC and precision–recall figures

The ROC sweep is appropriate for continuous H0/H2/H3/H4/H5 anomaly scores. A6 emits deterministic authorization findings, so no A6 ROC is inferred from these studies. A precision–recall panel accompanies ROC because positive prevalence is only 33/10,110 (0.326%). The black markers retain H5's original operating point: 7 true positives, 193 false positives, 21.21% recall, and 3.50% precision.

Rebuild the curves from existing external caches, without video inference or threshold fitting:

```sh
OPENBLAS_NUM_THREADS=1 .venv/bin/python docs/whitepaper/build_curves.py
```

The builder verifies all 48 evaluation-input hashes and four reference hashes against the original accuracy artifact. It uses the four `kope75` sequences and six arms, excluding each first frame exactly as before. It reproduces all five original confusion matrices and AUROC values, and validates ROC area independently against the archive's rank-based implementation. Only the calibration evaluation group is read; neither threshold-selection data nor sealed held-out examples are rescored.

Equal scores enter each threshold together. ROC is integrated with trapezoids; AP sums each recall increment times the precision at that threshold. The old evaluator averaged precision at each positive rank, with input-order tie breaking. Grouped AP differs slightly for H2/H3/H4: H2 rounds to **0.0043**, compared with the archive's **0.0042**; H3 and H4 keep the same four-decimal display. H5 AP remains **0.0515**, and all AUROCs and operating-point results are unchanged. The paper consistently uses grouped AP in the table and curve legends. PR connecting lines are visual threshold trajectories; their trapezoidal area is not reported as AP.

Exported CSVs retain all curve corners; only interior points on exactly horizontal or vertical segments are removed. The PR plot uses a logarithmic precision axis, labels that scale explicitly, and omits zero-precision points outside its range. The dashed PR reference is prevalence; the dashed ROC reference is chance ranking. The neural-score curves do not combine the discrete freeze guard with A6 or imply joint navigation accuracy.

## Maritime policy sources

Section 2.3 maps the implemented checks to [IMO COLREGs](https://www.imo.org/en/about/conventions/pages/colreg.aspx) and the [2026 MASS Code, resolution MSC.595(111)](https://wwwcdn.imo.org/localresources/en/MediaCentre/Documents/MSC%20111-22-Annex%2016%20(Secretariat).pdf). The code mapping was checked against [the rule evaluator](../../services/assurance/horizon_assurance/policy_shadow.py) and [the enforcement wrapper](../../services/assurance/horizon_assurance/policy_enforcement.py).

| Source | Connection to implementation |
|---|---|
| COLREG Rule 5 | Lookout availability and freshness evidence |
| COLREG Rule 6 | Configured speed and stopping-clearance checks |
| COLREG Rules 7–8 | Risk doubt and early, substantial action proxies |
| COLREG Rules 13–17 | Encounter classification and give-way/stand-on evidence |
| MASS Code sections 8.3–8.4, 8.7, 9.10 | Design reference for scope, fallback, supervision, and decision logs |

Numeric margins are bundle engineering parameters. The MASS source role is `design_assurance_reference`; a separate `runtime_authority` pin is required. The paper describes the supported daylight, clear-visibility government-USV profile rather than adding unimplemented local port rules.

## External research cited

- [Alshiekh et al., 2018: Safe Reinforcement Learning via Shielding](https://doi.org/10.1609/aaai.v32i1.11797) — independent restrictive checking.
- [Ames et al., 2017: Control Barrier Function Based Quadratic Programs for Safety Critical Systems](https://doi.org/10.1109/TAC.2016.2638961) — barrier-based physical correction.
- [Fawcett, 2006: An introduction to ROC analysis](https://doi.org/10.1016/j.patrec.2005.10.010) — ROC interpretation.
- [Gao et al., 2024: Scaling and evaluating sparse autoencoders](https://arxiv.org/abs/2406.04093) — controlled sparsity in autoencoders.
- [Marks et al., 2024: Sparse Feature Circuits](https://arxiv.org/abs/2403.19647) — causal feature intervention.
- [Saito and Rehmsmeier, 2015: The Precision-Recall Plot Is More Informative than the ROC Plot When Evaluating Binary Classifiers on Imbalanced Datasets](https://doi.org/10.1371/journal.pone.0118432) — rare-event evaluation.
- [Wabersich and Zeilinger, 2021: A predictive safety filter for learning-based control of constrained nonlinear dynamical systems](https://doi.org/10.1016/j.automatica.2021.109597) — predictive safety filtering.
- [Žust and Kristan, 2022: Temporal Context for Robust Maritime Obstacle Detection](https://arxiv.org/abs/2203.05352) — the underlying WaSR-T video model.

These citations support related methods. Horizon's numerical results remain its own recorded measurements, with provenance in the evidence index above.

## Build and review

From the repository root, with Tectonic and Poppler installed:

```sh
mkdir -p tmp/pdfs/whitepaper-build output/pdf
tectonic -X compile whitepaper/horizon-runtime-assurance-white-paper.tex \
  --outdir tmp/pdfs/whitepaper-build --keep-logs
pdfinfo tmp/pdfs/whitepaper-build/horizon-runtime-assurance-white-paper.pdf
pdftoppm -r 150 -png \
  tmp/pdfs/whitepaper-build/horizon-runtime-assurance-white-paper.pdf \
  tmp/pdfs/whitepaper-build/page
```

Check all six rendered pages and the compiler log, then copy the reviewed PDF to both canonical and delivery locations. The ACM class supplies title/author treatment, column geometry, headings, citations, bibliography, and page numbering; no ACM conference acceptance or publication is implied by the template.

## Verification for this revision

The final PDF contains six ACM pages. Every page was rendered and visually reviewed; compilation reported no overfull boxes or unresolved citations. All ten external bibliography entries are cited and contain clickable source links; the project link is clickable on page one. Curve reconstruction verifies the 48 cached input hashes, four reference hashes, five original confusion matrices, and five AUROCs. The plotted/table AP values use grouped score ties consistently. The canonical and delivery copies match byte for byte. `./scripts/check.sh` passed with **629 tests passed, 1 skipped**, plus contract, TypeScript, and console-build checks.
