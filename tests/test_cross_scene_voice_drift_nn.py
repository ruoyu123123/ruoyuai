"""🔴 2026-06-29 NN增强路径 — cross_scene_voice_drift embedding voice 漂移测试。

聚焦 EMBED_BACKEND 非 hash 时的嵌入 cosine 跨场景 voice 漂移路径（与 5 维统计取 max）：
  · 默认 hash → embed_backend_active False·完全走现有统计（零回归）。
  · 真语义后端（mstyle/api/ruoyu_style·此处 mock）→ 嵌入正交场景 → embedding_voice_drift。
  · 同声音两场景 → 嵌入近 → 不报。
  · embedding 后端/桥抛异常 → 降级走统计（不崩·失败不影响现有启发式）。
  · _has_semantic_embedding 用 embedding_store.embedding_method() 权威判定（后端回退 hash 即不启）。

桥用 monkeypatch 替身（不 spawn subprocess·不依赖 venv/真模型·与 test_style_embed_bridge 同范式）。
"""
import json
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
import sys  # noqa: E402
sys.path.insert(0, str(_SCRIPTS))
import cross_scene_voice_drift_scanner as vd  # noqa: E402
import embedding_store as es  # noqa: E402


# ─────────────────────────── 夹具 ───────────────────────────
def _draft_with_chars(text: str, characters=None) -> Path:
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    d = tmp / "draft.txt"
    d.write_text(text, encoding="utf-8")
    return tmp, d


_PAD = "旁白叙述内容补足场景长度。" * 80   # 每场景 >= split_scenes 的 500 字门槛


def _draft_orthogonal() -> tuple[Path, Path]:
    """阿强 在两场景对白等长（无句长 stat 漂移）但语义正交（AAA vs BBB）→ 仅 embedding 能捕获。"""
    sc0 = "\n".join('阿强开口：“AAA这是一句平常的对白内容。”' for _ in range(3)) + "\n" + _PAD
    sc1 = "\n".join('阿强开口：“BBB这是一句平常的对白内容。”' for _ in range(3)) + "\n" + _PAD
    return _draft_with_chars(sc0 + "\n---\n" + sc1, characters=[])


def _draft_same_voice() -> tuple[Path, Path]:
    """两场景对白完全一致 → embedding 近 → 不报。"""
    line = "\n".join('阿强开口：“AAA这是一句平常的对白内容。”' for _ in range(3))
    return _draft_with_chars((line + "\n" + _PAD) + "\n---\n" + (line + "\n" + _PAD), characters=[])


def _draft_stat_drift() -> tuple[Path, Path]:
    """阿强 句长在两场景剧烈漂移（短 vs 长）→ 统计层能捕获（用于零回归验证）。"""
    short = "\n".join('阿强开口：“嗯。”' for _ in range(4))
    longl = "\n".join(
        '阿强开口：“这是一段非常非常非常非常非常非常长长长长长的对白内容哈哈哈哈。”'
        for _ in range(4))
    return _draft_with_chars((short + "\n" + _PAD) + "\n---\n" + (longl + "\n" + _PAD),
                             characters=[])


def _fake_marker(text: str):
    """含 AAA → [1,0,0]；含 BBB → [0,1,0]；否则 [0,0,1]（单位正交·便于精确算距离）。"""
    if "AAA" in text:
        return [1.0, 0.0, 0.0]
    if "BBB" in text:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


@pytest.fixture(autouse=True)
def _isolate_d4d8(monkeypatch):
    """隔离 D4/D8（只测 drift 主逻辑 + embedding）。"""
    monkeypatch.setenv("VOICE_D4D8_MODE", "off")


# ─────────────────────────── _has_semantic_embedding 权威判定 ───────────────────────────
def test_has_semantic_embedding_hash_false(monkeypatch):
    """后端 hash（默认/回退）→ False（绝不拿 hash 假语义冒充）。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "hash")
    assert vd._has_semantic_embedding() is False


def test_has_semantic_embedding_nonhash_true(monkeypatch):
    """后端 mstyle/api → True。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:StyleDistance")
    assert vd._has_semantic_embedding() is True


# ─────────────────────────── 零回归：默认 hash 走现有统计 ───────────────────────────
def test_hash_backend_zero_regression(monkeypatch):
    """hash 后端 → embed_backend_active False·无 embedding_voice_drift·统计漂移照常工作。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "hash")
    root, d = _draft_stat_drift()
    rep = vd.scan(root, d)
    assert rep["embed_backend_active"] is False
    assert rep["embedding_drift_count"] == 0
    assert rep["drift_issues_count"] >= 1                     # 统计层仍捕获句长漂移
    assert all(it["type"] == "avg_dialogue_length_drift" for it in rep["drift_issues"])


# ─────────────────────────── NN 路径：嵌入捕获统计漏网 ───────────────────────────
def test_embedding_drift_caught_when_stats_miss(monkeypatch):
    """语义正交但句长一致 → 统计漏·embedding 捕获 → embedding_voice_drift（取 max=任一触发即报）。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:test")
    monkeypatch.setattr(es, "compute_embedding", _fake_marker)
    root, d = _draft_orthogonal()
    rep = vd.scan(root, d)
    assert rep["embed_backend_active"] is True
    types = {it["type"] for it in rep["drift_issues"]}
    assert "embedding_voice_drift" in types
    ev = next(it for it in rep["drift_issues"] if it["type"] == "embedding_voice_drift")
    assert ev["character"] == "阿强"
    assert ev["embedding_distance"] > 0.3        # 正交 → cosine 距离 1.0
    assert "scene_a" in ev and "scene_b" in ev
    assert rep["warning"] and "漂移" in rep["warning"]
    assert "embedding" in rep["warning"]          # warning 文案自适应提到 embedding


def test_embedding_no_drift_same_voice(monkeypatch):
    """两场景声音一致 → 嵌入近 → 无 embedding 漂移·无统计漂移 → 干净。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:test")
    monkeypatch.setattr(es, "compute_embedding", _fake_marker)
    root, d = _draft_same_voice()
    rep = vd.scan(root, d)
    assert rep["embed_backend_active"] is True
    assert rep["embedding_drift_count"] == 0
    assert all(it["type"] != "embedding_voice_drift" for it in rep["drift_issues"])


def test_embedding_distance_field_present(monkeypatch):
    """embedding_voice_drift issue details 带 embedding_distance 字段（团队约定）。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:test")
    monkeypatch.setattr(es, "compute_embedding", _fake_marker)
    root, d = _draft_orthogonal()
    rep = vd.scan(root, d)
    ev = [it for it in rep["drift_issues"] if it["type"] == "embedding_voice_drift"]
    assert ev
    assert all("embedding_distance" in it for it in ev)


# ─────────────────────────── 默认安全：异常降级不崩 ───────────────────────────
def test_embedding_exception_graceful_fallback(monkeypatch):
    """compute_embedding 抛异常 → scan 不崩·降级走统计·embedding_drift_count 0。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:test")

    def _boom(text):
        raise RuntimeError("backend down")

    monkeypatch.setattr(es, "compute_embedding", _boom)
    root, d = _draft_orthogonal()
    rep = vd.scan(root, d)                       # 不抛
    assert rep["embed_backend_active"] is True
    assert rep["embedding_drift_count"] == 0
    assert all(it["type"] != "embedding_voice_drift" for it in rep["drift_issues"])


# ─────────────────────────── advisory 边界 ───────────────────────────
def test_embedding_path_always_advisory(monkeypatch):
    """embedding 命中也永远 advisory·report 无 hard_gate。"""
    monkeypatch.setattr(es, "embedding_method", lambda: "mstyle:test")
    monkeypatch.setattr(es, "compute_embedding", _fake_marker)
    root, d = _draft_orthogonal()
    rep = vd.scan(root, d)
    assert rep["gate_level"] == "advisory"
    assert "hard_gate" not in json.dumps(rep, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
