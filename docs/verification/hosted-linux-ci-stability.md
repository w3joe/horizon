# Hosted Linux system-test stability

## Observed failure

GitHub Actions run `35688693868` failed five live service-stack tests: S02,
S22, the S12 watchdog case, and the S09/S12 restart-lineage cases. The same
implementation passed in run `35684603452`; the commits between those runs
changed documentation and research records, not the service stack or system
tests.

The failing runner exposed two logical CPUs. The required scheduling plan put
simulator, decision AI, collector, and fusion on CPU 0, and gate plus assurance
on CPU 1. Uploaded public diagnostics show that the failed S02 stack produced
only 12 decisions spanning ticks 644 through 668 while fusion reached tick 673.
The other four failed stacks repeatedly recorded
`PREDICTION_DEADLINE_EXHAUSTED`, `RECOVERY_INPUT_DEADLINE_MISSED`, or both.
Those results show a loaded fixture missing the existing deadline; they do not
justify extending the production deadline.

The tests were adding avoidable load to those lanes. The shared wait helper
queried service endpoints about 33 times per second, including endpoints that
serialize bounded telemetry histories. S02 explicitly queried fusion as often
as 1,000 times per second even though the control loop operates at 20 Hz. On a
two-CPU runner, repeated serialization and HTTP handling competed with the
gate, assurance, simulator, and fusion work being measured.

## Harness correction

System-test probes now use a 10 Hz default cadence. S02 uses the control-loop
cadence of 20 Hz. This correction left the original wall-clock bounds and all
runtime safety checks unchanged. Follow-up hosted run `35693340995` passed the
watchdog and both restart-lineage cases, but still failed S02 and S22. Their
protected simulations had reached only about 13.5 simulated seconds when the
12-second host wait expired, before either scenario's intervention window.
Recorded decisions remained valid and met the unchanged 40 ms deadline.

S02 and S22 now bound intervention by the safety-relevant simulation horizon:
the independently sampled recovery boundary for S02 and the counterfactual
collision time for S22. If simulation time reaches either horizon without an
accepted intervention, the test fails immediately. A 120-second host watchdog
only bounds a stalled fixture; it is not used as safety evidence. S22 retains
its exact scenario-duration horizon, and both tests still require the accepted
command's physical actuation before the corresponding boundary or collision.
Watchdog expectations, lineage assertions, and the exact 40 ms runtime decision
deadline remain unchanged.

Follow-up run `35694048139` demonstrated that longer host waits cannot make the
forced two-CPU partition valid functional evidence. S02 reached 30.06 simulated
seconds without an intervention, crossing its 30.0-second sampled recovery
boundary. S22 reached 38.56 simulated seconds without an intervention, after
the 38.42-second counterfactual collision. The retained S02 decisions were all
valid, met the 40 ms deadline, and used 7.9--15.8 ms of compute time, but the
loaded service chain did not produce a hazard intervention. S22 diagnostics
showed fusion outages and rejected or stale startup recovery inputs. The S09
and S12 restart stacks likewise recorded fusion outages, expired inputs, failed
gate submissions, and deadline-exhausted recovery validation while waiting for
fresh joined evidence.

## CI evidence split

Functional acceptance and required-affinity characterization therefore run as
separate checks:

- The complete functional suite runs without forced process affinity. It keeps
  the production 40 ms decision deadline and every scenario-time boundary,
  collision, actuation, watchdog, and lineage assertion.
- Deterministic scheduling unit tests continue to verify Linux command wrapping,
  required-mode failure behavior, the declared lane partition, and affinity
  observation.
- A separate Linux smoke starts the six services with required affinity and
  verifies their kernel-visible lanes and inherited default priority. The
  launcher also receives required mode for its bounded start/stop smoke.

The required-affinity checks establish configuration, process startup, and
cleanup only. They do not claim that an oversubscribed two-core hosted runner
meets safety timing. Conversely, the unpinned functional suite does not qualify
the isolated topology. Timing qualification, WCET, and safe operation under a
specific affinity plan require representative target hardware and a dedicated
resource budget.

Local verification on macOS cannot reproduce Linux CPU affinity. Three repeated
runs of the five affected cases passed 15/15 in 73.97, 74.28, and 75.56 seconds.
The complete system suite then passed 14 tests with the Linux-only affinity test
skipped in 83.87 seconds. Hosted Linux remains authoritative only for the
kernel-visible two-lane startup smoke. These results do not establish WCET,
hard real-time behavior, or target-hardware qualification.
