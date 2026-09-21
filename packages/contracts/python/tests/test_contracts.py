from horizon_contracts import SCHEMA_VERSION, GovernorInput, RecoveryInput


def test_generated_contracts_import() -> None:
    assert SCHEMA_VERSION == "0.1.0"
    assert GovernorInput.__required_keys__ >= {"run_id", "proposal", "snapshot"}
    assert RecoveryInput.__required_keys__ >= {
        "recovery_input_id",
        "plant_epoch",
        "recovery_deadline_monotonic_ns",
        "snapshot",
        "health",
    }
    assert "proposal" not in RecoveryInput.__required_keys__
