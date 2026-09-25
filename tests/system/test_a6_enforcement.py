"""Exercise policy denial through the actual independent service processes."""

from .horizon_stack import HorizonStack, request_json


def test_live_a6_without_policy_never_grants_normal_autonomy(tmp_path):
    stack = HorizonStack(tmp_path, scenario="normal_transit.json", candidate="A5",
                         a6_mode="enforce", assurance_loop=False)
    try:
        stack.start()
        evidence = stack.drive_joined_chain(require_exact_command=False)
        assert evidence["decision"]["action"] == "pass"
        assert evidence["receipt"]["authority"] == "recovery"
        assert "A6_POLICY_WITHHELD" in evidence["receipt"]["reason_codes"]
        status, payload, _ = request_json(stack.url("gate", "/v1/telemetry"))
        assert status == 200
        assert payload["a6_mode"] == "enforce"
        assert payload["last_policy_decision"]["authorization"] == "withhold"
        assert all(receipt["authority"] not in {"autonomy", "filtered_autonomy"}
                   for receipt in payload["receipts"] if receipt["accepted"])
    finally:
        stack.close()
