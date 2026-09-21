from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import run_modal_job as job  # noqa: E402
from compute_ledger import initialize  # noqa: E402


def spec_with_output(tmp_path: Path) -> dict:
    spec = json.loads(
        (SCRIPTS.parent / "infra/modal/jobs/a07-wasrt-sequence-002.json").read_text()
    )
    spec["output"]["local_path"] = str(tmp_path / "output")
    return spec


def test_attempt_003_has_unique_delivery_paths_and_finite_limits() -> None:
    previous = json.loads(
        (SCRIPTS.parent / "infra/modal/jobs/a07-wasrt-sequence-002.json").read_text()
    )
    spec = json.loads(
        (SCRIPTS.parent / "infra/modal/jobs/a07-wasrt-sequence-003.json").read_text()
    )

    job.validate_spec(spec, {"upper_bound_usd": 1.5})

    assert spec["job_id"] == spec["reservation_id"] == "a07-wasrt-sequence-003"
    assert spec["modal_entrypoint"] == previous["modal_entrypoint"]
    assert spec["modal_volume_name"] != previous["modal_volume_name"]
    assert spec["output"]["remote_path"] != previous["output"]["remote_path"]
    assert spec["output"]["local_path"] != previous["output"]["local_path"]
    assert spec["limits"]["controller_wall_timeout_s"] == 600
    assert spec["limits"]["total_attempt_cap"] == 1
    assert spec["limits"]["retries"] == 0


def launch_provenance() -> dict:
    return {
        "captured_utc": "2026-09-21T00:00:00+00:00",
        "repository_commit": "launch-commit",
        "entrypoint": "services/perception/modal_wasrt_smoke.py",
        "entrypoint_sha256": "entrypoint-hash",
        "job_spec_sha256": "spec-hash",
        "declared_source_tree": "services/perception",
        "declared_source_tree_sha256": "tree-hash",
    }


def test_validate_download_matches_a07_artifact_layout(tmp_path: Path) -> None:
    spec = spec_with_output(tmp_path)
    output = Path(spec["output"]["local_path"])
    (output / "class_masks").mkdir(parents=True)
    (output / "mask_previews").mkdir()
    (output / "manifest.json").write_text("{}")
    (output / "features.jsonl").write_text("{}\n")
    for index in range(85):
        (output / "class_masks" / f"{index:04d}.png").write_bytes(b"mask")
        (output / "mask_previews" / f"{index:04d}.png").write_bytes(b"preview")
    job.validate_download(spec)


def test_stop_uses_exact_app_id_and_verifies_zero_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(job.subprocess, "run", fake_run)
    monkeypatch.setattr(
        job,
        "list_apps",
        lambda _environment: {"ap-exact123": {"state": "stopped", "tasks": "0"}},
    )
    assert job.stop_and_verify("ap-exact123", {}) == "verified_stopped"
    assert commands[0][3] == "ap-exact123"


def test_directory_download_uses_existing_parent_and_publishes_validated_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = spec_with_output(tmp_path)
    output = Path(spec["output"]["local_path"])

    def fake_modal(command: list[str], _environment: dict, timeout_s: int) -> int:
        assert command[:2] == ["volume", "get"]
        parent = Path(command[-1])
        assert parent.is_dir(), "Modal treats a missing destination as a file path"
        assert not output.exists(), "incomplete artifacts must not be published"
        downloaded = parent / Path(command[-2]).name
        (downloaded / "class_masks").mkdir(parents=True)
        (downloaded / "mask_previews").mkdir()
        (downloaded / "manifest.json").write_text("{}")
        (downloaded / "features.jsonl").write_text("{}\n")
        for index in range(85):
            (downloaded / "class_masks" / f"{index}.png").write_bytes(b"mask")
            (downloaded / "mask_previews" / f"{index}.png").write_bytes(b"preview")
        return 0

    monkeypatch.setattr(job, "modal", fake_modal)
    job.download_artifacts(spec, {})
    job.validate_download(spec)
    assert not list(tmp_path.glob("horizon-download-*"))


def test_incomplete_download_is_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = spec_with_output(tmp_path)
    monkeypatch.setattr(job, "modal", lambda *_args, **_kwargs: 0)
    with pytest.raises(RuntimeError, match="incomplete"):
        job.download_artifacts(spec, {})
    assert not Path(spec["output"]["local_path"]).exists()


def test_download_failure_retains_volume_and_records_provider_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger.json"
    initialize(ledger)
    spec = spec_with_output(tmp_path)
    app_id = "ap-captured123"
    monkeypatch.setattr(job, "list_apps", lambda _environment: {})
    monkeypatch.setattr(
        job,
        "run_modal",
        lambda *_args: job.ModalRunResult(0, frozenset({app_id}), "created ap-captured123"),
    )
    monkeypatch.setattr(job, "stop_and_verify", lambda resolved, _environment: "verified_stopped")

    def fake_modal(command: list[str], _environment: dict[str, str], timeout_s: int = 120) -> int:
        del timeout_s
        return 1 if command[:2] == ["volume", "get"] else 0

    deleted: list[bool] = []
    monkeypatch.setattr(job, "modal", fake_modal)
    monkeypatch.setattr(job, "delete_volume", lambda *_args: deleted.append(True) or True)
    with pytest.raises(RuntimeError, match="download"):
        job.execute_job(
            ledger,
            "a07-wasrt-sequence-002",
            spec,
            Path("entrypoint.py"),
            {},
            launch_provenance(),
        )
    assert deleted == []
    record = next(
        item
        for item in json.loads(ledger.read_text())["reservations"]
        if item["reservation_id"] == "a07-wasrt-sequence-002"
    )
    assert record["status"] == "awaiting_reconciliation"
    assert record["provider_job_id"] == app_id
    assert "termination=verified_stopped" in record["reconciliation_note"]
    assert "volume_retained=True" in record["reconciliation_note"]


def test_unknown_app_id_never_claims_termination_or_deletes_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger.json"
    initialize(ledger)
    spec = spec_with_output(tmp_path)
    monkeypatch.setattr(job, "list_apps", lambda _environment: {})
    monkeypatch.setattr(
        job,
        "run_modal",
        lambda *_args: job.ModalRunResult(0, frozenset(), "no app identifier"),
    )
    monkeypatch.setattr(job, "modal", lambda *_args, **_kwargs: 0)
    deleted: list[bool] = []
    monkeypatch.setattr(job, "delete_volume", lambda *_args: deleted.append(True) or True)
    with pytest.raises(RuntimeError, match="unverified"):
        job.execute_job(
            ledger,
            "a07-wasrt-sequence-002",
            spec,
            Path("entrypoint.py"),
            {},
            launch_provenance(),
        )
    assert deleted == []
    record = next(
        item
        for item in json.loads(ledger.read_text())["reservations"]
        if item["reservation_id"] == "a07-wasrt-sequence-002"
    )
    assert record["provider_job_id"] is None
    assert "termination=unknown_no_app_id" in record["reconciliation_note"]
    assert "volume_retained=True" in record["reconciliation_note"]


def test_run_metadata_reuses_prelaunch_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = spec_with_output(tmp_path)
    output = Path(spec["output"]["local_path"])
    (output / "class_masks").mkdir(parents=True)
    (output / "mask_previews").mkdir()
    (output / "manifest.json").write_text("{}")
    (output / "features.jsonl").write_text("{}\n")
    (output / "class_masks/00000.png").write_bytes(b"mask")
    (output / "mask_previews/00000.png").write_bytes(b"preview")
    monkeypatch.setattr(
        job.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("late git read")),
    )
    provenance = launch_provenance()
    job.write_run_metadata(spec, "ap-exact123", provenance)
    metadata = json.loads((output / "platform-run.json").read_text())
    assert metadata["repository_commit"] == "launch-commit"
    assert metadata["code"]["declared_source_tree_sha256"] == "tree-hash"
