"""Explicitly synthetic A6 adapter for control-path development experiments."""

from datetime import datetime

from horizon_assurance.policy_enforcement import A6PolicyEnforcer, content_hash

from .a6_shadow import _configuration, _evidence


def development_enforcer(config):
    bundle, context, evaluated_at = _configuration(config)
    context = {**context, "in_narrow_channel": False, "in_traffic_separation_scheme": False}

    def provider(message, decision):
        return {
            "contract_type": "PolicyEvidence", "schema_version": "0.1.0",
            "run_id": message["run_id"], "branch_id": message["branch_id"],
            "tick_index": message["tick_index"],
            "snapshot_sha256": content_hash(message["snapshot"]),
            "command_sha256": content_hash(decision["issued_command"]),
            "observed_monotonic_ns": message["monotonic_time_ns"],
            "expires_monotonic_ns": decision["expires_monotonic_ns"],
            "provenance": "synthetic", "operational_context": context,
            "evidence": _evidence(message, decision, bundle.parameters),
        }

    return A6PolicyEnforcer(bundle, provider, allow_synthetic=True,
                           utc_now=lambda: datetime.fromisoformat(evaluated_at.replace("Z", "+00:00")))
