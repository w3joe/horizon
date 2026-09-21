"""Load the pinned upstream WaSR models without downloading backbone weights."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import hashlib
import importlib
import sys
import types
from typing import Iterator


@dataclass(frozen=True)
class ModelSpec:
    family: str
    source_dir: Path
    source_commit: str
    weights: Path
    weights_sha256: str
    architecture: str
    hist_len: int = 5

    def verify(self) -> None:
        if self.family not in {"wasr", "wasr_t"}:
            raise ValueError(f"unsupported model family: {self.family}")
        actual = sha256_file(self.weights)
        if actual != self.weights_sha256:
            raise ValueError(f"checkpoint hash mismatch: expected {self.weights_sha256}, got {actual}")
        git_dir = self.source_dir / ".git"
        marker = self.source_dir / ".horizon-source-commit"
        if git_dir.exists():
            head = (git_dir / "HEAD").read_text().strip()
            if head.startswith("ref: "):
                ref = head[5:]
                ref_path = git_dir / ref
                if ref_path.exists():
                    head = ref_path.read_text().strip()
                else:
                    packed = (git_dir / "packed-refs").read_text().splitlines()
                    head = next(line.split()[0] for line in packed if line.endswith(f" {ref}"))
        elif marker.exists():
            head = marker.read_text().strip()
        else:
            raise ValueError("source checkout has neither Git metadata nor a pinned commit marker")
        if head != self.source_commit:
            raise ValueError(f"source commit mismatch: expected {self.source_commit}, got {head}")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _upstream_import_path(path: Path) -> Iterator[None]:
    original = list(sys.path)
    # Upstream utils imports Lightning only to define training helpers. Sequential
    # inference does not use them; this compatibility shim avoids adding that old
    # training dependency to the inference image.
    installed_stub = "pytorch_lightning" not in sys.modules
    if installed_stub:
        lightning = types.ModuleType("pytorch_lightning")
        # Single-frame WaSR defines a training-only exporter during import.
        lightning.Callback = type("Callback", (), {})
        loggers = types.ModuleType("pytorch_lightning.loggers")
        loggers.LoggerCollection = type("LoggerCollection", (), {})
        # The pinned utility module evaluates this training-only return
        # annotation while importing the inference architecture.
        loggers.LightningLoggerBase = type("LightningLoggerBase", (), {})
        lightning.loggers = loggers
        sys.modules["pytorch_lightning"] = lightning
        sys.modules["pytorch_lightning.loggers"] = loggers
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        sys.path[:] = original
        if installed_stub:
            sys.modules.pop("pytorch_lightning.loggers", None)
            sys.modules.pop("pytorch_lightning", None)


def _checkpoint_state(torch_module: object, path: Path) -> dict:
    payload = torch_module.load(path, map_location="cpu", weights_only=False)
    return payload["model"] if isinstance(payload, dict) and "model" in payload else payload


def load_official_model(spec: ModelSpec, device: str = "cuda", fp16: bool = False):
    """Construct an official architecture and load the complete verified checkpoint.

    Both pinned repositories call ``resnet101(pretrained=True)`` even when the
    outer factory is passed ``pretrained=False``. We temporarily replace that
    symbol during construction so a complete checkpoint never causes a second,
    unpinned internet download.
    """

    spec.verify()
    import torch
    from torchvision.models import resnet101

    with _upstream_import_path(spec.source_dir):
        if spec.family == "wasr_t":
            module = importlib.import_module("wasr_t.wasr_t")
            factory = module.wasr_temporal_resnet101
            kwargs = {"pretrained": False, "hist_len": spec.hist_len, "sequential": True}
        else:
            module = importlib.import_module("wasr.models")
            factory = module.get_model
            kwargs = {"model_name": spec.architecture, "pretrained": False}

        original = module.resnet101
        module.resnet101 = lambda *args, **kwargs: resnet101(
            weights=None, replace_stride_with_dilation=kwargs.get("replace_stride_with_dilation")
        )
        try:
            model = factory(**kwargs)
        finally:
            module.resnet101 = original

    state = _checkpoint_state(torch, spec.weights)
    model.load_state_dict(state, strict=True)
    model.eval().to(device)
    if fp16:
        model.half()
    if spec.family == "wasr_t":
        model.sequential()
        model.clear_state()
    return model
