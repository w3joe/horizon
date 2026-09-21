# A4 discrete plant-map barrier filter

## Research boundary

Wei et al., *Robust Model Predictive Control with Control Barrier Functions for
Autonomous Surface Vessels* (ICRA 2024), optimize four commanded thruster
forces for a six-state plant. That control-affine input does not match
Horizon's target-heading/target-speed interface. The paper's printed Equation
22 also gives `b1 = h - c` and `b2 = -h - c` while requiring both barriers to
be nonnegative for positive `c`; those signs cannot encode the stated
symmetric tube and are not copied here.

Marley et al., *Maneuvering with safety guarantees using control barrier
functions* (IFAC 2021), place a virtual unicycle at the guidance layer and rely
on a proven resilient ship controller to track it. Horizon has no independently
validated guidance-to-plant tracking tube, so that result cannot certify its
current target interface. Zeng, Zhang, and Sreenath's discrete-time CBF work
motivates imposing the barrier directly on a sampled nonlinear plant map, but
their guarantees do not transfer without Horizon-specific invariance,
disturbance, and feasibility proofs.

Primary sources:

- <https://senseable.mit.edu/papers/pdf/20240211_Wei-etal_RobustModel_ICRA.pdf>
- <https://hdl.handle.net/11250/2982156>
- <https://arxiv.org/abs/2007.11718>

## Implemented formulation

The model state is

`x = (N, E, heading, surge, sway, yaw_rate, rudder, thrust)`.

For each candidate target `u = (target_heading, target_speed)`, the filter
computes `F_delta(x, u)` with the authoritative fixed-step plant map. This map
includes the heading PD and speed controller, rudder and thrust first-order
lags, rate and magnitude saturation, live actuator limits, current estimate,
and the configured plant parameters. Cartesian velocity is never treated as a
direct input.

For each collision, boundary, corridor, or depth constraint, `h_i(x)` is a
signed geometric margin. Hull extent, configured minimum clearance, declared
bounded position/heading/speed error, contact age and motion, and bounded
current error reduce that margin. Covariance is not converted to a hard bound.
The finite search accepts a candidate only when

`h_i(F_delta(x, u)) - rho >= max(0, (1 - lambda * delta) * h_i(x))`

for every barrier. The defaults are `delta = 2 s`, `lambda = 0.2 / s`, and a
separate configured plant-model residual reserve `rho = 0.5 m`. These are
engineering settings for the synthetic plant. They have not been identified
as worst-case bounds for a physical vessel.

The search lattice contains the proposal, bounded speed levels, heading
offsets, and recovery-library turn directions. It is sorted by a declared
weighted squared change in heading and speed; the default heading weight is
four, so the first feasible point is the exact optimum of that finite set.
`primal_residual` is the maximum positive violation of the
displayed inequality. `dual_residual` is `null` because a finite nonlinear
search has no QP/NLP dual. Exhausting the host work deadline returns `timeout`;
an empty feasible set returns `infeasible`; missing hard bounds or an initially
violated safe set returns `invalid`.

Every selected command is then rechecked by the existing full-horizon nonlinear
rollout. That check preserves swept oriented hull collision, boundary,
corridor, depth-zone, actuator, and recovery-continuation semantics. A failed
or timed-out recheck falls back through the recovery/minimum-risk path.

## Claim limit

This is a model-appropriate finite engineering filter, not a formal CBF
certificate. The endpoint condition and configured residual do not prove
continuous-time forward invariance, robust sampled-data invariance, recursive
feasibility, or physical tracking performance. Such a claim would require a
validated disturbance/model remainder, actuator tracking bound, safe initial
higher-order state set, and proof covering saturation and zero-speed rudder
authority. The final rollout also remains finite prediction, not exact
reachability.

`A4-VQP` preserves the earlier point-velocity QP under its original version
`a4-provisional-kinematic-filter-full-plant-validation-v1`. It remains an
explicit baseline and is not described as a plant CBF.
