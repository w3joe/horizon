# S01--S22 acceptance gap audit

## Scope

This audit compares the current full-process acceptance tests with the private
S01--S22 source-plan recipes. Catalogue validation and component tests are
useful implementation evidence, but they do not close a source-plan acceptance
condition. The current full-process suite closes bounded portions of S01, S07,
S09, S12, S17, and S22. None of those six has complete mission-level evidence.

The following tests are deliberately specific. They use the declared fixture,
policy, and orchestration step from each recipe and name the evidence needed to
close the remaining gap. Censored episodes, unavailable recovery boundaries,
and out-of-domain samples remain in the result rather than being converted to
successes or excluded after the run.

## Runnable follow-on tests

| ID | Proposed test | Required assertions beyond current coverage |
|---|---|---|
| S01 | `test_s01_nominal_completes_route_without_unnecessary_replacement` | Run `normal_transit.json` with `nominal` to a terminal outcome. Require a complete joined decision/receipt trace, mission success, and no accepted recovery or filtered intervention unless an independently evaluated constraint identifies a hazard. Record horizon expiry as censored. |
| S02 | `test_s02_crossing_intervenes_before_reference_boundary_and_preserves_clearance` | Run protected and unprotected `crossing_recoverable.json` branches with `unsafe_straight` and the same seed/noise tape. Derive the sampled recovery boundary from the unprotected finite library, require an accepted A1 modification or recovery before it, and verify no collision plus the configured truth clearance. Leave lead time unknown if the reference boundary is unavailable. |
| S03 | `test_s03_head_on_records_complete_clearance_trace` | Run `head_on_recoverable.json` with `nominal`. Require a terminal trace, join every accepted receipt to its decision by `decision_id`, verify no collision and the configured minimum hull clearance, and retain an explicit mission outcome or censoring reason. |
| S04 | `test_s04_overtaking_and_benign_close_pass_bound_false_intervention` | Run both `overtaking_recoverable.json` and `benign_close_pass.json` with `nominal`. Verify safe outcomes and compute benign unnecessary intervention from accepted receipts rather than emitted decisions. Report the paired route outcomes and censoring. |
| S05 | `test_s05_boundary_route_is_rejected_before_containment_loss` | Run `boundary_depth.json` with `unsafe_straight`. Require an accepted protected command change before the independently scored swept-hull corridor margin reaches zero. Join final-command constraint evidence and verify the truth margin remains nonnegative. |
| S06 | `test_s06_swept_path_ukc_remains_nonnegative` | Run `boundary_depth.json` with `nominal`. Recompute dynamic swept under-keel clearance from the private plant and geometry record, rather than using point depth; require no grounding and an explicit complete or censored route outcome. |
| S07 | `test_s07_bias_detection_or_explicit_unknown_precedes_dropout` | Run `navigation_fault.json` through its bias and dropout phases. Independent-source conflict must produce restricted authority or explicit unknown status. The truth-only audit records every engineering-bound violation, while public/controller records remain free of fault labels. |
| S08 | `test_s08_radar_hazard_survives_false_ais_claim` | Run `source_conflict.json` with `nominal`. Preserve separate radar and AIS receipt ancestry, retain the radar-supported contact in final-command constraints, and verify protected truth clearance. |
| S09 | `test_s09_ai_kill_restart_never_reuses_stale_lineage` | Stop decision AI at 20 s and restart it at 22 s. During absence, accept only watchdog or independently validated recovery authority. After restart, accept autonomy only for a current snapshot/proposal/decision chain, and prove no pre-restart proposal or decision is accepted again. |
| S10 | `test_s10_slow_and_stuck_rudder_reduce_declared_capability` | Run `vessel_degradation.json` and `rudder_stuck.json`. A linked actuator-response window must reduce the public capability before a later assurance evaluation; the protected command must respect the updated rate/lag. Keep the fault label private and audit actual response from truth. |
| S11 | `test_s11_link_loss_during_active_avoidance_triggers_onboard_recovery` | In `crossing_recoverable.json`, confirm an accepted active avoidance before disconnecting decision-AI-to-fusion at 24 s and restoring it at 27 s. Require onboard mitigation before restoration and before authority expiry or the recovery boundary; no remote command may be accepted during the outage. |
| S12 | `test_s12_assurance_restart_requires_fresh_joined_chain` | Stop assurance at 24 s and restart at 27 s. Require watchdog takeover within the existing expiry, then require the first restored autonomy evidence to join a fresh current snapshot, proposal, decision, and receipt. No pre-stop decision may be accepted after restart. |
| S13 | `test_s13_dense_traffic_intervenes_before_independent_escape_boundary` | Run `dense_traffic.json` with `unsafe_straight`. Compute the sampled escape boundary on a paired unprotected reference continuation, never from protected future states. Require accepted intervention before it or report lead time as unknown. |
| S15 | `test_s15_initially_unrecoverable_is_reported_as_minimum_risk` | Run `initially_unrecoverable.json` with `unsafe_straight`. Require an accepted minimum-risk command and retained `recoverability_class`. A collision may occur, but must not be counted as a preventable-case failure or success, and recovery lead time stays unknown. |
| S16 | `test_s16_release_requires_stability_and_operator_ack_without_chatter` | Run `vessel_degradation_recovery.json` and acknowledge at 75 s. Keep authority quarantined after physical recovery until the declared stable interval and acknowledgement, then require exactly one release with a new certificate or epoch and no authority oscillation. |
| S17 | `test_s17_delayed_consumption_exposes_original_ancestry` | Run `sensor_timing_fault.json` with `stale_lineage`, delaying collector-to-fusion by 1 s from 20--26 s. A live heartbeat must not refresh consumed input: the GovernorInput names the original source receipt/time, the gate rejects stale evidence, and restored traffic has fresh ancestry. |
| S18 | `test_s18_peer_claim_cannot_relax_radar_constraint` | Run `peer_intent_conflict.json` with `nominal`. Carry the distinct peer claim and radar receipt through fusion and decision evidence, require the final command to respect radar-supported motion, and verify truth clearance. |
| S22 | `test_s22_hazard_triggered_a1_precedes_collision_and_completes` | Retain the existing paired counterfactual collision. The protected intervention must join to an A1 decision/receipt rather than only a gate-watchdog command, precede the comparison collision, preserve clearance, and finish the mission or record explicit censoring. |

## External-artifact-dependent tests

These cases must remain blocked until the named real adapter or artifact exists.
A simulated alias would not close the source-plan condition.

| ID | Proposed test | Required artifact and assertions |
|---|---|---|
| S14 | `test_s14_recorded_fog_health_restricts_camera_authority` | Replay a recorded fog example or a predeclared synthetic-degradation artifact through the perception publisher. Bind frame, model, inference, and health identities; require the calibrated policy to restrict the mission without an unsupported all-clear or in-sight-rule claim. |
| S19 | `test_s19_normalization_mismatch_is_bound_to_exact_inference` | Compare the exact same frame and model with the declared normalization mismatch. Bind the incorrect output to its inference ID, report H2--H4 as measured or explicit unknown, and verify independent radar still drives protection. |
| S20 | `test_s20_diagnostic_loss_and_bounded_flood_preserve_control_deadlines` | Kill the bounded neural-health worker, then apply the declared finite load profile. Measure unchanged control deadlines and queue bounds, require diagnostics to report incomplete, and reject silent loss. This needs the bounded load injector. |
| S21 | `test_s21_output_only_mode_does_not_invent_internal_health` | Use the output-only perception adapter. Require H0/H1 output evidence, H2--H4 unknown rather than zero or healthy, and conservative camera authorization. |

## Recommended bounded order

Implement S22 and S02 first because they distinguish hazard-triggered assurance
from the existing watchdog-only result. Next add S09 and S12 restart lineage,
then S17 delayed ancestry. S05, S06, S10, and S16 exercise the most material
remaining geometry, plant-capability, and release-state gaps. S14 and
S19--S21 stay blocked on their exact external artifacts.

