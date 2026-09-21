# Independent scoring rules

Only the evaluation process reads truth. The scorer recomputes `EvaluationRecord` fields from timestamped truth frames, stable violation-event IDs, decisions, and gate receipts. UI state, governor status flags, and predicted trajectories are not outcome labels.

- A collision, grounding, or boundary event is counted once by stable event ID, even if it spans several simulation frames.
- Signed margins are the minimum truth margins over the branch. Negative values are violations.
- An intervention is the first `modify`, `recover`, or `minimum_risk` decision. Lead time is the sampled last validated recovery opportunity on a paired nominal/unprotected reference continuation minus the first intervention time; protected-branch states cannot define this boundary because a successful intervention may restore recoverability. Missing independent reference evidence produces unknown lead time. A negative value means the intervention was late relative to that sampled reference.
- False intervention rate uses only scenarios frozen as benign before execution.
- Route delay and extra distance are differences from the scenario's frozen nominal branch only when the mission completes. Collision, timeout, truncation, and non-completion produce censored/null cost rather than rewarding an early stop. Recovery duration remains observed time under recovery authority.
- Unsafe/stale command acceptance is independently derived by joining authorized proposal source, proposal and decision expiry, decision validity/authority, receipt time, and gate acceptance. Missing join evidence is `unknown`, not a trusted zero. A producer-supplied unsafe flag is ignored.
- A trace is complete only if every scored decision has a matching gate receipt and the required artifact hashes are present.

Collision prevention and mission cost are unavailable for `controller_isolation_replay`. That mode reports decision actions, response time, deadline misses, solver status, and margins predicted by the candidate, clearly labeled as response evidence.

The safety gate is applied before Pareto ranking. Any preventable held-out violation, unsafe/stale gate acceptance, undefined timeout/infeasibility behavior, or deadline miss at declared load excludes a candidate. Surviving candidates are compared on mission cost, false interventions, safety margin, and lead time. The simplest candidate on the Pareto frontier wins unless added complexity produces a material, repeatable benefit. H4 is selected only if it improves held-out fault performance over H1-H3 after false alarms and compute cost are included.
