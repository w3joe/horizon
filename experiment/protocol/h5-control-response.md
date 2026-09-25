# H5 paired simulation response study

## Question and claim boundary

Does enabling the implemented H5 warning response change simulated safety or
mission outcomes relative to disabling that response?

Both branches use the existing nominal decision-AI fixture, A5 and the actuator
gate, with the same synthetic radar/plant inputs. This is not a comparison of
WaSR-T-only navigation against H5 navigation. WaSR-T is a segmentation model
that can operate without the H5 monitor; it does not itself steer the vessel.

The video is recorded MODD2 data and does not depict or react to the simulated
traffic. Replaying its warnings tests the cost and effect of an exogenous
slowdown signal. Any changed collision margin must not be presented as evidence
that H5 recognised the simulated hazard. A camera that responds to vessel pose
and sees the simulated obstacles is required for that stronger comparison.

## Fixed design

- Use `normal-transit-v1` (100 s) and `crossing-recoverable-v1` (120 s), with
  seeds 1000, 1001 and 1002 in both branches.
- Reuse cached WaSR-T temporal-fusion features from all four `kope75`
  calibration sequences, in sorted sequence order, separately for nominal
  and `occlusion-v1` input arms. Replay their synthetic ordering at 10 Hz;
  concatenate without looping and reset temporal context at each clip boundary.
- Reuse the pinned H5 reference and product threshold `2.731332008015018`.
  Check feature hashes against the existing proxy accuracy report. Do not
  refit H5, select a new threshold, choose favourable frames, or open sealed
  held-out sequences.
- Run both responses (off/on) for every scenario, arm and seed: 12 pairs,
  24 episodes. Alternate branch execution order by seed index.
- Use `local-acceptance-load-v1` timing in both branches. Cached H5 computation
  does not add inference latency; wall durations and deadline misses are retained.
- Disable A6 enforcement equally in both branches to isolate the effect on A5.
- The off branch still receives unknown camera-health context and its score.
  The on branch additionally receives `simulation_h5_warning`; the existing
  fixture applies the 1 m/s warning speed cap. A5 and the gate remain authoritative.
- Preserve the ordinary current required-source checks. The existing H_FIXED
  experiment leaf remains a supplemental protocol record; it does not replace
  navigation, radar or actuator health with a constant.

The script writes the protocol, source hashes and tape hashes before running
any episode. A two-second pilot tests the harness only, and is excluded from
the full comparison. The first pilot exposed a missing perception-context
argument during post-recovery-prime reassembly; the corrected pilot must
deliver decisions to the gate before the full study starts.

## Measures

Report paired collision, grounding and boundary counts; minimum hull
clearance where there is another vessel; first arrival within 15 m of the last
waypoint; final and minimum remaining route distance; warning proposal counts;
accepted/rejected gate decisions; missed deadlines and trace completeness.

Arrival delay is defined only when both branches reach the arrival region.
Failure to arrive by the scenario horizon is censored; do not substitute a
zero delay or extrapolate a trip completion time.

Only after all online episodes complete, load the private bounding-box proxy
labels and count warning requests on proxy-negative frames. Integrate their
request intervals as a cost proxy. These labels never appear in the replay
tape, observation, planner, fusion or controller input. A proxy-negative
warning is not proof that the resulting manoeuvre was unnecessary in the
unrelated simulated scene.

The nominal arm has no expected H5 warnings based on the earlier diagnostic
experiment. It is a control for incidental differences from wall timing.
Report those differences, rather than attributing them to H5.

## Reproduction

From the repository root, with the existing external data and run artifacts:

```bash
export PYTHONPATH=.:packages/contracts/python:packages/marine-environment:services/simulator:services/collector:services/fusion:services/assurance:services/gate:services/perception:services/neural-health:adapters/maritime:fixtures/decision-ai
.venv/bin/python -m experiment.evaluation.h5_control \
  --output ../horizon-runs/development/h5-control-paired-20260925-v1
```

The output directory must not exist. `--seconds 2 --seeds 1000` runs a short
pilot with explicitly censored outcomes. Full branch bundles are compressed
outside Git; `summary.json` contains results and paired deltas.
