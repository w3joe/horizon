# Horizon

Horizon is a private maritime runtime-assurance research and simulation workspace. It keeps the decision AI, safety supervisor, actuator gate, plant, independent evaluation truth, and browser console at explicit process boundaries.

The current CPU slice runs the authoritative simulator, replaceable decision-AI fixture, and a same-origin console proxy. Pending services are reported as unavailable until their owners provide real entrypoints.

```sh
./scripts/bootstrap.sh
./scripts/check.sh
./scripts/launch_cpu.sh
```

Open `http://127.0.0.1:5173`. Each launch gets a unique directory under the external `horizon-runs/` location with logs, status, and mode-`0600` local capabilities. Press Ctrl-C for graceful shutdown. Use `./scripts/launch_cpu.sh --smoke-seconds 2` for a finite smoke run.

See [system boundaries](docs/architecture/system.md), [local runtime](docs/architecture/local-runtime.md), [compute controls](docs/architecture/compute.md), and [reviewed source material](docs/source/README.md). JSON Schema in `packages/contracts/schema/horizon.schema.json` is the shared language-neutral interface source.

This repository contains no project license. It must remain private. Large datasets, weights, captures, caches, and generated runs stay outside Git.
