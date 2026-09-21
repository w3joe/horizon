"""Stateful frame runner with explicit sequence reset and lineage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from .instrumentation import ActivationRecorder


IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class FrameResult:
    frame_id: str
    sequence_id: str
    timestamp_ns: int
    buffer_age: int
    cold_start: bool
    inference_ms: float
    instrumentation_ms: float
    logits: Any
    activation_summaries: dict[str, Any]


class SequentialPerceptionRunner:
    def __init__(self, model: Any, family: str, device: str = "cuda", fp16: bool = False):
        self.model = model
        self.family = family
        self.device = device
        self.fp16 = fp16
        self.recorder = ActivationRecorder(model, family).install()
        self.sequence_id: str | None = None
        self.frame_count = 0
        self.last_timestamp_ns: int | None = None

    def reset(self, sequence_id: str) -> None:
        if self.family == "wasr_t":
            self.model.clear_state()
        self.recorder.clear()
        self.sequence_id = sequence_id
        self.frame_count = 0
        self.last_timestamp_ns = None

    def infer(self, image_path: Path, frame_id: str, timestamp_ns: int) -> FrameResult:
        if self.sequence_id is None:
            raise RuntimeError("reset(sequence_id) is required before inference")
        if self.last_timestamp_ns is not None and timestamp_ns <= self.last_timestamp_ns:
            self.invalidate_sequence()
            raise ValueError("frame timestamps must be strictly increasing")
        import torch

        image = preprocess_image(image_path, self.device, self.fp16)
        self.recorder.clear()
        synchronize_device(self.device, torch)
        started = perf_counter_ns()
        with torch.inference_mode():
            output = self.model({"image": image})["out"]
        synchronize_device(self.device, torch)
        elapsed = perf_counter_ns() - started
        cold_start = self.frame_count == 0
        result = FrameResult(
            frame_id=frame_id,
            sequence_id=self.sequence_id,
            timestamp_ns=timestamp_ns,
            buffer_age=min(self.frame_count, 5) if self.family == "wasr_t" else 0,
            cold_start=cold_start,
            inference_ms=elapsed / 1_000_000,
            instrumentation_ms=self.recorder.capture_ns / 1_000_000,
            logits=output.detach(),
            activation_summaries={k: v.to_dict() for k, v in self.recorder.records.items()},
        )
        self.frame_count += 1
        self.last_timestamp_ns = timestamp_ns
        return result

    def invalidate_sequence(self) -> None:
        """Clear state after a lineage fault; a new explicit reset is required."""
        if self.family == "wasr_t":
            self.model.clear_state()
        self.recorder.clear()
        self.sequence_id = None
        self.frame_count = 0
        self.last_timestamp_ns = None

    def close(self) -> None:
        self.recorder.remove()


def preprocess_image(path: Path, device: str, fp16: bool):
    import torch
    from PIL import Image
    from torchvision.transforms.functional import pil_to_tensor, resize

    image = Image.open(path).convert("RGB")
    tensor = pil_to_tensor(image).float().div_(255.0)
    tensor = resize(tensor, [384, 512], antialias=True)
    mean = torch.tensor(IMAGENET_MEAN)[:, None, None]
    std = torch.tensor(IMAGENET_STD)[:, None, None]
    tensor = ((tensor - mean) / std).unsqueeze(0).to(device)
    return tensor.half() if fp16 else tensor


def synchronize_device(device: str, torch_module: Any) -> None:
    """Synchronize asynchronous accelerators before reading wall-clock time."""
    kind = str(device).split(":", 1)[0]
    if kind == "cuda":
        torch_module.cuda.synchronize()
    elif kind == "mps":
        torch_module.mps.synchronize()
