# A6 Singapore policy shadow: bounded first slice

Historical shadow-mode documentation. The product also implements
[gate-owned A6 enforcement](a6-policy-enforcement.md); the non-authority claims
below apply to `A6PolicyShadow`, not to that enforcement wrapper.

## Claim boundary

A6 is a deterministic engineering-policy shadow. It does not determine legal
compliance, does not certify COLREG compliance, and is not navigation advice.
It never creates an `AssuranceDecision`, enters the candidate registry, calls
the gate, or writes an actuator. A5 remains the physical-safety authority: only
an identity-matched, valid A5 `pass` or `modify` can receive positive shadow
support, and that support is still observation-only.

The slice covers a generic 12 m, power-driven USV operated by the Singapore
government on a non-commercial service, below 300 GT and 50 m, with remote
supervision, no passengers, tow, or hazardous cargo, in daylight and clear
visibility. Any fact missing from that profile makes applicability `unknown`;
an affirmative fact outside it makes the slice `not_applicable`. Both states
withhold support. Restricted visibility is unsupported and therefore unknown,
not silently treated as clear visibility.

Weapons, targeting, classified doctrine, and rules of engagement are excluded.
No fields or behavior for those domains belong in this shadow.

## Inputs and outputs

`A6PolicyShadow.evaluate` receives:

- the consumed `GovernorInput`, for identity only;
- the already-produced A5 decision, which is never modified;
- explicit operational-profile facts;
- bounded rule evidence;
- a hash-pinned `PolicyBundle`; and
- an explicit UTC evaluation time.

The caller supplies time, so replay never depends on the host clock. The same
JSON-shaped inputs return the same assessment and content-derived assessment
ID. The output record has `control_authority: none`, no issued command, and a
`shadow_support` value of `supported` or `withheld`.

Every finding has three-valued applicability:

| Applicability | Meaning |
| --- | --- |
| `applicable` | The bounded facts establish that the proxy applies. |
| `not_applicable` | An explicit fact puts the rule or operation outside this slice. |
| `unknown` | Evidence, classification, bundle validity, or scope is unresolved. |

An applicable finding is `satisfied`, `not_satisfied`, or `unknown`.
Non-applicable findings are `not_evaluated`. Any `unknown` or `not_satisfied`
finding withholds support.

## Rule evidence

R5 requires fresh visual, auditory, radar, and remote-supervisor availability
flags. It is only an availability proxy; it does not assert that a legally
proper lookout was kept.

R6 compares commanded speed with the bundle's clear-visibility speed bound and
checks that engineering stopping distance plus reserve fits within the declared
clear distance. It also requires an available traffic assessment. These are
engineering bounds, not a legal definition of safe speed.

For each in-sight power-driven contact, R7 requires doubt to be treated as
collision risk. R8 uses caller-provided action lead time, course change, speed
reduction, and detectability against hash-covered engineering thresholds.

R13-R15 classify overtaking, head-on, and crossing geometries from signed
relative bearings and course difference. Positive relative bearing and
positive course change mean starboard. Values inside the bundle's ambiguity
band at a classification boundary return `unknown`. R16 evaluates the same
early/substantial-action proxy for a give-way duty; a head-on proxy additionally
requires a positive/starboard course change. R17 evaluates declared maintenance
of course and speed for a stand-on duty. These are shadow classifications and
duties, not legal conclusions.

## Bundle provenance

`PolicyBundleMetadata` contains the bundle ID and version, inclusive start and
exclusive end UTC validity, a SHA-256 digest of canonical bundle content, and
source pins. Each `PolicySourcePin` names an exact revision, caller-computed
SHA-256 digest, and either `runtime_authority` or
`design_assurance_reference`. A bundle needs at least one runtime-authority
pin. The implementation provides interfaces and verification only; it includes
no placeholder or invented source digest.

The MASS Code can be recorded as a `design_assurance_reference` only. It is not
treated as Singapore runtime law and cannot replace a runtime-authority pin.

## Known limitations

- No restricted-visibility evaluation is implemented.
- The encounter geometry is planar and consumes upstream observations; it does
  not establish sensor truth.
- R5, R6, and R8 are evidence/engineering proxies, not full legal tests.
- This slice does not cover the remaining COLREG rules or local port directions.
- A6 is not connected to the live assurance-to-gate control loop.
