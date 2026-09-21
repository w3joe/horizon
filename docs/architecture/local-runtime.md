# Local CPU runtime

Run `./scripts/bootstrap.sh` once, `./scripts/check.sh` for the full repository checks, and `./scripts/launch_cpu.sh` for the local slice. The scripts select Python 3.12 at `/opt/homebrew/bin/python3.12` when available and otherwise require Python 3.11 or newer. Set `HORIZON_PYTHON` to override the interpreter.

| Component | Port | Current launch behavior |
|---|---:|---|
| Console | 5173 | Built with `/api` as its read-only simulator endpoint and served by the local proxy |
| A06 authoring preview | 5176 | Reserved; never bound by the integrated launcher |
| Simulator | 8100 | Started and health-checked |
| Decision-AI fixture | 8101 | Started as a separate process and health-checked |
| Gate | 8102 | Assigned; reported unavailable until A04 provides its entrypoint |
| Assurance | 8103 | Assigned; reported unavailable until A04 provides its entrypoint |
| Fusion | 8104 | Polls collector and Decision AI, then serves fresh `GovernorInput` records |
| Collector | 8105 | Polls the protected simulator branch and buffers bounded observations |

The launcher refuses occupied ports before starting each process. A launch receives a UTC/random run ID and a new external run directory. Set `HORIZON_RUNS_DIR` to choose another external root. `run.json` records the commit, ports, ready processes, unavailable components, smoke results, PIDs, exit codes, and start/stop timestamps.

SIGINT or SIGTERM stops only process groups created by the launcher. It first sends SIGTERM, waits up to five seconds, and then uses SIGKILL only for an owned child that did not exit. A finite `--smoke-seconds N` run follows the same shutdown path. Startup order is simulator, Decision AI, collector, fusion, then console. Startup verifies every process health endpoint, the console-to-public-simulator path, the `display_only` boundary, a schema-shaped Decision AI proposal, and a fresh `GovernorInput` produced through the observation/collector/fusion path.
