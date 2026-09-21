# A1-A5 harness integration

The current development harness constructs each of A1, A2, A3, A4, and A5 through its distinct A04
entrypoint. A bounded test runs every candidate through A03 simulation, A05 collection and fusion,
the shared governor contracts, and the independent gate. This is implementation evidence, not an
architecture comparison or a safety result.

One injected manual monotonic clock is shared by simulator, decision fixture, and gate. The harness
advances it for declared AI and dispatch costs and measured candidate, recovery-prime, gate, and
watchdog work. Recovery refresh is synchronous. Every episode closes the gate and confirms that no
worker remains; a separate lifecycle regression repeats this over 1,000 minimal episodes.

The full-pipeline path consumes the original fusion source-health records. `H_FIXED` is labeled
`synthetic fixed-health controller-isolation` and only fixes the experimental neural-health input;
it does not mask a stale or unavailable required source. The required and optional source lists are
derived from the active `AssuranceConfig`, including the radar-specific obstacle source.

Post-control scoring reads the exact active command ID from private truth and joins it to the
accepted protected-command envelope. It reports operational authority separately from the plant
writer channel and preserves startup-passive and receiver-expiry-fallback intervals. It does not
carry the latest accepted receipt's authority forward across an expiry.

The ODD audit uses the exact configured ownship and radar-contact `EngineeringBound` IDs and numeric
values. It never derives a hard bound from covariance. Contact truth is associated by a separately
labeled position matcher because fused track IDs are not oracle vessel IDs; unresolved matches and
every observed bound violation remain in the result and cannot exclude an episode.

The scheduler records its present limitation: AI proposal refresh and governor evaluation are 5 Hz,
while plant stepping and the watchdog are 50 Hz. It does not reissue a held proposal or claim 20 Hz
fresh-state assurance. Recovery feasibility remains null because no independent evaluator exists.
A2's probability trigger is uncalibrated. This packet pins A4 evidence to
`a4-provisional-kinematic-filter-full-plant-validation-v1`, a provisional kinematic QP with a
final 3-DOF rollout check. Later A4 implementations require new evidence and do not upgrade this
report.
