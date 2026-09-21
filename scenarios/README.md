# Synthetic scenario fixtures

These files describe a fictional 3 km by 2 km-class harbor test area in NED
meters. They are synthetic and do not reproduce a real chart. Every episode
must record the scenario file SHA-256, seed, external-AI policy version, raw
observation-tape hash (when replayed), and exogenous fault schedule hash.

The initial suite covers crossing, head-on, overtaking, benign close pass,
dense traffic, sensor timing, AIS/radar source conflict, actuator degradation,
boundary/depth constraints, and an explicitly initially unrecoverable case.
Parameter sweeps generate the larger development and held-out manifests; those
manifests belong to the experiment harness.

Fault definitions are private scenario configuration. Sensors express their
effects, but neither public snapshots nor online observations contain the fault
labels.
