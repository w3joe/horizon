"""Normalize the licensed tnnv RIB GLB to Horizon's 12 m by 3 m display proxy.

Run with Blender 4.5 LTS or newer:

    Blender --factory-startup --disable-autoexec --background --python \
      tools/assets/normalize_tnnv_rib.py -- INPUT.glb OUTPUT.glb REPORT.json
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


EXPECTED_SOURCE_SHA256 = "faef6ed67af626757ec1d4c0a15a747f046d92c60cb56af225bf0c7aaa67af01"
TARGET_LENGTH_M = 12.0
TARGET_BEAM_M = 3.0
SOURCE_WATERLINE_Z = 0.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    return (
        Vector(tuple(min(point[axis] for point in points) for axis in range(3))),
        Vector(tuple(max(point[axis] for point in points) for axis in range(3))),
    )


def main() -> None:
    try:
        separator = sys.argv.index("--")
        source, output, report = map(Path, sys.argv[separator + 1 : separator + 4])
    except (ValueError, IndexError):
        raise SystemExit("expected -- INPUT.glb OUTPUT.glb REPORT.json")
    if sha256(source) != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("source GLB hash does not match the acquired 1K asset")
    if output.exists() or report.exists():
        raise RuntimeError("refusing to overwrite an existing derived asset or report")

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(source))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 8:
        raise RuntimeError(f"expected 8 source meshes, found {len(meshes)}")
    source_min, source_max = bounds(meshes)
    source_extent = source_max - source_min
    if not (source_extent.y > source_extent.x > source_extent.z):
        raise RuntimeError(f"unexpected source axes: {tuple(source_extent)}")
    vertices = sum(len(obj.data.vertices) for obj in meshes)
    triangles = sum(sum(len(poly.vertices) - 2 for poly in obj.data.polygons) for obj in meshes)
    if (vertices, triangles) != (24_298, 40_036):
        raise RuntimeError(f"unexpected mesh counts: {(vertices, triangles)}")

    roots = [obj for obj in bpy.context.scene.objects if obj.parent is None]
    normalization = bpy.data.objects.new("HorizonRIB_12x3_DisplayProxy", None)
    bpy.context.collection.objects.link(normalization)
    for obj in roots:
        obj.parent = normalization
    scale_x = TARGET_BEAM_M / source_extent.x
    scale_yz = TARGET_LENGTH_M / source_extent.y
    normalization.scale = (scale_x, scale_yz, scale_yz)
    normalization.location = (
        -((source_min.x + source_max.x) / 2) * scale_x,
        -((source_min.y + source_max.y) / 2) * scale_yz,
        -SOURCE_WATERLINE_Z * scale_yz,
    )
    normalization["source_title"] = "Assault Boat"
    normalization["source_author"] = "tnnv"
    normalization["source_url"] = "https://sketchfab.com/3d-models/assault-boat-0d4fca4f2e014cd7b6aefd4653b1508c"
    normalization["license"] = "CC-BY-4.0"
    normalization["display_proxy"] = "12 m length x 3 m beam; visual only"
    normalization["local_forward_axis"] = "-Z after glTF export"
    normalization["waterline"] = "authored source Z=0 mapped to scene Y=0"

    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output),
        export_format="GLB",
        export_cameras=False,
        export_lights=False,
        export_extras=True,
        export_yup=True,
    )

    # Reimport the exported file into a clean scene for a round-trip bounds check.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(output))
    derived_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    derived_min, derived_max = bounds(derived_meshes)
    derived_extent = derived_max - derived_min
    if abs(derived_extent.x - TARGET_BEAM_M) > 1e-4:
        raise RuntimeError(f"derived beam is {derived_extent.x}, expected {TARGET_BEAM_M}")
    if abs(derived_extent.y - TARGET_LENGTH_M) > 1e-4:
        raise RuntimeError(f"derived Blender longitudinal extent is {derived_extent.y}, expected {TARGET_LENGTH_M}")
    waterline = -SOURCE_WATERLINE_Z * scale_yz
    result = {
        "schema_version": "horizon.maritime-asset-normalization.v1",
        "source": {
            "path": str(source),
            "sha256": EXPECTED_SOURCE_SHA256,
            "bounds_min_xyz": list(source_min),
            "bounds_max_xyz": list(source_max),
            "extent_xyz": list(source_extent),
            "meshes": len(meshes),
            "vertices": vertices,
            "triangles": triangles,
        },
        "transform": {
            "source_longitudinal_axis": "+/-Y, bow toward -Y",
            "gltf_local_forward_axis": "-Z",
            "scale_xyz": [scale_x, scale_yz, scale_yz],
            "source_waterline_z": SOURCE_WATERLINE_Z,
            "derived_waterline_y": waterline,
        },
        "derived": {
            "path": str(output),
            "sha256": sha256(output),
            "bytes": output.stat().st_size,
            "roundtrip_blender_extent_xyz": list(derived_extent),
            "declared_proxy_length_m": TARGET_LENGTH_M,
            "declared_proxy_beam_m": TARGET_BEAM_M,
        },
        "blender_version": bpy.app.version_string,
        "safety_scope": "display geometry only; no simulator dynamics, collision, or safety bounds changed",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
