from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any, Iterable


class NMEAError(ValueError):
    pass


@dataclass(frozen=True)
class NMEASentence:
    talker: str
    sentence_type: str
    fields: tuple[str, ...]
    checksum: int
    raw_sha256: str


def parse_sentence(raw: bytes | str) -> NMEASentence:
    value = raw if isinstance(raw, bytes) else raw.encode("ascii", "strict")
    value = value.strip(b"\r\n")
    if len(value) > 1024:
        raise NMEAError("sentence exceeds 1024 byte parser bound")
    if not value.startswith((b"$", b"!")) or b"*" not in value:
        raise NMEAError("sentence must have a start marker and checksum")
    body, checksum_text = value[1:].rsplit(b"*", 1)
    if len(checksum_text) != 2:
        raise NMEAError("checksum must contain two hexadecimal digits")
    try:
        expected = int(checksum_text, 16)
    except ValueError as exc:
        raise NMEAError("checksum is not hexadecimal") from exc
    actual = 0
    for byte in body:
        actual ^= byte
    if actual != expected:
        raise NMEAError(f"checksum mismatch: expected {expected:02X}, computed {actual:02X}")
    fields = body.decode("ascii", "strict").split(",")
    if not fields[0] or len(fields[0]) < 3:
        raise NMEAError("missing talker/sentence identifier")
    return NMEASentence(
        talker=fields[0][:-3],
        sentence_type=fields[0][-3:],
        fields=tuple(fields[1:]),
        checksum=expected,
        raw_sha256=hashlib.sha256(value).hexdigest(),
    )


def _float(value: str, name: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise NMEAError(f"invalid {name}") from exc
    if not math.isfinite(result):
        raise NMEAError(f"non-finite {name}")
    return result


def _coordinate(value: str, hemisphere: str, *, latitude: bool) -> float:
    raw = _float(value, "coordinate")
    degrees_width = 2 if latitude else 3
    text = value.split(".")[0]
    if len(text) < degrees_width + 2:
        raise NMEAError("coordinate is too short")
    divisor = 100.0
    degrees = int(raw // divisor)
    minutes = raw - degrees * divisor
    if minutes >= 60.0 or degrees > (90 if latitude else 180):
        raise NMEAError("coordinate outside range")
    decimal = degrees + minutes / 60.0
    if hemisphere in {"S", "W"}:
        decimal = -decimal
    elif hemisphere not in {"N", "E"}:
        raise NMEAError("invalid hemisphere")
    return decimal


def normalize(sentence: NMEASentence) -> dict[str, Any]:
    fields = sentence.fields
    kind = sentence.sentence_type
    if kind in {"HDG", "HDM", "HDT"}:
        if not fields:
            raise NMEAError("heading sentence has no value")
        return {"heading_rad": math.radians(_float(fields[0], "heading_deg") % 360.0), "heading_reference": {"HDG": "magnetic_with_deviation", "HDM": "magnetic", "HDT": "true"}[kind]}
    if kind == "MWV":
        if len(fields) < 4:
            raise NMEAError("MWV requires angle, reference, speed, and unit")
        speed = _float(fields[2], "wind_speed")
        factors = {"N": 0.5144444444444445, "M": 1.0, "K": 1.0 / 3.6}
        if fields[3] not in factors:
            raise NMEAError("unsupported MWV unit")
        return {"wind_angle_rad": math.radians(_float(fields[0], "wind_angle_deg") % 360.0), "wind_reference": fields[1], "wind_speed_mps": speed * factors[fields[3]]}
    if kind == "DBS":
        if len(fields) < 5:
            raise NMEAError("DBS requires depth fields")
        candidates = []
        if fields[2]:
            candidates.append(_float(fields[2], "depth_m"))
        if fields[0]:
            candidates.append(_float(fields[0], "depth_ft") * 0.3048)
        if fields[4]:
            candidates.append(_float(fields[4], "depth_fathom") * 1.8288)
        if not candidates:
            raise NMEAError("DBS contains no depth")
        return {"depth_m": candidates[0], "reported_depths_m": candidates}
    if kind in {"GGA", "RMC"}:
        offset = 1 if kind == "GGA" else 2
        if len(fields) < offset + 4:
            raise NMEAError(f"{kind} has incomplete coordinates")
        payload: dict[str, Any] = {
            "latitude_deg": _coordinate(fields[offset], fields[offset + 1], latitude=True),
            "longitude_deg": _coordinate(fields[offset + 2], fields[offset + 3], latitude=False),
        }
        if kind == "RMC" and len(fields) > 6 and fields[6]:
            payload["speed_mps"] = _float(fields[6], "speed_knots") * 0.5144444444444445
        return payload
    return {"sentence_type": kind, "fields": list(fields), "normalized": False}


def replay(path: str | Path, *, run_id: str, branch_id: str, start_monotonic_ns: int = 0, validity_s: float = 1.0) -> Iterable[dict[str, Any]]:
    source = Path(path)
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with source.open("rb") as handle:
        for sequence, line in enumerate(handle):
            parsed = parse_sentence(line)
            received = start_monotonic_ns + sequence * 100_000_000
            payload = normalize(parsed)
            payload["sentence_type"] = parsed.sentence_type
            payload["sentence_sha256"] = parsed.raw_sha256
            group = "navigation_environment"
            yield {
                "contract_type": "Observation",
                "schema_version": "0.1.0",
                "observation_id": f"{run_id}:{branch_id}:nmea:{sequence}",
                "run_id": run_id,
                "branch_id": branch_id,
                "input_group": group,
                "source_id": f"nmea/{parsed.talker or 'unknown'}",
                "sequence": sequence,
                "time": {"event_time_s": sequence * 0.1, "received_monotonic_ns": received, "valid_until_monotonic_ns": received + round(validity_s * 1e9), "clock_uncertainty_ms": 100.0},
                "units": "SI-normalized",
                "frame": "geodetic/sensor",
                "capability": "available",
                "provenance": {"kind": "recorded", "source_id": "signalk-nmea0183-sample", "artifact_uri": f"external:sha256:{file_sha}", "sha256": file_sha, "rights": "source terms recorded in data manifest"},
                "payload": payload,
            }
