# R4/R6 execution readiness — 2026-09-22

## Status

No A1--A5 winner is selected.  This is an execution-readiness record, not a
substitute for held-out evidence.

The previous `stage1-heldout-template.json` is correctly blocked: it has
placeholder provenance strings, `protocol_frozen: false`, and no calibration
artifact.  It must not be passed to `run-adapter`.

`experiment freeze-a1-a5-heldout` now generates a replacement only after it is
given an actual frozen calibration artifact.  The generated plan has five
headline cells, 240 independently seeded episode keys per cell (1,200 total),
at least 30 seeds in every headline cell, per-seed sensor/fault identities
checked by the production adapter, source/config/code hashes, and the
all-stages-20ms timing profile.  It expands to 6,000 candidate branches.

Use this exact sequence after R3 supplies its artifact:

```sh
RUN_ROOT=/Users/w3joe/Desktop/2026_sdth/horizon-runs/heldout/a1-a5-r4-20260922
mkdir -p "$RUN_ROOT"
.venv/bin/python -m experiment freeze-a1-a5-heldout \
  --calibration-artifact /absolute/path/to/frozen-calibration.json \
  --study-id a1-a5-r4-20260922 \
  --output "$RUN_ROOT/study-plan.json"
.venv/bin/python -m experiment plan --study-plan "$RUN_ROOT/study-plan.json"
.venv/bin/python -m experiment run-adapter \
  --study-plan "$RUN_ROOT/study-plan.json" \
  --entrypoint experiment.harness.closed_loop:run_assured_episode \
  --run-id a1-a5-r4-20260922 \
  --max-simulation-time-s 180 \
  --output "$RUN_ROOT/full-pipeline"
.venv/bin/python -m experiment controller-evidence \
  --index "$RUN_ROOT/full-pipeline/index.json" \
  --output "$RUN_ROOT/controller-evidence.json"
```

The runner refuses an existing output path.  The plan and output directory must
therefore be retained as immutable run evidence.  It also records the
study-plan digest and frozen status in `index.json`; the selection checker
rejects a development, mixed-split, or unpinned index.

## Local scale audit

One full-duration crossing branch (120 s simulation, A1, all-stages-20ms)
completed in 3.59 s wall-clock on the local machine and produced 6,001 truth
frames.  A sequential 6,000-branch run would therefore be at least about six
hours before accounting for the slower predictive candidates and I/O.  It was
not started because the calibration gate remains unsatisfied.  No partial
development run is represented as held-out evidence.

The current full-pipeline adapter has no controller-isolation replay entrypoint
that can consume a separately frozen evidence/proposal tape.  The full-pipeline
study above is ready after calibration.  The controller-isolation study remains
a required implementation gate: it must save one independently generated
unprotected evidence/proposal tape per episode and replay that exact tape into
every candidate, with `physical_outcomes_scored: false`.  A branch whose own
commands affected its tape cannot claim controller isolation.

## Selection rule

The selection checker now enforces the frozen ordering:

1. Every candidate must have zero preventable violations, zero unsafe/stale
   gate acceptances, complete traces, complete assumption audits, no censored
   missions, known recovery timing, and no deadline miss in a declared
   acceptance-load trace.
2. Only surviving candidates enter the Pareto comparison over mission cost,
   false intervention rate, minimum hull clearance, and intervention lead
   time.
3. A unique frontier member is selected.  A frontier tie remains explicitly
   unselected; the report does not invent a winner.

## R6 Singapore coverage

Coordinator-directed ownership exception: the R6 fixtures cross the normal
A02/A03 ownership boundary because the experiment required reproducible
simulator scenarios.

The hash-pinned synthetic Singapore set now provides five offline cells:

| Cell | Scenario | Evidence condition |
| --- | --- | --- |
| AIS absent | `singapore-ais-absent-v1` | AIS is dropped for all AIS-equipped contacts; radar remains available. |
| Stale/dropout | `singapore-traffic-mirror-synthetic-v1` | A stale report and temporary AIS dropout occur with the independent radar/camera channels. |
| Contradictory | `singapore-ais-contradictory-v1` | AIS position is spoofed while the radar observation remains independent. |
| Overloaded | `singapore-ais-overloaded-v1` | A compact synthetic source specification has 60 candidates and deterministically bounds the simulated tracked set to 50. |
| Radar-only | `singapore-radar-only-v1` | A radar-visible contact has no AIS profile while a separate AIS dropout occurs. |

The overload source is a deliberately narrow, byte-hashed
`parallel_lanes_v1` synthetic fixture.  It is neither decoded AIS nor a claim
about actual Singapore traffic density.  Every cell is scored only against
private deterministic simulator truth.

After the same R3 calibration gate, freeze and execute R6 with:

```sh
RUN_ROOT=/Users/w3joe/Desktop/2026_sdth/horizon-runs/heldout/r6-singapore-20260922
mkdir -p "$RUN_ROOT"
.venv/bin/python -m experiment freeze-r6-singapore-heldout \
  --calibration-artifact /absolute/path/to/frozen-calibration.json \
  --study-id r6-singapore-20260922 \
  --output "$RUN_ROOT/study-plan.json"
.venv/bin/python -m experiment run-adapter \
  --study-plan "$RUN_ROOT/study-plan.json" \
  --entrypoint experiment.harness.closed_loop:run_assured_episode \
  --run-id r6-singapore-20260922 \
  --max-simulation-time-s 180 \
  --output "$RUN_ROOT/full-pipeline"
```

Live AIS is never a scoring source.

### Bounded full-pipeline development smoke

Run bundle: `/Users/w3joe/Desktop/2026_sdth/horizon-runs/development/r6-singapore-full-pipeline-smoke-20260922`.
The committed manifest `r6-singapore-development-smoke.json` ran all five A1--A5
implementations across all five Singapore cells for 25 real full-pipeline
branches, with the conservative all-stages-20ms timing profile and a 20 s
simulation bound.  The output hashes are:

- `index.json`: `8447f01e5ffdea2c337d1e193cb37399fb41f7110820a6cc6b801d57d31b39ea`
- `summary.json`: `9bebf2db964ca0c48ca2dfdc2e6a1a7aa2a7be61902f625030af981a1ff19044`

The simulator loaded the 60-candidate/50-tracked overload fixture and scored
all 25 branches against private truth.  The bounded run had zero collisions,
groundings, and boundary events, but all 25 missions were censored and no
candidate decision reached the gate.  This reproduces R1's current
fail-closed fusion snapshot-origin blocker.  It is evidence that the fixtures
and protected pipeline execute; it is explicitly not controller-performance,
mission-completion, or held-out selection evidence.
