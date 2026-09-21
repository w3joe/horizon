"""Finite Modal entrypoint. Invocation is controlled by A01's central ledger."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import modal


LOCAL_DATA = Path(os.environ.get("HORIZON_DATA_ROOT", "/Users/w3joe/Desktop/2026_sdth/horizon-data"))
LOCAL_SERVICE = Path(__file__).resolve().parent
SOURCE = LOCAL_DATA / "sources" / "WaSR-T"
WEIGHTS = LOCAL_DATA / "weights" / "wasrt_mastr1325.pth"
RUN_ID = "a07-wasrt-sequence-001"
OUTPUT_CAP_BYTES = 157_286_400
VOLUME_NAME = os.environ.get("HORIZON_MODAL_VOLUME", "horizon-a07-artifacts")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.5.1", "torchvision==0.20.1", "Pillow==10.4.0")
    .add_local_dir(SOURCE, remote_path="/opt/wasr-t", copy=True)
    .run_commands("printf '%s\\n' 1b5360af20408e09bbf0116a0029f7e0c0800e7c > /opt/wasr-t/.horizon-source-commit")
    .add_local_file(WEIGHTS, remote_path="/opt/weights/wasrt_mastr1325.pth", copy=True)
    .add_local_dir(LOCAL_SERVICE, remote_path="/opt/horizon/services/perception", copy=True)
)
artifacts = modal.Volume.from_name(VOLUME_NAME, create_if_missing=False)
app = modal.App("horizon-a07-wasrt-smoke", image=image)


@app.function(
    gpu="L4",
    cpu=(4.0, 4.0),
    memory=(16384, 16384),
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    retries=0,
    startup_timeout=900,
    timeout=1200,
    volumes={"/outputs": artifacts},
    block_network=True,
)
def reproduce() -> dict:
    import sys
    sys.path.insert(0, "/opt/horizon/services/perception")
    from horizon_perception.model import ModelSpec
    from horizon_perception.reproduce import run_sequence

    sequence = Path("/opt/wasr-t/examples/sequence")
    frames = sorted(sequence.glob("*.jpg"))
    if len(frames) != 85:
        raise RuntimeError(f"finite job requires exactly 85 frames, found {len(frames)}")
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
        result = run_sequence(spec, sequence, output, "cuda", True)
        output_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        if output_bytes > OUTPUT_CAP_BYTES:
            raise RuntimeError(f"artifact cap exceeded: {output_bytes} > {OUTPUT_CAP_BYTES}")
        artifacts.commit()
        return {
            "run_id": RUN_ID,
            "frame_count": result["sequence_frame_count"],
            "manifest": str(output / "manifest.json"),
            "output_bytes": output_bytes,
        }
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        artifacts.commit()
        raise


@app.local_entrypoint()
def main() -> None:
    print(reproduce.remote())
