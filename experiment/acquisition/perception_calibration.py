"""Resumable MODD2 perception-health calibration acquisition.

Only the frozen calibration sequences in ``perception-stage2.json`` are
accepted.  This acquisition layer creates deterministic pixel stress arms and
records H0 output evidence.  H1 remains unavailable unless an independent
horizon and an occlusion measurement are actually supplied by an upstream
extractor.  H2--H4 are intentionally outside this acquisition step.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import random
from typing import Any, Callable


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _stable_seed(seed: int, frame_name: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{frame_name}".encode()).digest()[:8], "big")


@dataclass(frozen=True)
class AcquisitionJob:
    sequence_id: str
    arm_id: str
    kind: str
    perturbation: dict[str, Any] | None
    expected_frame_count: int
    source_frames_sha256: str

    @property
    def key(self) -> str:
        return f"{self.sequence_id}/{self.arm_id}"


def build_plan(config_path: str | Path, frame_root: str | Path) -> dict[str, Any]:
    """Hash and validate every predeclared calibration left-camera frame."""
    config_path, frame_root = Path(config_path), Path(frame_root)
    config = json.loads(config_path.read_text())
    sequences = config["calibration_sequences"]
    expected_counts = config["calibration_sequence_frame_counts"]
    _require(set(sequences) == set(expected_counts), "calibration sequence/count mismatch")
    arms = [{"id": "nominal", "kind": "nominal", "perturbation": None}]
    arms.extend(
        {"id": item["id"], "kind": "controlled", "perturbation": item}
        for item in config["controlled_perturbations"]
    )
    jobs: list[dict[str, Any]] = []
    for sequence_id in sequences:
        frames = sorted((frame_root / sequence_id / "frames").glob("*L.jpg"))
        _require(len(frames) == int(expected_counts[sequence_id]), f"{sequence_id} frame count differs from frozen config")
        sources = [{"name": frame.name, "sha256": _file_hash(frame)} for frame in frames]
        source_hash = _canonical_hash(sources)
        for arm in arms:
            jobs.append(
                {
                    "sequence_id": sequence_id,
                    "arm_id": arm["id"],
                    "kind": arm["kind"],
                    "perturbation": arm["perturbation"],
                    "expected_frame_count": len(frames),
                    "source_frames_sha256": source_hash,
                }
            )
    body = {
        "schema_version": "horizon.perception-calibration-acquisition-plan.v1",
        "partition": "calibration",
        "config_sha256": _file_hash(config_path),
        "split_manifest_sha256": config["split_manifest_sha256"],
        "dataset": config["dataset"],
        "frame_glob": "*L.jpg",
        "sequence_count": len(sequences),
        "expected_frame_count": sum(int(expected_counts[item]) for item in sequences),
        "controlled_perturbations": config["controlled_perturbations"],
        "perturbations_sha256": _canonical_hash(config["controlled_perturbations"]),
        "jobs": jobs,
    }
    return {**body, "plan_sha256": _canonical_hash(body)}


def _transform(image, arm: dict[str, Any] | None, previous):
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    if arm is None:
        return image.copy()
    arm_id, parameters = arm["id"], arm["parameters"]
    if arm_id == "blur-v1":
        return image.filter(ImageFilter.GaussianBlur(radius=float(parameters["gaussian_sigma_px"])))
    if arm_id == "underexposure-v1":
        return ImageEnhance.Brightness(image).enhance(float(parameters["linear_gain"]))
    if arm_id == "occlusion-v1":
        fraction = float(parameters["area_fraction"])
        width, height = image.size
        area = max(1, int(width * height * fraction))
        box_width = max(1, min(width, int(area ** 0.5)))
        box_height = max(1, min(height, (area + box_width - 1) // box_width))
        rng = random.Random(_stable_seed(int(arm["seed"]), image.info.get("horizon_frame_name", "")))
        x = rng.randrange(0, width - box_width + 1)
        y = rng.randrange(0, height - box_height + 1)
        result = image.copy()
        ImageDraw.Draw(result).rectangle((x, y, x + box_width - 1, y + box_height - 1), fill=(0, 0, 0))
        return result
    if arm_id == "jpeg-v1":
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=int(parameters["quality"]), optimize=False, progressive=False, subsampling=0)
        with Image.open(io.BytesIO(buffer.getvalue())) as encoded:
            return encoded.convert("RGB").copy()
    if arm_id == "temporal-drop-v1":
        _require(previous is not None, "temporal-drop arm requires a prior frame")
        return previous.copy()
    raise ValueError(f"unsupported frozen perturbation {arm_id}")


def materialize_job(
    plan: dict[str, Any],
    job: dict[str, Any],
    frame_root: str | Path,
    destination: str | Path,
    *,
    max_frames: int | None = None,
) -> dict[str, Any]:
    """Materialize one arm deterministically; existing matching frames are resumed."""
    from PIL import Image

    frame_root, destination = Path(frame_root), Path(destination)
    sequence_dir = frame_root / job["sequence_id"] / "frames"
    sources = sorted(sequence_dir.glob("*L.jpg"))
    _require(len(sources) == int(job["expected_frame_count"]), "source frame count changed after plan")
    selected = sources if max_frames is None else sources[:max_frames]
    _require(bool(selected), "max_frames must select at least one frame")
    destination.mkdir(parents=True, exist_ok=True)
    metadata_path = destination / "materialization.json"
    expected_metadata = {
        "schema_version": "horizon.perception-calibration-materialization.v1",
        "plan_sha256": plan["plan_sha256"],
        "job": job,
        "selected_frame_count": len(selected),
        "partial": len(selected) != len(sources),
    }
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text())
        same_identity = all(existing.get(key) == expected_metadata[key] for key in ("schema_version", "plan_sha256", "job"))
        _require(same_identity, "resume destination has incompatible materialization metadata")
        previous_count = int(existing.get("selected_frame_count", -1))
        _require(previous_count <= len(selected), "resume cannot shrink a materialized arm")
        if existing != expected_metadata:
            _write_json(metadata_path, expected_metadata)
    else:
        _write_json(metadata_path, expected_metadata)
    previous = None
    generated = []
    for index, source in enumerate(selected):
        target = destination / source.name
        if target.exists():
            generated.append({"frame_id": source.name, "source_sha256": _file_hash(source), "sha256": _file_hash(target), "resumed": True})
            with Image.open(target) as image:
                previous = image.convert("RGB").copy()
            continue
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        image.info["horizon_frame_name"] = source.name
        arm = job["perturbation"]
        if arm and arm["id"] == "temporal-drop-v1":
            interval = int(arm["parameters"]["drop_every_n"])
            result = _transform(image, arm, previous) if index > 0 and index % interval == 0 else image.copy()
        else:
            result = _transform(image, arm, previous)
        temporary = target.with_suffix(".tmp.jpg")
        result.save(temporary, format="JPEG", quality=95, optimize=False, progressive=False, subsampling=0)
        temporary.replace(target)
        previous = result.copy()
        generated.append({"frame_id": source.name, "source_sha256": _file_hash(source), "sha256": _file_hash(target), "resumed": False})
    hash_rows = [{key: row[key] for key in ("frame_id", "source_sha256", "sha256")} for row in generated]
    completed = {**expected_metadata, "frames": generated, "frames_sha256": _canonical_hash(hash_rows), "completed": True}
    _write_json(destination / "completed.json", completed)
    return completed


def _h0_rows(features_path: Path, job: dict[str, Any], materialized: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in features_path.read_text().splitlines() if line.strip()]
    condition = {"kind": job["kind"], "perturbation_id": None, "seed": None, "parameters_hash": None}
    if job["perturbation"] is not None:
        condition = {
            "kind": "controlled",
            "perturbation_id": job["perturbation"]["id"],
            "seed": job["perturbation"]["seed"],
            "parameters_hash": _canonical_hash(job["perturbation"]["parameters"]),
        }
    materialized_hashes = {row["frame_id"]: row for row in materialized["frames"]}
    return [
        {
            "method_id": "H0",
            "sequence_id": job["sequence_id"],
            "base_sample_id": row["frame_id"],
            "frame_id": row["frame_id"],
            "source_frame_sha256": materialized_hashes[row["frame_id"]]["source_sha256"],
            "transformed_frame_sha256": materialized_hashes[row["frame_id"]]["sha256"],
            "condition": condition,
            "score": row["output_health"]["entropy_p95"],
            "h1_status": "blocked",
            "h1_reason": "independent_horizon_and_occlusion_unavailable",
            "roi_capability": row["output_health"]["roi_capability"],
        }
        for row in rows
    ]


def join_h0_labels(job_root: str | Path, annotations_dir: str | Path, label_policy: dict[str, Any]) -> dict[str, Any]:
    """Join RAW MODD2 labels to H0 rows, or record the missing optional parser.

    SciPy is deliberately optional: the repository does not gain a root runtime
    dependency merely to produce a calibration label pass.  A missing parser is
    an explicit blocker, never an empty label denominator.
    """
    job_root, annotations_dir = Path(job_root), Path(annotations_dir)
    status_path = job_root / "label-join-status.json"
    try:
        import numpy as np
        from PIL import Image
        from scipy.io import loadmat  # noqa: F401 -- validates the optional MAT parser dependency
        from horizon_perception.modd2 import load_raw_annotation
    except ModuleNotFoundError as exc:
        status = {"status": "blocked_missing_optional_dependency", "dependency": exc.name, "labels_written": 0}
        _write_json(status_path, status)
        return status
    h0_rows = [json.loads(line) for line in (job_root / "h0-scores.jsonl").read_text().splitlines() if line.strip()]
    features = {
        row["frame_id"]: row
        for row in (json.loads(line) for line in (job_root / "inference" / "features.jsonl").read_text().splitlines() if line.strip())
    }
    masks_dir = job_root / "inference" / "class_masks"
    policy_hash = _canonical_hash(label_policy)
    labels = []
    for row in h0_rows:
        frame_id = row["frame_id"]
        feature = features.get(frame_id)
        _require(feature is not None, "label join feature lineage mismatch")
        width, height = feature["source_image_size"]
        annotation_path = annotations_dir / f"{Path(frame_id).stem}.mat"
        _require(annotation_path.exists(), f"annotation missing for {frame_id}")
        annotation = load_raw_annotation(annotation_path, width, height)
        with Image.open(masks_dir / f"{Path(frame_id).stem}.png") as image:
            mask = np.asarray(image)
        counts = []
        for x0, y0, x1, y1 in annotation.obstacle_xyxy_zero_based_inclusive:
            sx0 = max(0, min(mask.shape[1] - 1, int(x0 * mask.shape[1] // width)))
            sy0 = max(0, min(mask.shape[0] - 1, int(y0 * mask.shape[0] // height)))
            sx1 = max(sx0, min(mask.shape[1] - 1, int(((x1 + 1) * mask.shape[1] + width - 1) // width) - 1))
            sy1 = max(sy0, min(mask.shape[0] - 1, int(((y1 + 1) * mask.shape[0] + height - 1) // height) - 1))
            counts.append(int((mask[sy0 : sy1 + 1, sx0 : sx1 + 1] == 0).sum()))
        labels.append({
            **row,
            "label": {
                "policy_hash": policy_hash,
                "annotation_sha256": _file_hash(annotation_path),
                "annotated_obstacle_count": len(counts),
                "per_object_obstacle_pixel_count": counts,
                "missed_obstacle": bool(counts) and any(count == 0 for count in counts),
                "evaluable": True,
            },
        })
    with (job_root / "h0-labelled-scores.jsonl").open("w") as stream:
        for row in labels:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    status = {"status": "complete", "labels_written": len(labels), "label_policy_sha256": policy_hash, "labels_sha256": _file_hash(job_root / "h0-labelled-scores.jsonl")}
    _write_json(status_path, status)
    return status


def acquire(
    plan: dict[str, Any],
    frame_root: str | Path,
    output_root: str | Path,
    *,
    source_dir: str | Path,
    weights: str | Path,
    device: str,
    fp16: bool,
    max_jobs: int,
    max_frames_per_job: int | None,
    annotations_root: str | Path | None = None,
    label_policy: dict[str, Any] | None = None,
    run_sequence: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a bounded number of resumable acquisition jobs and checkpoint progress."""
    _require(max_jobs > 0, "max_jobs must be positive")
    from horizon_perception.model import ModelSpec
    from horizon_perception.reproduce import run_sequence as actual_run_sequence

    frame_root, output_root = Path(frame_root), Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / "plan.json", plan)
    state_path = output_root / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"plan_sha256": plan["plan_sha256"], "jobs": {}}
    _require(state.get("plan_sha256") == plan["plan_sha256"], "output root belongs to another acquisition plan")
    chosen = [
        job for job in plan["jobs"]
        if state["jobs"].get(f"{job['sequence_id']}/{job['arm_id']}", {}).get("eligible_for_calibration_bundle") is not True
    ][:max_jobs]
    runner = run_sequence or actual_run_sequence
    spec = ModelSpec(
        family="wasr_t", source_dir=Path(source_dir), source_commit="1b5360af20408e09bbf0116a0029f7e0c0800e7c",
        weights=Path(weights), weights_sha256="6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef", architecture="wasr_temporal_resnet101",
    )
    for job in chosen:
        key = f"{job['sequence_id']}/{job['arm_id']}"
        job_root = output_root / "jobs" / job["sequence_id"] / job["arm_id"]
        materialized = materialize_job(plan, job, frame_root, job_root / "materialization", max_frames=max_frames_per_job)
        inference_dir = job_root / "inference"
        existing_manifest = inference_dir / "manifest.json"
        if existing_manifest.exists():
            existing_count = int(json.loads(existing_manifest.read_text()).get("sequence_frame_count", -1))
            if existing_count != materialized["selected_frame_count"]:
                archive = job_root / f"inference-partial-{existing_count}"
                _require(not archive.exists(), "partial inference archive already exists")
                inference_dir.replace(archive)
        if not (inference_dir / "manifest.json").exists():
            runner(spec, job_root / "materialization", inference_dir, device, fp16, frame_glob="*L.jpg", evidence_partition="calibration")
        manifest = json.loads((inference_dir / "manifest.json").read_text())
        _require(manifest["sequence_frame_count"] == materialized["selected_frame_count"], "inference/materialization count mismatch")
        rows = _h0_rows(inference_dir / "features.jsonl", job, materialized)
        with (job_root / "h0-scores.jsonl").open("w") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        label_status = None
        if annotations_root is not None and label_policy is not None:
            label_status = join_h0_labels(
                job_root,
                Path(annotations_root) / job["sequence_id"] / "ground_truth",
                label_policy,
            )
        _write_json(job_root / "availability.json", {
            "H0": {"status": "evidence_acquired_not_calibrated", "reason": "labels and complete paired arms are required before threshold fitting"},
            "H1": {"status": "blocked", "reason": "independent_horizon_and_occlusion_unavailable"},
            "H2": {"status": "blocked", "reason": "target_runtime_reference_unavailable"},
            "H3": {"status": "blocked", "reason": "target_runtime_reference_unavailable"},
            "H4": {"status": "blocked", "reason": "reference_nonconverged_and_causal_controls_incomplete"},
        })
        complete = materialized["selected_frame_count"] == int(job["expected_frame_count"])
        state["jobs"][key] = {
            "status": "inference_complete",
            "selected_frame_count": materialized["selected_frame_count"],
            "eligible_for_calibration_bundle": complete,
            "inference_manifest_sha256": _file_hash(inference_dir / "manifest.json"),
            "h0_scores_sha256": _file_hash(job_root / "h0-scores.jsonl"),
            "label_join_status": label_status,
        }
        _write_json(state_path, state)
    completed_jobs = list(state["jobs"].values())
    report = {
        "schema_version": "horizon.perception-calibration-acquisition-progress.v1",
        "partition": "calibration",
        "plan_sha256": plan["plan_sha256"],
        "completed_job_count": len(completed_jobs),
        "eligible_job_count": sum(row["eligible_for_calibration_bundle"] for row in completed_jobs),
        "total_job_count": len(plan["jobs"]),
        "complete_for_bundle": len(completed_jobs) == len(plan["jobs"]) and all(row["eligible_for_calibration_bundle"] for row in completed_jobs),
        "h1_status": "blocked_independent_horizon_and_occlusion_unavailable",
        "h2_h4_status": "blocked_target_runtime_reference_or_causal_gate_unavailable",
        "state_sha256": _file_hash(state_path),
    }
    _write_json(output_root / "progress.json", report)
    return report


def retry_label_joins(
    plan: dict[str, Any], output_root: str | Path, annotations_root: str | Path, label_policy: dict[str, Any]
) -> dict[str, Any]:
    """Retry label joins for completed jobs after an optional local parser is installed."""
    output_root, annotations_root = Path(output_root), Path(annotations_root)
    state = json.loads((output_root / "state.json").read_text())
    _require(state.get("plan_sha256") == plan["plan_sha256"], "output root belongs to another acquisition plan")
    statuses = {}
    for job in plan["jobs"]:
        if f"{job['sequence_id']}/{job['arm_id']}" not in state["jobs"]:
            continue
        job_root = output_root / "jobs" / job["sequence_id"] / job["arm_id"]
        key = f"{job['sequence_id']}/{job['arm_id']}"
        statuses[key] = join_h0_labels(
            job_root, annotations_root / job["sequence_id"] / "ground_truth", label_policy
        )
        state["jobs"][key]["label_join_status"] = statuses[key]
    _write_json(output_root / "state.json", state)
    report = {"partition": "calibration", "joined_job_count": len(statuses), "statuses": statuses, "state_sha256": _file_hash(output_root / "state.json")}
    _write_json(output_root / "label-join-retry.json", report)
    return report
