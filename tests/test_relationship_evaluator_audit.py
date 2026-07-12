import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import relationship_evaluator as module


def test_trigger_requires_all_numeric_dimensions():
    assert module.trigger_satisfied({"trust": 2, "affinity": 1}, {"trust": 3, "affinity": 1})
    with pytest.raises(module.RelationshipContractError):
        module.trigger_satisfied({"trust": 2}, {"trust": "high"})


def test_mark_consumed_rejects_unknown_event():
    ensemble = {"characters": {"甲": {"heart_events": [{
        "event_id": "HE_1", "consumed": False, "trigger_at": {},
    }]}}}
    with pytest.raises(module.RelationshipContractError, match="未知"):
        module.mark_consumed_from_delta(
            ensemble,
            {"heart_events_revealed": [{"event_id": "HE_X", "evidence": "x"}]},
            "cluster_001",
        )


def test_character_cards_have_one_canonical_protagonist():
    assert module.get_protagonist({
        "characters": [{"id": "C_MAIN", "name": "主角", "role": "主角"}]
    }) == "主角"
    for invalid in ({"主角": {"role": "主角"}}, {"characters": []}):
        with pytest.raises(module.RelationshipContractError):
            module.get_protagonist(invalid)
