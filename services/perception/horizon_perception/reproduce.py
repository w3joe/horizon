"""Finite WaSR-T sequence reproduction command used locally or on Modal."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

from .model import ModelSpec, sha256_file, load_official_model
from .instrumentation import ActivationRecorder
from .runner import SequentialPerceptionRunner, preprocess_image


COLORS = ((247, 195, 37), (41, 167, 224), (90, 75, 164))


def run_sequence(spec: ModelSpec, sequence_dir: Path, output_dir: Path, device: str, fp16: bool) -> dict:
    import torch
    from PIL import Image
    from torchvision.transforms.functional import resize

    frames = sorted(sequence_dir.glob("*.jpg"))
    if not frames:
        raise ValueError(f"no JPG frames in {sequence_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    masks_dir = output_dir / "class_masks"
    masks_dir.mkdir()
    previews_dir = output_dir / "mask_previews"
    previews_dir.mkdir()
    model = load_official_model(spec, device=device, fp16=fp16)
    agreement = compare_instrumentation(model, spec.family, frames[:4], device, fp16)
    if not agreement["outputs_identical"] or not agreement["reset_reproducible"]:
        raise RuntimeError("instrumentation or sequential reset changed model output")
    runner = SequentialPerceptionRunner(model, spec.family, device=device, fp16=fp16)
    runner.reset(sequence_dir.name)
    records = []
    for index, frame in enumerate(frames):
        result = runner.infer(frame, frame.name, index * 100_000_000)
        logits = resize(result.logits.float(), [384, 512], antialias=True)
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
        records.append(row)
    runner.close()
    with (output_dir / "features.jsonl").open("w") as stream:
        for row in records:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    manifest = {
        "schema_version": "horizon.perception-reproduction.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_family": spec.family,
        "architecture": spec.architecture,
        "source_commit": spec.source_commit,
        "weights_sha256": spec.weights_sha256,
        "sequence": str(sequence_dir),
        "sequence_frame_count": len(frames),
        "class_ids": {"obstacle": 0, "water": 1, "sky": 2, "ignore_in_labels": 4},
        "model_input_size": [512, 384],
        "timestamp_source": "synthetic_10hz_order_only",
        "input_sha256": {frame.name: sha256_file(frame) for frame in frames},
        "device": device,
        "fp16": fp16,
        "instrumented_layers": ["encoder", "temporal_fusion", "decoder_logits"],
        "instrumentation_validation": agreement,
        "latency_ms": {
            "median": statistics.median(r["inference_ms"] for r in records),
            "maximum": max(r["inference_ms"] for r in records),
            "instrumentation_median": statistics.median(r["instrumentation_ms"] for r in records),
        },
        "limitations": [
            "Provided example sequence is a reproduction fixture, not held-out evidence.",
            "Scores and activations are perception evidence, not obstacle-free or safety truth.",
        ],
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
        outputs, latencies = [], []
        with torch.inference_mode():
            # Each arm gets the same one-frame sequential warm-up. Its latency is
            # excluded so hook overhead is not compared against a cold baseline.
            model({"image": inputs[0]})
            for image in inputs[1:]:
                if recorder:
                    recorder.clear()
                if device.startswith("cuda"):
                    torch.cuda.synchronize()
                started = time.perf_counter_ns()
                outputs.append(model({"image": image})["out"].detach().float().cpu())
                if device.startswith("cuda"):
                    torch.cuda.synchronize()
                latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        return outputs, latencies

    vanilla, vanilla_ms = run()
    replay, _replay_ms = run()
    recorder = ActivationRecorder(model, family).install()
    instrumented, instrumented_ms = run(recorder)
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
        "captured_layers": layers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--fp16", action="store_true")
    args = parser.parse_args()
    spec = ModelSpec(
        family="wasr_t",
        source_dir=args.source,
        source_commit="1b5360af20408e09bbf0116a0029f7e0c0800e7c",
        weights=args.weights,
        weights_sha256="6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef",
        architecture="wasr_temporal_resnet101",
    )
    print(json.dumps(run_sequence(spec, args.sequence, args.output, args.device, args.fp16), indent=2))


if __name__ == "__main__":
    main()
