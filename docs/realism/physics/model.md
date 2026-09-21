# Coupled marine environment model

`synthetic-12m-coupled-marine-v1` is a deterministic development model for
Horizon's synthetic 12 m hull. It adds sea-state forcing and heave, roll, and
pitch to the existing authoritative horizontal plant. It is not an identified
model of a real vessel, a CFD result, or a response-amplitude-operator (RAO)
model.

## Reuse decision

The implementation follows the decomposition documented in Thor I. Fossen's
[Marine Craft Model](https://www.fossen.biz/html/marineCraftModel.html):
inertia, damping, hydrostatic restoring, and external forces. Two existing
implementations were evaluated:

- [Python Vehicle Simulator](https://github.com/cybergalactic/PythonVehicleSimulator)
  is a useful MIT-licensed Python reference with ships and USVs. Its included
  vessels do not represent Horizon's synthetic 12 m hull, so importing one
  would substitute unvalidated coefficients and geometry.
- [Marine Systems Simulator](https://github.com/cybergalactic/MSS) provides a
  broad six-degree-of-freedom MATLAB/Octave reference. It is not a suitable
  runtime dependency for the Python simulator, and vessel-specific RAOs still
  require hydrodynamic data that Horizon does not have.

Horizon therefore implements the small standard-library model in
`packages/marine-environment`. It reuses the published physical structure and
coordinate conventions, not source code or coefficients from either project.
The existing 3-DOF plant remains the sole surge/sway/yaw and North/East
integrator.

## State and equations

The horizontal state remains

```text
[north, east, yaw, surge, sway, yaw-rate]
```

The marine response state is

```text
[heave-down, roll, pitch, heave-rate-down, roll-rate, pitch-rate]
```

Each added response mode uses the linear equation

```text
M q_ddot + C q_dot + K (q - q_target) = external moment
```

with semi-implicit Euler integration at the simulator's fixed 0.02 s step.
Hydrostatic coefficients use displaced volume, waterplane area, and declared
synthetic metacentric heights. Damping is expressed by modal damping ratios.
Wind uses relative air velocity and quadratic drag. The wave surface is a
bounded seeded sum of deep-water components. Its first-order slopes drive
target roll and pitch; surface height and vertical velocity drive heave. A
small documented wave-drift force excites the existing horizontal plant.

The force model and horizontal dynamics are advanced once per plant tick.
There is no second renderer-owned position integrator and no duplicate force
application. Traffic receives the same time-varying current in kinematics but
does not receive six-axis response in this version.

## Coordinates and shared wave phase

- World coordinates are North-East-Down (NED), in metres.
- Heading/yaw is clockwise from north.
- Body x is forward, body y starboard, body z down.
- Heave is positive down from the nominal waterline.
- Roll is positive starboard-down; pitch is positive bow-up.
- Surface elevation is positive up.
- Wave direction is the travel direction clockwise from north.

The public wave components reproduce the exact surface used by the force
model:

```text
eta_up(t,n,e) = sum(
  amplitude * cos(
    wave_number * (n*cos(direction) + e*sin(direction))
    - angular_frequency*t
    + phase
  )
)
```

At most 16 components are accepted; the committed sea states use four. The
snapshot carries the components so a recorded replay can render the same
surface without an untracked animation seed.

## Declared coefficients and limits

The default hull declares length 12 m, beam 3 m, equilibrium draft 1 m, block
coefficient 0.50, waterplane coefficient 0.70, seawater density 1,025 kg/m³,
and displacement 18,450 kg. Metacentric heights and drag/damping coefficients
are synthetic assumptions in `VesselHydrostatics`.

Each sea-state JSON contains both a development envelope and a hard unsupported
envelope. Within the development envelope the *physical model status* is
`characterized`, meaning only that deterministic tests and the response checks
in this repository cover those declared inputs. Between the bounds it is
`degraded`; beyond a hard bound it is `unknown`. This wording is not a real
vessel validation claim.

Marine mode always publishes `assurance_status: unknown` and
`MARINE_MODE_NOT_ASSURANCE_QUALIFIED`. The baseline RTA certificate is never
inherited from the 3-DOF mode solely because the marine response remains inside
its physical development envelope.

