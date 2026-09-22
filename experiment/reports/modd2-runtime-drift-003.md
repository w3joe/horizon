# MODD2 development runtime drift 003

## Scope and provenance

This report compares one frozen 296-frame MODD2 development sequence,
`kope81-00-00006800-00007095`, across the existing MPS/float32 extraction and
the recovered L4 CUDA/float16 extraction. It is descriptive development
evidence. It uses no calibration or held-out observations and does not fit an
equivalence tolerance.

The paired manifests contain the same 296 frame IDs and exact per-frame input
hash map (canonical SHA-256
`229b01af9524523a2ae22f3198abb71a960de46d5911204a2c68b3c08db37124`).
They bind WaSR-T source commit
`1b5360af20408e09bbf0116a0029f7e0c0800e7c`, checkpoint SHA-256
`6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef`,
and preprocessing SHA-256
`2056a83b36de33f4a8a123150cfe067238029934890bd625e4073333a90a2886`.
The CUDA manifest additionally binds frozen split SHA-256
`45a116eeeeeb55e9a2e566327c6d6045a5f8682f8ad7c5a6260ef4eaf301b425`.
Both arms report output-identical hooks and exact reset replay within their
own runtime.

External artifacts:

- MPS manifest SHA-256: `8591eb97d9ae7dc0aedb83322d94b195e7af25075d8a67cd2fc36875452fa642`
- MPS features SHA-256: `6ecbf7bf663cc2899d712f8713d937b5ac8bcaacbd19a14bf351393f74e2cacb`
- CUDA manifest SHA-256: `474ab75388a8c5d1af224e23c28a9bef68ab6474f5b8a19fbb2bd4f160b78cd9`
- CUDA features SHA-256: `f924129d05e2c0ff61a4dcbd827e66f2f5319f856ee6be4877b05c25bc89f56e`
- CUDA platform record SHA-256: `0fc9efc321ba9bf101d6deb0f03e3fddf99c76450ad44e414507c08c806db26d`
- Analysis file SHA-256: `f98a588b25b7e36e46fbf2e51fce2a47061fbedecb64cc51fb84c6ec48d9f18d`
- Analysis payload hash: `1ca18ff5a63567450cafa3dbc5a4756eaa83de01e6c8df7e8a86d0b3f254b34a`

The analysis is outside Git at
`horizon-runs/analysis/a02-modd2-runtime-drift-003.json`. The CUDA artifacts
are outside Git at
`horizon-runs/compute/a07-modd2-dev-runtime-alignment-003/`. The platform
record identifies NVIDIA L4, CUDA 12.4, cuDNN 90100, one bounded attempt, and
Modal app `ap-vGQLUF8L91XBN77IWg3xb5`.

## Paired drift results

All three captured representations changed between MPS/float32 and
CUDA/float16.

| Layer | Relative L2 median | Relative L2 p95 | Relative L2 maximum | Maximum absolute p95 |
|---|---:|---:|---:|---:|
| Encoder | 0.001197 | 0.003637 | 0.004907 | 0.005173 |
| Temporal fusion | 0.000734 | 0.009944 | 0.011112 | 0.028542 |
| Decoder logits | 0.000329 | 0.002857 | 0.003433 | 0.027863 |

The class-mask pixel disagreement fraction had median `0.00002035`, p95
`0.00017420`, maximum `0.00026449`, and mean `0.00004129`. Output-health
absolute differences were also nonzero. Their p95 values were `0.00011118`
for mean entropy, `0.00084275` for p95 entropy, `0.00005724` for mean
confidence, `0.00040618` for p05 confidence, and `0.00013225` for predicted
obstacle fraction.

The CUDA manifest reports a 34.91 ms median and 40.36 ms maximum instrumented
forward time on this sequence. Its CPU health-feature calculation reports a
108.31 ms median. The MPS manifest reports 799.92 ms and 839.92 ms for the
same forward fields and 75.37 ms for health features. These are descriptive
per-stage measurements from different hosts and precision modes, not paired
end-to-end latency measurements. In particular, the CUDA forward timing does
not establish a 20 Hz complete perception-to-gate path.

## Claim gate

The result records nonzero representation, output-health, and mask drift.
MPS-derived feature distributions and thresholds therefore cannot be treated
as interchangeable CUDA runtime references. H2--H4 calibration and any
health-policy threshold intended for the deployed CUDA/float16 path require
target-runtime CUDA artifacts with exact provenance.

The gate remains `descriptive_development_only`: one serially dependent
development sequence cannot estimate an acceptance distribution, fit an
equivalence limit, establish calibration, or support a held-out performance
claim. This result supplies the previously missing target-runtime drift
evidence but does not by itself authorize opening the sealed held-out split.

