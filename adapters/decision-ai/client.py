"""HTTP adapter for a replaceable external decision-making AI process."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DecisionAIError(RuntimeError):
    pass


class DecisionAIClient:
    def __init__(self, base_url: str, timeout_s: float = 0.15):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def propose(
        self,
        snapshot: dict[str, Any],
        *,
        perception_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request_body = {"snapshot": snapshot}
        if perception_context is not None:
            request_body["perception_context"] = perception_context
        body = json.dumps(request_body, allow_nan=False).encode()
        request = Request(
            f"{self.base_url}/v1/propose",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DecisionAIError(f"decision AI request failed: {exc}") from exc
        proposal = payload.get("proposal")
        trace = payload.get("inference_trace")
        if not isinstance(proposal, dict) or proposal.get("contract_type") != "ProposedCommand":
            raise DecisionAIError("decision AI returned no valid ProposedCommand envelope")
        if not isinstance(trace, dict) or trace.get("contract_type") != "AIInferenceTrace":
            raise DecisionAIError("decision AI returned no valid AIInferenceTrace envelope")
        return proposal, trace
