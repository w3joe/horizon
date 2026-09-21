# Horizon fusion

Fusion consumes the collector on port 8105 and the replaceable decision AI on
port 8101. It estimates ownship from GNSS/IMU, uses actual actuator feedback,
associates radar/AIS contacts by geometry without trusting their identifiers,
and suppresses shared-ancestor evidence before weighting. Claimed peer intent
is retained separately from independently observed motion.

Run from the repository root:

```sh
PYTHONPATH=services/fusion python3.12 -m horizon_fusion.http_api
```

Port 8104 exposes `GET /v1/governor-input?branch=protected` as a raw,
schema-valid `GovernorInput`, or HTTP 503 with reason codes until a fresh
record exists. `GET /v1/evidence` and `GET /v1/diagnostics` expose bounded
lineage and source-quality evidence. The service never receives plant-writing
authority. Neural internals default to `output_only/unknown`; unvalidated
camera or representation signals never shrink geometric uncertainty or clear
occupied space.

Actuator capability stays degraded while it is based on public configured
limits. The response monitor only restricts that capability after at least
five feedback samples over a sufficiently excited command step, and only when
each feedback record is explicitly linked to the command observation through
collector ancestry. A few matching samples never promote the configured
limits to nominal. The evidence route reports the exact command and feedback
observation IDs used by an assessment.
