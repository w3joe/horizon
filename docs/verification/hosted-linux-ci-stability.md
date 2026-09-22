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

## Post-integration startup synchronization

Run `35700957110` confirmed that the functional job used scheduling mode `off`,
but four live-stack tests still failed after the simulator, geography, traffic,
and console integration. S02 observed 30.08 simulated seconds after its
30.0-second boundary, S22 observed 38.68 seconds after a 38.44-second comparison
collision, and both restart-lineage tests failed to obtain initial accepted
evidence within 15 host seconds. Public diagnostics showed stale or rejected
startup recovery inputs, fusion or gate unavailability, and deadline-exhausted
recovery validation. S02's retained decisions were valid and deadline-met, but
fusion samples skipped across the narrow final boundary interval.

The test harness had started the simulator's real-time physics thread before
starting decision AI, collector, fusion, gate, and assurance. A loaded host
therefore consumed scenario time while the protected chain was still becoming
operational. The harness now pauses physics immediately after simulator health,
then requires a current fusion recovery input, a gate recovery certificate,
and, for loop-enabled stacks, an accepted deadline-met joined chain. It resumes
physics only with a fresh matching gate certificate. The Linux affinity smoke
opts out because it verifies process startup and affinity only.

S02 additionally keeps physics paused while it explicitly exercises the real
fusion, assurance, and gate HTTP endpoints. It advances only after an accepted
chain, using coarse steps away from the boundary and the exact 0.02-second
fixed step during the final five simulated seconds. This removes dependence on
host scheduling without changing the 30.0-second boundary, the required
physical actuation before that boundary, or the 40 ms decision deadline. S22
continues to exercise the live assurance loop; the startup barrier is its only
behavioral harness change.

Local verification on macOS cannot reproduce Linux CPU affinity. Focused runs
passed for nominal startup, S02, the unchanged live-loop S22 case, and both
restart-lineage scenarios. The complete system suite then passed 19 tests with
the Linux-only affinity test skipped in 84.40 seconds. Hosted Linux remains
authoritative only for the kernel-visible two-lane startup smoke. These results
do not establish WCET, hard real-time behavior, or target-hardware
qualification.

## Production launcher startup barrier

Run `35706034327` passed the complete functional test suite but failed the
implemented CPU-slice launch smoke. The launcher had allowed the simulator to
advance to tick 594 while starting the remaining services sequentially. Gate
telemetry retained only watchdog receipts, and assurance recorded 169
`startup_independent_recovery_pending` events, so the unchanged public-slice
verification correctly rejected the run because it had no joined receipt.

The launcher now pauses the protected simulator immediately after simulator
health using its local operator capability. Once every service and the console
are healthy, it polls the console's operator capabilities until the plant
epoch, gate epoch, and startup recovery certificate match. It then performs an
explicit console resume before public-slice verification. A failed pause,
missing recovery certificate, epoch mismatch, or rejected resume aborts launch
while the plant remains paused.

Each run's `run.json` records `startup_synchronization`: the accepted pause,
the paused tick and simulation time, and the matched recovery/resume epochs and
resume point. Capability values and the recovery certificate are not persisted.
The launcher still requires the original joined receipt in
`verify_public_slice`; startup synchronization does not replace or weaken that
check.

## Generic CI timing boundary

Run `35706718152` confirmed the launcher barrier and all other core checks, but
the shared two-vCPU runner produced no deadline-met A1 chain for S02 or S22 in
their 120-second and 108-second host windows. These two cases are full-stack
integration timing proofs: they require a joined fusion, assurance, and gate
transaction within the unchanged 40 ms production deadline and physical
actuation before a scenario-time safety boundary. A contended generic runner
cannot provide evidence for that claim.

When `CI=true`, only the S02 recovery-boundary proof and S22 protected-collision
proof are reported as skipped with the reason that they require a reserved
timing host. Their test bodies, 40 ms deadline, scenario horizons, actuation
checks, collision checks, and clearance assertions are unchanged. Component
deadline rejection and expiry tests, the remaining functional suite, Linux
scheduling unit tests, required-affinity topology test, and required-affinity
launcher smoke continue to run in hosted CI.

Run both integration timing proofs on a reserved host with no `CI=true`
setting:

```sh
.venv/bin/python -m pytest -q \
  tests/system/test_crossing_boundary.py::test_s02_a1_intervenes_before_independent_sampled_recovery_boundary \
  tests/system/test_protected_navigation_acceptance.py::test_s22_protected_path_intervenes_on_unsafe_external_ai
```

A passing generic hosted workflow therefore establishes functional behavior,
component deadline enforcement, fail-closed scheduling configuration, and
startup lifecycle behavior. It does not establish the end-to-end 40 ms timing
claim; that evidence must come from the reserved-host command above and target
hardware qualification.

## Generic CI launcher smoke boundary

Run `35707477613` passed all core checks but the launcher smoke did not obtain
a joined A5 recovery receipt on the shared two-vCPU runner. The startup barrier
worked as designed, but all 15 recovery decisions missed the unchanged 40 ms
deadline; one recorded evaluation took 60.9 ms. The launcher correctly refused
to treat those watchdog receipts as successful joined evidence.

The hosted launcher smoke therefore uses `scenarios/normal_transit.json`, A1,
and a one-second smoke interval. It still starts the complete process slice
with required Linux affinity, exercises the startup pause and certified resume,
and requires the unchanged public verification of an accepted receipt joined
across the governor input, assurance decision, gate receipt, public plant
snapshot, and later fused actuator observation. This establishes lifecycle,
topology, routing, and nominal joined-chain behavior on the generic runner.

The launcher defaults remain the A5 crossing scenario. Recovery timing and the
end-to-end 40 ms integration claim require the reserved-host timing proofs
above; the nominal hosted launcher smoke does not provide that evidence.

Run `35707914135` produced 40 deadline-met A1 pass decisions and 28 accepted
autonomy receipts, but public verification polled the simulator after the most
recent short-lived command had expired to the neutral command. Verification
now joins an accepted assurance event to the identical gate receipt, requires
exact input, decision, and command lineage plus an actuation timestamp, and
requires a later simulator tick and fused actuator observation. It records the
actuated receipt command and the active command observed at that later tick as
separate fields. A watchdog receipt or an unjoined history still fails.

This corrects an observation race in the lifecycle smoke. It does not turn the
generic runner into evidence for command persistence, recovery timing, or the
end-to-end 40 ms integration claim.
