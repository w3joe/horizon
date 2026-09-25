from experiment.evaluation.h5_control_report import audit_gate_receipts


def test_recovery_substitution_is_separate_from_expired_proposal():
    bundle = {
        "authorized_proposal_sources": ["fixture"],
        "proposals": [{"proposal_id": "p", "source_id": "fixture", "expires_monotonic_ns": 200}],
        "decisions": [{"decision_id": "d", "proposal_id": "p", "authority": "autonomy",
                       "valid": True, "expires_monotonic_ns": 200}],
        "protected_command_trace": [{"envelope": {
            "command_id": "c", "decision_id": "d", "authority": "recovery",
        }}],
        "gate_receipts": [{"command_id": "c", "decision_id": "d", "authority": "recovery",
                           "received_monotonic_ns": 100, "accepted": True}],
    }
    counts = audit_gate_receipts(bundle)
    assert counts["recovery_substitution"] == 1
    assert counts["expired_proposal"] == counts["expired_decision"] == 0
    bundle["gate_receipts"][0]["received_monotonic_ns"] = 200
    counts = audit_gate_receipts(bundle)
    assert counts["recovery_substitution"] == 1
    assert counts["expired_proposal"] == counts["expired_decision"] == 1
