"""Instrumented WaSR and WaSR-T perception adapters."""

from .instrumentation import ActivationRecorder, TensorSummary
from .model import ModelSpec, load_official_model
from .runner import SequentialPerceptionRunner

__all__ = [
    "ActivationRecorder",
    "ModelSpec",
    "SequentialPerceptionRunner",
    "TensorSummary",
    "load_official_model",
]
