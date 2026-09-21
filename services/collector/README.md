# Horizon collector

The collector polls only the simulator's public observation, snapshot, and
reference routes. It restamps receipt and validity onto the shared host
monotonic clock while preserving simulation event time and the upstream
receipt timestamp in lineage metadata. Its queue is bounded and duplicate or
out-of-order source sequences are rejected rather than made fresh again.

Run from the repository root:

```sh
PYTHONPATH=services/collector python3.12 -m horizon_collector.http_api
```

Port 8105 exposes `GET /health`, `GET /v1/diagnostics`,
`GET /v1/batch?branch=protected&after_cursor=0`, and bounded
`POST /v1/ingest`. Capture loss and stale source loss are separate counters.
The collector has no simulator gate or evaluation capability.
