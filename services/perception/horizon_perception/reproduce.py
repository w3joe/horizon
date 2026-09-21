"""Finite WaSR-T sequence reproduction command used locally or on Modal."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import inspect
import platform
from time import perf_counter_ns
import subprocess

from .model import ModelSpec, sha256_file, load_official_model
from .instrumentation import ActivationRecorder, SpatialActivationRecorder
from .runner import SequentialPerceptionRunner, preprocess_image, synchronize_device
from .health_features import ConventionalHealthTracker, summarize_segmentation_output


COLORS = ((247, 195, 37), (41, 167, 224), (90, 75, 164))


def run_sequence(
    spec: ModelSpec,
    sequence_dir: Path,
    output_dir: Path,
    device: str,
    fp16: bool,
    *,
    frame_glob: str = "*.jpg",
    evidence_partition: str = "integration",
    max_frames: int | None = None,
) -> dict:
    import torch
    from PIL import Image
    from torchvision.transforms.functional import resize

    if evidence_partition not in {"integration", "development", "calibration", "heldout"}:
        raise ValueError("invalid evidence partition")
    frames = sorted(sequence_dir.glob(frame_glob))
    if max_frames is not None:
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        frames = frames[:max_frames]
    if not frames:
        raise ValueError(f"no JPG frames in {sequence_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    masks_dir = output_dir / "class_masks"
    masks_dir.mkdir()
    previews_dir = output_dir / "mask_previews"
    previews_dir.mkdir()
    spatial_dir = output_dir / "spatial_probes"
    model = load_official_model(spec, device=device, fp16=fp16)
    agreement = compare_instrumentation(model, spec.family, frames[:4], device, fp16)
    if not agreement["outputs_identical"] or not agreement["reset_reproducible"]:
        raise RuntimeError("instrumentation or sequential reset changed model output")
    runner = SequentialPerceptionRunner(model, spec.family, device=device, fp16=fp16)
    runner.reset(sequence_dir.name)
    conventional = ConventionalHealthTracker()
    conventional.reset()
    records = []
    spatial_records = []
    probe_indexes = sorted({0, len(frames) // 4, len(frames) // 2, 3 * len(frames) // 4, len(frames) - 1})
    for index, frame in enumerate(frames):
        spatial = None
        if index in probe_indexes:
            channels = {"encoder": (0, 511, 1023, 1535, 2047), "decoder_logits": (0, 1, 2)}
            if spec.family == "wasr_t":
                channels["temporal_fusion"] = (0, 511, 1023, 1535, 2047)
            spatial = SpatialActivationRecorder(model, spec.family, channels).install()
        try:
            result = runner.infer(frame, frame.name, index * 100_000_000)
        finally:
            if spatial is not None:
                spatial.remove()
        if spatial is not None:
            spatial_records.extend(spatial.write(spatial_dir, frame.stem))
        # MPS lacks antialiased bilinear resize for this tensor path. Postprocess
        # detached logits on CPU; inference timing has already stopped.
        started = perf_counter_ns()
        cpu_logits = result.logits.float().cpu()
        copy_ms = (perf_counter_ns() - started) / 1_000_000
        started = perf_counter_ns()
        logits = resize(cpu_logits, [384, 512], antialias=True)
        resize_ms = (perf_counter_ns() - started) / 1_000_000
        started = perf_counter_ns()
        output_health = summarize_segmentation_output(logits)
        probabilities = torch.softmax(logits, dim=1)
        conventional_health = conventional.extract(frame, probabilities, index * 100_000_000)
        health_ms = (perf_counter_ns() - started) / 1_000_000
        classes = logits.argmax(1)[0].cpu()
        rgb = torch.tensor(COLORS, dtype=torch.uint8)[classes]
        Image.fromarray(classes.byte().numpy(), mode="L").save(masks_dir / f"{frame.stem}.png")
        Image.fromarray(rgb.numpy()).save(previews_dir / f"{frame.stem}.png")
        row = asdict(result)
        row.pop("logits")
        with Image.open(frame) as source_image:
            row["source_image_size"] = list(source_image.size)
        row["model_input_size"] = [512, 384]
        row["timestamp_source"] = "synthetic_10hz_order_only"
        row["output_health"] = output_health
        row["conventional_health"] = conventional_health
        row["postprocessing_ms"] = {
            "device_to_cpu": copy_ms,
            "cpu_resize": resize_ms,
            "health_features": health_ms,
        }
        records.append(row)
    runner.close()
    with (output_dir / "features.jsonl").open("w") as stream:
        for row in records:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    (output_dir / "spatial-probes.json").write_text(
        json.dumps(
            {
                "selection_rule": "five evenly spaced frames; fixed channel indices chosen before inference",
                "semantic_claim": "none; channel maps are unlabeled diagnostics",
                "records": spatial_records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    manifest = {
        "schema_version": "horizon.perception-reproduction.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_family": spec.family,
        "architecture": spec.architecture,
        "source_commit": spec.source_commit,
        "weights_sha256": spec.weights_sha256,
        "sequence": str(sequence_dir),
        "frame_glob": frame_glob,
        "evidence_partition": evidence_partition,
        "sequence_frame_count": len(frames),
        "class_ids": {"obstacle": 0, "water": 1, "sky": 2, "ignore_in_labels": 4},
        "model_input_size": [512, 384],
        "timestamp_source": "synthetic_10hz_order_only",
        "input_sha256": {frame.name: sha256_file(frame) for frame in frames},
        "device": device,
        "horizon_code": code_identity(),
        "fp16": fp16,
        "instrumented_layers": (
            ["encoder", "temporal_fusion", "decoder_logits"]
            if spec.family == "wasr_t"
            else ["encoder", "decoder_logits"]
        ),
        "instrumentation_validation": agreement,
        "latency_ms": {
            "instrumented_forward_median": statistics.median(r["inference_ms"] for r in records),
            "instrumented_forward_maximum": max(r["inference_ms"] for r in records),
            "hook_capture_median": statistics.median(r["instrumentation_ms"] for r in records),
            "device_to_cpu_median": statistics.median(
                r["postprocessing_ms"]["device_to_cpu"] for r in records
            ),
            "cpu_resize_median": statistics.median(
                r["postprocessing_ms"]["cpu_resize"] for r in records
            ),
            "health_features_median": statistics.median(
                r["postprocessing_ms"]["health_features"] for r in records
            ),
        },
        "limitations": [
            "Provided example sequence is a reproduction fixture, not held-out evidence.",
            "Scores and activations are perception evidence, not obstacle-free or safety truth.",
        ],
        "environment": environment_manifest(device),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def compare_instrumentation(model, family: str, frames: list[Path], device: str, fp16: bool) -> dict:
    """Compare the same short sequential prefix with and without hooks."""
    import time
    import torch

    if len(frames) < 2:
        raise ValueError("instrumentation comparison needs a warm-up and measured frame")
    inputs = [preprocess_image(frame, device, fp16) for frame in frames]

    def run(recorder=None):
        if family == "wasr_t":
            model.clear_state()
        outputs, latencies, copies = [], [], []
        with torch.inference_mode():
            # Each arm gets the same one-frame sequential warm-up. Its latency is
            # excluded so hook overhead is not compared against a cold baseline.
            model({"image": inputs[0]})
            synchronize_device(device, torch)
            for image in inputs[1:]:
                if recorder:
                    recorder.clear()
                synchronize_device(device, torch)
                started = time.perf_counter_ns()
                output = model({"image": image})["out"].detach()
                synchronize_device(device, torch)
                latencies.append((time.perf_counter_ns() - started) / 1_000_000)
                started = time.perf_counter_ns()
                outputs.append(output.float().cpu())
                copies.append((time.perf_counter_ns() - started) / 1_000_000)
        return outputs, latencies, copies

    vanilla, vanilla_ms, vanilla_copy_ms = run()
    replay, _replay_ms, _replay_copy_ms = run()
    recorder = ActivationRecorder(model, family).install()
    instrumented, instrumented_ms, instrumented_copy_ms = run(recorder)
    layers = sorted(recorder.records)
    recorder.remove()
    differences = [float((a - b).abs().max().item()) for a, b in zip(vanilla, instrumented)]
    reset_differences = [float((a - b).abs().max().item()) for a, b in zip(vanilla, replay)]
    return {
        "measured_frames": len(inputs) - 1,
        "warmup_frames_per_arm": 1,
        "timing_note": "small-n device timing; one equal sequential warm-up frame per arm",
        "outputs_identical": all(torch.equal(a, b) for a, b in zip(vanilla, instrumented)),
        "maximum_absolute_difference": max(differences, default=0.0),
        "reset_reproducible": all(torch.equal(a, b) for a, b in zip(vanilla, replay)),
        "reset_maximum_absolute_difference": max(reset_differences, default=0.0),
        "vanilla_mean_ms": sum(vanilla_ms) / len(vanilla_ms),
        "instrumented_mean_ms": sum(instrumented_ms) / len(instrumented_ms),
        "mean_overhead_ms": sum(instrumented_ms) / len(instrumented_ms) - sum(vanilla_ms) / len(vanilla_ms),
        "vanilla_device_to_cpu_mean_ms": sum(vanilla_copy_ms) / len(vanilla_copy_ms),
        "instrumented_device_to_cpu_mean_ms": sum(instrumented_copy_ms) / len(instrumented_copy_ms),
        "captured_layers": layers,
    }


def code_identity() -> dict:
    root = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "services/perception", "services/neural-health", "configs/perception"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unavailable", True
    files = (Path(__file__), Path(__file__).with_name("runner.py"), Path(__file__).with_name("instrumentation.py"), Path(__file__).with_name("health_features.py"))
    return {
        "git_commit": commit,
        "owned_paths_dirty": dirty,
        "source_sha256": {path.name: sha256_file(path) for path in files},
    }


def environment_manifest(device: str) -> dict:
    import numpy
    import PIL
    import torch
    import torchvision

    device_kind = str(device).split(":", 1)[0]
    accelerator = {"kind": device_kind}
    if device_kind == "cuda" and torch.cuda.is_available():
        accelerator.update(
            name=torch.cuda.get_device_name(0),
            cuda=torch.version.cuda,
            cudnn=torch.backends.cudnn.version(),
        )
    elif device_kind == "mps":
        accelerator.update(available=torch.backends.mps.is_available(), macos=platform.mac_ver()[0])
    source = inspect.getsource(preprocess_image).encode()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "pillow": PIL.__version__,
        "numpy": numpy.__version__,
        "accelerator": accelerator,
        "preprocessing": {
            "implementation": "horizon_perception.runner:preprocess_image",
            "sha256": __import__("hashlib").sha256(source).hexdigest(),
            "normalization": {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
            "resize": [512, 384],
            "antialias": True,
        },
        "postprocessing": {
            "logit_copy": "detached accelerator tensor to CPU after synchronized forward timing",
            "resize": "torchvision antialiased bilinear on CPU to 384x512",
            "health": "pixelwise softmax/entropy and conventional checks on CPU",
        },
        "compatibility_changes": [
            "construct ResNet101 without downloading backbone weights before loading full checkpoint",
            "training-only pytorch_lightning logger/callback import shim",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--frame-glob", default="*.jpg")
    parser.add_argument("--partition", default="integration", choices=("integration", "development", "calibration", "heldout"))
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    spec = ModelSpec(
        family="wasr_t",
        source_dir=args.source,
        source_commit="1b5360af20408e09bbf0116a0029f7e0c0800e7c",
        weights=args.weights,
        weights_sha256="6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef",
        architecture="wasr_temporal_resnet101",
    )
    print(json.dumps(run_sequence(
        spec,
        args.sequence,
        args.output,
        args.device,
        args.fp16,
        frame_glob=args.frame_glob,
        evidence_partition=args.partition,
        max_frames=args.max_frames,
    ), indent=2))


if __name__ == "__main__":
    main()
