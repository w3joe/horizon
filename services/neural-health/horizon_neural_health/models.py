"""Vectorized H2-H4 fitting on frozen real feature caches."""

from __future__ import annotations

import math


def _matrix(rows: list[list[float]]):
    import numpy as np

    matrix = np.asarray(rows, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] < 2 or matrix.shape[1] < 1:
        raise ValueError("feature matrix must have at least two rows and one column")
    if not np.isfinite(matrix).all():
        raise ValueError("feature matrix contains nonfinite values")
    return matrix


def _standardize(matrix):
    import numpy as np

    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0, ddof=1)
    constant = scale <= 1e-12
    scale = np.where(constant, 1.0, scale)
    return (matrix - mean) / scale, mean, scale, int(constant.sum())


def _standardized_vector(vector: list[float], parameters: dict):
    import numpy as np

    value = np.asarray(vector, dtype=np.float64)
    mean = np.asarray(parameters["standardization_mean"], dtype=np.float64)
    scale = np.asarray(parameters["standardization_scale"], dtype=np.float64)
    if value.shape != mean.shape or not np.isfinite(value).all():
        raise ValueError("feature vector dimension/finite check failed")
    return (value - mean) / scale


def fit_h2(rows: list[list[float]], regularization: float = 1e-3) -> dict:
    if not math.isfinite(regularization) or regularization <= 0:
        raise ValueError("regularization must be finite and positive")
    matrix = _matrix(rows)
    normalized, mean, scale, constant = _standardize(matrix)
    variance = normalized.var(axis=0, ddof=1)
    precision = 1.0 / (variance + regularization)
    return {
        "standardization_mean": mean.tolist(),
        "standardization_scale": scale.tolist(),
        "precision_diagonal": precision.tolist(),
        "regularization": regularization,
        "covariance": "diagonal_after_nominal_standardization",
        "constant_input_features": constant,
        "fit_samples": int(matrix.shape[0]),
    }


def score_h2(vector: list[float], parameters: dict) -> float:
    import numpy as np

    value = _standardized_vector(vector, parameters)
    precision = np.asarray(parameters["precision_diagonal"], dtype=np.float64)
    return float(np.sqrt(np.sum(value * value * precision)))


def fit_h3(rows: list[list[float]], components: int) -> dict:
    import numpy as np

    matrix = _matrix(rows)
    normalized, mean, scale, constant = _standardize(matrix)
    count = min(max(1, int(components)), normalized.shape[0] - 1, normalized.shape[1])
    _u, singular, vt = np.linalg.svd(normalized, full_matrices=False)
    explained = singular**2
    ratio = explained[:count].sum() / max(explained.sum(), 1e-12)
    return {
        "standardization_mean": mean.tolist(),
        "standardization_scale": scale.tolist(),
        "components": vt[:count].tolist(),
        "rank": count,
        "explained_variance_ratio": float(ratio),
        "constant_input_features": constant,
        "fit_samples": int(matrix.shape[0]),
    }


def score_h3(vector: list[float], parameters: dict) -> float:
    import numpy as np

    value = _standardized_vector(vector, parameters)
    components = np.asarray(parameters["components"], dtype=np.float64)
    reconstruction = (value @ components.T) @ components
    return float(np.mean((value - reconstruction) ** 2))


def fit_h4(
    rows: list[list[float]],
    hidden: int,
    epochs: int = 400,
    learning_rate: float = 0.01,
    l1: float = 1e-3,
    seed: int = 0,
    tolerance: float = 1e-7,
    optimizer: str = "adam",
    patience: int = 25,
) -> dict:
    """Fit a small ReLU SAE with deterministic vectorized full-batch updates.

    Adam is the default because the earlier fixed-step SGD reference continued
    descending at its epoch cap and therefore could not support the H4 claim
    gate.  Full-batch updates preserve deterministic fitting for a frozen cache.
    """
    import numpy as np

    if type(epochs) is not int or epochs < 1:
        raise ValueError("SAE epochs must be a positive integer")
    if not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("SAE learning rate must be finite and positive")
    if not math.isfinite(tolerance) or tolerance < 0 or not math.isfinite(l1) or l1 < 0:
        raise ValueError("SAE tolerance and sparsity weight must be finite and nonnegative")
    if optimizer not in {"adam", "sgd"}:
        raise ValueError("SAE optimizer must be adam or sgd")
    if type(patience) is not int or patience < 1:
        raise ValueError("SAE patience must be a positive integer")
    matrix = _matrix(rows)
    normalized, mean, scale, constant = _standardize(matrix)
    width = normalized.shape[1]
    hidden = min(max(1, int(hidden)), width * 2)
    rng = np.random.default_rng(seed)
    encoder = rng.uniform(-math.sqrt(2 / width), math.sqrt(2 / width), (hidden, width))
    decoder = encoder.T.copy()
    bias = np.zeros(hidden, dtype=np.float64)
    losses: list[float] = []
    converged = False
    adam_m = {
        "encoder": np.zeros_like(encoder),
        "decoder": np.zeros_like(decoder),
        "bias": np.zeros_like(bias),
    }
    adam_v = {key: np.zeros_like(value) for key, value in adam_m.items()}
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    for step in range(1, int(epochs) + 1):
        pre = normalized @ encoder.T + bias
        code = np.maximum(pre, 0.0)
        reconstruction = code @ decoder.T
        error = reconstruction - normalized
        loss = float(np.mean(error**2) + l1 * np.mean(np.abs(code)))
        if not math.isfinite(loss):
            raise ValueError("SAE fitting diverged")
        losses.append(loss)
        gradient_reconstruction = 2 * error / error.size
        gradient_decoder = gradient_reconstruction.T @ code
        gradient_code = gradient_reconstruction @ decoder + l1 / code.size
        gradient_pre = gradient_code * (pre > 0)
        gradient_encoder = gradient_pre.T @ normalized
        gradient_bias = gradient_pre.sum(axis=0)
        gradients = {
            "encoder": gradient_encoder,
            "decoder": gradient_decoder,
            "bias": gradient_bias,
        }
        parameters = {"encoder": encoder, "decoder": decoder, "bias": bias}
        for name, parameter in parameters.items():
            gradient = gradients[name]
            if optimizer == "adam":
                adam_m[name] = beta1 * adam_m[name] + (1 - beta1) * gradient
                adam_v[name] = beta2 * adam_v[name] + (1 - beta2) * gradient**2
                first = adam_m[name] / (1 - beta1**step)
                second = adam_v[name] / (1 - beta2**step)
                parameter -= learning_rate * first / (np.sqrt(second) + epsilon)
            else:
                parameter -= learning_rate * gradient
        if len(losses) > patience:
            window_start = losses[-patience - 1]
            window_best = min(losses[-patience:])
            relative_improvement = max(0.0, window_start - window_best) / max(
                1.0, abs(window_start)
            )
            if relative_improvement <= tolerance:
                converged = True
                break
    final_code = np.maximum(normalized @ encoder.T + bias, 0.0)
    dead = int((np.abs(final_code).max(axis=0) <= 1e-10).sum())
    return {
        "standardization_mean": mean.tolist(),
        "standardization_scale": scale.tolist(),
        "encoder": encoder.tolist(),
        "decoder": decoder.tolist(),
        "bias": bias.tolist(),
        "l1": float(l1),
        "seed": int(seed),
        "optimizer": optimizer,
        "hidden_features": hidden,
        "fit_samples": int(matrix.shape[0]),
        "epochs_completed": len(losses),
        "epochs_requested": epochs,
        "learning_rate": float(learning_rate),
        "convergence_tolerance": float(tolerance),
        "convergence_patience": patience,
        "convergence_rule": "relative_objective_improvement_over_patience_window",
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "converged": converged,
        "dead_features": dead,
        "constant_input_features": constant,
    }


def encode_h4(vector: list[float], parameters: dict) -> list[float]:
    import numpy as np

    value = _standardized_vector(vector, parameters)
    encoder = np.asarray(parameters["encoder"], dtype=np.float64)
    bias = np.asarray(parameters["bias"], dtype=np.float64)
    return np.maximum(encoder @ value + bias, 0.0).tolist()


def score_h4(vector: list[float], parameters: dict) -> float:
    import numpy as np

    value = _standardized_vector(vector, parameters)
    code = np.asarray(encode_h4(vector, parameters), dtype=np.float64)
    decoder = np.asarray(parameters["decoder"], dtype=np.float64)
    reconstruction = decoder @ code
    error = float(np.mean((reconstruction - value) ** 2))
    sparsity = float(np.mean(np.abs(code)))
    return error + float(parameters["l1"]) * sparsity
