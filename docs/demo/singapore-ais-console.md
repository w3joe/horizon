# Singapore AIS console fixture

The Singapore traffic view is a deterministic, offline console state. It uses bundled schematic GeoJSON and synthetic AIS-shaped records; it does not connect to AISStream, contain provider payloads, or claim live vessel positions.

Use these stable URLs after starting the console with `npm --workspace @horizon/console run dev`:

- traffic overview: `http://127.0.0.1:5176/?view=singapore&state=overview`
- RTA intervention and source conflict: `http://127.0.0.1:5176/?view=singapore&state=conflict`

Both views freeze the Three.js scene clock for repeatable screenshots. The WGS84 SVG inset and NED scene are built from the same local fixture instant and origin. Selecting a contact in either view keeps the evidence card synchronized. The hollow blue marker is a read-only live-shadow placeholder; solid recorded and synthetic contacts are frozen simulation traffic. The paired branch cards compare protected and evaluation-only paths from the same initial state.

The local coast and terminal geometry are presentation fixtures, not OSM-derived data or a navigational chart. `singapore-area-demo-v1.geojson` identifies visual and fixture-reviewed layers explicitly, while the console shows its bundle version, attribution, and `SIMULATION ONLY` notice.
