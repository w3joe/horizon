from __future__ import annotations

import json
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from console_proxy import compact_frame, load_artifact_frames, resolve_public_route  # noqa: E402


def test_public_proxy_routes_are_explicitly_allowlisted() -> None:
    assert resolve_public_route("/api/v1/public/snapshot?branch=protected") == (
        "simulator",
        "/v1/public/snapshot?branch=protected",
    )
    assert resolve_public_route("/api/fusion/v1/governor-input?branch=protected") == (
        "fusion",
        "/v1/governor-input?branch=protected",
    )
    assert resolve_public_route("/api/assurance/v1/evidence/latest") == (
        "assurance",
        "/v1/evidence/latest",
    )
    assert resolve_public_route("/api/v1/evaluation/truth") is None
    assert resolve_public_route("/api/gate/v1/decision") is None


def test_compact_artifact_frame_omits_pooled_vectors(tmp_path: Path) -> None:
    record = {
        "frame_id": "00000.jpg",
        "sequence_id": "sequence",
        "timestamp_ns": 0,
        "timestamp_source": "synthetic_10hz_order_only",
        "buffer_age": 0,
        "cold_start": True,
        "inference_ms": 10.0,
        "instrumentation_ms": 1.0,
        "source_image_size": [512, 384],
        "model_input_size": [512, 384],
        "activation_summaries": {
            "encoder": {
                "name": "encoder",
                "shape": [1, 2048, 48, 64],
                "dtype": "torch.float32",
                "finite": True,
                "minimum": 0.0,
                "maximum": 5.0,
                "mean": 0.1,
                "standard_deviation": 0.2,
                "pooled_mean": [0.1] * 2048,
                "pooled_standard_deviation": [0.2] * 2048,
            }
        },
    }
    (tmp_path / "features.jsonl").write_text(json.dumps(record) + "\n")
    frame = load_artifact_frames(tmp_path)["00000"]
    assert frame == compact_frame(record)
    encoded = json.dumps(frame)
    assert "pooled_mean" not in encoded
    assert frame["layers"]["encoder"]["std"] == 0.2
    assert frame["artifacts"]["raw_image_url"].endswith("/00000.jpg")
