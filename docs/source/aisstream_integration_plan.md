# AISStream integration plan for a Singapore-area Horizon demo

**Status:** feasible as an optional, backend-only live civilian-AIS overlay and as the source for a deterministic recorded traffic mirror, subject to service availability and a written data-use decision. This document is an implementation handoff. The A11 planning work did not request or handle an API key; a separate coordinator-run, sanitized feasibility smoke is summarized below.

**Research snapshot:** 2026-09-22. Recheck the linked service documentation, schema, availability reports, and data-use terms immediately before implementation or any public demonstration.

## Decision and safety boundary

AISStream can supply reported positions and identity data for participating civilian vessels around Singapore. It cannot reliably “pinpoint every ship”: AIS is cooperative, intermittent, terrestrially received, self-reported, and vulnerable to stale, erroneous, duplicated, reused, or spoofed identities. Some government or defence vessels may omit, restrict, or manipulate transmissions. Horizon must label each marker **AIS-reported**, show last-seen age and uncertainty, and never present coverage as complete surveillance.

The first network-facing implementation should be a read-only live overlay, isolated from deterministic recorded and synthetic experiments. A separate offline compiler may turn a permitted, hash-pinned capture into simulator traffic as specified below:

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

## AIS-derived traffic mirror

The useful demonstration is not a cloud of live dots. It is a closed-loop encounter in which Singapore-area AIS reports seed realistic surrounding traffic, the simulator generates independent onboard observations of those vessels, and the RTA prevents a collision. Keep three modes explicit:

| Mode | Purpose | Authority and reproducibility |
|---|---|---|
| `recorded_mirror` | Default hackathon and research mode. Convert one approved, hash-pinned AIS capture into a deterministic traffic snapshot or replay. | The simulator owns plant truth after initialization. Safe for paired, offline experiments when rights and provenance gates pass. |
| `live_shadow` | Private situational display of current AIS-reported traffic around the Singapore area. | Read-only and unscored. It cannot steer the simulated ownship or alter a deterministic result. |
| `synthetic_offline` | Guaranteed fallback and adversarial cases such as AIS dropout, spoofing, crossing, and non-transmitting contacts. | Fully deterministic and suitable for public demos. Clearly label it synthetic. |

Do not implement a `live_control` mode in v1. Internet timing, incomplete coverage, unauthenticated identities, and changing traffic would make results irreproducible and would confuse AIS reports with physical truth.

### End-to-end data path

```text
AISStream receiver -> validated per-MMSI track cache
                               |
                               +-> live_shadow console layer
                               |
                               +-> capture compiler -> hash-pinned TrafficSnapshot
                                                        |
OSM/GEBCO snapshot -> GeographyBundle -------------------+-> scenario compiler
                                                                |
                                                                v
                                                deterministic traffic plants
                                               /         |          \
                                      simulated radar  camera     simulated AIS
                                               \         |          /
                                                fusion -> RTA -> gate -> ownship
```

This separation is deliberate. An AIS report may initialize a traffic vessel's position, velocity, reported dimensions, and class. Once a `recorded_mirror` episode starts, the simulator plant is the truth source and independently produces radar, camera, and AIS observations with different error/dropout models. The RTA avoids the resulting fused contact; it never treats the original AIS marker as guaranteed obstacle truth or guaranteed free water.

### Capture-to-traffic compiler

Create a deterministic compiler, proposed at `tools/traffic/build_ais_snapshot.py`, with the following behavior:

1. Read an external, rights-approved capture and verify its artifact manifest and SHA-256 before parsing it.
2. Select a declared UTC instant or replay window. Resolve one newest valid dynamic report per MMSI at that instant and join static data only inside its independent TTL.
3. Exclude configured ownship MMSIs, aids to navigation, SAR aircraft, invalid coordinates, stale reports, and contacts outside the area of interest. Record every exclusion reason and count.
4. Convert WGS84 position to the scenario's pinned local NED frame. Convert speed over ground from knots to metres per second and course over ground from degrees true to a north/east velocity. Heading remains separate from course.
5. Use reported bow/stern/port/starboard dimensions only when valid. Otherwise assign an explicit conservative scenario-class hull assumption and set `dimensions_assumed=true`; never infer size from a vessel name.
6. Rank contacts deterministically by distance to ownship and then MMSI. Keep a configurable bounded number for the interactive simulation while preserving the total/excluded counts in the manifest.
7. Emit a versioned `TrafficSnapshot` with the capture hash, selection time/window, local-frame hash, compiler version, vessel states, motion-model assumptions, age and uncertainty, and rights status. Do not emit names or destinations unless the demo needs them and display rights are resolved.

For a snapshot episode, use a constant-course/constant-speed traffic model only over its declared short horizon, with turn and acceleration stress variants added synthetically. For replay, interpolate only between valid reports from the same identity generation; never interpolate across an impossible jump, reconnect ambiguity, or long gap. Do not use the free-text AIS destination as a route. Smooth a live visual marker if useful, but preserve the raw report time and uncertainty ring so smoothing does not imply fresh evidence.

The first bounded implementation should target 32 rendered vessels and retain a hard configurable ceiling of 64. These are demo-performance limits, not traffic-density claims. A deterministic spatial filter should retain every contact inside the ownship risk radius before filling remaining visual slots by distance.

### Traffic uncertainty and failure injection

Every mirrored vessel carries report age, last-receipt time, identity generation, position uncertainty, dimensions status, and source-health state. Propagation between reports increases the uncertainty envelope as a declared function of age and motion uncertainty and stops at a finite horizon. A stale contact becomes `unknown/degraded`; it does not vanish from the collision picture merely because AIS stopped.

The scenario compiler must be able to layer reproducible faults over the AIS-derived initial scene:

- complete AIS dropout for one target while simulated radar continues;
- stale, delayed, duplicated, and out-of-order reports;
- false MMSI identity or impossible geographic jump;
- wrong course/speed or dimensions;
- a radar-only non-transmitting contact; and
- dense traffic that exceeds the UI display cap but not the safety-track cap.

These cases make the demo relevant to RTA: the protected vessel should remain conservative when cooperative data disappears or conflicts with onboard sensing.

## Singapore geography bundle

Use a versioned, offline `GeographyBundle` rather than depending on public map servers during a demo. Keep visual geography and safety geometry as different artifacts:

```json
{
  "bundle_id": "singapore-area-demo-v1",
  "wgs84_bbox": [103.55, 1.10, 104.15, 1.50],
  "local_ned_origin": {"latitude_deg": 1.25, "longitude_deg": 103.85},
  "visual_layers": ["land.geojson", "shoreline.geojson", "seamarks.geojson", "bathymetry-contours.geojson"],
  "safety_layers": ["reviewed-water-envelope.geojson", "synthetic-no-go.geojson"],
  "source_manifest": "sources.json",
  "assurance_status": "simulation-only"
}
```

The bundle manifest records source URL, source/version date, download time, licence, attribution text, original and derived SHA-256, extraction command, bounds, coordinate reference system, simplification tolerance, and reviewer status. Large PBF, raster, tile, and raw AIS files remain under external `horizon-data/`; Git stores only acquisition/configuration code, compact derived fixtures when their licences permit redistribution, and manifests.

### Recommended open layers

| Layer | Source and use | Decision |
|---|---|---|
| Land, coastline, roads and context | OpenStreetMap regional PBF from the Geofabrik Malaysia/Singapore/Brunei extract; extract the small demo box with Osmium and render locally. OSM data is ODbL and requires attribution. | **Primary visual base.** Pin a dated extract and hash. Do not bulk-download `tile.openstreetmap.org`; its public tiles are best-effort and prohibit bulk/offline scraping. |
| Seamarks and aids to navigation | `seamark:*` features already present in the OSM extract and associated with OpenSeaMap conventions. | **Visual/context layer only.** Retain OSM/OpenSeaMap attribution and never describe it as an official electronic navigational chart. |
| Bathymetry and terrain shading | A subset of the current GEBCO grid plus its Type Identifier grid. GEBCO permits reuse with attribution but explicitly says the grid must not be used for navigation or safety at sea. | **Visual depth context only.** At 15 arc-seconds, the grid is hundreds of metres per cell near Singapore and is too coarse for harbour grounding protection. |
| Singapore national basemap | OneMap, the Singapore Land Authority's authoritative national map, subject to token, API terms, individual dataset conditions, and attribution. | **Optional visual cross-check.** Do not make the demo dependent on it and do not treat a land basemap as a nautical chart. |
| Low-detail offline fallback | Natural Earth public-domain land/coastline. | **Locator/inset only.** Its scale is unsuitable for local obstacle or shoreline decisions. |
| Navigational safety geometry | Official ENC/chart material, when separately licensed and used under its terms. Singapore ENC distribution is not an open-data substitute for the sources above. | **Future field-work requirement.** Excluded from the open-source hackathon bundle unless the proper product and licence are obtained. |

For the hackathon, generate `land.geojson`, `shoreline.geojson`, and selected seamarks from a dated OSM PBF; optionally derive visually labelled GEBCO contours; then produce a separately reviewed, conservative `reviewed-water-envelope.geojson` for the simulation. The RTA and plant may query only the reviewed safety layers. They must never query a display tile, OneMap response, raw OSM coastline, or GEBCO depth as if it were certified clearance.

The cross-border Singapore Strait view requires one consistent regional layer covering Singapore, southern Johor, and nearby Indonesian islands. OneMap alone cannot provide that regional context. The default renderer should therefore use the offline OSM-derived bundle, with visible `© OpenStreetMap contributors` attribution and a link to the ODbL notice. Render the local NED scene and a MapLibre 2D inset from the same bundle and origin so selecting a contact highlights the same vessel in both views.

### Map build and serving plan

1. Add `configs/geography/singapore-area-demo.json` with bounds, origin, required tags/layers, source versions, and allowed simplification error.
2. Add `scripts/fetch_geography.py --manifest-only` and an explicit `--download` action. The normal launcher never downloads map data. The fetcher verifies expected hashes, emits licence/attribution metadata, and refuses an unpinned `latest` source for reproducible runs.
3. Extract the area with Osmium, convert the bounded visual layers to GeoJSON, validate/fix polygon topology, simplify only within the declared visual tolerance, and create optional low-resolution depth contours from a GEBCO subset.
4. Build a compact same-origin bundle for the console. Start with bounded GeoJSON; move to PMTiles/vector tiles only if measured size or frame time requires it.
5. Validate that the map's WGS84-to-NED transform matches the simulator at the origin and corners. Check that all safety polygons have source, review status, uncertainty/buffer, and hash before they can be enabled.
6. Serve files read-only through the console proxy with immutable cache keys. The console shows bundle version, `SIMULATION ONLY`, source attribution, and whether shoreline/grounding constraints are visual or reviewed.

The first success scene should be a west-to-east or east-to-west Singapore-area transit with several AIS-derived commercial contacts, one synthetic radar-only contact, and one stale AIS contact. The unprotected branch follows the autonomy proposal into a closest-point-of-approach violation; the protected branch modifies speed or heading through the existing gate and shows the intervention, uncertainty envelopes, CPA/TCPA, source conflict, and paired counterfactual.

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
| A03 | `services/simulator/`, scenario schemas and fixtures, `tools/traffic/build_ais_snapshot.py`, simulator tests | Compile hash-pinned traffic snapshots, instantiate deterministic vessel plants, generate independent synthetic radar/camera/AIS observations, and add mirrored-traffic scenarios and fault injection. |
| A05 | `adapters/maritime/aisstream.py`, `services/collector/horizon_collector/aisstream_poller.py`, collector diagnostics/API, `services/fusion/horizon_fusion/core.py`, `tests/ingestion/test_aisstream_adapter.py`, `tests/ingestion/test_aisstream_fusion.py` | Pure frame normalization and geodesy; backend WSS client; queues/cache/health; collector ingestion; identity/conflict rules; conservative fusion policy. |
| A06 | `apps/console/src/hooks/useConsoleFeed.ts`, console types/components/styles, `docs/demo/` | Synchronized 2D Singapore map and 3D scene; `AIS REPORTED`, age, source health, uncertainty/conflict, CPA/TCPA, map attribution, availability fallback, and no key/raw payload in browser responses. Visually separate live context from recorded/synthetic protected traffic. |
| A09/A10 | `configs/geography/`, geography acquisition/build tooling, asset registry, marine visual layers | Build the pinned OSM/GEBCO geography bundle, preserve licensing/provenance, align it with the NED scene, and keep bathymetry/shoreline display separate from reviewed safety geometry. |
| A08 | `tests/system/test_live_ais_boundary.py`, `tests/system/test_aisstream_resilience.py`, `docs/verification/` | Mock WebSocket system tests, authority/isolation tests, recording/replay separation, failure/reconnect tests, and an opt-in manual live-test procedure. No real key in CI. |

If a separate `services/ais-ingest/` process is preferred for fault isolation, A01 must first add that ownership boundary and launcher lifecycle. The smaller v1 uses an isolated collector thread: its exception can only degrade AIS and must not terminate collection of simulator/sensor inputs. In either design, the browser never opens the provider socket.

### Phased handoff

1. **A01 contract/platform:** freeze the observation, `TrafficSnapshot`, `GeographyBundle`, config, diagnostics, and secret-redaction contracts. Provide schema-valid fixtures before downstream work.
2. **A05 adapter/collector:** implement pure parsing/geodesy first, then mocked transport, bounded queue/cache, static-message join, staleness, lineage, counters, and external recorder. Keep fusion and traffic injection disabled.
3. **A09/A10 geography:** build one dated, hash-pinned offline OSM visual bundle and optional GEBCO contours. Produce a distinct conservative simulation water envelope with visible review status.
4. **A03 recorded mirror:** compile a permitted capture into a deterministic traffic snapshot, instantiate traffic plants, and generate separate sensor observations. Add dropout, stale, spoofed, and radar-only contacts.
5. **A06 synchronized display:** consume only allowlisted normalized endpoints. Align the 2D map and 3D NED scene; show source state, age, uncertainty, CPA/TCPA, provenance, attribution, and synthetic/offline fallback.
6. **A05 fusion experiment:** add AIS as conservative untrusted evidence behind a second opt-in flag. Preserve radar tracks and uncertainty; record conflicts. Never route live AIS into scored deterministic runs.
7. **A08 verification:** run the matrix below and a paired recorded-mirror collision case. Perform a short private live-shadow smoke only when a key and rights decision exist. Publish no live positions or screenshots until the display gate is cleared.

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
| Traffic compiler | fixed capture/window, ownship exclusion, stale/static joins, unknown dimensions, cap overflow, cross-generation gaps | Byte-identical snapshot for identical inputs; deterministic selection; every assumption and exclusion is counted. |
| Geography | source/hash/license manifest, topology, transform at origin/corners, 2D/3D alignment, safety-layer allowlist | Offline bundle renders without network; attribution is visible; only reviewed safety layers can enter plant/RTA queries. |
| Mirrored encounter | same traffic snapshot and observation tape in protected/unprotected branches; AIS dropout and radar-only target | Counterfactual collision/CPA violation is reproducible; protected intervention is joined to the gate and preserves declared clearance without trusting AIS as truth. |
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
- [Geofabrik Malaysia, Singapore and Brunei extract](https://download.geofabrik.de/asia/malaysia-singapore-brunei.html) — downloadable regional OSM PBF suitable for an offline, pinned extract; inspected 2026-09-22.
- [OpenStreetMap copyright and licence](https://www.openstreetmap.org/copyright) and [OSMF tile-usage policy](https://operations.osmfoundation.org/policies/tiles/) — ODbL attribution obligations and the reason not to scrape or depend on public raster tiles for an offline demo; inspected 2026-09-22.
- [OpenSeaMap FAQ](https://www.openseamap.org/index.php?L=1&id=faq) — seamark/chart data provenance and licence context; inspected 2026-09-22. Treat it as community map context, not an official chart.
- [GEBCO gridded bathymetry](https://www.gebco.net/data-products/gridded-bathymetry-data) and [terms of use](https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use) — current global grid/TID availability, attribution, varying source quality, and explicit prohibition on navigation or safety-at-sea use; inspected 2026-09-22.
- [OneMap API documentation](https://www.onemap.gov.sg/apidocs/) and [API terms](https://www.onemap.gov.sg/legal/apitermsofservice.html) — authoritative Singapore national-map API, token requirements, open-data/API terms, and service limitations; inspected 2026-09-22.
- [MapLibre GL JS documentation](https://maplibre.org/maplibre-gl-js/docs/) — open WebGL renderer for local GeoJSON/vector/raster sources; inspected 2026-09-22.
