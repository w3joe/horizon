from __future__ import annotations

import pytest

from horizon_neural_health.interventions import _replay


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
