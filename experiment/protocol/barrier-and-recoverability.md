# Barrier filtering and recoverability protocol

Horizon uses a horizontal 3-DOF vessel state with north/east/yaw pose and surge/sway/yaw-rate velocity, plus explicit actuator state and lag. This follows the structure of [Fossen's marine-craft equations](https://www.fossen.biz/html/marineCraftModel.html); synthetic hull coefficients remain assumptions until characterized.

A4 is credited as a model-scoped barrier filter only when its manifest states:

- the exact control-affine plant or tracking abstraction;
- the differentiable barrier geometry and its relationship to the swept hull;
- relative degree by input channel;
- actuator, disturbance, estimation, solver, and sampled-data margins;
- the conditions that make the optimization a convex QP; and
- infeasibility, invalid-residual, timeout, and final-command revalidation behavior.

Position separation does not generally depend on rudder at its first derivative. Explicit rudder/thrust lag can increase relative degree beyond the force-input model. [Xiao and Belta](https://arxiv.org/abs/1903.04706) define high-order CBF conditions for constraints whose control appears only after repeated differentiation; their theorem applies under its stated smoothness, initial-set, and controller assumptions. It does not validate an arbitrary ship implementation.

A center-point or circular separation barrier is not treated as swept-hull clearance. A velocity-command CBF may be evaluated as a tracking-layer engineering filter, but its final command is independently rolled out through the same 3-DOF plant and swept-hull checker used for all candidates. Solver success alone is not evidence that the accepted trajectory is safe.

Recoverability means that the independent checker found a complete maneuver from the configured recovery library whose rollout satisfies collision, water-boundary, depth, actuator, and supported navigation-rule constraints under the declared uncertainty/bounds. A finite maneuver library is incomplete, so failure to find a maneuver is reported as `no validated recovery`, not proof that none exists. Horizon survival without a checked terminal continuation does not count as recoverability. This engineering use is motivated by the terminal safety reasoning in [Wabersich and Zeilinger's predictive safety filter](https://arxiv.org/abs/1812.05506), but no theorem transfers until Horizon verifies the paper's assumptions for its own model and implementation.

[Paine et al.](https://arxiv.org/abs/2601.11335) report a January 2026 USV CBF preprint with simulated and physical-vessel experiments. Horizon treats it as a reproduction/comparison target. Its reported results do not establish guarantees for Horizon's hull, traffic model, or controller.

The `last_recovery_opportunity_s` metric is the last sampled time at which the independent reference library validated a continuation. It is explicitly a sampled engineering quantity, not an exact viability-kernel boundary.
