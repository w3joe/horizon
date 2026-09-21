#!/usr/bin/env python3
"""Validate Horizon's checked-in maritime GLBs and provenance registry."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "assets" / "maritime" / "registry.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(left[row][inner] * right[inner][column] for inner in range(4)) for column in range(4)]
        for row in range(4)
    ]


def _identity() -> list[list[float]]:
    return [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]


def _node_matrix(node: dict[str, Any]) -> list[list[float]]:
    if "matrix" in node:
        values = node["matrix"]
        return [[float(values[column * 4 + row]) for column in range(4)] for row in range(4)]
    tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
    x, y, z, w = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    rotation = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale = [[sx, 0.0, 0.0, 0.0], [0.0, sy, 0.0, 0.0], [0.0, 0.0, sz, 0.0], [0.0, 0.0, 0.0, 1.0]]
    translation = _identity()
    translation[0][3], translation[1][3], translation[2][3] = tx, ty, tz
    return _multiply(translation, _multiply(rotation, scale))


def _transform(matrix: list[list[float]], point: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = (*point, 1.0)
    result = [sum(matrix[row][column] * vector[column] for column in range(4)) for row in range(4)]
    return (result[0] / result[3], result[1] / result[3], result[2] / result[3])


def read_glb(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError(f"{path}: truncated GLB")
    magic, version, declared_length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError(f"{path}: invalid GLB header")
    offset = 12
    chunks: dict[int, bytes] = {}
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        if offset + chunk_length > len(data):
            raise ValueError(f"{path}: invalid GLB chunk length")
        chunks[chunk_type] = data[offset : offset + chunk_length]
        offset += chunk_length
    if offset != len(data) or 0x4E4F534A not in chunks:
        raise ValueError(f"{path}: missing JSON chunk")
    document = json.loads(chunks[0x4E4F534A].rstrip(b" \x00"))
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError(f"{path}: glTF asset version is not 2.0")
    if document.get("extensionsRequired"):
        raise ValueError(f"{path}: unsupported required extensions")
    binary = chunks.get(0x004E4942, b"")
    if document.get("buffers"):
        expected = document["buffers"][0]["byteLength"]
        if len(binary) < expected or len(binary) - expected > 3:
            raise ValueError(f"{path}: binary buffer length mismatch")
    return document


def inspect_glb(path: Path) -> dict[str, Any]:
    document = read_glb(path)
    nodes = document.get("nodes", [])
    accessors = document.get("accessors", [])
    meshes = document.get("meshes", [])
    world_matrices: dict[int, list[list[float]]] = {}

    def visit(index: int, parent: list[list[float]], active: set[int]) -> None:
        if index in active:
            raise ValueError(f"{path}: node cycle")
        matrix = _multiply(parent, _node_matrix(nodes[index]))
        world_matrices[index] = matrix
        for child in nodes[index].get("children", []):
            visit(child, matrix, {*active, index})

    scene_index = document.get("scene", 0)
    for node_index in document.get("scenes", [{}])[scene_index].get("nodes", []):
        visit(node_index, _identity(), set())

    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for node_index, matrix in world_matrices.items():
        mesh_index = nodes[node_index].get("mesh")
        if mesh_index is None:
            continue
        for primitive in meshes[mesh_index].get("primitives", []):
            accessor = accessors[primitive["attributes"]["POSITION"]]
            if "min" not in accessor or "max" not in accessor:
                raise ValueError(f"{path}: POSITION accessor has no bounds")
            for point in itertools.product(*zip(accessor["min"], accessor["max"], strict=True)):
                transformed = _transform(matrix, point)
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], transformed[axis])
                    maximum[axis] = max(maximum[axis], transformed[axis])

    vertices = triangles = primitives = 0
    for mesh in meshes:
        for primitive in mesh.get("primitives", []):
            primitives += 1
            position_count = accessors[primitive["attributes"]["POSITION"]]["count"]
            vertices += position_count
            element_count = (
                accessors[primitive["indices"]]["count"]
                if "indices" in primitive
                else position_count
            )
            mode = primitive.get("mode", 4)
            if mode == 4:
                triangles += element_count // 3
            elif mode in {5, 6}:
                triangles += max(0, element_count - 2)
            else:
                raise ValueError(f"{path}: non-triangle primitive mode {mode}")

    root_extras = {
        node.get("name"): node.get("extras", {})
        for node in nodes
        if node.get("extras")
    }
    return {
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "bounds_min_xyz": minimum,
        "bounds_max_xyz": maximum,
        "extent_xyz": [maximum[index] - minimum[index] for index in range(3)],
        "nodes": sorted(node.get("name", "") for node in nodes),
        "meshes": len(meshes),
        "primitives": primitives,
        "vertices": vertices,
        "triangles": triangles,
        "materials": len(document.get("materials", [])),
        "textures": len(document.get("textures", [])),
        "images": len(document.get("images", [])),
        "root_extras": root_extras,
    }


def close_vector(actual: list[float], expected: list[float], tolerance: float) -> bool:
    return all(abs(left - right) <= tolerance for left, right in zip(actual, expected, strict=True))


def validate_registry(registry_path: Path = REGISTRY) -> None:
    registry = json.loads(registry_path.read_text())
    if registry.get("schema_version") != "horizon.maritime-asset-registry.v1":
        raise ValueError("unexpected registry schema_version")
    identifiers: set[str] = set()
    for asset in registry["assets"]:
        asset_id = asset["asset_id"]
        if asset_id in identifiers:
            raise ValueError(f"duplicate asset_id {asset_id}")
        identifiers.add(asset_id)
        canonical = ROOT / asset["canonical_file"]
        served = ROOT / asset["served_file"]
        inspected = inspect_glb(canonical)
        if inspected["sha256"] != asset["sha256"]:
            raise ValueError(f"{asset_id}: sha256 mismatch")
        if inspected["bytes"] != asset["bytes"] or inspected["bytes"] > asset["max_bytes"]:
            raise ValueError(f"{asset_id}: size mismatch or browser budget exceeded")
        if sha256(served) != inspected["sha256"]:
            raise ValueError(f"{asset_id}: served copy differs from canonical file")
        tolerance = asset.get("bounds_tolerance_m", 0.001)
        if not close_vector(inspected["bounds_min_xyz"], asset["bounds_min_xyz"], tolerance):
            raise ValueError(f"{asset_id}: minimum bounds mismatch")
        if not close_vector(inspected["bounds_max_xyz"], asset["bounds_max_xyz"], tolerance):
            raise ValueError(f"{asset_id}: maximum bounds mismatch")
        for node in asset["required_nodes"]:
            if node not in inspected["nodes"]:
                raise ValueError(f"{asset_id}: required node {node!r} missing")
        if asset["license"] not in {"CC0-1.0", "CC-BY-4.0"}:
            raise ValueError(f"{asset_id}: unapproved license")
        if not asset["original_page"].startswith("https://"):
            raise ValueError(f"{asset_id}: original page must be HTTPS")
        asset_record_path = ROOT / asset["asset_record"]
        if not asset_record_path.is_file():
            raise ValueError(f"{asset_id}: missing asset record")
        asset_record = json.loads(asset_record_path.read_text())
        if asset_record.get("schema_version") != "horizon.maritime-asset.v1":
            raise ValueError(f"{asset_id}: unexpected asset record schema")
        if asset_record.get("asset_id") != asset_id:
            raise ValueError(f"{asset_id}: asset record identifier mismatch")
        if asset_record.get("sha256") != inspected["sha256"]:
            raise ValueError(f"{asset_id}: asset record sha256 mismatch")
        if asset_record.get("bytes") != inspected["bytes"]:
            raise ValueError(f"{asset_id}: asset record size mismatch")
        source = asset_record.get("source", {})
        if source.get("license") != asset["license"]:
            raise ValueError(f"{asset_id}: asset record license mismatch")
        if source.get("original_page") != asset["original_page"]:
            raise ValueError(f"{asset_id}: asset record source page mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--inspect", nargs="*", type=Path)
    args = parser.parse_args()
    if args.inspect:
        print(
            json.dumps(
                {str(path): inspect_glb(path) for path in args.inspect},
                indent=2,
                sort_keys=True,
            )
        )
        return
    validate_registry(args.registry)
    print(f"valid {args.registry}")


if __name__ == "__main__":
    main()
