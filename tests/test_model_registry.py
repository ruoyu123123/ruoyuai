# 🔴 2026-06-29 可成长NN架构 · 模型注册表测试
"""test_model_registry.py — ModelRegistry 测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "ml" / "registry"))
from model_registry import ModelRegistry, enabled, VALID_STATUSES


@pytest.fixture
def reg(tmp_path):
    return ModelRegistry(registry_path=tmp_path / "registry.json")


def test_enabled_gate(monkeypatch):
    monkeypatch.delenv("RUOYU_MODEL_REGISTRY", raising=False)
    assert not enabled()
    monkeypatch.setenv("RUOYU_MODEL_REGISTRY", "1")
    assert enabled()


def test_register_new_model(reg):
    result = reg.register(
        "emotion_vad", "v1", "core/ml/emotion_vad/checkpoints/v1",
        {"mean_ccc": 0.80}, data_size=12000, env_gate="RUOYU_NN_VAD",
    )
    assert result["model"] == "emotion_vad"
    assert result["version"] == "v1"
    assert result["status"] == "shadow"


def test_register_as_active(reg):
    reg.register("test_model", "v1", "/path/v1", {"acc": 0.9}, status="active")
    active = reg.get_active("test_model")
    assert active is not None
    assert active["version"] == "v1"
    assert active["status"] == "active"


def test_register_invalid_status(reg):
    with pytest.raises(ValueError):
        reg.register("m", "v1", "/p", {}, status="invalid")


def test_promote_shadow_to_active(reg):
    reg.register("m", "v1", "/p1", {"acc": 0.8}, status="active")
    reg.register("m", "v2", "/p2", {"acc": 0.85}, status="shadow")
    result = reg.promote("m", "v2")
    assert result["promoted"] == "v2"
    assert result["retired"] == "v1"
    active = reg.get_active("m")
    assert active["version"] == "v2"


def test_promote_retires_old(reg):
    reg.register("m", "v1", "/p1", {}, status="active")
    reg.register("m", "v2", "/p2", {}, status="shadow")
    reg.promote("m", "v2")
    models = reg.list_models()
    versions = models["m"]["versions"]
    v1 = next(v for v in versions if v["version"] == "v1")
    assert v1["status"] == "retired"


def test_get_active_nonexistent(reg):
    assert reg.get_active("nonexistent") is None


def test_get_shadow(reg):
    reg.register("m", "v1", "/p", {}, status="shadow")
    shadow = reg.get_shadow("m")
    assert shadow is not None
    assert shadow["version"] == "v1"


def test_compare_active_vs_shadow(reg):
    reg.register("m", "v1", "/p1", {"acc": 0.80, "f1": 0.75}, status="active")
    reg.register("m", "v2", "/p2", {"acc": 0.85, "f1": 0.78}, status="shadow")
    cmp = reg.compare("m")
    assert cmp["active"] == "v1"
    assert cmp["shadow"] == "v2"
    assert cmp["metrics_comparison"]["acc"]["delta"] == pytest.approx(0.05)
    assert cmp["metrics_comparison"]["f1"]["delta"] == pytest.approx(0.03)


def test_compare_missing_shadow(reg):
    reg.register("m", "v1", "/p", {}, status="active")
    result = reg.compare("m")
    assert "error" in result


def test_set_status(reg):
    reg.register("m", "v1", "/p", {}, status="shadow")
    reg.set_status("m", "v1", "active")
    active = reg.get_active("m")
    assert active["version"] == "v1"


def test_set_status_invalid(reg):
    reg.register("m", "v1", "/p", {})
    with pytest.raises(ValueError):
        reg.set_status("m", "v1", "bogus")


def test_list_models(reg):
    reg.register("a", "v1", "/a", {"x": 1}, status="active")
    reg.register("b", "v1", "/b", {"y": 2}, status="shadow")
    models = reg.list_models()
    assert "a" in models
    assert "b" in models
    assert models["a"]["active"] == "v1"
    assert models["b"]["shadow"] == "v1"


def test_update_existing_version(reg):
    reg.register("m", "v1", "/p1", {"acc": 0.8})
    reg.register("m", "v1", "/p2", {"acc": 0.85})
    models = reg.list_models()
    assert models["m"]["total_versions"] == 1
    shadow = reg.get_shadow("m")
    assert shadow["path"] == "/p2"
    assert shadow["metrics"]["acc"] == 0.85


def test_promote_nonexistent(reg):
    result = reg.promote("nonexistent", "v1")
    assert "error" in result


def test_set_status_active_retires_old(reg):
    reg.register("m", "v1", "/p1", {}, status="active")
    reg.register("m", "v2", "/p2", {}, status="shadow")
    reg.set_status("m", "v2", "active")
    models = reg.list_models()
    v1 = next(v for v in models["m"]["versions"] if v["version"] == "v1")
    assert v1["status"] == "retired"
    assert models["m"]["active"] == "v2"


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "reg.json"
    r1 = ModelRegistry(path)
    r1.register("m", "v1", "/p", {"x": 1}, status="active")
    r2 = ModelRegistry(path)
    active = r2.get_active("m")
    assert active is not None
    assert active["metrics"]["x"] == 1
