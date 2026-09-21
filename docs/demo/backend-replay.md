# Recorded backend replay

The polished console demo uses a frozen recording by default so every viewer sees the same finite comparison. Live mode remains a separate console mode and continues to use the seven local services.

`scripts/demo_capture.py` records `unsafe-route-v1` from the real local service stack on ephemeral loopback ports. It pauses and resets the protected S22 branch, creates an unprotected branch from that exact paused state, applies the declared `unsafe_straight` command only to the unprotected branch, then resumes both branches on the same fixed-step simulator clock. The protected branch continues through the normal decision AI, fusion, assurance, independent recovery, exclusive gate, and plant paths. The recorder never writes an alternate command to the protected branch.

The replay timeline contains public `SimulationSnapshot` values for both branches. It also contains public AI proposals, assurance decisions, gate receipts, and gate events as `{time_s, record}` entries. A protected command annotation appears only when an accepted public receipt has the same command ID as a public snapshot's `active_command_id`. The intervention record uses that same match and reports the mechanism and reason codes actually emitted by the gate. It does not infer intent from the unsafe policy label or attribute watchdog intervention to an assurance candidate.

Private evaluator records are read only after the 45-second control run. They are reduced to collision count, first collision time, hull-clearance aggregates, and final position. Raw truth records, evaluator capabilities, service capabilities, and service logs are never written to the presentation artifact. The replay labels this block `evaluation_only_post_run` so the console can keep outcome scoring visually separate from information available to online control.

Create the frozen artifact from a clean commit with Python 3.12:

```sh
cd /path/to/horizon
.venv/bin/python scripts/demo_capture.py \
  --output ../horizon-runs/demo/unsafe-route-v1
```

The recorder refuses a dirty repository and refuses to overwrite an existing artifact unless `--replace` is explicit. It writes `manifest.json` and `replay.json` atomically and enforces a combined 10 MiB limit. The manifest pins the source commit, scenario version and hashes, seed, policy, environment, replay hash, and limitations.

The same-origin console proxy exposes only these read-only routes:

- `GET /api/demo/catalog` returns `{schema_version: "horizon.demo-catalog.v1", runs: [...]}`. Each run is its validated manifest plus its `replay_url`.
- `GET /api/demo/runs/unsafe-route-v1` returns `{manifest, ...replay}` after checking the directory ID, schemas, clean-source marker, size cap, and replay SHA-256.

Run IDs must match a narrow lowercase allowlist. The proxy does not expose directory browsing, filenames, logs, capability files, evaluator endpoints, or arbitrary JSON paths. A missing, oversized, malformed, dirty-source, or hash-mismatched artifact is omitted from the catalog and returns `404` by ID.

This remains a deterministic development demonstration rather than field validation. The counterfactual uses a held 6 m/s course command, while the protected branch receives fresh external proposals. Sequential HTTP reads of the two branches can differ by one fixed 20 ms simulator step even though both branches advance in the same locked runtime loop.
