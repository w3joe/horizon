# Horizon RIB display asset

The demo ownship uses a bounded derivative of **Assault Boat** by tnnv under
CC BY 4.0. The canonical derived file is `assets/maritime/horizon-rib.glb`;
its SHA-256 is
`3c8f6ccab43abc4fe74157b23ddd51300b425b0daea05331abb3aeed32c55733`.
The structured asset record and `THIRD_PARTY_NOTICES.md` carry the source,
author, license, mirror commit, and modifications.

## Dimension calibration

Blender 4.5.14 LTS imported the acquired 1K GLB with eight meshes, 24,298
vertices, and 40,036 triangles. The source world bounds were:

| Axis | Minimum | Maximum | Extent |
| --- | ---: | ---: | ---: |
| X | -90.159393 | 90.159393 | 180.318787 |
| Y | -204.708328 | 197.684662 | 402.393005 |
| Z | -17.881599 | 68.041801 | 85.923401 |

The source longitudinal axis is Y, with the bow toward negative Y and the
outboard motor at positive Y. The normalization scales X by
`0.016637201570704554` and Y/Z by `0.029821591925867086`, centers the X/Y
bounds, and retains source Z=0 as the presentation waterline. A clean export
and reimport measured `[2.99999976, 11.99999619, 2.56237173]` m in Blender.
The standard Blender glTF axis conversion therefore gives a Three.js local
forward direction of negative Z, a 12 m longitudinal extent, and a 3 m beam.

This nonuniform normalization makes the visual mesh agree with the simulator's
declared 12 m by 3 m ownship proxy. It is an appearance calibration rather than
a naval-architecture measurement of the source craft. It does not modify the
simulator hull, collision shape, mass, hydrodynamics, recovery bounds, or any
assurance calculation.

## Reproduction and serving

The deterministic conversion is:

```sh
horizon-tools/Blender-4.5.14.app/Contents/MacOS/Blender \
  --factory-startup --disable-autoexec --background \
  --python tools/assets/normalize_tnnv_rib.py -- \
  horizon-data/assets/maritime/tnnv-assault-boat-1k.glb \
  assets/maritime/horizon-rib.glb \
  horizon-runs/coordination/horizon-rib-normalization.json
```

The source hash is checked before import, existing outputs are never
overwritten, and the export is reimported to verify its bounds. The console
loads `/assets/maritime/horizon-rib.glb`; integration must copy or link the
canonical asset and attribution notice into
`apps/console/public/assets/maritime/`.

Ocean displacement, heave, roll, wake foam, lighting, and sky in the demo scene
are presentation effects driven by replay time. They are not physically
coupled water or vessel dynamics and carry no safety meaning.
