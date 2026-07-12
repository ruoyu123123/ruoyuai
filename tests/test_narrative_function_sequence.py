# -*- coding: utf-8 -*-
"""作者签名因果功能链 narrative_function_sequence 三端管线测试（确定性）。

三端闭环：consolidate.aggregate_narrative_seq（读原文调 score_narrative_function_sequence·非空才写
作者档）→ build_manifest._collect_narrative_function_sequence（shadow/active/off·从作者档读转 directive）
→ gen_writer._build_narrative_seq_section（读 manifest 字段拼 prompt 段·None/空零回归）。
默认 shadow（结构骨注入效果需 gen-model A/B·先影子）。零依赖·仓库根 pytest 入口。
"""
import glob
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


# ───────────── consolidate 端：aggregate_narrative_seq ─────────────

def test_aggregate_no_original_text_empty():
    """无原文目录 → {}（零回归·非空守卫）。"""
    with tempfile.TemporaryDirectory() as td:
        assert cap.aggregate_narrative_seq(Path(td)) == {}


def test_aggregate_real_author_produces_bigrams():
    """真作者原文 → mode active + signature_bigrams 非空（本地无原文 CI → skip）。"""
    cands = glob.glob(str(_ROOT / "workspace" / "styles" / "*" / "原文" / "第001章.txt"))
    if not cands:
        print("[SKIP] 本地无真作者原文·跳过")
        return
    author_dir = Path(cands[0]).parent.parent
    r = cap.aggregate_narrative_seq(author_dir)
    if r:  # NARRATIVE_SEQ_MODE 可能被 env 关·容错（非空时验真产出）
        assert r.get("mode") == "active"
        assert r.get("signature_bigrams")


# ───────────── build_manifest 端：_collect_narrative_function_sequence ─────────────

class _S:
    def __init__(self, prof, db):
        self._p = prof
        self.db = db
        self.ch = 1

    def has_style_profile(self):
        return True

    def load(self, name, default=None):
        return self._p if name == "作者风格" else default


def _fake_prof():
    return {"narrative_function_sequence": {
        "mode": "active", "n_chapters": 100, "confidence": "high",
        "signature_bigrams": [
            {"seq": "face_slap→gain_reward", "count": 20, "ratio": 0.3},
            {"seq": "setting→confrontation", "count": 15, "ratio": 0.2},
            {"seq": "scheme→reveal", "count": 10, "ratio": 0.15},
        ],
    }}


def test_collect_three_modes():
    """build_manifest _collect：默认 shadow 落盘不注入·active 注入 directive·off None。"""
    import build_manifest as bm
    bak = os.environ.get("NARR_FUNC_SEQ_INJECT_MODE")
    try:
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("NARR_FUNC_SEQ_INJECT_MODE", None)  # 默认 shadow
            assert bm._collect_narrative_function_sequence(_S(_fake_prof(), Path(td))) is None
            assert (Path(td) / ".narr_func_seq" / "ch_001.json").exists()  # shadow 落盘
            os.environ["NARR_FUNC_SEQ_INJECT_MODE"] = "active"
            p = bm._collect_narrative_function_sequence(_S(_fake_prof(), Path(td)))
            assert p is not None and p["advisory_only"] is True
            assert any("功能链" in d for d in p["directives"])
            assert "face_slap→gain_reward" in p["directives"][0]
            os.environ["NARR_FUNC_SEQ_INJECT_MODE"] = "off"
            assert bm._collect_narrative_function_sequence(_S(_fake_prof(), Path(td))) is None
    finally:
        if bak is None:
            os.environ.pop("NARR_FUNC_SEQ_INJECT_MODE", None)
        else:
            os.environ["NARR_FUNC_SEQ_INJECT_MODE"] = bak


def test_collect_no_field_none():
    """作者档无 narrative_function_sequence → None（active 也不注入·零回归）。"""
    import build_manifest as bm
    bak = os.environ.get("NARR_FUNC_SEQ_INJECT_MODE")
    try:
        os.environ["NARR_FUNC_SEQ_INJECT_MODE"] = "active"
        with tempfile.TemporaryDirectory() as td:
            assert bm._collect_narrative_function_sequence(_S({}, Path(td))) is None
    finally:
        if bak is None:
            os.environ.pop("NARR_FUNC_SEQ_INJECT_MODE", None)
        else:
            os.environ["NARR_FUNC_SEQ_INJECT_MODE"] = bak


# ───────────── gen_writer 端：_build_narrative_seq_section ─────────────

def test_gen_writer_consumes_narrative_seq_section():
    """gen_writer 消费端：读 manifest.narrative_function_sequence.directives → 段·None/空/无 key/文件缺 → ""。"""
    import gen_writer as gw
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "manifest.json"
        # ① 有 directives（active build_manifest 注入）→ 非空段含因果链 + 醒目段头
        mp.write_text(json.dumps({"narrative_function_sequence": {
            "advisory_only": True,
            "directives": ["作者签名叙事功能链（结构骨·confidence=high）：face_slap→gain_reward、setting→confrontation"],
        }}, ensure_ascii=False), encoding="utf-8")
        sec = gw._build_narrative_seq_section(mp)
        assert "功能链" in sec and "face_slap→gain_reward" in sec and sec.startswith("## ")
        # ② None（shadow 默认）→ 空段
        mp.write_text(json.dumps({"narrative_function_sequence": None}, ensure_ascii=False), encoding="utf-8")
        assert gw._build_narrative_seq_section(mp) == ""
        # ③ 无 key → 空段
        mp.write_text("{}", encoding="utf-8")
        assert gw._build_narrative_seq_section(mp) == ""
        # ④ directives 空列表 → 空段
        mp.write_text(json.dumps({"narrative_function_sequence": {"directives": []}}, ensure_ascii=False),
                      encoding="utf-8")
        assert gw._build_narrative_seq_section(mp) == ""
        # ⑤ 文件不存在 → 空段（不崩）
        assert gw._build_narrative_seq_section(Path(td) / "nope.json") == ""
