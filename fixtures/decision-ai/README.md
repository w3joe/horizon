# External decision-AI fixtures

This directory contains a standalone, replaceable decision-making process. It
is intentionally outside both the simulator and assurance services.

Policies:

- `nominal`: slows and turns starboard near a reported contact.
- `unsafe_straight`: commands maximum speed without avoiding contacts.
- `expired`: emits a command that is already expired.
- `stale_lineage`: reports an input snapshot it did not consume.
- `malformed`: returns JSON with a nonnumeric command speed for boundary tests.

Run it with Python 3.12:

```sh
/opt/homebrew/bin/python3.12 fixtures/decision-ai/service.py --policy nominal
```

The default fixture port is `8101`; the simulator defaults to `8100`.

The process receives only the public/noisy display snapshot. It has no plant
authority and no evaluation-truth capability.

`FixturePolicy(mode, monotonic_ns=...)` accepts an explicit monotonic callable
for `AIInferenceTrace` start/completion timestamps. Production defaults to
`time.monotonic_ns`; deterministic offline harnesses must pass their shared
manual clock and advance it by the measured fixture work they intend to model.
Proposal simulation timestamps remain tied to the consumed snapshot and are
never refreshed on receipt.
