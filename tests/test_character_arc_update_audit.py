"""character_arc_update.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 两处确定性数据投影健壮性修复（北极星⑤：advisory 状态滚动·不干涉创作判断）：

  · [L32] find_current_stage：stages_by_chapter 含非数字键（'_doc'/'_note'/'comment'/''）时
    旧代码 `int(k)` 无守卫 → ValueError 崩溃。修复加 `str(k).isdigit()` 过滤，对齐
    build_manifest.py:1470。守护点：非数字键被跳过且不崩、数字键照常解析最近 ≤ch 的阶段。

  · [L54] main() 角色表三 schema 归一化（对齐 build_manifest.py:1435-1481 /
    cluster_emergence_engine.py:617-623）：
      (A) {"characters": {name:{...}}}  旧唯一能跑形态
      (B) {"arcs": {name:{...}}}        旧 .get("characters") 返回 {} → 永久静默 no-op
      (C) {"characters": [ {...} ]}     旧对 list 调 .items() → AttributeError 崩
    修复：解析后统一成 name→dict 再遍历。守护点：三态全不崩 + A/B/C 都能滚 stage；
    list 内非 dict 元素被跳过；缺/空角色表安全 no-op。

跑法：PYTHONIOENCODING=utf-8 python tests/test_character_arc_update_audit.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import character_arc_update as cau  # noqa: E402

_MODULE_PATH = _SCRIPTS / "character_arc_update.py"
_INTERNAL_ENV_NAME = "RUOYUAI_CLUSTER_STATE_INTERNAL"


# ============================================================
# [L32] find_current_stage — 非数字键守卫
# ============================================================

def test_find_stage_basic_resolution():
    """数字键正常：最近一个 ≤ch 的 key 决定阶段。"""
    sbc = {"1": "lie", "8": "lie_cracking", "14": "want_threatened"}
    assert cau.find_current_stage(sbc, 5) == "lie"
    assert cau.find_current_stage(sbc, 10) == "lie_cracking"
    assert cau.find_current_stage(sbc, 99) == "want_threatened"


def test_find_stage_before_any_key_returns_pre_start():
    """ch 早于所有阶段起点 → pre_start。"""
    sbc = {"5": "lie", "8": "lie_cracking"}
    assert cau.find_current_stage(sbc, 1) == "pre_start"


def test_find_stage_nondigit_keys_do_not_crash():
    """[L32 核心] 含注解键（_doc/_note/comment/空串）不再 ValueError 崩溃，且被跳过。"""
    sbc = {
        "_doc": "这是给弱模型的说明，不是阶段映射",
        "_note": "x",
        "comment": "y",
        "": "z",
        "1": "lie",
        "8": "lie_cracking",
    }
    # 旧代码此处会因 int('_doc') 抛 ValueError。
    assert cau.find_current_stage(sbc, 10) == "lie_cracking"
    # 非数字键被过滤：只剩 1/8 参与计算。
    assert cau.find_current_stage(sbc, 5) == "lie"


def test_find_stage_only_nondigit_keys_returns_pre_start():
    """全是非数字键 → valid 为空 → pre_start（不崩）。"""
    sbc = {"_doc": "说明", "comment": "注释"}
    assert cau.find_current_stage(sbc, 7) == "pre_start"


# ============================================================
# [L54] main() 三 schema 归一化 —— 经子进程端到端驱动
# ============================================================

def _run_on(arc_obj, ch: int):
    """把 arc_obj 写进临时项目，跑 character_arc_update <项目> <ch>，回读 arc JSON + 退出码。

    返回 (returncode, stderr, arc_after_dict)。子进程驱动 = 复刻 save_state_updates.py:86
    的真实调用路径（它把 rc!=0 转模块失败）。
    """
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        arc_path = db / "character_arc_state.json"
        arc_path.write_text(json.dumps(arc_obj, ensure_ascii=False), encoding="utf-8")
        env = {
            **_os_environ(),
            "PYTHONIOENCODING": "utf-8",
            _INTERNAL_ENV_NAME: "1",
        }
        cp = subprocess.run(
            [sys.executable, str(_MODULE_PATH), str(proj), str(ch)],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        after = json.loads(arc_path.read_text(encoding="utf-8"))
        return cp.returncode, (cp.stderr or ""), after


def _os_environ():
    import os
    return dict(os.environ)


def _stage_of(node: dict) -> str:
    """从角色节点的 current_stage_at_ch='ch:stage' 取 stage 段。"""
    raw = node.get("current_stage_at_ch") or ""
    return raw.split(":", 1)[1] if ":" in raw else ""


def test_schema_A_characters_dict_rolls_stage():
    """schema A：characters={name:{...}}（旧唯一能跑形态）→ 正常滚 stage、exit 0。"""
    arc = {
        "characters": {
            "陆衍": {
                "stages_by_chapter": {"1": "lie", "8": "lie_cracking", "14": "want_threatened"},
            }
        }
    }
    rc, err, after = _run_on(arc, 10)
    assert rc == 0, err
    assert "Traceback" not in err, err
    assert _stage_of(after["characters"]["陆衍"]) == "lie_cracking"
    assert after["characters"]["陆衍"]["_last_updated_at_ch"] == 10


def test_schema_B_arcs_dict_rolls_stage_not_silent_noop():
    """[L54] schema B：{"arcs":{name:{...}}}（无 characters 键）→ 旧代码永久静默 no-op；
    修复后 arcs 里带 stages_by_chapter 的角色照样滚 stage。"""
    arc = {
        "arcs": {
            "陆建国": {
                "framework": "mckee_arc",
                "stages_by_chapter": {"1": "protect", "6": "reveal", "12": "collapse"},
            }
        }
    }
    rc, err, after = _run_on(arc, 7)
    assert rc == 0, err
    assert "Traceback" not in err, err
    # 旧代码：arc.get("characters",{}) → {} → 0 更新 → 该断言失败。
    assert _stage_of(after["arcs"]["陆建国"]) == "reveal"


def test_schema_C_characters_list_no_attribute_error():
    """[L54 核心] schema C：characters=[{...}]（list）→ 旧代码 .items() AttributeError 崩（exit 1）；
    修复后用 name/id 归一成 dict，正常滚 stage、exit 0。"""
    arc = {
        "characters": [
            {"name": "陆衍", "id": "luyan",
             "stages_by_chapter": {"1": "lie", "8": "lie_cracking"}},
        ]
    }
    rc, err, after = _run_on(arc, 9)
    assert rc == 0, err
    assert "Traceback" not in err, err
    assert "AttributeError" not in err, err
    # 回读 list 形态：找到 name==陆衍 的条目，验证已滚到 lie_cracking。
    node = next(c for c in after["characters"] if c.get("name") == "陆衍")
    assert _stage_of(node) == "lie_cracking"


def test_schema_C_list_skips_non_dict_and_keyless_entries():
    """[L54] list 内非 dict 元素 / 无 name 无 id 的 dict 被安全跳过（不崩、不误写）。"""
    arc = {
        "characters": [
            "我是一个杂质字符串",            # 非 dict → 跳过
            123,                              # 非 dict → 跳过
            {"stages_by_chapter": {"1": "x"}},  # 无 name/id → 归一时丢弃
            {"name": "重黎", "stages_by_chapter": {"1": "intro", "5": "rise"}},
        ]
    }
    rc, err, after = _run_on(arc, 6)
    assert rc == 0, err
    assert "Traceback" not in err, err
    # 回读时也要跳过非 dict 杂质（源文件原样保留了它们；只验证目标角色被正确滚动）。
    node = next(c for c in after["characters"]
                if isinstance(c, dict) and c.get("name") == "重黎")
    assert _stage_of(node) == "rise"


def test_schema_B_arcs_without_stages_is_safe_noop():
    """schema B（v2 用 stages[].active_cluster 而非 stages_by_chapter）→ stages 为空被
    L74 `if not stages: continue` 自然跳过：不崩、不引入错误更新（triage 修复范围说明）。"""
    arc = {
        "arcs": {
            "林若昭": {
                "framework": "truby_arc",
                "current_stage": "stage_1_supervisor",
                "stages": [{"id": "stage_1_supervisor", "active_cluster": ["cluster_001"]}],
            }
        }
    }
    rc, err, after = _run_on(arc, 6)
    assert rc == 0, err
    assert "Traceback" not in err, err
    # 没 stages_by_chapter → 不应写出 current_stage_at_ch（不伪造）。
    assert "current_stage_at_ch" not in after["arcs"]["林若昭"]


def test_nondigit_keys_in_main_path_no_crash():
    """[L32×L54 合验] 真实 schema A 角色的 stages_by_chapter 混入 _doc 注解键，
    main 全链路（subprocess）不崩、stage 正确滚动。"""
    arc = {
        "characters": {
            "陆衍": {
                "stages_by_chapter": {
                    "_doc": "弱模型说明：键是章号、值是阶段 id",
                    "1": "lie",
                    "8": "lie_cracking",
                },
            }
        }
    }
    rc, err, after = _run_on(arc, 10)
    assert rc == 0, err
    assert "ValueError" not in err, err
    assert _stage_of(after["characters"]["陆衍"]) == "lie_cracking"


def test_empty_characters_dict_safe_noop():
    """空角色表（{"characters": {}}）→ 0 更新、exit 0、不崩。"""
    rc, err, after = _run_on({"characters": {}}, 5)
    assert rc == 0, err
    assert "Traceback" not in err, err


def test_unexpected_characters_type_safe_noop():
    """characters 既非 dict 也非 list（如字符串）→ chars_map={} → 安全 no-op、不崩。"""
    rc, err, after = _run_on({"characters": "坏数据"}, 5)
    assert rc == 0, err
    assert "Traceback" not in err, err


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
