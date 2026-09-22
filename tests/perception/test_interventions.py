from __future__ import annotations

import pytest

from horizon_neural_health.interventions import _replay, run_sae_direction_intervention


class Handle:
    def __init__(self):
        self.removed = False

    def remove(self):
        self.removed = True


class Model:
    def __init__(self, fail=False):
        self.clears = 0
        self.calls = []
        self.fail = fail

    def clear_state(self):
        self.clears += 1

    def __call__(self, frame):
        self.calls.append(frame)
        if self.fail:
            raise RuntimeError("fault")
        return frame


def test_replay_resets_and_replays_context_in_order():
    model = Model()
    result = _replay(model, [{"id": 1}, {"id": 2}])
    assert model.clears == 1
    assert model.calls == [{"id": 1}, {"id": 2}]
    assert result == {"id": 2}


def test_replay_removes_intervention_hook_on_fault():
    model = Model(fail=True)
    handle = Handle()
    with pytest.raises(RuntimeError, match="fault"):
        _replay(model, [{"id": 1}], lambda _count: handle)
    assert handle.removed is True


def test_sae_intervention_preserves_negative_ablation_sign():
    torch = pytest.importorskip("torch")

    class DirectionModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Identity()

        def clear_state(self):
            pass

        def forward(self, frame):
            return {"out": self.layer(frame["image"])}

    model = DirectionModel()
    observed = []

    def metric(output):
        value = output["out"].mean()
        observed.append(float(value))
        return value

    result = run_sae_direction_intervention(
        model,
        model.layer,
        [{"image": torch.zeros((1, 2, 1, 1))}],
        [1.0, 0.0],
        feature_index=0,
        coefficient=-2.0,
        output_metric=metric,
        pair_id="negative-ablation",
        offline=True,
    )
    assert observed[1] < observed[0]
    assert result.perturbation_norm == 2.0
