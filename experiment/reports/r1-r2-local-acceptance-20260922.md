# R1/R2 local acceptance-load result

This report records development evidence only. It does not select an architecture or unlock the
held-out split.

## Reproducible artifacts

- Plan: `experiment/manifests/r1-r2-local-acceptance-development.json`
- Plan content hash: `58ef4c18326238f5d0f40c741d19e216aa7a79da215ad1a44e81cd7d1bd273d3`
- Parent conservative plan hash: `bc2329681094bcba3f8e769ef6279549e6a9f66c6df2799e43c627acb939252e`
- External run directory: `horizon-runs/development/r1-r2-local-acceptance-v3-20260922/`
- Index SHA-256: `d8d48a6264b9bcc965a77d6fc959600198e0bfba444dd8b70d312c4a80dcdf25`
- Summary SHA-256: `73766b986336e374a5aed1f79a50f9368cef07beb0198764a09fb8711f92c684`

The local acceptance profile retains 20 ms sensing, 20 ms fusion, and 40 ms AI service before
governor-input assembly. It then assigns 20 ms to candidate service. Recovery priming, gate
validation, and simulator dispatch are synchronous local calls whose wall durations are retained in
the diagnostics. This profile supports a local integration claim only. The separately frozen
`conservative-service-v1` overload profile still fails closed because its post-assembly service is
longer than the 40 ms decision lifetime.

## Results

| Scenario | Candidate | Gate accepted | Gate rejected | Candidate deadline misses | First protected intervention | Lead time to sampled recovery boundary | ODD status |
|---|---:|---:|---:|---:|---:|---:|---|
| Crossing | A1 | 0 | 599 | 0 | none | unknown | in domain |
| Crossing | A2 | 0 | 599 | 2 | none | unknown | in domain |
| Crossing | A3 | 36 | 561 | 10 | 18.7 s | 21.3 s | in domain |
| Crossing | A4 | 27 | 571 | 3 | 20.7 s | 19.3 s | in domain |
| Crossing | A5 | 183 | 412 | 7 | 0.3 s | 39.7 s | in domain |
| Dense traffic | A1 | 0 | 749 | 0 | none | unknown | out of domain |
| Dense traffic | A2 | 0 | 747 | 2 | none | unknown | out of domain |
| Dense traffic | A3 | 0 | 742 | 6 | none | unknown | out of domain |
| Dense traffic | A4 | 0 | 747 | 2 | none | unknown | out of domain |
| Dense traffic | A5 | 0 | 743 | 6 | none | unknown | out of domain |

All ten branches had zero collision, boundary, and grounding violations. That fact is not enough to
claim success: every mission reached the predeclared horizon with the route incomplete, and the
dense-traffic truth audit exceeded the configured engineering bounds.

A1 and A2 repeatedly declared the unsafe-straight proposal clear, but the independent predictive
gate rejected it for collision-margin violations and then enforced its startup recovery interlock.
A3 and A4 produced protected recovery commands in the in-domain crossing case, but their heavier
work frequently consumed the source-validity window. A5 produced the earliest intervention and the
largest accepted-command count in that case. It also incurred seven candidate deadline misses and
many recovery-release or quarantine rejections, so the result is a development lead rather than a
scientific selection.

## Pairing and interpretation

Both five-branch episode groups share the same initial state, scenario, seed, observation schedule,
fault schedule, and AI policy identity. Candidate computation is part of the treatment. The pairing
audit therefore requires identical proposals until either a protected command is accepted or a
recorded candidate-service overrun advances one branch's scheduler. The crossing pair has one common
proposal before that first endogenous effect; the dense pair has 35. The index records each branch's
first overrun index so later divergence cannot be mistaken for exogenous nondeterminism.

The current evidence supports continuing A5 as the leading development candidate, with A3 and A4 as
comparators. A formal winner remains withheld until controller-input calibration is frozen, the
perception-health methods are evaluated at matched false-positive rate, the declared held-out study
is run, and the mission-censoring condition is resolved.
