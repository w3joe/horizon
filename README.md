# Horizon

Horizon is a private maritime runtime-assurance research and simulation workspace. It keeps the decision AI, safety supervisor, actuator gate, plant, independent evaluation truth, and browser console at explicit process boundaries.

The current local slice starts seven processes: the authoritative simulator,
replaceable decision-AI fixture, observation collector, fusion service, assurance
supervisor, actuator gate, and same-origin browser console. The launcher verifies
an identity-linked input/decision/gate-receipt chain and a subsequent public
actuator observation before it reports the slice ready.

```sh
./scripts/bootstrap.sh
./scripts/check.sh
./scripts/launch_cpu.sh
```

Open `http://127.0.0.1:5173`. The console opens in the guided recorded demo when a verified replay is present and keeps the live seven-service console as a separate mode. The recording compares protected and evaluation-only counterfactual branches from one exact paused-reset state, with evidence-linked intervention and post-run outcome panels. See the [guided demo](docs/demo/guided-demo.md) and [backend replay provenance](docs/demo/backend-replay.md).

Generate the finite 45-second S22 recording from a clean commit without using paid compute:

```sh
.venv/bin/python scripts/demo_capture.py \
  --output ../horizon-runs/demo/unsafe-route-v4
```

Each live launch gets a unique directory under the external `horizon-runs/` location with logs, status, and mode-`0600` local capabilities. Press Ctrl-C for graceful shutdown. Use `./scripts/launch_cpu.sh --smoke-seconds 2` for a finite live smoke run.

The reset flow pauses physics, advances the plant epoch, obtains fresh sensor
evidence and a bounded gate recovery certificate, and requires an explicit
operator Resume. A separate fusion reader publishes sensor-only `RecoveryInput`
records, and the gate polls them without calling the decision AI. The gate
continues only a recovery that finishes validation before its original sensor,
health, option, and compute deadlines; otherwise it reports unknown assurance.
WaSR-T reproduction and development extraction have run locally on Apple MPS
with outputs kept under external `horizon-runs/`. Those runs are development
evidence rather than deployment timing or safety evidence.

The workspace is still under active validation. Independent system acceptance,
expanded simulator realism, calibration, and held-out evaluation remain to be
completed before release claims.

See [system boundaries](docs/architecture/system.md), [local runtime](docs/architecture/local-runtime.md), [compute controls](docs/architecture/compute.md), and [reviewed source material](docs/source/README.md). JSON Schema in `packages/contracts/schema/horizon.schema.json` is the shared language-neutral interface source.

This repository contains no project license. It must remain private. Large datasets, weights, captures, caches, and generated runs stay outside Git.
