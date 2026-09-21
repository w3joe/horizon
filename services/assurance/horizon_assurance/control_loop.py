"""Host-timed 20 Hz fusion -> assurance -> gate consuming loop."""

from __future__ import annotations

from collections import deque
import copy
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .candidates import Candidate


class EndpointError(RuntimeError):
    def __init__(self, status: int | None, payload: dict[str, Any]):
        self.status = status
        self.payload = payload
        super().__init__(f"endpoint status={status}: {payload}")


def _json_request(
    url: str,
    *,
    body: dict[str, Any] | None = None,
    bearer: str | None = None,
    timeout_s: float = 0.04,
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(
        url,
        data=None if body is None else json.dumps(body, allow_nan=False).encode(),
        method="GET" if body is None else "POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - local configured services
            return json.load(response), {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"error": "HTTP_ERROR"}
        raise EndpointError(exc.code, payload) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise EndpointError(None, {"error": "TRANSPORT_ERROR", "detail": type(exc).__name__}) from exc


class FusionClient:
    def __init__(self, base_url: str, branch_id: str = "protected"):
        query = urlencode({"branch": branch_id})
        self.url = f"{base_url.rstrip('/')}/v1/governor-input?{query}"

    def fetch(self, *, timeout_s: float) -> tuple[dict[str, Any], str]:
        payload, headers = _json_request(self.url, timeout_s=timeout_s)
        sample_id = headers.get("x-horizon-sample-id", payload.get("snapshot", {}).get("snapshot_id", ""))
        if not sample_id:
            raise EndpointError(None, {"error": "MISSING_SAMPLE_ID"})
        return payload, sample_id


class GateClient:
    def __init__(
        self,
        base_url: str,
        *,
        decision_token_file: str | Path,
        operator_token_file: str | Path,
    ):
        self.base_url = base_url.rstrip("/")
        self.decision_token_file = Path(decision_token_file)
        self.operator_token_file = Path(operator_token_file)

    @staticmethod
    def _token(path: Path) -> str:
        token = path.read_text().strip()
        if not token:
            raise EndpointError(None, {"error": "EMPTY_CAPABILITY_FILE"})
        return token

    def status(self, *, timeout_s: float) -> dict[str, Any]:
        return _json_request(f"{self.base_url}/health", timeout_s=timeout_s)[0]

    def reset(self, *, timeout_s: float) -> dict[str, Any]:
        return _json_request(
            f"{self.base_url}/v1/operator/reset-handshake",
            body={},
            bearer=self._token(self.operator_token_file),
            timeout_s=timeout_s,
        )[0]

    def prime(self, governor_input: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        return _json_request(
            f"{self.base_url}/v1/recovery/prime",
            body=governor_input,
            bearer=self._token(self.decision_token_file),
            timeout_s=timeout_s,
        )[0]

    def submit(
        self,
        governor_input: dict[str, Any],
        decision: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        return _json_request(
            f"{self.base_url}/v1/decision",
            body={"input": governor_input, "decision": decision},
            bearer=self._token(self.decision_token_file),
            timeout_s=timeout_s,
        )[0]


def input_epoch(governor_input: dict[str, Any]) -> int:
    values = (
        str(governor_input.get("episode_id", "")),
        str(governor_input.get("snapshot", {}).get("snapshot_id", "")),
    )
    found = {int(match.group(1)) for value in values if (match := re.search(r"epoch-(\d+)", value))}
    if len(found) != 1:
        raise ValueError("GovernorInput must carry one consistent epoch-N identity")
    return found.pop()


class AssuranceControlLoop:
    def __init__(
        self,
        *,
        fusion: FusionClient,
        gate: GateClient,
        candidate: Candidate,
        period_s: float = 0.05,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        evidence_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.fusion = fusion
        self.gate = gate
        self.candidate = candidate
        self.period_s = period_s
        self.event_sink = event_sink
        self.evidence_sink = evidence_sink
        self.events: deque[dict[str, Any]] = deque(maxlen=2_000)
        self.last_sample_id: str | None = None
        self.stop_event = threading.Event()

    def _record(self, event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(event)
        if self.event_sink is not None:
            self.event_sink(event)
        return event

    @staticmethod
    def _remaining_s(deadline_ns: int, *, cap_s: float = 0.04) -> float:
        return max(0.001, min(cap_s, (deadline_ns - time.monotonic_ns()) / 1e9))

    @staticmethod
    def _permission_valid_until_ns(governor_input: dict[str, Any]) -> int:
        values = [
            int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
            int(governor_input["proposal"]["expires_monotonic_ns"]),
        ]
        values.extend(
            int(item["valid_until_monotonic_ns"])
            for item in governor_input["health"].get("summaries", [])
        )
        values.extend(
            int(item["valid_until_monotonic_ns"])
            for item in governor_input.get("recovery_options", [])
        )
        return min(values)

    @staticmethod
    def _input_summary(governor_input: dict[str, Any]) -> dict[str, Any]:
        """Bounded public lineage/state context for an assurance event."""

        snapshot = governor_input["snapshot"]
        proposal = governor_input["proposal"]
        return {
            "run_id": governor_input["run_id"],
            "episode_id": governor_input["episode_id"],
            "branch_id": governor_input["branch_id"],
            "tick_index": governor_input["tick_index"],
            "simulation_time_s": governor_input["simulation_time_s"],
            "monotonic_time_ns": governor_input["monotonic_time_ns"],
            "decision_deadline_monotonic_ns": governor_input[
                "decision_deadline_monotonic_ns"
            ],
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_valid_until_monotonic_ns": snapshot[
                "valid_until_monotonic_ns"
            ],
            "ownship": copy.deepcopy(snapshot["ownship"]),
            "contacts": copy.deepcopy(snapshot["contacts"]),
            "actuator": copy.deepcopy(snapshot["actuator"]),
            "proposal": {
                "command_id": proposal["command_id"],
                "origin_snapshot_id": proposal["origin_snapshot_id"],
                "sequence": proposal["sequence"],
                "command": copy.deepcopy(proposal["command"]),
                "expires_monotonic_ns": proposal["expires_monotonic_ns"],
            },
            "health": copy.deepcopy(governor_input["health"]),
        }

    def run_once(self) -> dict[str, Any]:
        started = time.monotonic_ns()
        try:
            governor_input, sample_id = self.fusion.fetch(timeout_s=self.period_s * 0.8)
        except EndpointError as exc:
            return self._record(
                {
                    "event_type": "fusion_unavailable",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "status": exc.status,
                    "detail": exc.payload,
                }
            )
        if sample_id == self.last_sample_id:
            return self._record(
                {
                    "event_type": "duplicate_sample_skipped",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample_id": sample_id,
                }
            )
        deadline_ns = int(governor_input["decision_deadline_monotonic_ns"])
        permission_valid_until_ns = self._permission_valid_until_ns(governor_input)
        input_summary = self._input_summary(governor_input)
        epoch = input_epoch(governor_input)
        epoch_synchronized = False
        try:
            gate_status = self.gate.status(
                timeout_s=self._remaining_s(permission_valid_until_ns)
            )
            if int(gate_status["epoch"]) != epoch:
                reset = self.gate.reset(
                    timeout_s=self._remaining_s(permission_valid_until_ns)
                )
                if not reset.get("accepted") or int(reset["epoch"]) != epoch:
                    raise EndpointError(None, {"error": "GATE_EPOCH_SYNC_FAILED", "reset": reset})
                epoch_synchronized = True
                gate_status = {"epoch": epoch, "startup_recovery_ready": False}
            if not gate_status.get("startup_recovery_ready", False):
                if time.monotonic_ns() >= permission_valid_until_ns:
                    self.last_sample_id = sample_id
                    return self._record(
                        {
                            "event_type": "startup_recovery_input_stale",
                            "host_monotonic_ns": time.monotonic_ns(),
                            "sample_id": sample_id,
                            "epoch": epoch,
                            "epoch_synchronized": epoch_synchronized,
                            "input_summary": input_summary,
                        }
                    )
                prime = self.gate.prime(
                    governor_input,
                    timeout_s=self._remaining_s(permission_valid_until_ns, cap_s=2.0),
                )
                self.last_sample_id = sample_id
                return self._record(
                    {
                        "event_type": (
                            "startup_recovery_primed"
                            if prime.get("accepted") is True
                            else "startup_recovery_rejected"
                        ),
                        "host_monotonic_ns": time.monotonic_ns(),
                        "sample_id": sample_id,
                        "epoch": epoch,
                        "epoch_synchronized": epoch_synchronized,
                        "input_summary": input_summary,
                        "result": prime,
                    }
                )
        except EndpointError as exc:
            return self._record(
                {
                    "event_type": "gate_unavailable",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample_id": sample_id,
                    "epoch": epoch,
                    "epoch_synchronized": epoch_synchronized,
                    "input_summary": input_summary,
                    "status": exc.status,
                    "detail": exc.payload,
                }
            )

        if time.monotonic_ns() >= deadline_ns:
            self.last_sample_id = sample_id
            return self._record(
                {
                    "event_type": "input_expired",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample_id": sample_id,
                    "input_summary": input_summary,
                }
            )

        decision = self.candidate.evaluate(governor_input)
        self.last_sample_id = sample_id
        if not decision["valid"] or time.monotonic_ns() >= deadline_ns:
            return self._record(
                {
                    "event_type": "decision_not_submitted",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample_id": sample_id,
                    "input_summary": input_summary,
                    "decision": decision,
                }
            )
        try:
            receipt = self.gate.submit(
                governor_input,
                decision,
                timeout_s=self._remaining_s(deadline_ns),
            )
        except EndpointError as exc:
            return self._record(
                {
                    "event_type": "gate_submission_failed",
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample_id": sample_id,
                    "input_summary": input_summary,
                    "decision": decision,
                    "status": exc.status,
                    "detail": exc.payload,
                }
            )
        event = {
            "event_type": "decision_receipt",
            "host_monotonic_ns": time.monotonic_ns(),
            "sample_id": sample_id,
            "input_summary": input_summary,
            "cycle_time_ns": time.monotonic_ns() - started,
            "decision": decision,
            "receipt": receipt,
        }
        if self.evidence_sink is not None and receipt.get("accepted") is True:
            self.evidence_sink(
                {
                    "governor_input": copy.deepcopy(governor_input),
                    "decision": copy.deepcopy(decision),
                    "receipt": copy.deepcopy(receipt),
                }
            )
        return self._record(event)

    def run(self) -> None:
        next_tick = time.monotonic()
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self._record(
                    {
                        "event_type": "control_loop_error",
                        "host_monotonic_ns": time.monotonic_ns(),
                        "detail": type(exc).__name__,
                    }
                )
            next_tick += self.period_s
            delay = next_tick - time.monotonic()
            if delay <= 0.0:
                next_tick = time.monotonic()
                continue
            self.stop_event.wait(delay)

    def stop(self) -> None:
        self.stop_event.set()
