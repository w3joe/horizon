# Marine response characterization

These checks characterize the synthetic equations and committed configuration;
they do not validate behavior against sea trials or a particular vessel.

## Hydrostatics and free response

For the declared 12 m × 3 m × 1 m hull:

| Quantity | Result |
|---|---:|
| Displacement volume | 18.0 m³ |
| Displacement mass | 18,450 kg |
| Waterplane area | 25.2 m² |
| Heave stiffness | 253,305.7695 N/m |
| Roll stiffness | 144,746.154 N·m/rad |
| Pitch stiffness | 2,171,192.31 N·m/rad |
| Undamped heave period | 2.0064 s |
| Undamped roll period | 2.5080 s |
| Undamped pitch period | 2.3393 s |

The calm-water test begins both at equilibrium and at a displaced state. The
equilibrium remains exactly fixed. A 0.25 m heave, 0.08 rad roll, and -0.05 rad
pitch displacement decays close to zero over 30 simulated seconds. The first
acceleration has the restoring sign in all three axes.

## Forced response

The following deterministic run used four seeded components, a constant 2 m/s
surge, 0.2 rad heading, and 15,000 steps (300 simulated seconds):

| Sea state | Max abs heave | Max abs roll | Max abs pitch | Status samples |
|---|---:|---:|---:|---|
| sheltered-harbor-v1 | 0.1924 m | 2.929° | 3.954° | 15,000 characterized |
| harbor-chop-v1 | 0.6359 m | 9.981° | 5.324° | 15,000 characterized |
| rough-water-unsupported-v1 | 1.6161 m | 16.447° | 4.845° | 15,000 unknown |

The rough-water case also exceeds hard configured wave, wind, and current
bounds, so it begins and remains `unknown`. It exists to verify that the
qualification barrier fails closed.

On the local Apple Silicon development machine, each 15,000-step isolated
marine response run took about 0.13 s. This measures the pure environment and
response functions, not HTTP, sensor, governor, rendering, or end-to-end
deadline behavior. A08 must characterize those workloads independently.

## Reproduction

Run the maintained tests with Python 3.12:

```sh
PYTHONPATH=packages/marine-environment:services/simulator \
  .venv/bin/pytest -q tests/marine-environment services/simulator/tests/test_marine_mode.py
```

The tests cover strict finite configuration parsing, exact seeded replay,
surface reconstruction from public components, equilibrium, restoring signs,
natural-period ranges, qualification, reset determinism, schema-valid public
snapshots, effective draft/under-keel clearance, and the assurance barrier.
