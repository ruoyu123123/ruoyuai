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


def test_multi_protagonist_book_resolves_primary_not_fatal():
    """双主角书（如双女主）合法：get_protagonist 取信号强度排序主位，不再 FATAL。
    回归锁：真机《衔石与朝云》女娃/瑶姬双「主角·」前缀曾撞旧「恰一个」断言 exit 2。"""
    cards = {"characters": [
        {"id": "C_001", "name": "神农", "role": "末代神农·二女之父"},
        {"id": "C_002", "name": "女娃", "role": "主角·炎帝幼女·刚烈幼妹"},
        {"id": "C_003", "name": "瑶姬", "role": "主角·炎帝长女·柔深长姐"},
    ]}
    assert module.get_protagonist(cards) == "女娃"
    with pytest.raises(module.RelationshipContractError):
        module.get_protagonist({"characters": [{"id": "C_X", "name": "路人", "role": "配角"}]})
