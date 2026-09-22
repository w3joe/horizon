# Linux gate process scheduling

The local launcher supports an opt-in scheduling partition for deadline-sensitive
gate validation:

```sh
./scripts/launch_cpu.sh --gate-cpu-isolation required
```

`HORIZON_GATE_CPU_ISOLATION` accepts the same `off`, `best-effort`, or `required`
values. CI uses `required` so the process-level acceptance suite exercises the
partition rather than merely testing command construction.

On Linux, the launcher reads the process's allowed CPU set with
`sched_getaffinity(0)`. When at least two CPUs are allowed, it assigns the highest
numbered CPU to the complete gate process and confines all other Horizon service
processes to the remaining CPUs. Every process retains its inherited default
scheduling priority. The gate's HTTP threads, watchdog, and independent recovery
validator therefore share the gate process CPU and are isolated from the simulator,
fusion, AI, assurance, collector, console, and optional perception processes without
starving those upstream producers through a priority difference.

`required` fails before the run starts when the host is not Linux, fewer than two
CPUs are allowed, `taskset` is unavailable, or the kernel-observed child
affinity differs from the plan. `best-effort` leaves commands unchanged and records
the concrete unsupported status. `off` preserves the ordinary platform behavior,
including on macOS.

Every launcher `run.json` records the requested mode, selected CPU sets, inherited
priority policy, and observed affinity for each process. System-test failure
diagnostics carry the same fields. These records explicitly state
`hard_realtime: false` and `operating_system_cpu_exclusive: false`: process
affinity keeps other Horizon services off the gate CPU, but it cannot exclude
kernel work, unrelated host processes, virtualization pauses, or runner
preemption. The 40 ms validation deadline and fail-closed control behavior remain
unchanged.
