class ExperimentError(RuntimeError):
    """Base class for protocol and execution failures."""


class ManifestError(ExperimentError):
    """Raised when a manifest would make a run ambiguous or invalid."""


class MissingImplementationError(ExperimentError):
    """Raised when a requested research candidate is not implemented."""
