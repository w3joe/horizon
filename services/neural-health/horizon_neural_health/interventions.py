"""Offline matched activation interventions with equal-norm controls."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import random
from typing import Any, Callable


@dataclass(frozen=True)
class InterventionResult:
    pair_id: str
    layer: str
    feature_index: int
    intervention: str
    target_delta: float
    random_control_delta: float
    equal_norm_control_delta: float
    perturbation_norm: float
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_controlled_intervention(
    diagnostic_model: Any,
    layer: Any,
    matched_source: dict[str, Any],
    matched_target: dict[str, Any],
    feature_index: int,
    output_metric: Callable[[Any], Any],
    pair_id: str,
    seed: int = 0,
    offline: bool = False,
) -> InterventionResult:
    """Mask one channel and compare random-channel/equal-norm controls.

    The caller must provide a separate diagnostic model copy and explicitly set
    ``offline=True``. This function is intentionally absent from runtime monitor
    entrypoints.
    """
    if not offline:
        raise ValueError("activation interventions require explicit offline=True")
    import torch

    rng = random.Random(seed)
    with torch.inference_mode():
        baseline = output_metric(diagnostic_model(matched_target)).detach().float()
        captured: dict[str, Any] = {}

        def remember(_module, _inputs, output):
            captured["source"] = output.detach().clone()

        handle = layer.register_forward_hook(remember)
        diagnostic_model(matched_source)
        handle.remove()
        source = captured["source"]
        channels = source.shape[1]
        if not 0 <= feature_index < channels:
            raise IndexError("feature index outside layer channels")
        random_index = rng.choice([index for index in range(channels) if index != feature_index])

        def execute(kind: str, index: int):
            state: dict[str, float] = {}

            def edit(_module, _inputs, output):
                modified = output.clone()
                before = modified[:, index].clone()
                if kind == "patch":
                    replacement = source[:, index].to(modified.device)
                    modified[:, index] = replacement
                else:
                    modified[:, index] = 0
                state["norm"] = float((modified[:, index] - before).norm().item())
                return modified

            hook = layer.register_forward_hook(edit)
            value = output_metric(diagnostic_model(matched_target)).detach().float()
            hook.remove()
            return float((value - baseline).abs().mean().item()), state["norm"]

        target_delta, norm = execute("patch", feature_index)
        random_delta, _ = execute("patch", random_index)

        # Equal-norm Gaussian perturbation controls magnitude without selecting
        # the hypothesized source feature direction.
        def equal_norm(_module, _inputs, output):
            generator = torch.Generator(device=output.device).manual_seed(seed)
            noise = torch.randn(output.shape, generator=generator, device=output.device, dtype=output.dtype)
            noise = noise / noise.norm().clamp_min(1e-12) * norm
            return output + noise

        hook = layer.register_forward_hook(equal_norm)
        equal_value = output_metric(diagnostic_model(matched_target)).detach().float()
        hook.remove()
        equal_delta = float((equal_value - baseline).abs().mean().item())
    return InterventionResult(pair_id, layer.__class__.__name__, feature_index, "matched_patch", target_delta, random_delta, equal_delta, norm, seed)
