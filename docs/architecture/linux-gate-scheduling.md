# Linux trusted recovery lane scheduling

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
numbered CPU to a trusted recovery lane containing the gate and fusion processes.
Simulator, decision AI, collector, assurance, console, and optional perception
processes are confined to the remaining support-lane CPUs. This keeps fresh recovery
input assembly and independent validation together while preventing high-rate plant,
ingestion, autonomy, and UI services from consuming that CPU. Every child process
retains its inherited default scheduling priority. The system-test orchestrator
retains its host-provided affinity and is not assigned to either child-process lane.

`required` fails before the run starts when the host is not Linux, fewer than two
CPUs are allowed, `taskset` is unavailable, or the kernel-observed child
affinity differs from the plan. `best-effort` leaves commands unchanged and records
the concrete unsupported status. `off` preserves the ordinary platform behavior,
including on macOS.

Every launcher `run.json` records the requested mode, selected CPU sets, inherited
priority policy, and observed affinity for each process. System-test failure
diagnostics carry the same fields. These records explicitly state
`hard_realtime: false` and `operating_system_cpu_exclusive: false`: process
affinity keeps support-lane Horizon services off the recovery CPU, but gate and
fusion still share it, and the policy cannot exclude kernel work,
unrelated host processes, virtualization pauses, or runner preemption. The 40 ms
validation deadline and fail-closed control behavior remain unchanged.
