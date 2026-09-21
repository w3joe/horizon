"""A02-loadable H0-H4 functions. Artifact paths are explicit request fields."""

from __future__ import annotations

from .artifact import CalibrationArtifact, ReferenceArtifact
from .monitors import evaluate


def _run(method_id: str, request: dict) -> dict:
    payload = {**request, "method_id": method_id}
    calibration = CalibrationArtifact.load(request["calibration_path"]) if request.get("calibration_path") else None
    reference = ReferenceArtifact.load(request["reference_path"]) if request.get("reference_path") else None
    return evaluate(payload, calibration, reference)


def h0(request: dict) -> dict: return _run("H0", request)
def h1(request: dict) -> dict: return _run("H1", request)
def h2(request: dict) -> dict: return _run("H2", request)
def h3(request: dict) -> dict: return _run("H3", request)
def h4(request: dict) -> dict: return _run("H4", request)
