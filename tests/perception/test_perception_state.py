from __future__ import annotations

import pytest

from horizon_perception.runner import SequentialPerceptionRunner


class FakeModel:
    def __init__(self):
        self.clears = 0

    def clear_state(self):
        self.clears += 1


class FakeRecorder:
    def __init__(self):
        self.clears = 0

    def clear(self):
        self.clears += 1


def test_reset_clears_temporal_model_and_diagnostics():
    runner = SequentialPerceptionRunner.__new__(SequentialPerceptionRunner)
    runner.family = "wasr_t"
    runner.model = FakeModel()
    runner.recorder = FakeRecorder()
    runner.frame_count = 9
    runner.last_timestamp_ns = 123
    runner.sequence_id = "old"
    runner.reset("new")
    assert runner.model.clears == 1
    assert runner.recorder.clears == 1
    assert runner.frame_count == 0
    assert runner.last_timestamp_ns is None
    assert runner.sequence_id == "new"


def test_lineage_fault_invalidates_and_clears_temporal_state():
    runner = SequentialPerceptionRunner.__new__(SequentialPerceptionRunner)
    runner.family = "wasr_t"
    runner.model = FakeModel()
    runner.recorder = FakeRecorder()
    runner.frame_count = 2
    runner.last_timestamp_ns = 123
    runner.sequence_id = "sequence"
    runner.invalidate_sequence()
    assert runner.model.clears == 1
    assert runner.recorder.clears == 1
    assert runner.sequence_id is None
    assert runner.last_timestamp_ns is None


def test_hooks_do_not_mutate_tiny_model_output():
    torch = pytest.importorskip("torch")
    from horizon_perception.instrumentation import ActivationRecorder

    class Decoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.aspp = torch.nn.Conv2d(2, 3, 1)

        def forward(self, value):
            return self.aspp(value)

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone = torch.nn.ModuleDict({"layer4": torch.nn.Conv2d(1, 2, 1)})
            self.decoder = Decoder()

        def forward(self, value):
            return self.decoder(self.backbone["layer4"](value))

    torch.manual_seed(4)
    model = Tiny().eval()
    value = torch.randn(1, 1, 4, 4)
    expected = model(value).detach().clone()
    recorder = ActivationRecorder(model, "wasr").install()
    actual = model(value).detach()
    recorder.remove()
    assert torch.equal(expected, actual)
    assert sorted(recorder.records) == ["decoder_logits", "encoder"]
