# External decision-AI fixtures

This directory contains a standalone, replaceable decision-making process. It
is intentionally outside both the simulator and assurance services.

Policies:

- `nominal`: slows and turns starboard near a reported contact.
- `unsafe_straight`: commands maximum speed without avoiding contacts.
- `expired`: emits a command that is already expired.
- `stale_lineage`: reports an input snapshot it did not consume.

Run it with Python 3.12:

```sh
/opt/homebrew/bin/python3.12 fixtures/decision-ai/service.py --policy nominal
```

The process receives only the public/noisy display snapshot. It has no plant
authority and no evaluation-truth capability.
