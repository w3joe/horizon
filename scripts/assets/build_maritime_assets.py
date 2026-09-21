"""Normalize licensed source assets into bounded browser-ready GLBs.

Run this file through Blender 4.5.14 LTS. Source files remain outside Git in
the sibling horizon-data directory; exact source hashes make each build fail
closed if upstream content changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


SOURCE_HASHES = {
    "cargo": "b873a1a6f7ccd3b0646beb020aa95bc062f72ecf2d8c3c47fdf630e824caa9b6",
    "cargo-stack": "b873a1a6f7ccd3b0646beb020aa95bc062f72ecf2d8c3c47fdf630e824caa9b6",
    "buoy": "7a6e3ba5b2258575464b2af622dbe63b89a74cb9885f8105cfc8fdf30c734d5c",
}

ASSET_IDS = {
    "cargo": "sketlux-container-ship-120m-v1",
    "cargo-stack": "sketlux-harbor-cargo-stack-v1",
    "buoy": "polyhaven-ocean-buoy-1k-v1",
}

ROOT_NAMES = {
    "cargo": "HorizonCargoShip",
    "cargo-stack": "HorizonCargoStack",
    "buoy": "HorizonOceanBuoy",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    return (
        Vector(min(point[index] for point in points) for index in range(3)),
        Vector(max(point[index] for point in points) for index in range(3)),
    )


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def use_pbr_materials() -> None:
    palette = {
        "body": (0.20, 0.24, 0.27, 1.0),
        "body color": (0.025, 0.045, 0.070, 1.0),
        "body rim.001": (0.50, 0.34, 0.06, 1.0),
        "body rim.002": (0.05, 0.12, 0.28, 1.0),
        "body rim.003": (0.40, 0.05, 0.04, 1.0),
        "body.001": (0.72, 0.76, 0.76, 1.0),
        "body.002": (0.32, 0.36, 0.38, 1.0),
        "deck": (0.32, 0.08, 0.055, 1.0),
        "crain": (0.55, 0.25, 0.035, 1.0),
        "glass": (0.025, 0.12, 0.17, 1.0),
        "glass.001": (0.025, 0.12, 0.17, 1.0),
        "window": (0.008, 0.018, 0.025, 1.0),
        "steam outets": (0.035, 0.04, 0.045, 1.0),
        "crate": (0.34, 0.075, 0.035, 1.0),
        "crate.001": (0.42, 0.17, 0.035, 1.0),
        "crate.002": (0.045, 0.15, 0.29, 1.0),
        "crate.003": (0.08, 0.25, 0.15, 1.0),
        "crate.004": (0.37, 0.28, 0.06, 1.0),
        "Material.007": (0.05, 0.25, 0.09, 1.0),
    }
    for material in bpy.data.materials:
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is None:
            continue
        color = palette.get(material.name, tuple(material.diffuse_color))
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Metallic"].default_value = 0.05
        principled.inputs["Roughness"].default_value = 0.62


def apply_transform_to_objects(objects: list[bpy.types.Object], scale: Vector, offset: Vector) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.data = obj.data.copy()
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        obj.select_set(False)
        scale_matrix = Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        obj.data.transform(scale_matrix @ Matrix.Translation(offset))


def join_as_root(objects: list[bpy.types.Object], mesh_name: str, root_name: str) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    mesh = bpy.context.object
    mesh.name = mesh_name
    mesh.data.name = f"{mesh_name}Geometry"
    root = bpy.data.objects.new(root_name, None)
    bpy.context.collection.objects.link(root)
    mesh.parent = root


def build_cargo(source: Path) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(source))
    objects = mesh_objects()
    minimum, maximum = bounds(objects)
    extent = maximum - minimum
    scale = Vector((24.0 / extent.x, 120.0 / extent.y, 24.0 / extent.z))
    offset = Vector((-(minimum.x + maximum.x) / 2, -(minimum.y + maximum.y) / 2, 0.0))
    use_pbr_materials()
    apply_transform_to_objects(objects, scale, offset)
    join_as_root(objects, "CargoShipMesh", ROOT_NAMES["cargo"])


def build_cargo_stack(source: Path) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(source))
    candidates = []
    for obj in mesh_objects():
        if not obj.name.startswith("crate.") or not any(
            material and material.name.startswith("crate") for material in obj.data.materials
        ):
            continue
        minimum, maximum = bounds([obj])
        if min(maximum - minimum) > 0.05:
            candidates.append(obj)
    source_containers = sorted(candidates, key=lambda obj: obj.name)[:8]
    if len(source_containers) != 8:
        raise RuntimeError("expected at least eight source container meshes")
    for obj in list(bpy.context.scene.objects):
        if obj not in source_containers:
            bpy.data.objects.remove(obj, do_unlink=True)
    use_pbr_materials()
    bpy.ops.object.select_all(action="DESELECT")
    for index, obj in enumerate(source_containers):
        obj.data = obj.data.copy()
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        obj.select_set(False)
        minimum, maximum = bounds([obj])
        extent = maximum - minimum
        scale = Vector((2.44 / extent.x, 12.19 / extent.y, 2.59 / extent.z))
        center = (minimum + maximum) / 2
        row = index % 2
        column = (index // 2) % 2
        tier = index // 4
        target = Vector(((row - 0.5) * 2.64, (column - 0.5) * 12.39, tier * 2.69 + 1.295))
        scale_matrix = Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        obj.data.transform(
            Matrix.Translation(target) @ scale_matrix @ Matrix.Translation(-center)
        )
    join_as_root(source_containers, "CargoStackMesh", ROOT_NAMES["cargo-stack"])


def build_buoy(source: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects = mesh_objects()
    minimum, maximum = bounds(objects)
    offset = Vector((-(minimum.x + maximum.x) / 2, -(minimum.y + maximum.y) / 2, 0.0))
    apply_transform_to_objects(objects, Vector((1.0, 1.0, 1.0)), offset)
    join_as_root(objects, "OceanBuoyMesh", ROOT_NAMES["buoy"])


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", choices=sorted(SOURCE_HASHES), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(arguments)

    if sha256(args.source) != SOURCE_HASHES[args.asset]:
        raise RuntimeError(f"source hash mismatch for {args.asset}")
    if args.output.exists() or args.report.exists():
        raise FileExistsError("output and report paths must not already exist")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    {"cargo": build_cargo, "cargo-stack": build_cargo_stack, "buoy": build_buoy}[
        args.asset
    ](args.source)
    root = bpy.data.objects[ROOT_NAMES[args.asset]]
    root["asset_id"] = ASSET_IDS[args.asset]
    root["license"] = "CC0-1.0"
    root["navigation_scope"] = "presentation_only_no_weapon_systems"

    bpy.ops.export_scene.gltf(
        filepath=str(args.output),
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_extras=True,
        export_materials="EXPORT",
    )
    args.report.write_text(
        json.dumps(
            {
                "asset_id": ASSET_IDS[args.asset],
                "source_sha256": SOURCE_HASHES[args.asset],
                "output_sha256": sha256(args.output),
                "output_bytes": args.output.stat().st_size,
                "blender_version": bpy.app.version_string,
                "root_node": ROOT_NAMES[args.asset],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
