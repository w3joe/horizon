# Horizon collector

The collector polls the simulator's public observation, snapshot, and
reference routes. It can also run a bounded, read-only AISStream shadow mirror
when `--aisstream-config` is supplied and `AISSTREAM_API_KEY` exists in the
server environment. It restamps receipt and validity onto the shared host
monotonic clock while preserving simulation event time and the upstream
receipt timestamp in lineage metadata. Its queue is bounded and duplicate or
out-of-order source sequences are rejected rather than made fresh again.

Run from the repository root:

```sh
PYTHONPATH=services/collector python3.12 -m horizon_collector.http_api
```

Port 8105 exposes `GET /health`, `GET /v1/diagnostics`,
`GET /v1/batch?branch=protected&after_cursor=0`, and bounded
`POST /v1/ingest`. `GET /v1/traffic/snapshot` returns at most 50 normalized,
pseudonymous live contacts. It excludes the API key, raw frames, MMSI
identifiers, reported names, and frame hashes. Capture loss and stale source
loss are separate counters.
The collector has no simulator gate or evaluation capability.

The simulator poller uses one atomic, bounded observation page with its public
snapshot, reference, and plant epoch. Its delivery cursor is independent of the
individual sensors' sequence counters. Unchanged pages do not reprocess history;
live resets restart the cursor in the new epoch. Transport overflow and backlog
are explicit, and fusion withholds proposals while either is unresolved. The
collector anchors validity before the HTTP request so transport time cannot make
an observation fresh again.

Live AIS remains `read_only_unscored`. The console automatically falls back to
the recorded mirror when the stream is unavailable, silent, or has no valid
contacts. Recorded and synthetic modes remain available for repeatable judging
and fault injection.
