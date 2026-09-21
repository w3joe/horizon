"""Fit and score H2-H4 reference models on cached real features."""

from __future__ import annotations

import math
import random

from .linalg import covariance, jacobi_eigen, mean_vector, squared_reconstruction_error


def fit_h2(rows: list[list[float]], regularization: float = 1e-3) -> dict:
    center = mean_vector(rows)
    denom = max(1, len(rows) - 1)
    variances = [sum((row[i] - center[i]) ** 2 for row in rows) / denom for i in range(len(center))]
    precision_diagonal = [1.0 / (value + regularization) for value in variances]
    return {"mean": center, "precision_diagonal": precision_diagonal, "regularization": regularization, "covariance": "diagonal"}


def score_h2(vector: list[float], parameters: dict) -> float:
    return math.sqrt(sum(
        (value - parameters["mean"][i]) ** 2 * parameters["precision_diagonal"][i]
        for i, value in enumerate(vector)
    ))


def fit_h3(rows: list[list[float]], components: int) -> dict:
    center = mean_vector(rows)
    cov = covariance(rows, center, 1e-9)
    eigens = jacobi_eigen(cov)
    count = min(max(1, components), len(center))
    return {"mean": center, "components": [vector for _value, vector in eigens[:count]], "rank": count}


def score_h3(vector: list[float], parameters: dict) -> float:
    return squared_reconstruction_error(vector, parameters["mean"], parameters["components"])


def fit_h4(
    rows: list[list[float]], hidden: int, epochs: int = 80, learning_rate: float = 0.01, l1: float = 1e-3, seed: int = 0
) -> dict:
    """Fit a small ReLU sparse autoencoder with deterministic full-batch SGD."""
    center = mean_vector(rows)
    normalized = [[value - center[i] for i, value in enumerate(row)] for row in rows]
    width = len(center)
    hidden = min(max(1, hidden), width * 2)
    rng = random.Random(seed)
    scale = math.sqrt(2 / max(1, width))
    encoder = [[rng.uniform(-scale, scale) for _ in range(width)] for _ in range(hidden)]
    decoder = [[encoder[j][i] for j in range(hidden)] for i in range(width)]
    bias = [0.0] * hidden
    for _ in range(epochs):
        ge = [[0.0] * width for _ in range(hidden)]
        gd = [[0.0] * hidden for _ in range(width)]
        gb = [0.0] * hidden
        for row in normalized:
            pre = [sum(w * x for w, x in zip(weights, row)) + bias[j] for j, weights in enumerate(encoder)]
            code = [max(0.0, value) for value in pre]
            recon = [sum(decoder[i][j] * code[j] for j in range(hidden)) for i in range(width)]
            error = [recon[i] - row[i] for i in range(width)]
            for i in range(width):
                for j in range(hidden):
                    gd[i][j] += 2 * error[i] * code[j] / width
            for j in range(hidden):
                if pre[j] <= 0:
                    continue
                upstream = sum(2 * error[i] * decoder[i][j] / width for i in range(width)) + l1
                gb[j] += upstream
                for i in range(width):
                    ge[j][i] += upstream * row[i]
        rate = learning_rate / len(rows)
        encoder = [[w - rate * ge[j][i] for i, w in enumerate(weights)] for j, weights in enumerate(encoder)]
        decoder = [[w - rate * gd[i][j] for j, w in enumerate(weights)] for i, weights in enumerate(decoder)]
        bias = [value - rate * gb[j] for j, value in enumerate(bias)]
    return {"mean": center, "encoder": encoder, "decoder": decoder, "bias": bias, "l1": l1, "seed": seed}


def encode_h4(vector: list[float], parameters: dict) -> list[float]:
    centered = [value - parameters["mean"][i] for i, value in enumerate(vector)]
    return [
        max(0.0, sum(w * x for w, x in zip(weights, centered)) + parameters["bias"][j])
        for j, weights in enumerate(parameters["encoder"])
    ]


def score_h4(vector: list[float], parameters: dict) -> float:
    centered = [value - parameters["mean"][i] for i, value in enumerate(vector)]
    code = encode_h4(vector, parameters)
    recon = [sum(parameters["decoder"][i][j] * code[j] for j in range(len(code))) for i in range(len(vector))]
    reconstruction = sum((recon[i] - centered[i]) ** 2 for i in range(len(vector))) / len(vector)
    sparsity = sum(abs(value) for value in code) / len(code)
    return reconstruction + float(parameters["l1"]) * sparsity
