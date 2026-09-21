"""Actual H0 output and H1 conventional evidence extraction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any


def summarize_segmentation_output(logits: Any, roi_mask: Any | None = None, roi_definition: str | None = None) -> dict:
    """Summarize pixelwise predictive entropy before logits are discarded."""
    import torch

    if logits.ndim != 4 or logits.shape[0] != 1 or logits.shape[1] < 2:
        raise ValueError("expected logits with shape [1, classes>=2, height, width]")
    probabilities = torch.softmax(logits.float(), dim=1)
    entropy = -(probabilities * probabilities.clamp_min(1e-12).log()).sum(1) / math.log(logits.shape[1])
    confidence = probabilities.max(1).values
    if roi_mask is None:
        selected_entropy = entropy.flatten()
        selected_confidence = confidence.flatten()
        selected_probabilities = probabilities[0].flatten(1)
        selected_classes = probabilities.argmax(1).flatten()
        definition = "full_frame_uncalibrated_geometry"
        capability = "whole_frame_only"
    else:
        mask = roi_mask.to(device=logits.device, dtype=torch.bool)
        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        if mask.shape != entropy.shape or not bool(mask.any().item()) or not roi_definition:
            raise ValueError("ROI mask must be non-empty, shape-matched, and versioned")
        selected_entropy = entropy[mask]
        selected_confidence = confidence[mask]
        selected_probabilities = probabilities[0, :, mask[0]]
        selected_classes = probabilities.argmax(1)[mask]
        definition = roi_definition
        capability = "versioned_roi"
    means = selected_probabilities.mean(dim=-1)
    return {
        "class_mean_probabilities": [float(value) for value in means.detach().cpu().tolist()],
        "entropy_mean": float(selected_entropy.mean().item()),
        "entropy_p95": float(torch.quantile(selected_entropy, 0.95).item()),
        "confidence_mean": float(selected_confidence.mean().item()),
        "confidence_p05": float(torch.quantile(selected_confidence, 0.05).item()),
        "predicted_obstacle_fraction": float((selected_classes == 0).float().mean().item()),
        "roi_definition": definition,
        "roi_capability": capability,
        "pixel_count": int(selected_entropy.numel()),
        "class_ids": {"obstacle": 0, "water": 1, "sky": 2},
    }


@dataclass(frozen=True)
class ConventionalConfig:
    underexposed_luma: float = 0.05
    overexposed_luma: float = 0.95
    blur_laplacian_scale: float = 0.01
    maximum_frame_gap_ns: int = 250_000_000
    horizon_error_scale_pixels: float = 24.0


class ConventionalHealthTracker:
    """Stateful H1 evidence with explicit unavailable capabilities."""

    def __init__(self, config: ConventionalConfig = ConventionalConfig()):
        self.config = config
        self.previous_digest: str | None = None
        self.previous_timestamp_ns: int | None = None
        self.previous_probabilities: Any | None = None

    def reset(self) -> None:
        self.previous_digest = None
        self.previous_timestamp_ns = None
        self.previous_probabilities = None

    def extract(
        self,
        image_path: Path,
        probabilities: Any,
        timestamp_ns: int,
        *,
        occlusion_fraction: float | None = None,
        predicted_horizon_y: float | None = None,
        independent_horizon_y: float | None = None,
    ) -> dict[str, Any]:
        import torch
        from PIL import Image
        from torchvision.transforms.functional import pil_to_tensor

        image_bytes = image_path.read_bytes()
        digest = hashlib.sha256(image_bytes).hexdigest()
        image = pil_to_tensor(Image.open(image_path).convert("RGB")).float().div_(255.0)
        luma = 0.299 * image[0] + 0.587 * image[1] + 0.114 * image[2]
        kernel = torch.tensor([[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]])[None, None]
        laplacian = torch.nn.functional.conv2d(luma[None, None], kernel, padding=1)
        variance = float(laplacian.var(unbiased=False).item())
        blur = 1.0 / (1.0 + variance / self.config.blur_laplacian_scale)
        frozen = 1.0 if self.previous_digest == digest else 0.0
        timestamp_fault = 0.0
        if self.previous_timestamp_ns is not None:
            delta = timestamp_ns - self.previous_timestamp_ns
            timestamp_fault = 1.0 if delta <= 0 or delta > self.config.maximum_frame_gap_ns else 0.0
        temporal_change = None
        if self.previous_probabilities is not None:
            current = probabilities.detach().float().cpu().clamp_min(1e-12)
            previous = self.previous_probabilities.clamp_min(1e-12)
            midpoint = (current + previous) / 2
            divergence = 0.5 * (
                (current * (current / midpoint).log()).sum(1)
                + (previous * (previous / midpoint).log()).sum(1)
            )
            temporal_change = float(divergence.mean().item() / math.log(2))
        horizon_error = None
        if predicted_horizon_y is not None and independent_horizon_y is not None:
            horizon_error = min(
                1.0,
                abs(predicted_horizon_y - independent_horizon_y) / self.config.horizon_error_scale_pixels,
            )
        checks = {
            "underexposure": float((luma < self.config.underexposed_luma).float().mean().item()),
            "overexposure": float((luma > self.config.overexposed_luma).float().mean().item()),
            "blur": blur,
            "occlusion": occlusion_fraction,
            "frozen_frame": frozen,
            "timestamp_fault": timestamp_fault,
            "horizon_error": horizon_error,
            "temporal_output_change": temporal_change,
        }
        self.previous_digest = digest
        self.previous_timestamp_ns = timestamp_ns
        self.previous_probabilities = probabilities.detach().float().cpu()
        return {
            "checks": checks,
            "raw": {"laplacian_variance": variance, "image_sha256": digest},
            "capability": {key: value is not None for key, value in checks.items()},
            "complete": all(value is not None for value in checks.values()),
        }
