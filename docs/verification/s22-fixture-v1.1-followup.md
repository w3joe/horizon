# S22 fixture v1.1 compatibility follow-up

This follow-up preserves the historical results in
`bounded-system-acceptance.md`, which were measured on integrated base
`5f6d49d` with fixture v1.0. It records only the later compatibility recheck.

The recheck merged integrated main `873bc4a`, including fixture adjustment
`92af587`. `static_obstacle_approach.json` now declares version `1.1.0` and
places the stationary barge 180 m ahead. Its normalized two-space JSON plus a
trailing newline has SHA-256
`79cec9604db5a8a9bffd3291c11736370edb77890500724d7c1cb3e052f8e229`.
That serialization matches the external coordination record; the repository's
compact file-byte hash is different and is not substituted for the declared
scenario hash.

The independent system regression clones the live simulator branch as an
unprotected evaluation branch, commands 6 m/s straight ahead with a 46 second
simulation-time expiry, and advances 2,250 fixed 20 ms steps. The 45 second
evaluation window contains a physical collision event. This is consistent with
the separately generated seed-7 evidence, which reports collision at 37.5 s
and minimum signed hull clearance -4.5 m.

The gate now consumes proposal-free `RecoveryInput` records through a
dedicated capability and keeps that validation independent of the primary
decision-AI call path. The system regression first runs an unprotected clone
at 6 m/s to obtain the comparison collision time. It then runs a separate
protected stack for at least 45 simulated seconds, joins an accepted external
6 m/s command, and requires a subsequently actuated gate-watchdog command
below 6 m/s before the comparison collision. Authoritative protected truth
must contain no collision and must retain positive signed hull clearance for
the complete window.

The strict expected-failure marker was removed after that full test passed.
In the recorded development run, the protected branch reached 45.4 s with no
collision and a minimum signed hull clearance of 98.27 m, while the fixed
counterfactual collides at 37.5 s. The first accepted A1 decisions still passed
the far-field 6 m/s proposal. The observed intervention was the gate watchdog
continuing or reducing to bounded recovery when timely supervisor authority
was absent. This establishes bounded protected clearance for this episode; it
does not establish that A1 detected the obstacle or that the mission route was
completed.
