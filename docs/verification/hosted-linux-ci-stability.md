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
cadence of 20 Hz. The change leaves all wall-clock test bounds, watchdog
expectations, lineage assertions, actuation checks, and the 40 ms runtime
decision deadline unchanged.

Local verification on macOS cannot reproduce Linux CPU affinity. Three repeated
runs of the five affected cases passed 15/15 in 73.97, 74.28, and 75.56 seconds.
The complete system suite then passed 14 tests with the Linux-only affinity test
skipped in 83.87 seconds. Hosted Linux remains the authoritative check for the
two-lane scheduling topology; these results do not establish WCET, hard
real-time behavior, or target-hardware qualification.
