# A6 enforcement development results — 2026-09-25

Implemented gate-owned authorization of A5 normal commands, with explicit
recovery/minimum-risk policy overrides. This supersedes shadow-only product
behavior; the earlier shadow evaluator and artifacts remain reproducible.

## Functional counterfactuals

Command: `PYTHONPATH=.:packages/contracts/python:services/assurance:services/gate:services/simulator:services/collector:services/fusion .venv/bin/python scripts/benchmark_a6_enforcement.py --output ../horizon-runs/development/a6-enforcement-counterfactual-20260925-v5`

Normal transit, seeds 1000–1002, 5 seconds per branch, three arms (nine branches).
Every arm produced 72 A5 pass decisions. Synthetic engineering parameters:
permissive speed cap 6 m/s; veto cap 1 m/s.

| Arm | Normal commands accepted | Recovery commands accepted | Watchdog commands | A6 authorizations / initial vetoes |
|---|---:|---:|---:|---:|
| A5 baseline | 72 | 0 | 69 | n/a |
| A5 + A6 permissive | 72 | 0 | 69 | 72 / 0 |
| A5 + A6 speed veto | 0 | 72 | 69 | 0 / 3 |

The restrictive arm vetoed one command per seed, then retained the recovery
latch for the remaining 69 decisions. Thus 72 substituted commands are not 72
independent policy evaluations. All arms had zero collisions and zero command
trace mismatches. Each arm also had 72 command-expiry events: the sparse fresh
proposal cadence causes expiry/watchdog turnover. These are functional tests,
not evidence of uninterrupted autonomy or production timing readiness.

Timing profile: `local-acceptance-load-v1` (nonzero sensing/fusion/AI, 20 ms
candidate, zero modeled gate service; actual gate work recorded separately).
Source pins, lookout facts, context and evidence provider are synthetic and
explicitly excluded from the operational file loader.

Summary: `../horizon-runs/development/a6-enforcement-counterfactual-20260925-v5/summary.json`

SHA-256: `0f63c151344bf6f576257d38efc0302c511f0df127987d9f95223dbbf32543f1`

## Original A1–A5 design rerun with A6 enforcement

Manifest: `experiment/manifests/a6-a32-enforcement-development.json`.
Output: `../horizon-runs/development/a6-a32-enforcement-30s-20260925-v2/`.
Same four scenarios, three seeds each, source hashes, AI policies, fixed health,
30-second horizon and idealized timing profile as the historical A32 design.

- 12 episodes, 1,788 A5 decisions: 885 recovery, 885 minimum-risk, 18 invalid.
- Gate accepted 1,770; rejected 18 (deadline invalidity).
- A6 normal authorizations / vetoes: 0 / 0. No valid permissive A5 commands
  reached policy evaluation; all normal submission acceptances used recovery
  authority and were explicitly tagged as emergency policy overrides.
- 2,664 total emergency actuation reservations, including watchdog actions.
- Zero trace mismatches; zero unsafe/stale accepted commands in the scorer.
- Nine declared-recoverable episodes remained safe in the scorer; all three
  initially-unrecoverable episodes were unsafe. All missions were censored.

Index SHA-256: `ffe5043f88f1993a8fe1b4623103d462269d9e01aeef457cac80b85419e13412`.

The recovered clock-domain bug in gate recovery priming is fixed in this run.
The checker now receives a host deadline computed from the remaining injected
clock budget. Results are therefore current-code reruns of the same design,
not bitwise historical replays. Wall-sensitive deadline counts must not be
attributed solely to A6.

## Verification and deployment limits

`./scripts/check.sh`: **600 passed, 1 skipped**, plus schema/generated-contract,
TypeScript and console-build checks. Includes 27 focused enforcement tests and
a multi-process test proving missing policy cannot grant normal autonomy.

The product gate and launcher default to enforcement. The local adapter supports
recorded, source-byte-verified bundles and snapshot-bound evidence with optional
command-independent conservative braking data. Without configured operational
sources and live policy facts, normal autonomy is withheld. No complete naval
policy corpus, vessel approval, or legal compliance is asserted. The first
release enforces a single A5 command using veto/recovery, not alternate-maneuver
search. See `services/assurance/docs/a6-policy-enforcement.md`.

Earlier exploratory counterfactual runs v1–v3 remain outside Git: v1 exposed
the recovery clock bug and an expired policy authorization under the 20+20 ms
profile; v2/v3 rejected incompatible timing configuration. v4 established the
functional result; v5 is the retained final evidence for the current gate path.
