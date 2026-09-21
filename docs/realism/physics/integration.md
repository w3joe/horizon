# Simulator and assurance integration

## Selecting a mode

With no marine configuration, the simulator runs the unchanged
`synthetic-12m-3dof-v1` baseline. The higher-fidelity mode is explicit:

```sh
PYTHONPATH=services/simulator:packages/marine-environment \
python -m horizon_sim.http_api \
  --scenario scenarios/normal_transit.json \
  --marine-config configs/sea-state/sheltered-harbor-v1.json \
  --run-id marine-development
```

The simulator validates the versioned configuration before serving. Its
horizontal `integrate_step` remains the sole North/East/yaw owner. Marine wind,
wave, and current are converted to one effective environment and supplied to
that one call.

## Public and private boundaries

`SimulationSnapshot.ownship` gains display-only modeled heave, roll/pitch, and
angular-rate fields. `marine_environment` reports the config identity, current,
wind, sampled surface, exact wave basis, and physical qualification. The static
reference repeats config identity and states
`modeled_display_only_not_sensor_measurement` as attitude provenance.

These fields support synchronized rendering and recorded replay. They are not
silently converted into online navigation observations. The sensor suite still
provides the state used by the collector and fusion pipeline. Private truth
records include marine motion and effective draft for post-run scoring; no
private truth is added to online inputs.

Positive heave-down increases effective draft in the private grounding and
under-keel-clearance calculation. The public nominal hull dimensions remain
unchanged.

## Assurance qualification barrier

The public reference contains:

```json
{
  "operating_mode_qualification": {
    "plant_mode_id": "synthetic-12m-coupled-marine-v1",
    "physical_model_status": "characterized",
    "assurance_status": "unknown",
    "reason_codes": [
      "WITHIN_DECLARED_DEVELOPMENT_ENVELOPE",
      "MARINE_MODE_NOT_ASSURANCE_QUALIFIED"
    ],
    "config_sha256": "..."
  }
}
```

Fusion must map this to the required health source
`operating_mode_qualification`. Only the exact baseline plant mode is healthy
and available. Marine modes are unknown/unavailable until A02/A04 explicitly
version and validate new bounds; physical `characterized` is insufficient.
Both normal decisions and independent recovery must require this health source.
This allows the simulator and renderer to exercise marine physics while
preventing an old baseline certificate from authorizing the new dynamics.

## Horizontal projection

The marine package's `horizontal_projection` computes a conservative geometric
offset `sensor_height * abs(tan(max_abs_tilt))`. The static simulator reference
publishes the formula and assumed sensor height. It documents the
minimum attitude-dependent error that a future calibrated sensor transform
must incorporate. The current governor must not consume this value as a
qualified bound because camera-to-vessel pose, latency, and calibration error
remain unavailable. Those missing items are part of the reason marine
assurance stays unknown.

## Remaining validation

- Identify coefficients or RAOs for a selected vessel if one is adopted.
- Calibrate camera pose and attitude measurements with bounded error.
- Rerun the safety experiments under versioned marine configurations.
- Verify render and marine workloads cannot starve host command expiry,
  watchdog recovery, or sensing.
- Add traffic-vessel six-axis response only with explicit per-vessel models.
