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

The protected-path test remains a strict expected failure because the live
gate path is not yet wired to consume the independent `RecoveryInput`. On this
integrated base, the farther obstacle allows startup to produce accepted
evidence through the older GovernorInput priming path, but the first observed
A1 decision passes the 6 m/s proposal. This bounded compatibility rerun does
not claim a complete pre-collision protected episode. It verifies the revised
hazard premise and keeps protected S22 acceptance open for the pending wiring
and subsequent full-episode rerun.
