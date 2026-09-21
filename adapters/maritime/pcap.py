from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path
import struct
from typing import Any, Iterable


class PcapError(ValueError):
    pass


def _endian_and_scale(magic: bytes) -> tuple[str, float]:
    options = {
        b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
        b"\xa1\xb2\xc3\xd4": (">", 1e-6),
        b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
        b"\xa1\xb2\x3c\x4d": (">", 1e-9),
    }
    if magic not in options:
        raise PcapError("unsupported PCAP magic")
    return options[magic]


def _addresses(packet: bytes) -> tuple[str, str]:
    if len(packet) >= 34 and packet[12:14] == b"\x08\x00" and packet[14] >> 4 == 4:
        return str(ipaddress.IPv4Address(packet[26:30])), str(ipaddress.IPv4Address(packet[30:34]))
    return "unknown", "unknown"


def replay(path: str | Path, *, run_id: str, branch_id: str, start_monotonic_ns: int = 0, maximum_packets: int = 10_000) -> Iterable[dict[str, Any]]:
    source = Path(path)
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with source.open("rb") as handle:
        header = handle.read(24)
        if len(header) != 24:
            raise PcapError("truncated global header")
        endian, fraction_scale = _endian_and_scale(header[:4])
        first_time: float | None = None
        for sequence in range(maximum_packets):
            record_header = handle.read(16)
            if not record_header:
                break
            if len(record_header) != 16:
                raise PcapError("truncated packet header")
            seconds, fraction, captured_length, original_length = struct.unpack(f"{endian}IIII", record_header)
            if captured_length > 16_777_216:
                raise PcapError("packet exceeds 16 MB parser bound")
            packet = handle.read(captured_length)
            if len(packet) != captured_length:
                raise PcapError("truncated packet body")
            timestamp = seconds + fraction * fraction_scale
            if first_time is None:
                first_time = timestamp
            event_time = max(0.0, timestamp - first_time)
            received = start_monotonic_ns + round(event_time * 1e9)
            source_ip, destination_ip = _addresses(packet)
            # The archive filename/sidecar labels are evaluation-only and never emitted.
            yield {
                "contract_type": "NetworkObservation",
                "schema_version": "0.1.0",
                "observation_id": f"{run_id}:{branch_id}:marsim-packet:{sequence}",
                "run_id": run_id,
                "branch_id": branch_id,
                "source_id": source_ip,
                "destination_id": destination_ip,
                "sequence": sequence,
                "time": {"event_time_s": event_time, "received_monotonic_ns": received, "valid_until_monotonic_ns": received + 1_000_000_000, "clock_uncertainty_ms": 1.0},
                "capture_status": "observed",
                "application_status": "unknown",
                "provenance": {"kind": "recorded", "source_id": "MARSIM public simulated network capture", "artifact_uri": f"external:marsim-pcap:sha256:{file_sha}", "sha256": file_sha, "rights": f"CC BY 4.0; offline simulated onboard traffic; packet {captured_length}/{original_length} bytes; packet sha256 {hashlib.sha256(packet).hexdigest()}"},
            }
