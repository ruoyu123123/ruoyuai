# -*- coding: utf-8 -*-
"""润色引号占比守恒（quote guard）回归锁。

实证根因（长恨 cluster_001 · 对话占比专项调查定谳）：gemini 润色把 64% 的引号心声改写成
无引号自由间接引语（11.74%→4.24%），总对话占比 16.5%→8.7% 腰斩——等体量润色的字数守恒带
只管字数、不管引号承载的对话量。gemini 只被授权等体量润色，改叙述模式=越权，须确定性核修。

本文件锁死：
  ① 守恒常量（floor 0.85 与字数守恒带下限同源 · 绝对降幅门槛 0.02 防近零噪声）；
  ② quote_ratio_violated 判定口径（含调查实证数字的真实案例）；
  ③ polish_pipeline 行为：净降→带指令重试 1 次；重试恢复→收润色稿；重试仍降→
     整段保留 Claude 亲笔原稿（亲笔优先）；未触发→零额外调用；
  ④ 遥测链：polish_trace.quote_guard → save_output 写进 dialogue_telemetry.quote_guard
     （schema 封闭清单可过校验）；changes_io.sync_cjk_actual 回写 polished_dialogue_* 磁盘真值；
  ⑤ 润色 prompt 常驻引号守恒指令。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import gen_writer as gw  # noqa: E402
import changes_io  # noqa: E402
import cluster_state_delta as csd  # noqa: E402
from style_analyzer import calc_dialogue_ratio  # noqa: E402
from text_metrics import count_cjk  # noqa: E402

SCHEMA = json.loads(
    (ROOT / "core" / "claude-home" / "schemas" / "changes_schema.json").read_text(
        encoding="utf-8"))

# 含真实对白的场景稿：剥掉弯引号后 CJK 不变（字数守恒 1.0）但引号占比归零——
# 精准复刻「引号心声/对白转自由间接引语」的越权改写形态。
_SRC = "他望着山口没有说话。\n\n“你们都与我记着，上古的人不肯低头。”\n\n她伸手按住他的腕子。"
_STRIPPED = _SRC.replace("“", "").replace("”", "")


# ---------- ① 守恒常量 ----------

def test_quote_guard_constants():
    """floor 与段级字数守恒带下限同源；绝对降幅门槛防近零对话段比值噪声。"""
    assert gw.POLISH_QUOTE_FLOOR == 0.85
    assert gw.POLISH_QUOTE_FLOOR == gw.POLISH_CJK_LOW
    assert gw.POLISH_QUOTE_MIN_ABS_DROP == 0.02


# ---------- ② quote_ratio_violated 判定口径 ----------

@pytest.mark.parametrize("src_r,out_r,expect", [
    # 调查实证：Claude 草稿 16.51% → gemini 终稿 8.69%（净降 47%）必须触发
    (0.1651, 0.0869, True),
    # 引号心声被砍 64%（11.74%→4.24%）必须触发
    (0.1174, 0.0424, True),
    # 无净降 / 净增：不触发
    (0.20, 0.20, False),
    (0.20, 0.30, False),
    # 净降在 floor 带内（0.95x）：不触发
    (0.20, 0.19, False),
    # 相对降幅大但绝对降幅 < 0.02（近零对话段噪声）：不触发
    (0.01, 0.005, False),
    # 纯叙述段（src=0）：不触发
    (0.0, 0.0, False),
])
def test_quote_ratio_violated(src_r, out_r, expect):
    assert gw.quote_ratio_violated(src_r, out_r) is expect


# ---------- ③ polish_pipeline 行为 ----------

def _run_pipeline(monkeypatch, replies):
    """跑单场景 polish_pipeline：build_prompt/call_gen_model 打桩，replies 按序弹出。"""
    calls = {"n": 0}

    def fake_build_prompt(project_root, cluster_id, ch_start, polish_view=None):
        assert polish_view is not None
        return "SYS", "USER", {"snippet_seed_mode": "off", "injected": False}

    def fake_call(loader, system, user, creative=False):
        idx = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return replies[idx], types.SimpleNamespace(name="fake", model="fake-model")

    monkeypatch.setattr(gw, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(gw, "call_gen_model", fake_call)
    body, _profile, trace = gw.polish_pipeline(
        None, Path("."), 1, 1, [("scene_00.txt", _SRC)])
    return body, trace, calls["n"]


def test_no_violation_no_extra_call(monkeypatch):
    """润色稿引号守恒 → 不触发重试，单场景只调 1 次模型。"""
    body, trace, n_calls = _run_pipeline(monkeypatch, [_SRC])
    assert n_calls == 1
    sc = trace["scenes"][0]
    assert sc["quote_retried"] is False
    assert sc["quote_kept_claude"] is False
    assert trace["quote_guard"]["scenes_triggered"] == 0
    assert trace["quote_guard"]["scenes_kept_claude"] == 0


def test_violation_retry_recovers(monkeypatch):
    """首稿剥引号（净降超界）→ 带指令重试 1 次 → 重试稿恢复引号 → 收重试稿。"""
    body, trace, n_calls = _run_pipeline(monkeypatch, [_STRIPPED, _SRC])
    assert n_calls == 2
    assert body == _SRC
    sc = trace["scenes"][0]
    assert sc["quote_retried"] is True
    assert sc["quote_kept_claude"] is False
    assert sc["out_quote_ratio"] == round(calc_dialogue_ratio(_SRC), 4)
    qg = trace["quote_guard"]
    assert qg == {"floor_ratio": 0.85, "min_abs_drop": 0.02,
                  "scenes_triggered": 1, "scenes_retried": 1, "scenes_kept_claude": 0}


def test_violation_retry_still_drops_keeps_claude(monkeypatch):
    """重试仍剥引号 → 整段保留 Claude 亲笔原稿（亲笔优先·不收越权润色稿）。"""
    body, trace, n_calls = _run_pipeline(monkeypatch, [_STRIPPED, _STRIPPED])
    assert n_calls == 2
    assert body == _SRC, "重试仍净降必须回退 Claude 原段，不得收剥引号稿"
    sc = trace["scenes"][0]
    assert sc["quote_kept_claude"] is True
    assert sc["ratio"] == 1.0
    assert trace["quote_guard"]["scenes_kept_claude"] == 1


def test_retry_must_also_hold_word_band(monkeypatch):
    """引号守恒重试稿字数出带 → 同样拒收，保留 Claude 原段（双守恒都要过）。"""
    bloated = _SRC * 3  # 引号占比守恒但字数 3.0x 远超 [0.85, 1.30]
    body, trace, _ = _run_pipeline(monkeypatch, [_STRIPPED, bloated])
    assert body == _SRC
    assert trace["scenes"][0]["quote_kept_claude"] is True


# ---------- ④ 遥测链：quote_guard 进 dialogue_telemetry · polished_* 磁盘真值 ----------

def test_save_output_writes_quote_guard_into_dialogue_telemetry():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        qg = {"floor_ratio": 0.85, "min_abs_drop": 0.02,
              "scenes_triggered": 1, "scenes_retried": 1, "scenes_kept_claude": 1}
        ptrace = {"mode": "per_scene_polish_v29", "scenes": [],
                  "conservation_band": [0.85, 1.30], "quote_guard": qg}
        gw.save_output(root, 1, _SRC, {"self_eval": {"waivers": []}}, 1,
                       types.SimpleNamespace(name="fake", model="fake-model"),
                       polish_trace=ptrace)
        changes = json.loads(
            (root / "章节" / "cluster_001_draft" / "cluster_001_changes.json").read_text(
                encoding="utf-8"))
        dt = changes["self_eval"]["dialogue_telemetry"]
        assert dt["quote_guard"] == qg
        # 落盘产物必须过封闭 schema（G2 契约·自造字段 = save-state exit 2 FATAL）
        csd.validate_schema(changes, SCHEMA)


def test_sync_cjk_actual_writes_polished_dialogue_truth():
    """changes_io 唯一回写入口：polished_dialogue_* 与字数同刻对齐磁盘草稿真值。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "章节" / "cluster_002_draft"
        d.mkdir(parents=True)
        draft = d / "cluster_002_draft.txt"
        draft.write_text(_SRC, encoding="utf-8")
        (d / "cluster_002_changes.json").write_text(
            json.dumps({"self_eval": {"waivers": [], "dialogue_telemetry": {
                "polished_dialogue_ratio": 0.9999}}}, ensure_ascii=False),
            encoding="utf-8")
        out = changes_io.sync_cjk_actual(draft)
        dt = out["changes"]["self_eval"]["dialogue_telemetry"]
        assert dt["polished_dialogue_ratio"] == round(calc_dialogue_ratio(_SRC), 4)
        expected_cjk = round(calc_dialogue_ratio(_SRC) * count_cjk(_SRC))
        assert dt["polished_dialogue_cjk"] == expected_cjk
        csd.validate_schema(out["changes"], SCHEMA)


# ---------- ⑤ 润色 prompt 常驻引号守恒指令 ----------

def test_polish_tail_contains_quote_conservation_instruction(monkeypatch):
    monkeypatch.setenv("SNIPPET_SEED_MODE", "off")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "_数据库"
        db.mkdir(parents=True)
        (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}),
                                      encoding="utf-8")
        (db / "事件簇.json").write_text(json.dumps({"clusters": []}),
                                        encoding="utf-8")
        _, user, _ = gw.build_prompt(root, 1, 1,
                                     polish_view={"idx": 0, "total": 1,
                                                  "scene_text": _SRC})
        assert "引号守恒" in user
        assert "自由间接引语" in user


# ---------- 作者档对话双配比透传（prompt 侧） ----------

def test_author_para_dialogue_reads_split_ratios():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td)
        (db / "作者风格.json").write_text(json.dumps({"quantitative": {
            "dialogue_ratio": {"mean": 0.3062},
            "dialogue_only_ratio": {"mean": 0.2045},
            "inner_monologue_ratio": {"mean": 0.1017},
            "single_sentence_para_ratio": {"mean": 0.1788},
        }}, ensure_ascii=False), encoding="utf-8")
        got = gw._author_para_dialogue(db)
        assert got["dialogue_only"] == 0.2045
        assert got["inner_mono"] == 0.1017


def test_para_contract_line_has_dual_ratio_ban():
    line = gw._para_contract_line({"single": 0.18, "para_mean": 97.0,
                                   "dialogue": 0.3062,
                                   "dialogue_only": 0.2045, "inner_mono": 0.1017})
    assert "禁止用引号心声顶替外部对白" in line
    assert "20%" in line  # dialogue_only 主体配比下达
    assert "自由间接引语" in line
