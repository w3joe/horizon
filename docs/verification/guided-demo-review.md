# Guided demo review — 2026-09-21

The default console experience is a guided replay of a recorded local service run.
The recording is `unsafe-route-v4`, captured from clean source
`bca91dcc55da7214930a5c167e8e6da7016b612f`. Its replay SHA-256 is
`bf84e00b9785912bd27b17345bb8e3f394d49fa9195a19e7c96de683432e8b8c`.
Large replay files remain outside Git in `horizon-runs/demo/`.

## Evidence review

Independent review verified the replay hash, clean-source manifest, exact initial
branch-state agreement, aligned timeline, and proposal → assurance decision →
accepted receipt → publicly observed active-command lineage. Earlier diagnostic
captures are outside the served catalog.

The actual intervention is a preventive recovery observed at 0.04 seconds. No
unsafe command was observed active before it. The protected branch has zero
collisions and a minimum evaluated hull clearance of 144.557 metres. The
evaluation-only counterfactual collides at 37.5 seconds, with a minimum hull
clearance of −4.5 metres. Outcomes are post-run evaluation, not controller inputs.

The intervention navigation lands at the 0.1-second sample carrying the matched
command. Technical details retain the exact 0.040-second event time. Other
playhead positions show an issued command only when that sample has a receipt
matching its public active-command ID; missing matches are explicitly unavailable.

## Browser review

Manual review used Safari at approximately 890 × 768 on the local proxy. Observed:

- A missing recording produces an unavailable state without substitute data.
- A valid recording opens paused at zero with recorded-simulation labeling.
- Play scrolls the synchronized scenes into view; both branches advance together.
- Pause, timeline scrubbing, speed selection, event jumps, and restart work.
- The intervention jump shows the actual 70-degree, 1 m/s recovery command.
- The collision marker appears at 37.5 seconds; the oblique camera visibly shows
  the counterfactual boat reaching the obstacle while the protected boat stays clear.
- Oblique and tactical views render the licensed vessel model and both branches.
- Technical details expose the recorded source, digest, identifiers, and limits.
- The separate live console loads. Its neural inspector displays the 85-frame
  WaSR-T reproduction with explicit separation from the simulated encounter.
- Live mode no longer displays a selected fixture scenario as the launched scenario.

## Automated checks

The final implementation passed `./scripts/check.sh`: 327 Python tests,
contract generation/schema validation, TypeScript checks, and the production
console build. The tested implementation commit is
`354e9c961cbcb6274bea80b788baeacdca927155`; integrated main differs only in this
review document. The focused experiment suite passed 71 tests.

The last full run exposed an experiment-boundary accounting issue: a candidate
decision could finish at the finite horizon before reaching the gate. The fix
records submitted, censored, and scheduler-rejected decisions explicitly and
joins real receipts by decision ID. Deterministic regressions cover censored A5
decisions and ensure unsubmitted recovery proposals are not scored as applied
interventions. No receipt is fabricated and control deadlines are unchanged.

## Limits

This review establishes local demo behavior, not deployment safety, calibrated
perception risk, or field validation. Water, wake, heave, and lighting are visual
effects and do not alter authoritative vessel physics. The guided replay does not
claim that neural-network evidence caused its intervention. Responsive CSS exists,
but this review did not certify a mobile-device/browser matrix.

The preceding Linux CI run had failures in four live service-chain timing tests;
local checks do not establish Linux timing portability. Control deadlines were
not relaxed to obtain a demo result.
