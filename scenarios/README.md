# Synthetic scenario fixtures

These files describe a fictional 3 km by 2 km-class harbor test area in NED
meters. They are synthetic and do not reproduce a real chart. Every episode
must record the scenario file SHA-256, seed, external-AI policy version, raw
observation-tape hash (when replayed), and exogenous fault schedule hash.

The physical suite covers normal transit, crossing, head-on, overtaking, benign
close pass, dense traffic, sensor timing, navigation loss, AIS/radar source
conflict, peer-intent conflict, actuator degradation and recovery,
boundary/depth constraints, and an explicitly initially unrecoverable case.
Physical water boundaries, operational corridors, and polygonal depth zones are
represented separately; the boundary/depth fixture exercises all three.
Parameter sweeps generate the larger development and held-out manifests; those
manifests belong to the experiment harness.

Fault definitions are private scenario configuration. Sensors express their
effects, but neither public snapshots nor online observations contain the fault
labels.

`source-plan/` maps S01--S22 to these physical fixtures, decision-AI fixture
policies, and explicit orchestration steps. Load the catalogue with
`horizon_sim.source_plan.load_source_plan_catalog`; validation rejects missing
cases, unimplemented simulator faults, unknown AI policies, path traversal, and
unordered orchestration. The recipe is evaluation configuration, not an online
input.

The simulator executes scheduled physical and sensor faults. The standalone
decision-AI process executes its named policies, for example
`fixtures/decision-ai/service.py --policy unsafe_straight`. A08 owns process
kill, transport-delay, and load injection. A07 owns recorded-perception and
explicit neural fault artifacts. Recipes retain those dependencies instead of
substituting a simulator fault or copying one physical fixture under a new
claim.

`singapore_traffic_mirror_synthetic.json` loads its traffic from the checked-in
CC0 `TrafficSnapshot` fixture under `fixtures/`. It contains no received AIS
records. The scenario schedules observable stale and dropout behavior while a
non-transmitting contact remains independently visible to simulated radar and
camera.
