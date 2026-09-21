from horizon_contracts import SCHEMA_VERSION, GovernorInput


def test_generated_contracts_import() -> None:
    assert SCHEMA_VERSION == "0.1.0"
    assert GovernorInput.__required_keys__ >= {"run_id", "proposal", "snapshot"}
