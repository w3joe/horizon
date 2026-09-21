"""Non-mutating hooks for a fixed, documented set of WaSR tensors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter_ns
from typing import Any


@dataclass(frozen=True)
class TensorSummary:
    name: str
    shape: tuple[int, ...]
    dtype: str
    finite: bool
    minimum: float
    maximum: float
    mean: float
    standard_deviation: float
    pooled_mean: tuple[float, ...]
    pooled_standard_deviation: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["shape"] = list(self.shape)
        value["pooled_mean"] = list(self.pooled_mean)
        value["pooled_standard_deviation"] = list(self.pooled_standard_deviation)
        return value


class ActivationRecorder:
    """Capture detached summaries; hook callbacks always return ``None``."""

    def __init__(self, model: Any, family: str):
        self.model = model
        self.family = family
        self.records: dict[str, TensorSummary] = {}
        self.hooks: list[Any] = []
        self.capture_ns = 0

    def _modules(self) -> dict[str, Any]:
        modules = {
            "encoder": self.model.backbone["layer4"],
            "decoder_logits": self.model.decoder.aspp,
        }
        if self.family == "wasr_t":
            modules["temporal_fusion"] = self.model.decoder.tcm
        return modules

    def install(self) -> "ActivationRecorder":
        if self.hooks:
            raise RuntimeError("activation hooks already installed")
        for name, module in self._modules().items():
            self.hooks.append(module.register_forward_hook(self._hook(name)))
        return self

    def _hook(self, name: str):
        def capture(_module: Any, _inputs: Any, output: Any) -> None:
            started = perf_counter_ns()
            self.records[name] = summarize_tensor(name, output)
            self.capture_ns += perf_counter_ns() - started
            return None

        return capture

    def clear(self) -> None:
        self.records.clear()
        self.capture_ns = 0

    def remove(self) -> None:
        for hook in self.hooks:
            hook.remove()
        self.hooks.clear()

    def __enter__(self) -> "ActivationRecorder":
        return self.install()

    def __exit__(self, *_args: object) -> None:
        self.remove()


def summarize_tensor(name: str, value: Any) -> TensorSummary:
    import torch

    if not torch.is_tensor(value):
        raise TypeError(f"hook {name} produced {type(value).__name__}, expected Tensor")
    tensor = value.detach().float()
    finite = bool(torch.isfinite(tensor).all().item())
    spatial_dims = tuple(range(2, tensor.ndim))
    if spatial_dims:
        mean = tensor.mean(dim=spatial_dims).flatten()
        std = tensor.std(dim=spatial_dims, unbiased=False).flatten()
    else:
        mean = tensor.flatten()
        std = torch.zeros_like(mean)
    return TensorSummary(
        name=name,
        shape=tuple(tensor.shape),
        dtype=str(value.dtype),
        finite=finite,
        minimum=float(tensor.min().item()),
        maximum=float(tensor.max().item()),
        mean=float(tensor.mean().item()),
        standard_deviation=float(tensor.std(unbiased=False).item()),
        pooled_mean=tuple(float(x) for x in mean.cpu().tolist()),
        pooled_standard_deviation=tuple(float(x) for x in std.cpu().tolist()),
    )
