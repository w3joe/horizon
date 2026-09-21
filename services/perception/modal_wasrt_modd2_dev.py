"""Finite, development-only MODD2 WaSR-T extraction entrypoint for Modal.

The central compute launcher owns reservation, launch, timeout, and reconciliation.
This module only describes one immutable 296-frame extraction job.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import modal


LOCAL_REPO = Path(__file__).resolve().parents[2]
LOCAL_DATA = Path(os.environ.get("HORIZON_DATA_ROOT", "/Users/w3joe/Desktop/2026_sdth/horizon-data"))
LOCAL_SERVICE = Path(__file__).resolve().parent
SOURCE = LOCAL_DATA / "sources" / "WaSR-T"
WEIGHTS = LOCAL_DATA / "weights" / "wasrt_mastr1325.pth"
SEQUENCE_NAME = "kope81-00-00006800-00007095"
FRAMES = LOCAL_DATA / "datasets" / "modd2" / "video" / "video_data" / SEQUENCE_NAME / "frames"
SPLIT_MANIFEST = LOCAL_REPO / "configs" / "perception" / "modd2-splits.json"

RUN_ID = os.environ["HORIZON_MODAL_RUN_ID"]
VOLUME_NAME = os.environ["HORIZON_MODAL_VOLUME"]
EXPECTED_FRAME_COUNT = 296
EXPECTED_INPUT_BYTES = 105_562_636
OUTPUT_CAP_BYTES = 100 * 1024 * 1024
SPLIT_MANIFEST_SHA256 = "45a116eeeeeb55e9a2e566327c6d6045a5f8682f8ad7c5a6260ef4eaf301b425"

# These values come from the clean host checkout and central job spec. Container
# source trees intentionally do not pretend to be Git checkouts.
LAUNCH_PROVENANCE_KEYS = (
    "HORIZON_LAUNCH_COMMIT",
    "HORIZON_LAUNCH_SOURCE_TREE_SHA256",
    "HORIZON_LAUNCH_ENTRYPOINT_SHA256",
    "HORIZON_LAUNCH_JOB_SPEC_SHA256",
)
launch_provenance = {key: os.environ[key] for key in LAUNCH_PROVENANCE_KEYS}

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.5.1", "torchvision==0.20.1", "Pillow==10.4.0")
    .add_local_dir(SOURCE, remote_path="/opt/wasr-t", copy=True)
    .run_commands("printf '%s\\n' 1b5360af20408e09bbf0116a0029f7e0c0800e7c > /opt/wasr-t/.horizon-source-commit")
    .add_local_dir(LOCAL_SERVICE, remote_path="/opt/horizon/services/perception", copy=True)
    .add_local_dir(FRAMES, remote_path=f"/opt/input/{SEQUENCE_NAME}/frames", copy=True)
    .add_local_file(SPLIT_MANIFEST, remote_path="/opt/input/modd2-splits.json", copy=True)
    .env(
        {
            "HORIZON_MODAL_RUN_ID": RUN_ID,
            "HORIZON_MODAL_VOLUME": VOLUME_NAME,
            **launch_provenance,
        }
    )
    .add_local_file(WEIGHTS, remote_path="/opt/weights/wasrt_mastr1325.pth", copy=False)
)
artifacts = modal.Volume.from_name(VOLUME_NAME, create_if_missing=False)
app = modal.App("horizon-a07-wasrt-modd2-dev", image=image)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _launch_record() -> dict[str, str]:
    return {
        "clean_host_commit": os.environ["HORIZON_LAUNCH_COMMIT"],
        "source_tree_sha256": os.environ["HORIZON_LAUNCH_SOURCE_TREE_SHA256"],
        "entrypoint_sha256": os.environ["HORIZON_LAUNCH_ENTRYPOINT_SHA256"],
        "job_spec_sha256": os.environ["HORIZON_LAUNCH_JOB_SPEC_SHA256"],
    }


@app.function(
    gpu="L4",
    cpu=(4.0, 4.0),
    memory=(16384, 16384),
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    retries=0,
    startup_timeout=900,
    timeout=1200,
    volumes={"/outputs": artifacts},
    block_network=True,
)
def extract_development_sequence() -> dict:
    import sys

    sys.path.insert(0, "/opt/horizon/services/perception")
    from horizon_perception.model import ModelSpec
    from horizon_perception.reproduce import run_sequence

    sequence = Path(f"/opt/input/{SEQUENCE_NAME}/frames")
    frames = sorted(sequence.glob("*L.jpg"))
    input_bytes = sum(path.stat().st_size for path in frames)
    if len(frames) != EXPECTED_FRAME_COUNT or input_bytes != EXPECTED_INPUT_BYTES:
        raise RuntimeError(
            f"immutable input mismatch: frames={len(frames)}, bytes={input_bytes}"
        )
    split_manifest = Path("/opt/input/modd2-splits.json")
    if _sha256(split_manifest) != SPLIT_MANIFEST_SHA256:
        raise RuntimeError("development split manifest digest mismatch")

    output = Path("/outputs") / RUN_ID
    spec = ModelSpec(
        family="wasr_t",
        source_dir=Path("/opt/wasr-t"),
        source_commit="1b5360af20408e09bbf0116a0029f7e0c0800e7c",
        weights=Path("/opt/weights/wasrt_mastr1325.pth"),
        weights_sha256="6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef",
        architecture="wasr_temporal_resnet101",
    )
    try:
        result = run_sequence(
            spec,
            sequence,
            output,
            "cuda",
            True,
            frame_glob="*L.jpg",
            evidence_partition="development",
        )
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["split_manifest"] = {
            "path": "configs/perception/modd2-splits.json",
            "sha256": SPLIT_MANIFEST_SHA256,
        }
        manifest["controller_launch_provenance"] = _launch_record()
        manifest["container_git_identity_authoritative"] = False
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        feature_count = sum(1 for line in (output / "features.jsonl").read_text().splitlines() if line)
        mask_count = len(list((output / "class_masks").glob("*.png")))
        preview_count = len(list((output / "mask_previews").glob("*.png")))
        probes = json.loads((output / "spatial-probes.json").read_text())["records"]
        probe_frame_count = len({record["frame_id"] for record in probes})
        observed = (feature_count, mask_count, preview_count, probe_frame_count)
        expected = (EXPECTED_FRAME_COUNT, EXPECTED_FRAME_COUNT, EXPECTED_FRAME_COUNT, 5)
        if observed != expected:
            raise RuntimeError(f"artifact cardinality mismatch: {observed} != {expected}")

        output_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        if output_bytes > OUTPUT_CAP_BYTES:
            raise RuntimeError(f"artifact cap exceeded: {output_bytes} > {OUTPUT_CAP_BYTES}")
        artifacts.commit()
        return {
            "run_id": RUN_ID,
            "frame_count": result["sequence_frame_count"],
            "manifest": str(manifest_path),
            "features": str(output / "features.jsonl"),
            "mask_count": mask_count,
            "preview_count": preview_count,
            "probe_frame_count": probe_frame_count,
            "output_bytes": output_bytes,
        }
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        artifacts.commit()
        raise


@app.local_entrypoint()
def main() -> None:
    print(extract_development_sequence.remote())
