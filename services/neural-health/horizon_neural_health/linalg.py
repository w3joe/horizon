"""Small deterministic linear algebra routines for health artifacts."""

from __future__ import annotations

import math


def mean_vector(rows: list[list[float]]) -> list[float]:
    if not rows:
        raise ValueError("at least one row is required")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("feature rows must have one non-zero dimension")
    return [sum(row[i] for row in rows) / len(rows) for i in range(width)]


def covariance(rows: list[list[float]], mean: list[float], regularization: float) -> list[list[float]]:
    if regularization <= 0:
        raise ValueError("regularization must be positive")
    denom = max(1, len(rows) - 1)
    size = len(mean)
    return [
        [
            sum((row[i] - mean[i]) * (row[j] - mean[j]) for row in rows) / denom
            + (regularization if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]


def inverse(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    if not size or any(len(row) != size for row in matrix):
        raise ValueError("matrix must be non-empty and square")
    work = [row[:] + [1.0 if i == j else 0.0 for j in range(size)] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) < 1e-12:
            raise ValueError("matrix is singular")
        work[column], work[pivot] = work[pivot], work[column]
        scale = work[column][column]
        work[column] = [value / scale for value in work[column]]
        for row in range(size):
            if row == column:
                continue
            factor = work[row][column]
            work[row] = [a - factor * b for a, b in zip(work[row], work[column])]
    return [row[size:] for row in work]


def mahalanobis(vector: list[float], mean: list[float], precision: list[list[float]]) -> float:
    delta = [value - center for value, center in zip(vector, mean)]
    projected = [sum(row[j] * delta[j] for j in range(len(delta))) for row in precision]
    squared = sum(a * b for a, b in zip(delta, projected))
    return math.sqrt(max(0.0, squared))


def jacobi_eigen(matrix: list[list[float]], tolerance: float = 1e-10, iterations: int = 200):
    size = len(matrix)
    work = [row[:] for row in matrix]
    vectors = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    for _ in range(iterations):
        p, q = max(((i, j) for i in range(size) for j in range(i + 1, size)), key=lambda ij: abs(work[ij[0]][ij[1]]), default=(0, 0))
        if p == q or abs(work[p][q]) < tolerance:
            break
        angle = 0.5 * math.atan2(2 * work[p][q], work[q][q] - work[p][p])
        c, s = math.cos(angle), math.sin(angle)
        for i in range(size):
            aip, aiq = work[i][p], work[i][q]
            work[i][p], work[i][q] = c * aip - s * aiq, s * aip + c * aiq
        for j in range(size):
            apj, aqj = work[p][j], work[q][j]
            work[p][j], work[q][j] = c * apj - s * aqj, s * apj + c * aqj
        for i in range(size):
            vip, viq = vectors[i][p], vectors[i][q]
            vectors[i][p], vectors[i][q] = c * vip - s * viq, s * vip + c * viq
    pairs = sorted(((work[i][i], [vectors[j][i] for j in range(size)]) for i in range(size)), reverse=True)
    return pairs


def squared_reconstruction_error(vector: list[float], mean: list[float], components: list[list[float]]) -> float:
    centered = [a - b for a, b in zip(vector, mean)]
    reconstruction = [0.0] * len(vector)
    for component in components:
        coefficient = sum(a * b for a, b in zip(centered, component))
        reconstruction = [a + coefficient * b for a, b in zip(reconstruction, component)]
    return sum((a - b) ** 2 for a, b in zip(centered, reconstruction)) / len(vector)
