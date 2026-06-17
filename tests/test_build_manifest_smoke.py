#!/usr/bin/env python3
"""build_manifest 入口冒烟 + scaffold↔build_manifest 契约测试。

背景（sweep2 workflow wa28emn1k）：build_manifest 含 40+ 个 _collect_* collector，主入口
build_manifest() 此前无专属测试。本测试在 scaffold_subsystems emit 出的 34 骨架项目上跑通
build_manifest()，钉死：(1) 主入口在最小骨架上不崩、返回 dict；(2) scaffold 产出可被
build_manifest 消费（契约耦合·防骨架与消费方漂移）。build_manifest 纯确定性（0 gen-model 调用）。
"""
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402


def _scaffolded_project(tmp: Path) -> Path:
    proj = tmp / "smoke_book"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    rc = scaf.cmd_emit([str(proj)])
    assert rc == 0, "scaffold emit 应成功"
    return proj


def test_build_manifest_runs_on_scaffolded_skeleton():
    """主入口在 34 骨架最小项目上跑通·返回非空 dict·不崩。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _scaffolded_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        assert isinstance(m, dict), f"build_manifest 应返回 dict，得 {type(m)}"
        assert m, "manifest 不应为空"


def test_build_manifest_idempotent_same_input():
    """同输入两次 build_manifest 结构稳定（确定性·无随机/时间依赖泄漏）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _scaffolded_project(Path(tmp))
        m1 = bm.build_manifest(proj, 1)
        m2 = bm.build_manifest(proj, 1)
        assert set(m1.keys()) == set(m2.keys()), "两次 manifest 顶层键应一致（确定性）"


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
