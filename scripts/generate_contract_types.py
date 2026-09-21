#!/usr/bin/env python3
"""Generate lightweight Python and TypeScript declarations from the contract schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "packages/contracts/schema/horizon.schema.json"
PYTHON_OUT = ROOT / "packages/contracts/python/horizon_contracts/types.py"
TYPESCRIPT_OUT = ROOT / "packages/contracts/typescript/src/index.ts"


def ref_name(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def py_type(node: dict[str, Any]) -> str:
    if "$ref" in node:
        return ref_name(node["$ref"])
    if "const" in node:
        return f"Literal[{node['const']!r}]"
    if "enum" in node:
        return "Literal[" + ", ".join(repr(item) for item in node["enum"]) + "]"
    if "oneOf" in node:
        return " | ".join(py_type(item) for item in node["oneOf"])
    kind = node.get("type")
    if isinstance(kind, list):
        return " | ".join(py_type({"type": item}) for item in kind)
    if kind == "string":
        return "str"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "null":
        return "None"
    if kind == "array":
        return f"list[{py_type(node.get('items', {}))}]"
    if kind == "object":
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {py_type(extra)}]"
        return "dict[str, Any]"
    return "Any"


def ts_type(node: dict[str, Any]) -> str:
    if "$ref" in node:
        return ref_name(node["$ref"])
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(item) for item in node["enum"])
    if "oneOf" in node:
        return " | ".join(ts_type(item) for item in node["oneOf"])
    kind = node.get("type")
    if isinstance(kind, list):
        return " | ".join(ts_type({"type": item}) for item in kind)
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        return f"Array<{ts_type(node.get('items', {}))}>"
    if kind == "object":
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {ts_type(extra)}>"
        return "Record<string, unknown>"
    return "unknown"


def generate(schema: dict[str, Any]) -> tuple[str, str]:
    defs = schema["$defs"]
    # Python aliases and TypedDict annotations are evaluated at import time.
    # Emit referenced definitions first, regardless of JSON member order.
    ordered: dict[str, Any] = {}
    visiting: set[str] = set()

    def references(node: Any):
        if isinstance(node, dict):
            if "$ref" in node:
                yield ref_name(node["$ref"])
            for child in node.values():
                yield from references(child)
        elif isinstance(node, list):
            for child in node:
                yield from references(child)

    def visit(name: str) -> None:
        if name in ordered:
            return
        if name in visiting:
            raise ValueError(f"recursive contract requires forward-reference support: {name}")
        visiting.add(name)
        for dependency in references(defs[name]):
            visit(dependency)
        visiting.remove(name)
        ordered[name] = defs[name]

    for name in defs:
        visit(name)
    py = [
        '"""Generated from packages/contracts/schema/horizon.schema.json; do not edit."""',
        "",
        "from typing import Any, Literal, NotRequired, TypedDict, TypeAlias",
        "",
    ]
    ts = ["// Generated from packages/contracts/schema/horizon.schema.json; do not edit.", ""]
    for name, node in ordered.items():
        if node.get("type") == "object" and "properties" in node:
            required = set(node.get("required", []))
            py.append(f"class {name}(TypedDict):")
            ts.append(f"export interface {name} {{")
            for prop, child in node["properties"].items():
                ptype = py_type(child)
                if prop not in required:
                    ptype = f"NotRequired[{ptype}]"
                py.append(f"    {prop}: {ptype}")
                optional = "" if prop in required else "?"
                ts.append(f"  {prop}{optional}: {ts_type(child)};")
            if not node["properties"]:
                py.append("    pass")
            py.append("")
            ts.extend(["}", ""])
        else:
            py.append(f"{name}: TypeAlias = {py_type(node)}")
            ts.append(f"export type {name} = {ts_type(node)};")
    return "\n".join(py).rstrip() + "\n", "\n".join(ts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    python_text, typescript_text = generate(json.loads(SCHEMA.read_text()))
    expected = ((PYTHON_OUT, python_text), (TYPESCRIPT_OUT, typescript_text))
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, text in expected if not path.exists() or path.read_text() != text]
        if stale:
            print("stale generated files: " + ", ".join(stale))
            return 1
        return 0
    for path, text in expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
