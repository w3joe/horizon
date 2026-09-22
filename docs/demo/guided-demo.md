# Guided recorded-simulation demo

The console now opens on a guided replay of a real local service run. It does not generate a
browser fixture or silently fall back to synthetic display data. If the recorded-run API is absent,
malformed, dirty, or inconsistent with its catalog manifest, the page shows an unavailable state.
The existing operational workspace remains available through **Live console**.

## Presenter walkthrough

1. Open the console through the platform proxy. Confirm the cyan **RECORDED SIMULATION** badge and
   the opening frame before pressing play.
2. Select **Play safety demo**. Playback defaults to 2×, so a 45-second run takes about 23 seconds.
   Both viewports use one replay clock: cyan is the actual RTA-protected plant and red is the
   evaluation-only without-RTA counterfactual.
3. Use the four evidence-timed story markers. The first is the recorded unsafe policy proposal. The
   second is the actual intervention time and names the recorded mechanism, including
   `gate_watchdog` when that is what happened. A `preventive_guard` run says that recovery was
   applied before an unsafe command was observed active at the protected plant; it does not claim a
   takeover after unsafe actuation. The third marker is the counterfactual collision time from
   post-run scoring. The fourth is the end of the recorded run.
4. At the intervention, point out **AI proposed** and **Actually issued**. Those values come from the
   latest public proposal, receipt, and protected-command record at or before the playhead. Reason
   text is a plain-English rendering of the recorded reason codes.
5. Scrub backward and forward, pause, use **Replay**, or switch between 0.5×, 1×, 2×, and 4×. Oblique
   and tactical cameras change only presentation. Scene positions are interpolated between recorded
   public snapshots; no synthetic path points are added.
6. Read the outcome cards as post-run evaluation. They show collision counts and hull-clearance
   metrics with units for both branches. These results are not online controller inputs.
7. Expand **Technical details** only when provenance is useful. It contains the run, source commit,
   scenario version, digest, current identifiers, exact intervention mechanism, recorded candidate
   ID/version, and the limitations embedded in the run manifest. Candidate identity is read from
   the public decision records; if a replay records A5, the header and evidence panel say `A5`.

The harbour and platform treatment is explicitly presentational: a Singapore Strait-inspired setting,
naval-gray patrol craft, sensor mast/radome, EO/IR sensor, mission-bay/RHIB cue, and secure-comms
display. It does not claim that the recorded run occurred in Singapore. Simple deck and CIWS-like
silhouettes are static, non-functional presentation geometry: they have no controls, target/contact
linkage, tracking, engagement logic, firing animation, performance parameters, or fire-control
behavior. These visuals do not change public hulls, scene coordinates, command lineage, or scoring.

At an 890 × 768 review window the two branch viewports remain side by side. Below 760 px they stack
for legibility. The primary play action is above the fold at both review and desktop sizes.

## Read-only replay API

The experience reads only these same-origin routes:

- `GET /api/demo/catalog` returns `horizon.demo-catalog.v1` and one or more clean recorded-run
  manifests with allowlisted replay URLs.
- `GET /api/demo/runs/<run_id>` returns `horizon.demo-replay.v1`, paired public snapshots on a shared
  timeline, public proposals/decisions/receipts/gate events, the recorded intervention, and an
  evaluation-only outcome summary.

The browser verifies the schema tags, clean-source declaration, manifest/run identity, replay hash
identity, nonempty ordered timeline, public snapshot tags, and same-origin replay URL shape before
rendering. The artifact API is self-contained and does not require the seven live services to be
running.

## Evidence boundaries

The protected viewport represents the public snapshots from the actual protected plant. The red
viewport is explicitly labeled **evaluation-only counterfactual**. Collision markers appear only
after the corresponding `first_collision_time_s`; intervention visuals appear only after the
recorded intervention time.

The vessel model is “Assault boat” by tnnv under CC BY 4.0, linked in the interface. Water, wake,
lighting, cameras, Singapore-inspired harbour treatment, and patrol-platform fittings are visual
context. Procedural sensor fittings are original presentation geometry and are not sourced equipment
models. Recorded neural artifacts remain a separate inspector in the live console. The guided replay
does not claim that neural evidence caused the intervention.
