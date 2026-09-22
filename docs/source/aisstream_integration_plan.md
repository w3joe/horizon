# AISStream integration plan for a Singapore-area Horizon demo

**Status:** feasible as an optional, backend-only live civilian-AIS overlay, subject to service availability and a written data-use decision. This document is an implementation handoff. The A11 planning work did not request or handle an API key; a separate coordinator-run, sanitized feasibility smoke is summarized below.

**Research snapshot:** 2026-09-22. Recheck the linked service documentation, schema, availability reports, and data-use terms immediately before implementation or any public demonstration.

## Decision and safety boundary

AISStream can supply reported positions and identity data for participating civilian vessels around Singapore. It cannot reliably “pinpoint every ship”: AIS is cooperative, intermittent, terrestrially received, self-reported, and vulnerable to stale, erroneous, duplicated, reused, or spoofed identities. Some government or defence vessels may omit, restrict, or manipulate transmissions. Horizon must label each marker **AIS-reported**, show last-seen age and uncertainty, and never present coverage as complete surveillance.

The first implementation should be a read-only live overlay, isolated from deterministic recorded and synthetic experiments:

```text
AISStream WSS (server side only)
       │ binary UTF-8 JSON
       ▼
bounded receiver queue ──► parser/validator ──► per-MMSI live cache
       │                         │                       │
       │                         └── diagnostics         └──► read-only console overlay
       │
       └── optional external recording (off by default; never in Git)

radar / lidar / camera ──► fusion ──► assurance ──► gate ──► simulated plant
                     AIS may later add conservative evidence only ──┘
```

Live AIS must not replace a scenario's traffic, become evaluation truth, make a recorded run nondeterministic, delete a radar-supported track, reduce a radar-derived uncertainty envelope, establish that water is clear, or directly authorize any command. An AIS-only report may add a conservatively treated possible obstacle or appear on the operator overlay. A contradictory AIS report remains explicit contradicting evidence while the radar-supported contact persists.

### Bounded feasibility evidence

On 2026-09-22, the coordinator ran a 25.941-second server-side smoke against the Singapore Strait vicinity development box `[[1.10, 103.45], [1.55, 104.20]]`. The service returned a `SubscriptionConfirmation`, two `PositionReport` messages, and one `ShipStaticData` message. All three messages exposed finite, in-range metadata positions. Observed metadata keys were `MMSI`, `MMSI_String`, `ShipName`, `latitude`, `longitude`, and `time_utc`. No raw vessel record or API key was retained. The sanitized result is external to Git at `horizon-runs/aisstream/singapore-smoke-20260922/result.json` with SHA-256 `4f0dc1a26b358ebce108c18deb4146b613aa5ab55033721d1aad16bdc1f24fe2`.

This establishes endpoint and envelope-shape feasibility at one time, from one network, for two of the five requested message types. It does not establish continuous Singapore coverage, latency, completeness, identity authenticity, safe navigation use, an SLA, data-display rights, or the behavior of the three other selected message types. The trial box is broad development context and has no operational chart meaning.

Proceed through the following gates:

1. **Protocol gate:** mock-server tests pass without a real key.
2. **Private live gate:** a local, time-bounded connectivity trial succeeds with a user-provided server secret and produces complete drop/availability diagnostics.
3. **Fusion gate:** adversarial association tests prove that false or disappearing AIS cannot erase or relax noncooperative sensor evidence.
4. **Display/retention gate:** obtain and record permission or governing terms for the intended display, screenshots, storage, and redistribution. Until then, keep live data operator-local, disable recording by default, and use clearly synthetic or separately licensed data in public materials.

## What the official interface supports

The current official documentation specifies `wss://stream.aisstream.io/v0/stream`, `permessage-deflate`, and one complete subscription within three seconds of connection. The API key belongs in the JSON field `APIKey`; it must remain in a server-side environment variable. Bounding boxes are required. `FiltersShipMMSI` and `FilterMessageTypes` are optional. A replacement subscription replaces the old one, may be sent at most once per second, and should yield `SubscriptionConfirmation`. The service sends binary WebSocket frames containing UTF-8 JSON; fragmented subscription messages are unsupported.

Horizon should subscribe only to:

- `PositionReport` — Class A dynamic position reports;
- `StandardClassBPositionReport` — Class B dynamic position reports;
- `ExtendedClassBPositionReport` — extended Class B position plus selected static fields;
- `ShipStaticData` — Class A static/voyage information; and
- `StaticDataReport` — Class B static data parts A/B.

The receive path must select the typed object at `Message[MessageType]`, retain `MessageType`, and treat `MetaData` as provider-normalized context rather than an independent source. Static messages update a bounded identity/dimensions cache; they do not create a position without a valid dynamic report.

The service documents three subscribed connections per account, three open connections per originating IP, continuous reading, exponential reconnect backoff with jitter, no SLA, and no durable replay. It also says uncompressed connections become subject to per-user bandwidth limits beginning in September 2026. Horizon therefore enables `permessage-deflate`, uses one connection, and does not promise availability. Open reports in the provider's official issue trackers described sockets that connected but delivered no messages during parts of 2026; these are user reports, not a provider status statement, but justify a silence watchdog and an offline demo fallback.

## Singapore-area configuration

Store configuration without secrets in `configs/maritime/aisstream-singapore-demo.json`. A proposed development default is:

```json
{
  "schema_version": "0.1.0",
  "endpoint": "wss://stream.aisstream.io/v0/stream",
  "region_id": "singapore-area-demo-v1",
  "bounding_boxes_lat_lon_deg": [
    [[1.10, 103.55], [1.50, 104.15]]
  ],
  "local_ned_origin_wgs84": {
    "latitude_deg": 1.25,
    "longitude_deg": 103.85,
    "height_m": 0.0
  },
  "message_types": [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "ShipStaticData",
    "StaticDataReport"
  ],
  "position_ttl_s": 30,
  "static_ttl_s": 21600,
  "silence_degraded_s": 30,
  "silence_reconnect_s": 90,
  "raw_queue_capacity": 4096,
  "track_capacity": 4096,
  "recording_enabled": false
}
```

These coordinates are a broad **demo area of interest**, not Singapore port limits, traffic-separation geometry, navigational chart data, rules-of-the-road geometry, or an operating authorization. Normalize each box to `(min_lat, min_lon, max_lat, max_lon)` before serializing it as two `[latitude, longitude]` corners. Reject antimeridian-spanning or overlapping boxes in v1; overlapping subscriptions could produce duplicates. The origin is an arbitrary local visualization reference, not a waypoint. Later west/central/east presets may be added only as named visualization presets with the same disclaimer.

## Normalized observation contract

One received dynamic frame becomes one `Observation` initially. If message rate requires batching, a batch must contain a bounded list and preserve a unique lineage item for each source frame. Proposed fields are:

```json
{
  "contract_type": "Observation",
  "schema_version": "0.1.0",
  "observation_id": "<run>:protected:aisstream:<connection-epoch>:<sequence>",
  "run_id": "<active-run>",
  "branch_id": "protected",
  "input_group": "obstacle_perception",
  "source_id": "aisstream",
  "sequence": 123,
  "time": {
    "event_time_s": 17.2,
    "received_monotonic_ns": 123456789,
    "valid_until_monotonic_ns": 153456789,
    "clock_uncertainty_ms": 1000.0
  },
  "units": "position:m; speed:m/s; angle:rad",
  "frame": "NED",
  "capability": "degraded",
  "provenance": {
    "kind": "recorded",
    "source_id": "aisstream.io-live",
    "artifact_uri": null,
    "sha256": null,
    "rights": "unresolved; public display and retention disabled pending review"
  },
  "payload": {
    "payload_version": "aisstream-contact-v1",
    "provider_message_type": "PositionReport",
    "mmsi": "123456789",
    "reported_name": null,
    "latitude_deg": 1.25,
    "longitude_deg": 103.85,
    "position_ne_m": [0.0, 0.0],
    "sog_mps": 4.1,
    "cog_rad": 1.57,
    "true_heading_rad": null,
    "navigation_status_code": null,
    "position_accuracy_reported": false,
    "raim_reported": false,
    "provider_valid": true,
    "provider_event_utc": null,
    "receiver_utc": "2026-09-22T12:00:00Z",
    "ais_utc_second": 42,
    "connection_epoch": 3,
    "source_frame_sha256": "<sha256>",
    "identity_generation": 1,
    "conflict_flags": [],
    "position_sigma_m": 100.0,
    "hull": {"length_m": 20.0, "beam_m": 6.0},
    "_collector": {"ancestor_ids": ["aisstream-frame:<sha256>"]}
  }
}
```

The contract patch should make this payload typed and bounded rather than leaving its shape implicit. MMSI stays a nine-character string to preserve leading zeroes. Unknown values remain `null`; AIS sentinel values must not be converted into plausible headings, speeds, or coordinates. Do not infer hull dimensions from a vessel name or type without a declared, conservative assumption.

Validate finite latitude in `[-90, 90]`, longitude in `[-180, 180]`, membership in the configured box plus a small documented numeric tolerance, nine decimal MMSI digits, provider `Valid`, finite nonnegative speed, and documented AIS sentinel ranges. Compare body and metadata coordinates when both exist. A disagreement becomes a conflict; metadata must not silently override the typed body.

### Time mapping

Keep four notions separate:

- local monotonic receipt time, captured before decode;
- local UTC receipt time, for operator audit;
- provider `MetaData` UTC when present and parseable; and
- AIS `Timestamp`, which is a reported UTC second within a minute and **not an epoch timestamp**.

For the read-only overlay, store UTC and calculate last-seen age from a monotonic receipt anchor. For any later simulator/fusion bridge, maintain an explicit mapping sampled from `(host_monotonic_ns, host_utc, simulation_time_s)`. Map provider event UTC to simulation-relative `event_time_s` using estimated age; if provider UTC is absent, use receipt time and enlarge uncertainty. `clock_uncertainty_ms` must include timestamp resolution, wall/monotonic mapping error, transport-age uncertainty, and any missing-provider-time penalty. The current fusion contact gate rejects clock uncertainty above 50 ms, which ordinary internet AIS is unlikely to satisfy. A05 must implement a source-specific policy or keep AIS overlay-only; it must never declare an artificially small value just to pass the current gate.

### WGS84 to local NED

Declare and hash the WGS84 origin in every run manifest. Convert `(latitude, longitude, height)` to WGS84 ECEF and rotate the ECEF delta into local NED at the declared origin. For surface AIS with no altitude, use declared height `0 m`, retain `height_unknown=true`, and consume north/east only. Test the implementation against an independent geodesy reference at the origin, axis-aligned short offsets, and the corners of the configured area. Never reuse the conversion as chart, depth, boundary, or collision-ground-truth data.

## Identity, ordering, and conflict rules

- Increment a local sequence per accepted frame and connection epoch. On reconnect, increment `connection_epoch`; do not let a provider reconnect make older data fresh.
- Deduplicate within a bounded time window using a hash over message type, MMSI, dynamic fields, provider time, and normalized coordinates. Count duplicates. Do not assume retransmissions from different receiving stations are independent evidence.
- Reject a late position from replacing a newer per-MMSI position. Preserve its hash and increment the out-of-order counter; an old report never extends TTL.
- Treat an MMSI as a reported identifier, not authenticated identity. After a long gap, impossible geographic jump, or incompatible static-data change, start a new `identity_generation` and raise a conflict rather than merging history silently.
- Flag body/metadata mismatch, implausible speed or acceleration, teleportation, rapid name/dimension changes, repeated invalid fields, and radar/AIS disagreement. These checks detect inconsistency; they do not prove spoofing.
- Static-data cache entries expire independently from positions. Stale names, dimensions, destinations, or callsigns must not be attached to a new identity generation as current facts.
- Association uses geometry, time, uncertainty, and identity only as supporting information. MMSI equality alone cannot override radar geometry. AIS disappearance or a claimed “not under command” state cannot delete a contact.

## Connection, queue, and failure behavior

The WebSocket receive loop must do minimal work: decode one bounded binary frame as strict UTF-8, reject oversized/non-JSON data, timestamp it, and enqueue it. A separate worker validates and normalizes messages. Use bounded memory throughout:

- raw frame queue: 4,096 entries, drop oldest for live situational display and increment `queue_drop_oldest`;
- current track cache: 4,096 MMSIs with LRU/TTL eviction and counters;
- static cache: 4,096 MMSIs with independent TTL;
- maximum decoded frame: 256 KiB, maximum JSON depth/element/string bounds aligned with the collector;
- optional recorder queue: separately bounded; a recorder drop is explicit and makes the capture incomplete.

If overload occurs, mark AIS degraded and preserve the newest report per MMSI where possible. Never hide loss by coalescing counters away. Export connection state, confirmation state, compression status, last-frame and last-valid-position ages, frames received, parsed/rejected/unsupported counts, queue high-water mark and drops, duplicate/out-of-order counts, coordinate/time/identity conflicts, reconnect count, and last close/error category without secret-bearing payloads.

Send the complete subscription immediately on `open` and enforce a local deadline below three seconds. Require `SubscriptionConfirmation` before marking the source connected and record whether compression is enabled. Reconnect after failure with full-jitter exponential backoff (for example cap of `min(60 s, 1 s × 2^attempt)`), then send the complete replacement subscription again. Reset the attempt counter only after a sustained healthy interval. Respect the one-update-per-second limit. Do not run several local clients that consume the provider's three-connection limits.

AIS is event-driven, so silence is not proof of failure. For the busy demo area, provisional diagnostics may mark data `degraded` after 30 seconds and reconnect after 90 seconds only when transport keepalive/confirmation signals also indicate a problem. Keep the thresholds configurable and test them with a fake clock. A silent but open service is `unknown/degraded`, never `healthy` merely because the TCP connection exists.

## Secrets, data rights, and retention

Read `AISSTREAM_API_KEY` only in the backend process environment. Never place it in JSON config, CLI arguments, the browser bundle, same-origin responses, run manifests, subprocess listings, logs, exception strings, recordings, screenshots, or test fixtures. Redact subscription frames before diagnostics. Use a dedicated hackathon key, rotate after the event, and fail closed when absent.

The official model repository states that its generated/schema code is MIT-licensed. That does not grant rights to the streamed AIS data. As of the research date, the service's main repository exposed no repository license through GitHub metadata, and official issue threads were still asking for clarification about commercial use, public vessel display, attribution, caching, and retention. Therefore:

- do not copy the provider's code unless its specific repository/file license is confirmed;
- do not assume the model-code MIT license covers received data;
- keep public display, screenshots, redistribution, and live recording behind a documented rights decision;
- default raw retention to disabled;
- if approved for a private trial, write frames only under external `horizon-runs/aisstream/<run-id>/`, with restrictive permissions, a short declared retention period, source/terms snapshot, hashes, completeness/drop counters, and deletion date; and
- never commit raw frames, derived vessel histories, or keys to Git.

AISStream provides no durable replay. Horizon's optional recorder must therefore be treated as its own incomplete capture. Replays consume a frozen external artifact with a manifest, SHA-256 hashes, source timestamps, receiver timestamps, bounding-box/config hash, parser version, connection epochs, drop counters, and rights status. Replay runs must not make a live connection and must remain distinct from synthetic safety experiments.

## Implementation ownership and exact files

The coordinator should allocate implementation after reviewing this plan:

| Owner | Files | Work |
|---|---|---|
| A01 | `AGENTS.md`, root Python dependency/lock files, `packages/contracts/schema/horizon.schema.json`, generated contract types, `configs/maritime/aisstream-singapore-demo.json`, `scripts/launch.py`, `scripts/console_proxy.py`, `docs/architecture/local-runtime.md` | Register ownership/config, add the WebSocket dependency, typed bounded payload, `--aisstream` opt-in launch, secret-safe run metadata, and explicit read-only proxy route. Default launch remains offline. |
| A05 | `adapters/maritime/aisstream.py`, `services/collector/horizon_collector/aisstream_poller.py`, collector diagnostics/API, `services/fusion/horizon_fusion/core.py`, `tests/ingestion/test_aisstream_adapter.py`, `tests/ingestion/test_aisstream_fusion.py` | Pure frame normalization and geodesy; backend WSS client; queues/cache/health; collector ingestion; identity/conflict rules; conservative fusion policy. |
| A06 | `apps/console/src/hooks/useConsoleFeed.ts`, console types/components/styles, `docs/demo/` | Singapore-area map/scene overlay with `AIS REPORTED`, age, source health, uncertainty/conflict state, availability fallback, and no key or raw provider payload in browser responses. Do not mix markers into recorded replay outcomes. |
| A08 | `tests/system/test_live_ais_boundary.py`, `tests/system/test_aisstream_resilience.py`, `docs/verification/` | Mock WebSocket system tests, authority/isolation tests, recording/replay separation, failure/reconnect tests, and an opt-in manual live-test procedure. No real key in CI. |

If a separate `services/ais-ingest/` process is preferred for fault isolation, A01 must first add that ownership boundary and launcher lifecycle. The smaller v1 uses an isolated collector thread: its exception can only degrade AIS and must not terminate collection of simulator/sensor inputs. In either design, the browser never opens the provider socket.

### Phased handoff

1. **A01 contract/platform:** freeze the payload/config schemas, dependency version, opt-in flag, diagnostics route, secret redaction, and generated types. Provide schema-valid fixtures before downstream work.
2. **A05 adapter/collector:** implement pure parsing/geodesy first, then the mocked transport, bounded queue/cache, static-message join, staleness, lineage, counters, and external recorder. Keep fusion disabled.
3. **A06 overlay:** consume only the allowlisted normalized endpoint. Add source-state and synthetic/offline fallback indicators. Visually separate live context from protected simulated traffic.
4. **A05 fusion experiment:** add AIS as conservative untrusted evidence behind a second opt-in flag. Preserve radar tracks and uncertainty; record conflicts. Do not route live AIS into scored deterministic runs.
5. **A08 verification:** run the matrix below, then a short private live smoke only when a key and rights decision exist. Publish no positions or screenshots until the display gate is cleared.

## Test matrix and acceptance criteria

| Area | Required tests | Acceptance |
|---|---|---|
| Subscription | fake server checks first frame, exact `APIKey` casing, configured boxes/types, confirmation, compression flag, under-three-second send, update throttle | No secret in captured logs; late/malformed/unconfirmed connection never reports healthy. |
| Framing/parser | binary UTF-8, invalid UTF-8, text/protocol drift, fragmented/oversized data, malformed/deep JSON, all five selected message types, unknown types | Bounded rejection with counters; process remains alive; unknown fields do not bypass bounds. |
| Coordinates/time | valid Singapore samples, range/sentinel/NaN rejection, bbox tolerance, body/metadata conflict, AIS second vs epoch, absent provider UTC, stale and future data | No invalid coordinate reaches overlay/fusion; old or uncertain data cannot be made fresh. |
| Geodesy | origin, north/east offsets, box corners, round-trip/reference comparison | Error tolerance is declared and met across the configured demo area; origin hash appears in lineage. |
| Ordering/identity | duplicate receptions, out-of-order frames, reconnect epoch, MMSI reuse, static-data expiry, impossible jumps | Newest valid report persists; every suppression/conflict is counted; stale static data is not silently reused. |
| Resilience | disconnects, invalid key, silent open socket, no confirmation, slow parser, queue overflow, recorder overflow, DNS/TLS failure, clean shutdown | Backoff has jitter/cap; memory remains bounded; AIS degrades independently; no automatic unbounded restart loop. |
| Safety boundary | AIS agrees/disagrees with radar, spoofed extra target, spoofed removal, AIS dropout, MMSI collision, clock uncertainty above gate | Radar-supported track cannot be deleted or relaxed; AIS never establishes clear water or directly changes actuator authority. |
| Experiment isolation | replay with network disabled, live flag during scored run, artifact hash mismatch, missing capture frames | Deterministic runs refuse live input; replay is hash-verified and visibly marked recorded/incomplete. |
| UI/security | browser network inspection, source-state transitions, stale/conflict styling, secret scan, proxy route allowlist | Browser receives normalized minimum fields only; no key/raw subscription; markers show source and age; unavailable feed has an honest fallback. |
| Live smoke (manual) | one bounded Singapore-area session after gates, then forced reconnect | Confirmation and messages, or explicit silent/unavailable result, are recorded locally with zero secret leakage and complete counters. |

Completion means the offline test suite passes, memory and cardinality are bounded, source loss is visible, deterministic evidence remains reproducible without network access, and the safety test proves AIS cannot weaken radar-supported protection. A successful socket connection alone is not completion.

## Official sources inspected

- [AISStream developer documentation](https://aisstream.io/documentation) — endpoint, backend-only key handling, three-second subscription, bounding boxes and filters, binary UTF-8 frames, confirmation/compression, limits, reconnect guidance, no SLA, and no durable replay; inspected 2026-09-22.
- [AISStream official repository](https://github.com/aisstream/aisstream) — service description and links to official examples/models/issues; inspected 2026-09-22.
- [Official example repository](https://github.com/aisstream/example) — supported client-language examples; inspected 2026-09-22. Treat examples as examples, not the normative source where they differ from current documentation.
- [Official message-model repository](https://github.com/aisstream/ais-message-models) and [OpenAPI type definition](https://github.com/aisstream/ais-message-models/blob/master/type-definition.yaml) — envelope and typed message fields; repository states MIT for its code/models; inspected 2026-09-22.
- [Official issue: public-display licensing question](https://github.com/aisstream/aisstream/issues/24) and [official issue: commercial-use clarification](https://github.com/aisstream/aisstream/issues/18) — evidence that users were still seeking data-use clarification; inspected 2026-09-22. These questions do not themselves grant or deny rights.
- [Official issue tracker report: silent stream from 2026-08-05](https://github.com/aisstream/issues/issues/269) — availability risk reported by a user; inspected 2026-09-22. This is not an official uptime determination.
