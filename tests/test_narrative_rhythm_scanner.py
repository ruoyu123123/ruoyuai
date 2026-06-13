"""阶段1：narrative_rhythm_scanner 测试（金标准 + 合成反例 + advisory 边界）。

钉死：真作者样本 PASS（金标准·零误伤）；过早收束/匀速平铺/节拍单调被抓；gate_level 永远 advisory。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import narrative_rhythm_scanner as nr  # noqa: E402


def _para(n_sent, word, punct="。"):
    """造一个段落：n_sent 句·每句含 word。"""
    return "".join(f"{word}的事情发生了一些变化{punct}" for _ in range(n_sent))


def test_gate_level_always_advisory():
    """任何结果 gate_level 必 advisory·无 hard_gate（北极星⑤）。"""
    import json
    txt = "\n\n".join(_para(3, "平静") for _ in range(20))
    r = nr.scan(txt)
    assert r["gate_level"] == "advisory"
    assert "hard_gate" not in json.dumps(r, ensure_ascii=False)


def test_short_text_pass():
    """文本过短直接 PASS（不误判）。"""
    r = nr.scan("第001章 测试\n\n短短一段。")
    assert r["verdict"] == "PASS"


_HOT = "血光迸溅惨叫撕心裂肺！残肢断裂骨头碎了！痛！杀！崩塌冲撞嘶吼！"   # ~30 CJK·高张力
_COLD = "他平静地走在长长的道路上面慢慢地思考着一些非常寻常而普通的日常琐碎事情罢了。"  # ~36·冷


def test_premature_resolution_flagged():
    """前段高张力→后段彻底平静→过早收束被抓（>500 CJK）。"""
    hot = "\n\n".join(_HOT for _ in range(10))      # 前段高张力 ~300 CJK
    cold = "\n\n".join(_COLD for _ in range(10))    # 后段冷 ~360 CJK
    r = nr.scan(hot + "\n\n" + cold)
    kinds = [v["kind"] for v in r["violations"]]
    assert "premature_resolution" in kinds, r["metrics"]
    assert r["metrics"]["post_climax_retention"] < 0.5


def test_tension_flatline_flagged():
    """全程一个调子(无情绪标点无强度词无短句)→匀速平铺被抓。"""
    flat = "\n\n".join(_COLD for _ in range(20))   # ~720 CJK 匀速冷
    r = nr.scan(flat)
    kinds = [v["kind"] for v in r["violations"]]
    assert "tension_flatline" in kinds, r["metrics"]
    assert r["metrics"]["tension_cv"] < nr.FLATLINE_CV


def test_beat_monotony_flagged():
    """连续 >=8 段纯推进(强度词密+短句)无缓冲→节拍单调被抓。"""
    push = "\n\n".join(_HOT for _ in range(20))    # ~600 CJK 全推进
    r = nr.scan(push)
    kinds = [v["kind"] for v in r["violations"]]
    assert "beat_monotony" in kinds, r["metrics"]
    assert r["metrics"]["max_push_streak"] >= 8


def test_real_author_golden_passes():
    """金标准：真作者《人生长恨水长东》原文必须 PASS（零误伤·阈值校准锚）。"""
    orig = _ROOT / "workspace" / "styles" / "人生长恨水长东" / "原文"
    style = _ROOT / "workspace" / "styles" / "人生长恨水长东" / "作者风格.json"
    if not orig.is_dir():
        return  # 风格库不在则跳过（CI 环境无 workspace）
    chs = sorted(orig.glob("*.txt"))[:3]
    if not chs:
        return
    text = "\n\n".join(c.read_text(encoding="utf-8") for c in chs)
    r = nr.scan(text, style_path=style if style.exists() else None)
    assert r["verdict"] == "PASS", f"真作者被误判: {[v['kind'] for v in r['violations']]}"


def test_author_baseline_reported_not_used_as_threshold():
    """作者档 post_climax_retention 基线仅信息上报（judge 0-10 口径与文本代理量纲不同·
    不当 premature 文本阈值·否则重蒸后高基线误判真作者 3 章样本）。"""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "作者风格.json"
        sp.write_text(json.dumps({"narrative_rhythm": {
            "tension_trajectory": {"post_climax_retention": 0.93}}}, ensure_ascii=False),
            encoding="utf-8")
        txt = "\n\n".join(_HOT for _ in range(20))   # >500 CJK 走到 violations 路径
        r = nr.scan(txt, style_path=sp)
        # 基线被上报（信息）
        assert r["author_baseline"]["from_author_profile"] is True
        assert r["author_baseline"]["post_climax_retention"] == 0.93
        # 但 premature 阈值仍是固定绝对值（不被 0.93 抬高）
        assert nr.RETENTION_DEFAULT == 0.25


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[narrative_rhythm_scanner] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
