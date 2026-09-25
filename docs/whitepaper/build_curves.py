"""Recreate ROC/PR curves from hash-pinned cached evaluations, without inference.

Run from the repository root: .venv/bin/python docs/whitepaper/build_curves.py
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from experiment.evaluation.h_stack_accuracy import ARMS, METHODS, _job_scores, _metrics  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove only interior points on horizontal/vertical straight segments."""
    result: list[tuple[float, float]] = []
    for point in points:
        if result and point == result[-1]:
            continue
        result.append(point)
        while len(result) >= 3:
            a, b, c = result[-3:]
            if a[0] == b[0] == c[0] or a[1] == b[1] == c[1]:
                del result[-2]
            else:
                break
    return result


def main() -> None:
    run_root = REPO.parent / 'horizon-runs'
    archive_path = run_root / 'analysis/h-stack-accuracy-mps-v1.json'
    archive = json.loads(archive_path.read_text())
    references = {}
    for method, record in archive['references'].items():
        path = (REPO / record['path']).resolve()
        assert sha256(path) == record['sha256'], path
        references[method] = json.loads(path.read_text())
    sequences = sorted({key.split('/')[0] for key in archive['inputs_sha256'] if key.startswith('kope75-')})
    assert len(sequences) == 4
    rows = []
    verified = {}
    for sequence in sequences:
        for arm in ARMS:
            job = run_root / 'calibration-acquisition-mps-v1/jobs' / sequence / arm
            for kind, relative in [('features', 'inference/features.jsonl'), ('labels', 'h0-labelled-scores.jsonl')]:
                key = f'{sequence}/{arm}/{kind}'
                digest = sha256(job / relative)
                assert digest == archive['inputs_sha256'][key], key
                verified[key] = digest
            rows.extend(_job_scores(job, references))
    assert len(rows) == 10110
    labels = np.asarray([row['missed_obstacle'] for row in rows], dtype=bool)
    assert int(labels.sum()) == 33
    summary = {
        'source_report_sha256': sha256(archive_path),
        'source_report_artifact_hash': archive['artifact_hash'],
        'evaluation_records': len(rows), 'positives': int(labels.sum()),
        'negatives': int((~labels).sum()), 'prevalence': float(labels.mean()),
        'method': 'Cached activation rescoring; original references and thresholds; no model execution, fitting, or held-out access. Equal scores enter each threshold together. ROC area uses trapezoids; grouped AP uses recall increments times precision. Export removes only collinear axis-aligned points.',
        'scorer_sha256': sha256(REPO / 'experiment/evaluation/h_stack_accuracy.py'),
        'builder_sha256': sha256(Path(__file__)),
        'verified_inputs': verified, 'methods': {},
    }
    output = REPO / 'whitepaper/figures'
    output.mkdir(parents=True, exist_ok=True)
    for method in METHODS:
        scores = np.asarray([row['scores'][method] for row in rows])
        expected = archive['results'][method]
        measured = _metrics(labels, scores, expected['threshold'])
        assert measured['confusion'] == expected['evaluation']['confusion']
        for metric in ['auroc', 'average_precision', 'accuracy', 'recall', 'precision', 'false_positive_rate']:
            assert abs(measured[metric] - expected['evaluation'][metric]) < 1e-12, (method, metric)
        order = np.argsort(-scores, kind='mergesort')
        end = np.r_[np.flatnonzero(np.diff(scores[order])), len(scores) - 1]
        tp = np.cumsum(labels[order])[end]
        fp = (end + 1) - tp
        tpr = tp / int(labels.sum())
        fpr = fp / int((~labels).sum())
        precision = tp / (tp + fp)
        roc = [(0.0, 0.0)] + list(zip(fpr.tolist(), tpr.tolist()))
        pr = [(0.0, 1.0)] + list(zip(tpr.tolist(), precision.tolist()))
        area = float(np.trapezoid([p[1] for p in roc], [p[0] for p in roc]))
        grouped_ap = float(np.sum(np.diff(np.r_[0, tpr]) * precision))
        assert abs(area - measured['auroc']) < 1e-12
        exported = {}
        for name, points in [('roc', roc), ('pr', pr)]:
            path = output / f'{method.lower()}-{name}.csv'
            compressed = compact(points)
            if name == 'roc':
                assert abs(float(np.trapezoid([p[1] for p in compressed], [p[0] for p in compressed])) - area) < 1e-12
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerow(['x', 'y'])
                writer.writerows((format(x,'.17g'), format(y,'.17g')) for x, y in compressed)
            exported[name] = {'points': len(compressed), 'sha256': sha256(path)}
        summary['methods'][method] = {'threshold': expected['threshold'], 'metrics': measured, 'grouped_average_precision': grouped_ap, 'files': exported}
        print(method, 'AUC', round(area,6), 'AP', round(grouped_ap,6), 'archive AP', round(measured['average_precision'],6), flush=True)
    (output / 'curve-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Verified all 48 input hashes, four reference hashes, five AUCs and five confusion matrices.')


if __name__ == '__main__':
    main()
