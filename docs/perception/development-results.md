# MODD2 development evidence

The initial analysis below covers one frozen development sequence,
`kope81-00-00006800-00007095`. It contains 296 left-camera frames. The
calibration and held-out partitions remain unopened, and the 85-frame upstream
example remains an integration fixture rather than evaluation data.

## Reproduction and instrumentation

WaSR-T ran locally in float32 on an Apple M1 Max MPS device with the pinned
upstream commit `1b5360af20408e09bbf0116a0029f7e0c0800e7c` and checkpoint SHA-256
`6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef`.
The run retained raw class-ID masks, color previews, real encoder/temporal/
decoder summaries, pixelwise output-health summaries, and unlabeled spatial
probes at five frames. Its immutable manifest SHA-256 is
`8591eb97d9ae7dc0aedb83322d94b195e7af25075d8a67cd2fc36875452fa642`;
its feature cache SHA-256 is
`6ecbf7bf663cc2899d712f8713d937b5ac8bcaacbd19a14bf351393f74e2cacb`.

The three-frame, equal-warm-up comparison produced exactly equal vanilla and
instrumented logits and exact reset replay. Mean synchronized forward latency
was 795.273 ms vanilla and 796.743 ms instrumented, a 1.470 ms difference for
this small sample. Across all 296 instrumented frames, synchronized forward
latency had a 799.921 ms median and 839.923 ms maximum. Median device-to-CPU
copy, CPU resize, and health-feature times were 0.278 ms, 0.376 ms, and 75.369
ms. The recorded 770.982 ms hook-capture median includes the MPS synchronization
wait caused by transferring hooked tensors to CPU inside the forward pass. It
is not an estimate of instrumentation overhead. These are local development
measurements and do not establish 20 Hz operation or GPU performance.

The frozen manifest used generic wording that called its input an example
sequence. `manifest-amendment.json` preserves the original digest and corrects
that statement: this input is MODD2 development evidence, not held-out
evidence. No numerical result changed.

## H0-H4 development results

The H0 full-frame normalized-entropy p95 had a median of 0.159654 and a 95th
percentile of 0.600675 across the sequence. Region-aware values remain
available per frame where a declared obstacle region exists. H1 had zero
complete frames because independent occlusion and horizon signals were not
available; the implementation exposes these capabilities as missing instead
of inferring them from model output.

H2-H4 were fitted and scored on this same development cache. Their scores are
descriptive novelty or reconstruction values, without calibrated alert or
missed-obstacle-risk meaning.

| Method | Minimum | Median | Mean | 95th percentile | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| H2 diagonal Mahalanobis | 3.5655 | 7.2333 | 7.6010 | 11.9951 | 14.6538 |
| H3 PCA reconstruction | 0.003715 | 0.012332 | 0.013111 | 0.024092 | 0.032529 |
| H4 sparse-autoencoder reconstruction | 0.069645 | 0.156003 | 0.168748 | 0.290593 | 0.422852 |

The H4 fit used 296 samples and reached its 3,000-epoch cap. Loss fell from
0.992552 to 0.168764, with no dead features, but the fit is recorded as not
converged. Correlations against a bounding-box class-0 pixel proxy are retained
in the external report for diagnosis only. Serial dependence and same-cache
fitting make them unsuitable as generalization evidence. The proxy is not an
official MODD2 score.

## Offline causal controls

The fixed development probe used target indices 75, 223, and 295, a six-frame
context for each arm, and seed 0. Each intervention arm cleared temporal state
and replayed the same context. The pair-specific SAE direction produced a
larger absolute obstacle-box class-0-logit change than the random and equal-
norm controls in all three pairs. Absolute changes were small: 0.001119,
0.001281, and 0.012779 for the selected directions.

The claim gate remains closed. Features were selected independently for each
pair from adjacent-frame code differences, the SAE did not converge, only one
sequence and one random seed were used, and no held-out confirmation exists.
No maritime semantic label is assigned to any feature. `report-amendment.json`
links the earlier report to the later causal artifact without changing either
artifact's recorded results.

The external artifacts are under
`horizon-runs/compute/a07-modd2-dev-kope81-00006800/` and
`horizon-runs/compute/a07-modd2-dev-health-kope81-00006800/`. They remain
outside Git because they contain masks, feature caches, and run output.


## Additional development extraction and metadata correction

Two further frozen development sequences completed locally with the same pinned
WaSR-T source, checkpoint, float32 MPS configuration, and four CPU threads. Each
contains 521 left-camera frames, bringing completed development extraction to
1,338 frames across three sequences. Both include masks, feature caches, and five
fixed spatial probes. Calibration and held-out partitions remain unopened.

| Sequence | Frames | Median forward latency | Maximum forward latency |
| --- | ---: | ---: | ---: |
| `kope81-00-00000560-00001080` | 521 | 800.242 ms | 855.083 ms |
| `kope81-00-00004330-00004850` | 521 | 800.224 ms | 835.508 ms |

These are extraction results; H2-H4 have not yet been refitted or evaluated on
these additional caches. The launch record captures commit `cc917b2` and hashes
of every perception and neural-health Python source, checked unchanged after
each sequence. External artifacts live in `horizon-runs/compute/dev-extra-<sequence>/`.

SHA-256 identities, in the same sequence order:

- Manifest: `7d8af69f37e32819a0a87a8d8966362391e950aab0d0eb6d1bca342de4820d5d`;
  features: `97b79afc24dae1b1745c1fa6f043dd3f0f38ee8daf0d4fcd64bfcf657abf8c3d`.
- Manifest: `3f9a34713f227dd21b70f57c3cd31de6e16fc0ceab582911f5f49ff76655dfdf`;
  features: `7c1257f554ae5a126abb4f3b50704773cc94927549d42cb9f0276891d5126604`.

A subsequent code audit found two metadata/configuration issues in the original
296-frame health fit. Its references incorrectly labelled `fit_split` as
`nominal_reference`, although the input was the development sequence described
above. Its requested H4 convergence tolerance of `1e-6` was not forwarded by
`build_reference`; the actual fit used the default `1e-7`. Commit `9cb897b` fixes
both for future runs and records the effective fit parameters. Original hashed
artifacts remain unchanged. The original fit still reached 3,000 epochs without
converging; this correction does not open the causal claim gate or turn its
same-cache results into validation evidence.
