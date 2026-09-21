# S01--S22 execution and dependency matrix

Each JSON file is a private evaluation recipe. `physical_fixtures` names real
loadable simulator configurations. `decision_ai_policies` names modes exposed
by the separate fixture service. `orchestrator_steps` describes service or
transport operations that A08 must run against the live stack. Fault names and
acceptance labels are never copied into public observations.

`fixture_status` has three values:

- `simulator_executable`: the local plant and nominal AI fixture are sufficient
  to execute the fixture.
- `integration_recipe`: the physical/AI fixture exists, while the acceptance
  condition also depends on the named live services or A08 orchestration.
- `external_artifact_required`: an A07 recorded replay or declared synthetic
  neural fault artifact is still required. A rendered navigation scene is not
  treated as a camera recording.

| Case | Physical fixture or recipe | Local regression | Remaining dependency |
|---|---|---|---|
| S01 | `normal_transit.json`, nominal AI | catalogue/fixture validation | A08 mission acceptance |
| S02 | crossing, `unsafe_straight` AI | unsafe policy contract | A04/A08 closed loop |
| S03 | head-on | catalogue/fixture validation | A04/A08 clearance trace |
| S04 | overtaking + benign close pass | catalogue/fixture validation | A04/A08 intervention rate |
| S05 | boundary/depth, unsafe AI | geometry regression | A04 swept-hull check |
| S06 | boundary/depth | geometry regression | A04 UKC check |
| S07 | scheduled GNSS bias then dropout | `test_gnss_bias_and_dropout_affect_only_public_measurements` | A05/A04 loss-of-assurance response |
| S08 | AIS/radar conflict | ingestion conflict regressions | A04/A08 closed loop |
| S09 | expired, stale, malformed, and stopped AI | decision-AI fixture tests | A08 process orchestration + A04 gate |
| S10 | slow-rudder and stuck-rudder variants | `test_stuck_rudder_freezes_physical_actuator_without_online_fault_label` | A05 sliding-window monitor + A04 |
| S11 | decision-AI link disconnect/restore | recipe validation | A08 link injector + A04 recovery |
| S12 | assurance SIGTERM/restart | recipe validation | A08 process runner + A04 watchdog |
| S13 | dense traffic, unsafe AI | catalogue/fixture validation | A04 recovery-margin comparison |
| S14 | fog/OOD perception variant | dependency validation | A07 artifact + A08 runner |
| S15 | initially unrecoverable scene | recoverability fixture validation | A04/A08 minimum-risk report |
| S16 | actuator faults clear at 70 s | scheduled-fault validation | A04 release state + A08 acknowledgement |
| S17 | sensor timing + stale self-reported lineage + internal link delay | stale-lineage fixture test | A08 link injector + A05/A04 |
| S18 | peer claim opposite radar-supported motion | `test_peer_intent_claim_is_distinct_from_radar_supported_motion` | A05/A04 constraint check |
| S19 | normalization mismatch replay | dependency validation | A07 instrumented artifact + A08 |
| S20 | diagnostic worker kill/load recipe | dependency validation | A07 bounded worker + A08 load injector |
| S21 | output-only neural appliance | dependency validation | A07 adapter + A05 unknown handling |
| S22 | valid snapshot, `unsafe_straight` AI | unsafe policy contract | A04/A08 behavior attribution |

All cases remain `pending_A08` for independent end-to-end verification. The
local tests establish fixture semantics and hidden-label boundaries; they do
not claim the live acceptance condition has passed.
