# R1/R2 validity-gate evidence

This report records development-only validity work. It does not select an
architecture and it does not open calibration or held-out evidence.

## R1 execution

Two complete-horizon A1 branches were run with the frozen
`conservative-service-v1` discrete-event profile and saved outside Git under
`horizon-runs/development/r1-r2-completion-20260922/`.

| Case | Bundle SHA-256 | Terminal outcome | ODD audit | Recovery reference | Intervention lead time |
|---|---|---|---|---|---|
| `crossing-recoverable-v1`, seed 1000 | `5f4c6ffd57970bc06cb1939a3b46682afdeba51db07ab202b0a70dc32f4139ad` | `route_incomplete` at the 120 s scenario horizon | 0 bound violations | independent unprotected finite-library reference, closest-approach window | unknown: no accepted intervention |
| `dense-traffic-v1`, seed 1001 | `fe62a0df165e51a93271c2256727564faca3c3cf6a927c754662c768e5740f50` | `route_incomplete` at the 150 s scenario horizon | 0 bound violations | independent unprotected finite-library reference, closest-approach window | unknown: no accepted intervention |

Both bundles contain terminal lineage, all seven declared service stages,
truth-only ODD audits, source/fault identities, and the candidate-independent
recovery-reference contract. The recovery reference is explicitly labelled a
finite-library sample, not a viability-kernel proof.

R1 is **not passed** yet. The conservative timing profile produced autonomy
proposal traces but no assembled governor input: advancing the plant between
proposal construction and `FusionEngine.assemble` changes the current fusion
snapshot identifier, and the fusion boundary correctly rejects the stale
proposal origin. This is a meaningful fail-closed result, not a safe outcome.
The next implementation task is a bounded queued-fusion snapshot interface so
the AI can consume a specific, timestamped completed fusion snapshot through
the declared service stages. Only then can the cases establish accepted
intervention lead time under nonzero front-end service delays.

## R2 freeze

The frozen development-completion plan is
`experiment/manifests/r1-r2-complete-development.json`, content hash
`bc2329681094bcba3f8e769ef6279549e6a9f66c6df2799e43c627acb939252e`.
The split partition is `experiment/manifests/r2-frozen-splits.json`, content
hash `c6c77cf480819897a1adc2c61b66807b816a9dbeb5ad1f41e0212c85f3c8916c`.

They pin candidate versions, safety selection rule, contracts, scoring
protocol, scenario/geography/traffic bundles, source-provenance split rule,
the two R1 scenario identities, command-library recovery contract, and all
seven stage latency assumptions. The partition unit keeps simulator family and
seed bands, recorded sessions, AIS snapshot captures, and source provenance
groups indivisible across development, calibration, and held-out splits.

R2 is ready for downstream consumers only at the protocol level. The R4/R6
runner must verify the listed artifact hashes before it expands a study, and a
held-out study still remains blocked on R3 calibration artifacts.
