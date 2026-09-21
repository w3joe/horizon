"""Offline matched interventions with identical temporal replay per arm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Any, Callable, Sequence


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
    context_frames: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reset(model: Any) -> None:
    clear = getattr(model, "clear_state", None)
    if callable(clear):
        clear()


def _replay(model: Any, context: Sequence[dict[str, Any]], hook_factory=None):
    if not context:
        raise ValueError("a non-empty temporal context is required")
    _reset(model)
    handle = hook_factory(len(context)) if hook_factory else None
    try:
        output = None
        for frame in context:
            output = model(frame)
        return output
    finally:
        if handle is not None:
            handle.remove()


def run_controlled_intervention(
    diagnostic_model: Any,
    layer: Any,
    matched_source_context: Sequence[dict[str, Any]],
    matched_target_context: Sequence[dict[str, Any]],
    feature_index: int,
    output_metric: Callable[[Any], Any],
    pair_id: str,
    seed: int = 0,
    offline: bool = False,
) -> InterventionResult:
    """Patch one raw CNN channel with temporal history held identical per arm."""
    if not offline:
        raise ValueError("activation interventions require explicit offline=True")
    if len(matched_source_context) != len(matched_target_context):
        raise ValueError("matched contexts must contain the same number of frames")
    import torch

    rng = random.Random(seed)
    with torch.inference_mode():
        source: dict[str, Any] = {}
        target: dict[str, Any] = {}

        def capture_factory(store):
            def factory(count):
                calls = {"remaining": count}

                def capture(_module, _inputs, output):
                    calls["remaining"] -= 1
                    if calls["remaining"] == 0:
                        store["activation"] = output.detach().clone()
                    return None

                return layer.register_forward_hook(capture)

            return factory

        _replay(diagnostic_model, matched_source_context, capture_factory(source))
        baseline_output = _replay(diagnostic_model, matched_target_context, capture_factory(target))
        baseline = output_metric(baseline_output).detach().float()
        source_activation = source["activation"]
        channels = source_activation.shape[1]
        if not 0 <= feature_index < channels:
            raise IndexError("feature index outside layer channels")
        random_index = rng.choice([index for index in range(channels) if index != feature_index])

        def patch_factory(index: int):
            state: dict[str, float] = {}

            def factory(count):
                calls = {"remaining": count}

                def edit(_module, _inputs, output):
                    calls["remaining"] -= 1
                    if calls["remaining"] != 0:
                        return None
                    modified = output.clone()
                    before = modified[:, index].clone()
                    modified[:, index] = source_activation[:, index].to(modified.device)
                    state["norm"] = float((modified[:, index] - before).norm().item())
                    return modified

                return layer.register_forward_hook(edit)

            return factory, state

        target_factory, target_state = patch_factory(feature_index)
        target_value = output_metric(_replay(diagnostic_model, matched_target_context, target_factory)).detach().float()
        random_factory, _random_state = patch_factory(random_index)
        random_value = output_metric(_replay(diagnostic_model, matched_target_context, random_factory)).detach().float()
        norm = target_state["norm"]

        def equal_norm_factory(count):
            calls = {"remaining": count}

            def edit(_module, _inputs, output):
                calls["remaining"] -= 1
                if calls["remaining"] != 0:
                    return None
                generator = torch.Generator(device=output.device).manual_seed(seed)
                noise = torch.randn(output.shape, generator=generator, device=output.device, dtype=output.dtype)
                return output + noise / noise.norm().clamp_min(1e-12) * norm

            return layer.register_forward_hook(edit)

        equal_value = output_metric(_replay(diagnostic_model, matched_target_context, equal_norm_factory)).detach().float()
    return InterventionResult(
        pair_id=pair_id,
        layer=layer.__class__.__name__,
        feature_index=feature_index,
        intervention="matched_raw_channel_patch",
        target_delta=float((target_value - baseline).abs().mean().item()),
        random_control_delta=float((random_value - baseline).abs().mean().item()),
        equal_norm_control_delta=float((equal_value - baseline).abs().mean().item()),
        perturbation_norm=norm,
        context_frames=len(matched_target_context),
        seed=seed,
    )


def run_sae_direction_intervention(
    diagnostic_model: Any,
    layer: Any,
    target_context: Sequence[dict[str, Any]],
    decoded_direction: Sequence[float],
    feature_index: int,
    coefficient: float,
    output_metric: Callable[[Any], Any],
    pair_id: str,
    seed: int = 0,
    offline: bool = False,
) -> InterventionResult:
    """Perturb a real activation along one fitted SAE decoder direction."""
    if not offline:
        raise ValueError("SAE interventions require explicit offline=True")
    import torch

    with torch.inference_mode():
        baseline = output_metric(_replay(diagnostic_model, target_context)).detach().float()
        direction = torch.tensor(list(decoded_direction), dtype=torch.float32)
        if not bool(torch.isfinite(direction).all()) or float(direction.norm().item()) == 0:
            raise ValueError("decoded direction must be finite and nonzero")
        direction = direction / direction.norm()
        rng = random.Random(seed)
        random_direction = torch.tensor(
            [rng.gauss(0.0, 1.0) for _ in range(direction.numel())], dtype=torch.float32
        )
        random_direction = random_direction / random_direction.norm()
        perturbation_norm = abs(float(coefficient))

        def direction_factory(selected):
            def factory(count):
                calls = {"remaining": count}

                def edit(_module, _inputs, output):
                    calls["remaining"] -= 1
                    if calls["remaining"] != 0:
                        return None
                    if output.ndim != 4 or output.shape[1] != selected.numel():
                        raise ValueError("decoded SAE direction does not match layer channels")
                    delta = selected.to(output.device, output.dtype)[None, :, None, None]
                    delta = delta.expand_as(output)
                    delta = delta / delta.norm().clamp_min(1e-12) * perturbation_norm
                    return output + delta

                return layer.register_forward_hook(edit)

            return factory

        selected = output_metric(
            _replay(diagnostic_model, target_context, direction_factory(direction))
        ).detach().float()
        random_value = output_metric(
            _replay(diagnostic_model, target_context, direction_factory(random_direction))
        ).detach().float()

        def equal_factory(count):
            calls = {"remaining": count}

            def edit(_module, _inputs, output):
                calls["remaining"] -= 1
                if calls["remaining"] != 0:
                    return None
                generator = torch.Generator(device=output.device).manual_seed(seed + 1)
                noise = torch.randn(output.shape, generator=generator, device=output.device, dtype=output.dtype)
                return output + noise / noise.norm().clamp_min(1e-12) * perturbation_norm

            return layer.register_forward_hook(edit)

        equal_value = output_metric(_replay(diagnostic_model, target_context, equal_factory)).detach().float()
    return InterventionResult(
        pair_id=pair_id,
        layer=layer.__class__.__name__,
        feature_index=feature_index,
        intervention="sae_decoded_direction",
        target_delta=float((selected - baseline).abs().mean().item()),
        random_control_delta=float((random_value - baseline).abs().mean().item()),
        equal_norm_control_delta=float((equal_value - baseline).abs().mean().item()),
        perturbation_norm=perturbation_norm,
        context_frames=len(target_context),
        seed=seed,
    )
