from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def replay(path: str | Path, *, run_id: str, branch_id: str, start_monotonic_ns: int = 0) -> Iterable[dict[str, Any]]:
    source = Path(path)
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    sequence = 0
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            dialog = json.loads(line)
            for utterance in dialog.get("utterances", []):
                received = start_monotonic_ns + sequence * 1_000_000_000
                yield {
                    "contract_type": "Observation",
                    "schema_version": "0.1.0",
                    "observation_id": f"{run_id}:{branch_id}:martts:{sequence}",
                    "run_id": run_id,
                    "branch_id": branch_id,
                    "input_group": "inter_ship_communications",
                    "source_id": f"martts/speaker-{utterance.get('speaker_id', 'unknown')}",
                    "sequence": sequence,
                    "time": {"event_time_s": float(sequence), "received_monotonic_ns": received, "valid_until_monotonic_ns": received + 5_000_000_000, "clock_uncertainty_ms": 1000.0},
                    "units": "utf-8 text claim",
                    "frame": "communications",
                    "capability": "available",
                    "provenance": {"kind": "synthetic", "source_id": "MARTTS synthetic maritime dialogue", "artifact_uri": f"external:sha256:{file_sha}", "sha256": file_sha, "rights": "CC BY 4.0; text dialogue, not recorded radio audio"},
                    "payload": {"dialog_id": dialog.get("dialog_id"), "topic": dialog.get("topic"), "speaker_id": utterance.get("speaker_id"), "turn": utterance.get("turn"), "text": utterance.get("text"), "claim_only": True, "observed_motion": None},
                }
                sequence += 1
