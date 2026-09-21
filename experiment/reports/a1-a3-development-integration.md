# A1/A3 development integration check

Run `a02-a05-dev-integration-20260921` exercised A1 and A3 through the A05 collector and fusion
constructors, public simulator observations, the shared GovernorInput/AssuranceDecision contracts,
and the independent actuator gate. It used two development scenarios, one seed each, with a 0.45 s
truncation. Outputs are external at
`horizon-runs/development/a02-a05-dev-integration-20260921`.

This is an integration check, not a safety or architecture comparison. All four episodes were
censored before mission completion. No violation occurred in the short prefix, which provides no
useful safety evidence (the Wilson 95% upper bound is 0.658 for each two-episode candidate cell).
A1 missed 0 of 4 decision deadlines, with p95 3.3 ms. A3 missed 4 of 4, with p95 70.1 ms. The A05
fusion contract deliberately supplies covariance without asserting bounded errors. A1 therefore
returned invalid minimum-risk decisions because it could not validate a recovery command; the gate
rejected them. A3's late decisions were also invalid and rejected. The 40 ms budget was not moved.
Runtime is host computation time and remains separate from the simulated interval.

The simulator receiver and synchronous gate path share the explicit offline clock. The current gate
background recovery-cache worker still reads the host monotonic clock, so this short run does not
validate deterministic cached-recovery continuity. A clock-injection request is open with A04 before
the longer study.

Truth-only assumption-audit sidecars accompany every record. For this A05 path they report that no
hard bounded-error assumption was supplied, rather than deriving one from covariance. They
explicitly forbid held-out exclusion. The adapter does not expose truth values to fusion or the
controller.
