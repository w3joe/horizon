# Licensed maritime display pack

This package adds a civilian cargo silhouette and two harbor props alongside
the existing detailed patrol RIB. All assets are presentation-only. They do
not alter simulated hulls, collision geometry, hydrodynamics, sensor inputs,
or runtime-assurance evidence.

## Selected assets

| Asset | Source and creator | License | Browser file | Geometry | Use |
|---|---|---|---:|---:|---|
| Horizon RIB | [Assault Boat by tnnv](https://sketchfab.com/3d-models/assault-boat-0d4fca4f2e014cd7b6aefd4653b1508c) | CC BY 4.0 | 3.48 MiB | 40,036 triangles, three 1K textures | Existing patrol/ownship silhouette; unchanged by this packet |
| Container ship | [Container ship full by Sketlux](https://opengameart.org/content/container-ship-full) | CC0 1.0 | 4.91 MiB | 109,258 triangles, no textures | 120 m civilian traffic silhouette |
| Cargo stack | Derived from the same Sketlux source | CC0 1.0 | 272 KiB | 8,736 triangles, no textures | Static dock/terminal context |
| Ocean buoy | [Ocean Buoy by Mateusz Sadek, Poly Haven](https://polyhaven.com/a/ocean_buoy) | CC0 1.0 | 2.90 MiB | 12,240 triangles, four embedded 1K PBR textures | Static visual channel marker |

The Poly Haven page identifies Mateusz Sadek as author, 12K triangles, a
2.7 m real-world height, and CC0. [Poly Haven's license page](https://polyhaven.com/license)
expressly permits use, modification, and redistribution. The OpenGameArt page
identifies Sketlux, the downloadable Blender source, and CC0. The exact
download URLs and byte hashes are recorded in the asset JSON files.

The cargo model is intentionally texture-free: its silhouette, bridge,
containers, cranes, and navigation lights remain readable at normal demo
camera distances while avoiding a large texture payload. Materials were
converted to restrained PBR colors. It is a display proxy rather than a
vessel-identification model.

## Rejected candidates

- Sketchfab's attractive `Container Ship` by LavaWave and `Patrol Boat PBR
  MK2` by Savy both state CC BY 4.0, but the downloadable archives require an
  authenticated acceptance flow. They were not scraped or redistributed
  without a reviewable acquisition record.
- Kenney's Watercraft Kit 2.1 is clearly CC0 and was downloaded and checked,
  but its very low-poly toy styling did not meet this demo's visual target.
- OpenGameArt's Sketlux patrol boat is CC0, but its source centers a historical
  weapons silhouette and is less detailed than the already selected RIB. It is
  excluded from the repository. Horizon adds no weapon model or behavior.
- The 4.5-million-triangle Sketchfab cargo model by gogiart was rejected as
  unsuitable for a browser budget before download.

## Coordinate and placement contract

Every checked-in file is glTF 2.0 GLB, in metres, with `+Y` up. Vessel forward
is `-Z`. The cargo and RIB retain an authored waterline at local `Y=0`; the
buoy retains its authored waterline at local `Y=0`. The cargo stack sits on
local `Y=0`.

The exported root nodes are stable:

- `HorizonRIB_12x3_DisplayProxy`
- `HorizonCargoShip`
- `HorizonCargoStack`
- `HorizonOceanBuoy`

The cargo ship measures 120 m long, 24 m wide, and 24 m from keel to highest
geometry. A renderer must scale or select it according to a scenario's public
hull dimensions; it must not imply that the simulator's collision hull became
120 m. The cargo stack measures 24.58 m long, 5.08 m wide, and 5.28 m high.
The buoy is 2.656 m from lowest to highest geometry.

`assets/maritime/registry.json` freezes hashes, bounds, node names, and browser
budgets. `scripts/assets/validate_maritime_assets.py` checks both canonical and
served copies, traverses node transforms, and rejects changed bounds, files,
licenses, missing attribution records, or required glTF extensions.

## Rendering budget

The three new files total 8.07 MiB, and the complete four-asset pack totals
11.55 MiB. Only assets present in the current scene should be loaded. The buoy
is the only new textured object. Cargo and harbor props use material colors,
which keeps their transfer cost independent of screen resolution.

Collision proxies and levels of detail are not bundled. The simulator's public
hull remains the collision/safety geometry. If the renderer needs many cargo
ships or buoys simultaneously, A06 should instance the loaded scene and add a
separate low-detail silhouette rather than cloning GLB downloads or changing
the authoritative plant.
