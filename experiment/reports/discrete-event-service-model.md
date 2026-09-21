# Discrete-event service model for development experiments

The controller-isolation harness uses `fixed-step-discrete-service-v1`. This is a declared
simulation model, not a measurement of production end-to-end latency. Safety outcomes from this
harness are conditional on the frozen stage latencies in each episode request and result.

Two development profiles are registered before execution:

| Profile | AI | Recovery prime | Candidate | Gate |
|---|---:|---:|---:|---:|
| `idealized-front-zero-v1` | 0 ms | 0 ms | 20 ms | 20 ms |
| `all-stages-20ms-v1` | 20 ms | 20 ms | 20 ms | 20 ms |

The first profile is an explicitly idealized diagnostic reference. It cannot support a headline
architecture conclusion. The second profile exposes proposal and evidence loss caused by a plant
event during front-end service; those fail-closed outcomes remain in the results. Profiles are
selected in the study plan before execution, and the harness rejects per-stage values that do not
match the selected profile.

Nonzero stage times are bounded to two seconds and must be multiples of the 20 ms plant period.
The candidate completion time is the first plant-grid event at or after both its declared service
completion and its emitted `decided_monotonic_ns`. The harness never rewrites that decision. A
decision that reports an actual computation deadline failure remains invalid.

During a pending candidate or gate stage, the plant continues under its current authority. Fresh
observations are consumed through the simulator's cursor-paged interface and the independent gate
watchdog runs at each plant event. A gate request records the epoch and control generation at queue
time. If either changes before completion, the harness records a scheduler rejection and does not
invent a `GateReceipt`. Otherwise it calls `gate.submit` without a supplied `now_ns`; the gate and
simulator receiver read the shared live clock and reject expired evidence before plant mutation.

Wall durations for candidate evaluation, recovery validation, gate submission, and watchdog calls
are recorded separately. They do not advance the simulated timeline and cannot be described as
measured end-to-end response time. In particular, gate validation is instantaneous at the modeled
gate-completion event. The default zero-time AI and recovery stages are explicit limitations. A
study using different assumptions must put those exact finite times in every episode request and
retain them in the raw diagnostics.

The raw runner writes one bounded diagnostics sidecar per episode. It records the candidate version
read from the instantiated plugin, the complete timing model, scheduler cancellations, action
counts, gate and watchdog receipt counts, and operational authority/fallback counts. These fields
support audit and reporting; they do not upgrade a development study to calibration or held-out
evidence.
