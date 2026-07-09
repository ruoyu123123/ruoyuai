#!/usr/bin/env python3
"""伏笔明暗线隔离回归锁（2026-06-28）。

防 gemini 提前泄露暗线（hidden_payoff / 未到期 secret）：build_manifest 注入写手的 manifest
只能见明线（surface_clue），未到触发时机的秘密内容绝不出现；到 trigger_cluster 才暴露 hidden_payoff。

钉死：
  1. due_foreshadowing() 不再把 hidden_secrets 内容塞进 result（永远空列表）；未到期 secret 只
     计 pending_secret_count；到揭晓时机的 secret 进 reveal_this_ch。
  2. 整份 manifest 序列化后 **不含** 未到期 secret 内容 / plant 的 hidden_payoff。
  3. event_cluster_context.foreshadowing_to_plant 只有 surface_clue（剥离 hidden_payoff）。
  4. event_cluster_context.foreshadowing_to_callback 到 trigger_cluster 时含 hidden_payoff + reveal_directive。
  5. 旧格式（纯字符串 / desc 字段 / 无 hidden_payoff）兼容读·零回归。

纯确定性（0 gen-model 调用）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402


# ---- 秘密内容标记串（只在伏笔表/事件簇里出现，绝不该泄漏进写手 manifest）----
PENDING_SECRET_BODY = "PENDINGSECRETBODY_校长是怪谈本体"
PENDING_SECRET_PAYOFF = "PENDINGPAYOFF_校长夜里吞噬掉队学生"
PLANT_HIDDEN_PAYOFF = "SECRETLAMPISPORTAL_那盏应急灯是通往B区的开关"
CALLBACK_HIDDEN_PAYOFF = "PAYOFFFOOTSTEP_脚步声是已死的前任班主任"
REVEAL_SECRET_BODY = "REVEALSECRETBODY_主角其实早已死亡"


def _make_project(tmp: Path) -> Path:
    proj = tmp / "fs_isolation_book"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    rc = scaf.cmd_emit([str(proj)])
    assert rc == 0, "scaffold emit 应成功"
    db = proj / "_数据库"

    # 2026-07：auto_fate_draw 收编为 cluster-write step1 required 生产者后，
    # build_manifest 硬要求 .manifest/ch_<NNN>_fate_draw_decision.json（缺失即 RuntimeError）。
    # 写 not_required 模拟 step1 已按正式链路执行（本文件只测伏笔明暗线隔离，不测抽签本身）。
    manifest_dir = db / ".manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "ch_001_fate_draw_decision.json").write_text(json.dumps({
        "_schema": "fate_draw_decision_v1",
        "producer": "auto_fate_draw.py",
        "chapter": 1,
        "status": "not_required",
        "reason": "测试夹具：事件池为骨架空池，无可抽事件",
    }, ensure_ascii=False), encoding="utf-8")

    # 事件簇：cluster_001 active 非空 brief，带明暗线拆分的 plant + callback
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{
            "cluster_id": "cluster_001",
            "status": "in_progress",
            "chapter_range": [1, 5],
            "scope_summary": "主角进入育新中学，初遇规则怪谈的诡异。",
            "scene_storyboard": [
                {"ch": 1, "title": "灾难开场", "key_events": "走廊灯全灭",
                 "characters": ["林越"]},
            ],
            "foreshadowing_to_plant": [
                {"fs_id": "FS_101", "surface_clue": "墙角那盏总是亮着的应急灯",
                 "hidden_payoff": PLANT_HIDDEN_PAYOFF,
                 "trigger_cluster": "cluster_004", "tier": "A"},
            ],
            "foreshadowing_to_callback": [
                {"fs_id": "FS_050", "surface_clue": "走廊尽头的脚步声",
                 "hidden_payoff": CALLBACK_HIDDEN_PAYOFF,
                 "trigger_cluster": "cluster_001", "tier": "A"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    # 伏笔表：一条未到期 hidden secret（cluster_009）+ 一条到揭晓时机 secret（cluster_001）
    (db / "伏笔表.json").write_text(json.dumps({
        "promises": [],
        "deadlines": [],
        "pledges": [],
        "secrets": [
            {"id": "SEC_pending", "secret": PENDING_SECRET_BODY,
             "hidden_payoff": PENDING_SECRET_PAYOFF,
             "status": "hidden", "reveal_at_cluster": "cluster_009"},
            {"id": "SEC_reveal", "secret": REVEAL_SECRET_BODY,
             "status": "hidden", "reveal_at_cluster": "cluster_001"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return proj


def test_due_foreshadowing_hides_pending_secret_content():
    """due_foreshadowing：hidden_secrets 永远空·未到期只计数·到期进 reveal_this_ch·内容不入 result。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        s = bm.DatabaseScanner(proj, 1)
        due = s.due_foreshadowing()

        assert due["hidden_secrets"] == [], "hidden_secrets 必须为空（不再装秘密内容）"
        assert due["pending_secret_count"] == 1, f"未到期 secret 应计 1，得 {due['pending_secret_count']}"
        assert len(due["reveal_this_ch"]) == 1, "cluster_001 揭晓的 secret 应进 reveal_this_ch"

        blob = json.dumps(due, ensure_ascii=False)
        assert PENDING_SECRET_BODY not in blob, "未到期 secret 正文泄漏进 due"
        assert PENDING_SECRET_PAYOFF not in blob, "未到期 secret 的 hidden_payoff 泄漏进 due"


def test_manifest_excludes_pending_secret_and_plant_payoff():
    """整份 manifest 不含未到期 secret 内容 + plant 的 hidden_payoff。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        assert m.get("preflight", {}).get("passed", True), f"preflight 不应失败: {m.get('preflight')}"
        blob = json.dumps(m, ensure_ascii=False)

        # 泄漏红线：未到期秘密内容 + 埋设暗线绝不出现
        assert PENDING_SECRET_BODY not in blob, "未到期 secret 正文泄漏进 manifest"
        assert PENDING_SECRET_PAYOFF not in blob, "未到期 secret hidden_payoff 泄漏进 manifest"
        assert PLANT_HIDDEN_PAYOFF not in blob, "埋设伏笔 hidden_payoff 泄漏进 manifest"

        fsum = m["foreshadowing_summary"]
        assert fsum["pending_secret_count"] == 1, f"pending_secret_count 应为 1，得 {fsum}"
        assert fsum["must_reveal_this_ch"] == 1, "cluster_001 到期揭晓应计入 must_reveal_this_ch"


def test_manifest_plant_only_surface_callback_reveals_payoff():
    """plant 只注入 surface_clue（剥离 hidden_payoff）；callback 到 trigger_cluster 暴露 hidden_payoff + reveal 指令。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        ecc = m["event_cluster_context"]
        assert ecc.get("mode") == "on", f"事件簇 context 应 mode=on，得 {ecc.get('mode')}"

        plant = ecc["foreshadowing_to_plant"]
        assert len(plant) == 1
        assert plant[0].get("surface_clue") == "墙角那盏总是亮着的应急灯", "明线 surface_clue 应保留"
        assert "hidden_payoff" not in plant[0], "埋设侧必须剥离 hidden_payoff"
        assert plant[0].get("fs_id") == "FS_101", "fs_id 等非秘密字段应保留"

        cb = ecc["foreshadowing_to_callback"]
        assert len(cb) == 1
        # callback 的 trigger_cluster == 当前 cluster_001 → 暴露暗线让写手兑现
        assert cb[0].get("hidden_payoff") == CALLBACK_HIDDEN_PAYOFF, "到 trigger_cluster 的 callback 应暴露 hidden_payoff"
        assert "reveal_directive" in cb[0], "应注入「现在揭晓/兑现」reveal_directive"
        assert "FS_050" in cb[0]["reveal_directive"], "reveal_directive 应点名 fs_id"


def test_callback_strips_payoff_when_not_at_trigger_cluster():
    """callback 列出但 trigger_cluster 指向别的 cluster（未到期）→ 剥离 hidden_payoff 防提前泄露。"""
    items = [{"fs_id": "FS_999", "surface_clue": "门后的影子",
              "hidden_payoff": "NOTYET_影子是分身", "trigger_cluster": "cluster_005"}]
    out = bm._resolve_foreshadowing_to_callback(items, "cluster_001")
    assert out[0].get("surface_clue") == "门后的影子"
    assert "hidden_payoff" not in out[0], "未到 trigger_cluster 的 callback 不得暴露 hidden_payoff"
    assert "reveal_directive" not in out[0]


def test_legacy_format_compat_no_regression():
    """旧格式兼容：纯字符串 / desc 字段 / 无 hidden_payoff → 不崩·surface_clue 回填·零内容丢失。"""
    # 纯字符串 → 包成 surface_clue
    out = bm._sanitize_foreshadowing_to_plant(["一把生锈的钥匙"])
    assert out == [{"surface_clue": "一把生锈的钥匙"}]

    # 旧 dict（desc，无 hidden_payoff）→ 原字段保留 + surface_clue 回填
    out = bm._sanitize_foreshadowing_to_plant([{"id": "FS_old", "description": "旧伏笔描述", "tier": 1}])
    assert out[0]["id"] == "FS_old"
    assert out[0]["tier"] == 1
    assert out[0]["surface_clue"] == "旧伏笔描述"

    # 已有 surface_clue 不被 desc 覆盖
    out = bm._sanitize_foreshadowing_to_plant(
        [{"surface_clue": "明线", "description": "别用这个", "hidden_payoff": "暗线"}])
    assert out[0]["surface_clue"] == "明线"
    assert "hidden_payoff" not in out[0]

    # None / 空 → 空列表
    assert bm._sanitize_foreshadowing_to_plant(None) == []
    assert bm._resolve_foreshadowing_to_callback(None, "cluster_001") == []


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
