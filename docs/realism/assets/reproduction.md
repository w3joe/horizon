# Maritime asset acquisition and build

Source archives and loose glTF files stay outside Git in sibling
`horizon-data/assets/maritime`. All derived browser assets and their exact
provenance records are checked in.

## Acquire sources

The acquisition helper downloads only the selected CC0 sources and verifies
every SHA-256 before writing it:

```sh
/opt/homebrew/bin/python3.12 scripts/assets/acquire_maritime_sources.py \
  --data-root ../horizon-data/assets/maritime
```

Review the live license records before a new acquisition:

- <https://opengameart.org/content/container-ship-full>
- <https://polyhaven.com/a/ocean_buoy>
- <https://polyhaven.com/license>
- <https://creativecommons.org/publicdomain/zero/1.0/>

The script refuses an existing file with unexpected bytes and refuses an
upstream download whose content has changed. It does not download rejected
candidates.

## Build derived GLBs

The normalization requires Blender 4.5.14 LTS. Each command refuses to
overwrite an existing output or report. Use fresh staging paths, compare the
reported hash with the registry, and only then replace a versioned asset.

```sh
BLENDER=../horizon-tools/Blender-4.5.14.app/Contents/MacOS/Blender
DATA=../horizon-data/assets/maritime
REPORTS=../horizon-runs/coordination/a09-build

$BLENDER --factory-startup --disable-autoexec --background \
  --python scripts/assets/build_maritime_assets.py -- \
  --asset cargo \
  --source "$DATA/opengameart-sketlux/container-ship-full.blend" \
  --output assets/maritime/container-ship.glb \
  --report "$REPORTS/container-ship.json"

$BLENDER --factory-startup --disable-autoexec --background \
  --python scripts/assets/build_maritime_assets.py -- \
  --asset cargo-stack \
  --source "$DATA/opengameart-sketlux/container-ship-full.blend" \
  --output assets/maritime/harbor-cargo-stack.glb \
  --report "$REPORTS/harbor-cargo-stack.json"

$BLENDER --factory-startup --disable-autoexec --background \
  --python scripts/assets/build_maritime_assets.py -- \
  --asset buoy \
  --source "$DATA/polyhaven-ocean-buoy/ocean_buoy_1k.gltf" \
  --output assets/maritime/ocean-buoy.glb \
  --report "$REPORTS/ocean-buoy.json"
```

The build outputs are deterministic with the pinned Blender release. A clean
second cargo build and buoy build produced byte-identical SHA-256 hashes.

Copy the approved files and notice to
`apps/console/public/assets/maritime/`, then validate:

```sh
/opt/homebrew/bin/python3.12 scripts/assets/validate_maritime_assets.py
```

The existing `horizon-rib.glb` is included in validation but is not rebuilt or
modified by this packet.
