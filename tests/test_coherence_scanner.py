# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""test_coherence_scanner.py — 连贯性评分全管线单测（data_prep / model / bridge / scanner）。

覆盖：
  1. data_prep（自监督·CoUDA 双通道扰动·stdlib·无 torch）
       generate_positive(label=1) / global_shuffle(label=0) / local_replace(label=0) / 70-30 切分
  2. model（CoherenceClassifier·需 torch → 无 torch 自动 skip·用微型离线 encoder 不联网）
       实例化 + forward/predict_proba 形状 + save/load 往返 + coherence_meta.json 内容
  3. nn_coherence_bridge（env 门控·默认安全）
       RUOYU_NN_COHERENCE 未设 → enabled()=False；关时 predict_batch/predict_pairs 全 None；mock 解析
  4. coherence_scanner（mock bridge·3 类 advisory issue）
       COHERENCE_BREAK / COHERENCE_LOW_OVERALL / COHERENCE_UNSTABLE；关闭→空；全 advisory

确定性·零网络（mock subprocess / bridge·微型离线模型）。测试类形式：根 pytest 只调
模块级 test_* 函数·不实例化测试类 → fixture 用例仅在 pytest 下跑（与 test_surprisal_scanner 同范式）。
跑法：py -m pytest -q
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# ── sys.path：scanner + bridge 在 core/scripts；model + data_prep 在 core/ml/coherence ──
_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "core" / "scripts"
_ML_COHERENCE = _REPO / "core" / "ml" / "coherence"
for _p in (str(_SCRIPTS), str(_ML_COHERENCE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 核心脚本名唯一·torch-free → 直接 import（与 test_surprisal_scanner 同范式）
import coherence_scanner as cs        # noqa: E402
import nn_coherence_bridge as cb       # noqa: E402


def _load_by_path(unique_name: str, file_path: Path):
    """按显式路径加载并注册唯一模块名。

    防撞名：emotion_vad / quality_clf / style_embed / coherence 四条 ML 管线都有
    model.py / data_prep.py，裸 `import data_prep` 会被 sys.modules 缓存串味。
    """
    spec = importlib.util.spec_from_file_location(unique_name, str(file_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


# data_prep 纯 stdlib（无 torch）→ 模块顶层加载。
# 🔴 data_prep.py 在 import 时把 sys.stdout/stderr 重写成 TextIOWrapper(原 .buffer)（Windows CLI
# 中文兜底）。在 pytest 下这会把 capture buffer 包进 TextIOWrapper·GC 时连带关闭它 →「I/O operation
# on closed file」collection 崩。用一次性 BytesIO 流隔离该 import 期副作用·随后恢复（不碰 pytest
# capture·不影响 data_prep 功能）。根因建议：data_prep 应像 coherence_infer 那样把流重配挪进 main()。
_saved_streams = (sys.stdout, sys.stderr)
sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
try:
    data_prep = _load_by_path("coherence_data_prep", _ML_COHERENCE / "data_prep.py")
finally:
    sys.stdout, sys.stderr = _saved_streams

# torch + transformers 仅在 core/ml/.venv·系统 py(3.14) 无 → model 用例 skip
_HAS_TORCH = (importlib.util.find_spec("torch") is not None
              and importlib.util.find_spec("transformers") is not None)


# =====================================================================================
# 1. data_prep — 自监督连贯性数据准备
# =====================================================================================

import random as _random


def _para(doc: str, j: int) -> str:
    """构造够长（CJK ≥ min_para_cjk）的段落·不被碎段/标题过滤。"""
    return f"{doc}的第{j}段正文内容足够长确保通过碎段过滤"


class TestDataPrep:
    """CoUDA 双通道自监督：正例 + global_shuffle/local_replace 负例 + doc-level 70/30 切分。"""

    def test_positive_records_label_1(self):
        # _generate_for_docs 产正例（原文连续段落·label=1·augment_type=original）
        rng = _random.Random(0)
        docs = [("docA", [_para("docA", j) for j in range(8)])]
        recs = data_prep._generate_for_docs(docs, 5, 8, 0, 4, ["global", "local"], rng)
        pos = [r for r in recs if r["augment_type"] == "original"]
        assert len(pos) >= 1
        assert all(r["label"] == data_prep.LABEL_COHERENT for r in pos)  # 1=连贯
        src = set(docs[0][1])
        # 正例 text 各行均来自源文档（保留天然接续）
        assert all(all(line in src for line in r["text"].split("\n")) for r in pos)

    def test_global_shuffle_label_0_reordered(self):
        rng = _random.Random(1)
        window = [_para("d", j) for j in range(6)]
        shuffled = data_prep._global_shuffle(window, rng)
        assert shuffled is not None
        assert set(shuffled) == set(window)     # 同组段落（仅顺序变）
        assert shuffled != window                # 顺序确被打乱
        # 经 _generate_for_docs → label=0 / augment_type=global_shuffle
        recs = data_prep._generate_for_docs([("d", window)], 5, 8, 0, 4, ["global"], rng)
        gs = [r for r in recs if r["augment_type"] == "global_shuffle"]
        assert gs and all(r["label"] == data_prep.LABEL_INCOHERENT for r in gs)  # 0=不连贯

    def test_global_shuffle_single_paragraph_none(self):
        # 窗口 < 2 段无法打乱 → None
        assert data_prep._global_shuffle(["只有一段"], _random.Random(0)) is None

    def test_local_replace_label_0_cross_doc(self):
        rng = _random.Random(2)
        window = [_para("self", j) for j in range(5)]
        pool = [("other", _para("other", j)) for j in range(20)]   # 跨文池（doc_id 不同）
        replaced = data_prep._local_replace(window, pool, "self", 4, rng)
        assert replaced is not None
        assert len(replaced) == len(window)
        diffs = [i for i in range(len(window)) if replaced[i] != window[i]]
        assert len(diffs) == 1                                      # 恰好替换 1 段
        assert replaced[diffs[0]] in [p for _, p in pool]          # 替换段来自其他文档
        # 经 _generate_for_docs（两篇文档·跨文替换池）→ label=0 / augment_type=local_replace
        docs = [("self", window), ("other", [p for _, p in pool])]
        recs = data_prep._generate_for_docs(docs, 5, 8, 0, 4, ["local"], rng)
        lr = [r for r in recs if r["augment_type"] == "local_replace"]
        assert lr and all(r["label"] == data_prep.LABEL_INCOHERENT for r in lr)

    def test_local_replace_same_doc_only_none(self):
        # 池只含同 doc_id → 无法跨文替换（防泄漏）→ None
        rng = _random.Random(3)
        window = [_para("self", j) for j in range(5)]
        pool = [("self", p) for p in window]
        assert data_prep._local_replace(window, pool, "self", 4, rng) is None

    def test_extract_windows_size_bounds(self):
        rng = _random.Random(4)
        windows = data_prep._extract_windows([f"para_{i}" for i in range(20)], 5, 8, 0, rng)
        assert len(windows) > 0
        assert all(5 <= len(w) <= 8 for w in windows)

    def test_doc_level_70_30_split(self, tmp_path):
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        for i in range(10):  # 10 文档 × 12 段 → 充足窗口·doc-level 30% = 3 篇
            (in_dir / f"doc_{i}.txt").write_text(
                "\n".join(_para(f"doc{i}", j) for j in range(12)), encoding="utf-8")
        out_dir = tmp_path / "output"

        stats = data_prep.prepare_data(
            input_dir=str(in_dir), output_dir=str(out_dir),
            min_paras=5, max_paras=8, target_total=400, val_ratio=0.3, seed=42)

        assert "error" not in stats
        # 🔴 防泄漏核心 = doc-level 70/30 切分（同文档窗口不跨 split）·精确复刻实现公式
        n = stats["n_docs"]
        assert n == 10
        exp_val = max(1, min(n - 1, int(round(n * 0.3))))   # = 3
        assert stats["n_docs_val"] == exp_val
        assert stats["n_docs_train"] == n - exp_val
        assert stats["n_docs_train"] + stats["n_docs_val"] == n
        assert stats["n_train"] > 0 and stats["n_val"] > 0
        assert (out_dir / "train.jsonl").exists()
        assert (out_dir / "val.jsonl").exists()
        assert (out_dir / "data_stats.json").exists()

    def test_empty_input_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        stats = data_prep.prepare_data(input_dir=str(empty), output_dir=str(tmp_path / "o"))
        assert stats.get("error") == "no_documents"

    def test_collect_documents_filters_short(self, tmp_path):
        # 2 段（< min_paras=5）→ 滤掉；12 段 → 保留。min_para_cjk=4 / min_paras=5
        (tmp_path / "short.txt").write_text(_para("s", 0) + "\n" + _para("s", 1), encoding="utf-8")
        (tmp_path / "ok.txt").write_text(
            "\n".join(_para("ok", j) for j in range(12)), encoding="utf-8")
        docs = data_prep._collect_documents(str(tmp_path), "", 4, 5)
        ids = [doc_id for doc_id, _ in docs]
        assert any("ok.txt" in i for i in ids)
        assert not any("short.txt" in i for i in ids)


# =====================================================================================
# 2. model — CoherenceClassifier（需 torch → 无 torch 自动 skip·微型离线 encoder）
# =====================================================================================

@pytest.mark.skipif(not _HAS_TORCH,
                    reason="torch/transformers 仅在 core/ml/.venv·系统 py(3.14) 无 → skip")
class TestModel:
    """二分类连贯性模型定义 + 持久化往返。用微型 encoder_config → 离线·快·无下载。"""

    def _tiny(self):
        cm = _load_by_path("coherence_model_under_test", _ML_COHERENCE / "model.py")
        from transformers import BertConfig
        cfg = BertConfig(vocab_size=64, hidden_size=16, num_hidden_layers=1,
                         num_attention_heads=2, intermediate_size=32,
                         max_position_embeddings=32, type_vocab_size=2)
        model = cm.CoherenceClassifier(base_model="tiny-test-encoder",
                                       pooling="mean", encoder_config=cfg)
        return cm, model

    def test_instantiate(self):
        cm, model = self._tiny()
        assert cm.N_CLASSES == 2
        assert tuple(cm.LABEL_NAMES) == ("incoherent", "coherent")
        assert model.n_classes == 2
        assert model.head.out_features == 2

    def test_forward_shape(self):
        import torch
        _, model = self._tiny()
        model.eval()
        input_ids = torch.randint(0, 64, (2, 8))
        attn = torch.ones(2, 8, dtype=torch.long)
        with torch.no_grad():
            logits = model(input_ids, attn)
        assert tuple(logits.shape) == (2, 2)        # 原始 logits (B, 2)

    def test_predict_proba_sums_to_one(self):
        import torch
        _, model = self._tiny()
        model.eval()
        input_ids = torch.randint(0, 64, (3, 8))
        attn = torch.ones(3, 8, dtype=torch.long)
        with torch.no_grad():
            probs = model.predict_proba(input_ids, attn)
        assert tuple(probs.shape) == (3, 2)
        assert torch.allclose(probs.sum(dim=-1), torch.ones(3), atol=1e-5)

    def test_save_load_roundtrip(self, tmp_path):
        import torch
        cm, model = self._tiny()
        model.eval()
        input_ids = torch.randint(0, 64, (2, 8))
        attn = torch.ones(2, 8, dtype=torch.long)
        with torch.no_grad():
            logits_before = model(input_ids, attn)

        ckpt = tmp_path / "ckpt"
        model.save(str(ckpt), extra_meta={"test_key": "test_value"})
        # 落盘契约：权重 + meta + encoder config（离线自包含）
        assert (ckpt / "pytorch_model.bin").exists()
        assert (ckpt / "coherence_meta.json").exists()
        assert (ckpt / "config.json").exists()

        # coherence_meta.json 内容（落盘文件 + load 返回值双校验）
        meta_disk = json.loads((ckpt / "coherence_meta.json").read_text(encoding="utf-8"))
        assert meta_disk["task"] == "coherence_binary"
        assert meta_disk["n_classes"] == 2
        assert meta_disk["label_names"] == ["incoherent", "coherent"]
        assert meta_disk["base_model"] == "tiny-test-encoder"
        assert meta_disk["pooling"] == "mean"
        assert meta_disk["test_key"] == "test_value"

        loaded, meta = cm.CoherenceClassifier.load(str(ckpt))
        assert meta["task"] == "coherence_binary"
        assert loaded.n_classes == 2
        # 权重往返：同输入 → 同输出（eval·确定性）
        with torch.no_grad():
            logits_after = loaded(input_ids, attn)
        assert torch.allclose(logits_before, logits_after, atol=1e-5)

    def test_focal_loss_near_zero_on_correct(self):
        import torch
        cm, _ = self._tiny()
        logits = torch.tensor([[0.0, 10.0], [10.0, 0.0]])   # 强预测 class1 / class0
        targets = torch.tensor([1, 0])
        assert cm.focal_loss(logits, targets).item() < 0.01

    def test_bce_loss_near_zero_on_correct(self):
        import torch
        cm, _ = self._tiny()
        logits = torch.tensor([[0.0, 10.0], [10.0, 0.0]])
        targets = torch.tensor([1, 0])
        assert cm.bce_loss(logits, targets).item() < 0.01


# =====================================================================================
# 3. nn_coherence_bridge — env 门控 + 默认安全
# =====================================================================================

def _fake_run(out_lines):
    """伪 subprocess.run：解析 argv 的 --out，把 canned jsonl 写进去，返回 returncode=0。"""
    def runner(cmd, capture_output=True, timeout=None, env=None):
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.write_text(("\n".join(out_lines) + "\n") if out_lines else "", encoding="utf-8")

        class _R:
            returncode = 0
            stderr = b""
            stdout = b""
        return _R()
    return runner


def _force_enabled(monkeypatch):
    """强制 bridge 视为可用（不依赖真 venv/ckpt）。"""
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    monkeypatch.setattr(cb, "_venv_python", lambda: Path("py.exe"))
    monkeypatch.setattr(cb, "_resolve_ckpt", lambda: "ckpt")


class TestBridgeDisabled:
    """默认安全：env 未开 / 先决条件缺 → 全 None（调用方回退·绝不崩）。"""

    def test_enabled_false_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
        assert cb.enabled() is False

    def test_enabled_false_when_env_is_0(self, monkeypatch):
        monkeypatch.setenv("RUOYU_NN_COHERENCE", "0")
        assert cb.enabled() is False

    def test_predict_batch_all_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
        assert cb.predict_batch(["甲", "乙", "丙"]) == [None, None, None]

    def test_predict_pairs_all_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
        assert cb.predict_pairs([("a", "b"), ("c", "d")]) == [None, None]

    def test_predict_one_and_pair_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
        assert cb.predict_one("x") is None
        assert cb.predict_pair("a", "b") is None

    def test_empty_inputs(self):
        assert cb.predict_batch([]) == []
        assert cb.predict_pairs([]) == []

    def test_enabled_false_missing_venv(self, monkeypatch):
        monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
        monkeypatch.setattr(cb, "_venv_python", lambda: None)
        assert cb.enabled() is False

    def test_enabled_false_missing_ckpt(self, monkeypatch):
        monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
        monkeypatch.setattr(cb, "_venv_python", lambda: Path("py.exe"))
        monkeypatch.setattr(cb, "_resolve_ckpt", lambda: None)
        assert cb.enabled() is False


class TestBridgeParsing:
    """解析 + 失败安全（mock subprocess·零网络）。命中模型 = coherence_score 字段。"""

    def test_predict_pairs_model_hit(self, monkeypatch):
        _force_enabled(monkeypatch)
        out_lines = [
            json.dumps({"coherence_score": 0.92, "is_coherent": True, "source": "model"}),
            json.dumps({"coherence_score": 0.12, "source": "model"}),   # is_coherent 缺 → 由 score 推
        ]
        monkeypatch.setattr(cb.subprocess, "run", _fake_run(out_lines))
        res = cb.predict_pairs([("a", "b"), ("c", "d")])
        assert res[0]["coherence_score"] == 0.92 and res[0]["is_coherent"] is True
        assert res[1]["coherence_score"] == 0.12 and res[1]["is_coherent"] is False  # 0.12<0.5
        assert res[0]["source"] == "model"

    def test_predict_batch_model_hit(self, monkeypatch):
        _force_enabled(monkeypatch)
        out_lines = [json.dumps({"coherence_score": 0.8, "is_coherent": True, "source": "model"})]
        monkeypatch.setattr(cb.subprocess, "run", _fake_run(out_lines))
        assert cb.predict_batch(["单文本窗口"])[0]["coherence_score"] == 0.8

    def test_non_model_source_filtered(self, monkeypatch):
        _force_enabled(monkeypatch)
        out_lines = [json.dumps({"coherence_score": 0.7, "source": "unavailable"})]
        monkeypatch.setattr(cb.subprocess, "run", _fake_run(out_lines))
        assert cb.predict_batch(["x"]) == [None]   # 非模型 → None（让调用方回退）

    def test_null_score_filtered(self, monkeypatch):
        _force_enabled(monkeypatch)
        out_lines = [json.dumps({"coherence_score": None, "source": "model"})]
        monkeypatch.setattr(cb.subprocess, "run", _fake_run(out_lines))
        assert cb.predict_batch(["x"]) == [None]

    def test_count_mismatch_all_none(self, monkeypatch):
        _force_enabled(monkeypatch)
        out_lines = [json.dumps({"coherence_score": 0.5, "source": "model"})]  # 1 ≠ 2 输入
        monkeypatch.setattr(cb.subprocess, "run", _fake_run(out_lines))
        assert cb.predict_pairs([("a", "b"), ("c", "d")]) == [None, None]

    def test_nonzero_returncode_all_none(self, monkeypatch):
        _force_enabled(monkeypatch)

        def fail(cmd, capture_output=True, timeout=None, env=None):
            class _R:
                returncode = 1
                stderr = b"boom"
                stdout = b""
            return _R()
        monkeypatch.setattr(cb.subprocess, "run", fail)
        assert cb.predict_batch(["x", "y"]) == [None, None]

    def test_timeout_all_none(self, monkeypatch):
        _force_enabled(monkeypatch)

        def boom(*a, **k):
            raise cb.subprocess.TimeoutExpired(cmd="x", timeout=1)
        monkeypatch.setattr(cb.subprocess, "run", boom)
        assert cb.predict_pairs([("a", "b")]) == [None]


# =====================================================================================
# 4. coherence_scanner — 3 类 advisory issue（mock bridge）
# =====================================================================================

def _draft(n: int) -> str:
    """n 段草稿·一行一段（scanner 按 \\n 切段）。各段互异。"""
    return "\n".join(f"第{i + 1}段的正文内容是关于某个特定话题的描述" for i in range(n))


def _pairs_payload(scores):
    """构造 predict_pairs mock 返回值。

    🔴 scanner 经 _extract_coherence 读 r['coherence_score']（canonical 键·与 bridge/infer 一致）→
    mock 按此键返回，方能驱动 scanner 检测逻辑。
    """
    return [None if s is None else
            {"coherence_score": s, "is_coherent": s >= 0.5, "source": "model"} for s in scores]


class TestScanner:
    """scan_coherence 完整流程（mock nn_coherence_bridge.enabled + predict_pairs）。"""

    def test_coherence_break_detected(self):
        # 5 段 → 4 对·第 2 对(段2→段3)低连贯 < 0.3
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([0.8, 0.2, 0.7, 0.9])):
            issues = cs.scan_coherence(_draft(5), "")
        breaks = [i for i in issues if i["code"] == cs.COHERENCE_BREAK]
        assert len(breaks) == 1
        assert breaks[0]["gate_level"] == "advisory"
        assert breaks[0]["details"]["paragraph_from"] == 2
        assert breaks[0]["details"]["paragraph_to"] == 3
        assert breaks[0]["details"]["coherence"] == 0.2

    def test_coherence_low_overall_detected(self):
        # 全 0.4：均值<0.5(LOW)·≥0.3(无 BREAK)·方差 0(无 UNSTABLE)
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([0.4] * 5)):
            issues = cs.scan_coherence(_draft(6), "")
        codes = [i["code"] for i in issues]
        assert cs.COHERENCE_LOW_OVERALL in codes
        assert cs.COHERENCE_BREAK not in codes
        assert cs.COHERENCE_UNSTABLE not in codes
        low = [i for i in issues if i["code"] == cs.COHERENCE_LOW_OVERALL][0]
        assert low["details"]["avg_coherence"] < 0.5

    def test_coherence_unstable_detected(self):
        # 高低交替(0.95/0.35)·都 ≥0.3(无 BREAK)·均值 0.65≥0.5(无 LOW) → 只 UNSTABLE
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([0.95, 0.35, 0.95, 0.35, 0.95])):
            issues = cs.scan_coherence(_draft(6), "")
        codes = [i["code"] for i in issues]
        assert cs.COHERENCE_UNSTABLE in codes
        assert cs.COHERENCE_BREAK not in codes
        unstable = [i for i in issues if i["code"] == cs.COHERENCE_UNSTABLE][0]
        assert unstable["details"]["variance"] > 0.04

    def test_all_issues_advisory(self):
        # 全 0.1：每对 BREAK + LOW_OVERALL（多 issue）→ 全部 advisory
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([0.1] * 5)):
            issues = cs.scan_coherence(_draft(6), "")
        assert len(issues) > 0
        codes = {i["code"] for i in issues}
        assert cs.COHERENCE_BREAK in codes and cs.COHERENCE_LOW_OVERALL in codes
        for issue in issues:
            assert issue["gate_level"] == "advisory", \
                f"issue {issue['code']} gate_level={issue['gate_level']} 不是 advisory！"

    def test_no_issues_when_all_coherent(self):
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([0.9, 0.85, 0.92, 0.88, 0.91])):
            assert cs.scan_coherence(_draft(6), "") == []

    def test_empty_when_bridge_disabled_real_env(self, monkeypatch):
        # 真 bridge·RUOYU_NN_COHERENCE 未开 → enabled()=False → 空（北极星⑤无 NN 也能跑）
        monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
        assert cs.scan_coherence(_draft(8), "") == []

    def test_empty_when_bridge_disabled_mock(self):
        with mock.patch("nn_coherence_bridge.enabled", return_value=False):
            assert cs.scan_coherence(_draft(8), "") == []

    def test_empty_when_bridge_import_fails(self):
        with mock.patch.object(cs, "_load_bridge", return_value=None):
            assert cs.scan_coherence(_draft(8), "") == []

    def test_empty_when_all_bridge_results_none(self):
        # bridge 全降级（逐条 None）→ 静默空（不崩）
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload([None] * 7)):
            assert cs.scan_coherence(_draft(8), "") == []

    def test_too_few_paragraphs(self):
        with mock.patch("nn_coherence_bridge.enabled", return_value=True):
            assert cs.scan_coherence("只有一段", "") == []

    def test_window_scan_detects_low_window(self):
        # 10 段 → 9 对·前段低连贯 → 窗口级 COHERENCE_BREAK
        scores = [0.1, 0.15, 0.2, 0.1, 0.15, 0.9, 0.85, 0.9, 0.88]
        with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
             mock.patch("nn_coherence_bridge.predict_pairs",
                        return_value=_pairs_payload(scores)):
            issues = cs.scan_coherence_windows(_draft(10), "", window_size=6)
        assert any(i["code"] == cs.COHERENCE_BREAK for i in issues)
        assert all(i["gate_level"] == "advisory" for i in issues)


class TestScannerHelpers:
    """私有检测器 + 文本预处理 + 阈值覆盖直测（绕过 bridge·精确锁定）。"""

    def test_detect_break_pairs(self):
        paras = [f"p{i}" for i in range(4)]
        issues = cs._detect_break_pairs(paras, [0.9, 0.2, 0.95], cs.COHERENCE_BREAK_FLOOR)
        assert len(issues) == 1
        assert issues[0]["code"] == cs.COHERENCE_BREAK
        assert issues[0]["details"]["paragraph_from"] == 2

    def test_detect_break_skips_none(self):
        paras = [f"p{i}" for i in range(4)]
        issues = cs._detect_break_pairs(paras, [None, 0.1, 0.9], 0.3)
        assert len(issues) == 1

    def test_detect_low_overall_needs_min_points(self):
        # 有效点 < _MIN_POINTS_FOR_AGGREGATE → 不报（无统计意义）
        assert cs._detect_low_overall([0.1, 0.1], 0.5) == []

    def test_detect_unstable_stable_ok(self):
        assert cs._detect_unstable([0.70, 0.72, 0.69, 0.71], cs.COHERENCE_VARIANCE_CEIL) == []

    def test_extract_coherence(self):
        assert cs._extract_coherence({"coherence_score": 0.7, "source": "model"}) == 0.7
        assert cs._extract_coherence({"coherence_score": 1}) == 1.0     # int → float
        assert cs._extract_coherence(None) is None
        assert cs._extract_coherence({"coherence_score": True}) is None  # bool 排除
        assert cs._extract_coherence({}) is None

    def test_split_paragraphs(self):
        assert cs._split_paragraphs("p1\np2\n\np3") == ["p1", "p2", "p3"]

    def test_strip_changes(self):
        assert cs._strip_changes("正文内容\n\n---CHANGES---\n变更记录") == "正文内容"

    def test_overrides_from_project(self, tmp_path):
        db = tmp_path / "_数据库"
        db.mkdir()
        (db / "style_scanner_overrides.json").write_text(json.dumps(
            {"coherence_scanner": {"break_floor": 0.5, "low_overall_floor": 0.7,
                                   "variance_ceil": 0.02}}, ensure_ascii=False), encoding="utf-8")
        th = cs._resolve_thresholds(str(tmp_path))
        assert th["break_floor"] == 0.5
        assert th["low_overall_floor"] == 0.7
        assert th["variance_ceil"] == 0.02


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
