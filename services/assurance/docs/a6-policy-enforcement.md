# A6 command policy enforcement

The product launcher and standalone gate now default to `--a6-mode enforce`.
The normal command path is A5 physical assurance, gate-owned A6 policy
authorization, final gate validation, then plant dispatch. A6 is a restrictive
authorization layer over A5, not a sixth interchangeable physical solver.
`A6PolicyShadow` remains available to reproduce earlier research.

## Command behavior

- Normal A5 `pass` / `modify` requires a supported A6 assessment of the exact
  command and snapshot. No externally supplied authorization is accepted.
- A6 withholds on missing, expired, malformed, mismatched, synthetic operational
  evidence, unsupported scope, unresolved rule findings, or its 10 ms compute
  budget being exceeded. This budget is checked after evaluation; it is not
  thread preemption. The independently running watchdog can still take over.
- Withholding substitutes a fresh stored recovery, rechecked against the current
  input. If none exists or it fails that check, the gate derives its canonical
  minimum-risk command and marks assurance `unknown`. This is not a claim that
  any maneuver is universally safe.
- A5 recovery, minimum-risk, recovery-latch substitutions and watchdog actions
  retain safety authority, with `A6_EMERGENCY_POLICY_OVERRIDE`. An override is
  explicitly not policy authorization. Recovery release still requires the
  operator handshake and clear-decision hysteresis.
- Identity, replay, physical safety, epoch/generation and source-expiry checks
  remain mandatory. An authorization expires at the earliest A5 decision,
  cycle deadline, policy evidence, or policy-bundle expiry. Expiry is rechecked
  immediately before dispatch.

`PolicyEvidence` and `A6PolicyDecision` are shared JSON Schema contracts with
generated Python/TypeScript types. Policy decisions hash the complete A5 input,
decision, evidence, and bundle (including validity metadata). They appear in
gate telemetry. Gate health reports the configured mode and cumulative counts;
override counts count actuation reservations, not guaranteed plant receipts.
The console displays the mode and authorization/withholding counts.

## Operational configuration and trusted evidence

Use `scripts/launch.py --a6-policy-config /absolute/deployment.json
--a6-evidence-file /absolute/policy-evidence.json`. Omitting these paths keeps
enforcement enabled, but normal autonomy is withheld. Legacy A1–A5 experiments
must explicitly choose `--a6-mode disabled`; test-library `GateConfig` retains
that default for existing research callers.

The deployment JSON has this structure (values below are descriptions, not a
ready-to-authorize vessel configuration):

```json
{
  "provenance": "recorded",
  "bundle": {
    "metadata": {
      "bundle_id": "operator-assigned-id",
      "version": "operator-assigned-version",
      "content_sha256": "SHA256_FROM_policy_content_sha256",
      "valid_from_utc": "ISO_8601_WITH_OFFSET",
      "valid_until_utc": "ISO_8601_WITH_OFFSET",
      "source_pins": []
    },
    "parameters": {}
  },
  "source_artifacts": {"source-id": "relative/path/to/exact/source-artifact"}
}
```

Populate the existing `PolicySourcePin` and `ShadowPolicyParameters` fields.
At least one runtime-authority pin is required. The loader hashes every source
artifact and rejects mismatches, synthetic deployment provenance, stale bundles
and invalid parameters at startup. It freezes this bundle until process restart.
MASS design-assurance references cannot replace the runtime-authority source.
Matching source bytes does not establish legal applicability or approval.

A trusted adapter atomically replaces the evidence file per snapshot. It uses
the `PolicyEvidence` schema: run, branch, tick, canonical snapshot hash,
observation/expiry monotonic timestamps, recorded provenance, operational context,
and rule evidence. The adapter must share the gate's monotonic clock domain.
It is independent of the decision AI and must preserve unavailable facts.
Context includes explicit `in_narrow_channel: false` and
`in_traffic_separation_scheme: false`; true or unknown is outside this slice.

For precomputed command-specific evidence, set `command_sha256` to the exact
issued-command hash and supply its conservative stopping distance. For live
command-independent observations, the file adapter also accepts
`command_sha256: null` together with positive
`evidence.safe_speed.minimum_deceleration_mps2` and nonnegative
`maximum_response_time_s`. It computes stopping distance using the greater of
current and commanded speed and binds the result to the actual command before
schema validation. Source observations are still snapshot bound. Course change,
speed reduction, detectability and course/speed maintenance are always derived
from the actual command, overriding any adapter action claims.

## Scope and evidence limits

This release enforces the existing bounded engineering proxies for R5–8 and
R13–17. It does not encode all naval policies or establish legal compliance.
Restricted visibility, channels/TSS, contacts outside the supported power-driven
in-sight class, and unknown context withhold authorization. Weapons, targeting,
classified doctrine and ROE are excluded. No operational policy approval,
auditory-lookout sensor or full vessel policy feed is fabricated by this release.

The shipped control change is veto-and-recovery enforcement of A5's single
selected command. It does not yet search a set of alternative A5 maneuvers to
maximize progress after a policy veto. The benchmark's synthetic policy/evidence
provider is confined to `experiment/` and is never enabled by the product CLI.

## Verification

Run `./scripts/check.sh`. The enforcement tests exercise exact-command
authorization, source/command/tick mismatches, expiry, malformed evidence,
unsupported scope, source-pin validation, replay, reset during validation,
watchdog independence, and a real multi-process policy-denial path.

`scripts/benchmark_a6_enforcement.py` runs three normal-transit seeds with A5
alone, A6 permissive policy, and A6 speed veto. It asserts positive authorization
coverage and absence of normal-authority actuation under the restrictive policy.
The separate `a6-a32-enforcement-development.json` manifest retains the original
four-scenario, three-seed, 30-second design. Its recovery-heavy episodes cannot
establish useful positive-policy coverage by themselves.
