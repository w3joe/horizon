# Offline geography tooling

`fetch_geography.py` defaults to manifest-only operation. It performs no
network access and no write unless `--output-manifest` is supplied. The
Singapore config intentionally leaves source hashes empty: an operator must
provide a `horizon.geography-source-pins.v1` file containing an exact SHA-256
and explicit UTC acquisition time for each approved artifact. Only an explicit
`--download` performs network access. It rejects `latest` paths and public
OpenStreetMap tile hosts, verifies every byte, and writes raw artifacts under
the external `horizon-data/geography/` tree.

Example planning command:

```sh
/opt/homebrew/bin/python3.12 tools/geography/fetch_geography.py
```

`build_geography.py` consumes a verified acquisition manifest, a pinned layer
build manifest, and already extracted GeoJSON. It canonicalizes JSON, records
derived hashes and provenance, writes disjoint visual and safety allowlists,
adds fixed WGS84/NED checkpoints, then runs the simulator bundle validator.
OSM PBF extraction, topology repair, and optional GEBCO subset generation are
external preprocessing commands recorded verbatim in the layer build manifest;
this repository does not conceal those steps or silently invoke a tile/API
download. Safety layers require explicit simulation review metadata. OSM and
GEBCO outputs remain visual-only.

`validate_geography.py <bundle.json>` can be run independently against any
built bundle. It prints only the bundle hash, status, and layer allowlists.

Large PBF, raster, NetCDF, raw tile, and source extracts are never committed.
The tiny checked-in bundle under
`services/simulator/tests/fixtures/geography/` is generated synthetic CC0 data
for validation tests and carries no navigational claim.
