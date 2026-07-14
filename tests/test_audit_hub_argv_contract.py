# -*- coding: utf-8 -*-
"""🔴 audit_hub cluster 模式 argv ↔ scanner argparse 契约回归锁（G3·2026-07-14）。

病史：audit_hub cluster 模式给 spatial_continuity_scanner 传 `--cluster cluster_001`（带值），
而该 scanner 的 `--cluster` 定义成 action='store_true'（开关）→ argparse exit 2
「unrecognized arguments」→ 该 scanner 在 cluster 主链上【从未产出过结果】。audit_hub 的
ok_set={0,1} 容不下 exit 2，结果被静默丢弃却仍报「审计完整」。

这类「audit_hub 传的 argv 与 scanner 的 argparse 声明不兼容」是【整类】契约债。本文件把
「遍历 cluster 模式全部 task 的真实 argv，逐个对被调 scanner 做 argparse 干跑校验」固化成
回归锁，杜绝同类复发：任何新接线 scanner 若 argv 与其 argparse 不兼容，本测试立刻红。

校验器 tests/argparse_contract.py：AST 抽 scanner 源码的 add_argument 声明重建等价 parser，
不 import/执行被测脚本（scanner 带重模型/子进程依赖，import 即污染），拿真实 argv 干跑。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT / "tests"))

import audit_hub  # noqa: E402
from argparse_contract import build_parser_from_source, dry_run  # noqa: E402


def _make_project(root: Path) -> Path:
    """最小可跑 cluster audit 的项目骨架（够构造出全部 cluster-mode task）。"""
    p = root / "novel"
    (p / "_数据库" / ".manifest").mkdir(parents=True, exist_ok=True)
    (p / "_数据库" / ".audit").mkdir(parents=True, exist_ok=True)
    draft_dir = p / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "cluster_001_draft.txt").write_text(
        "顾长风走进大殿。\n\n他抬起头。\n", encoding="utf-8")
    (draft_dir / "cluster_001_changes.json").write_text(
        json.dumps({"self_eval": {"waivers": []}}, ensure_ascii=False), encoding="utf-8")
    (p / "_数据库" / "作者风格.json").write_text(
        json.dumps({"genre_tags": ["xianxia"]}, ensure_ascii=False), encoding="utf-8")
    (p / "_数据库" / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 4],
                      "scene_storyboard": [{"ch": 0, "scene_id": "s1"}]}]
    }, ensure_ascii=False), encoding="utf-8")
    (p / "_数据库" / "进度.json").write_text(json.dumps({
        "cluster_blueprint": {"cluster_001": {"chapter_range": [1, 4],
                                              "scene_storyboard": []}}
    }, ensure_ascii=False), encoding="utf-8")
    ch = p / "章节" / "第001章"
    ch.mkdir(parents=True, exist_ok=True)
    (ch / "第001章.txt").write_text("顾长风走进大殿。\n", encoding="utf-8")
    (p / "_数据库" / ".manifest" / "ch_001.json").write_text("{}", encoding="utf-8")
    return p


def _capture_cluster_tasks_argv():
    """monkeypatch audit_hub._run 捕获 cluster 模式全部 task 的真实 argv。"""
    captured = []
    original_run = audit_hub._run

    def fake_run(cmd, env_extra=None, timeout=180):
        captured.append(list(cmd))
        return 0, "{}", ""

    audit_hub._run = fake_run
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(Path(td))
            audit_hub.audit_cluster(proj, "001", auto_fix=False, waivers=[])
    finally:
        audit_hub._run = original_run
    return captured


def _classify(captured):
    """把 (script_path, argv) 按 argparse / manual sys.argv 分组，并干跑 argparse 组。"""
    checked, manual, incompatible = [], [], []
    for cmd in captured:
        script = Path(cmd[1])
        argv = cmd[2:]
        assert script.exists(), f"scanner 脚本不存在: {script}"
        parser, _notes = build_parser_from_source(script)
        if parser is None:
            manual.append(script.name)
            continue
        checked.append(script.name)
        err = dry_run(parser, argv)
        if err:
            incompatible.append((script.name, argv, err))
    return checked, manual, incompatible


def test_every_cluster_scanner_argv_compatible():
    """🔴 整类锁：cluster 模式全部 argparse-based scanner 的真实 argv 都必须被接受。"""
    captured = _capture_cluster_tasks_argv()
    assert len(captured) > 100, f"cluster 模式 task 数异常偏少: {len(captured)}"
    checked, _manual, incompatible = _classify(captured)
    assert checked, "没有任何 argparse-based scanner 被校验（探针失效）"
    assert not incompatible, (
        "audit_hub 传的 argv 与 scanner argparse 不兼容（契约债）:\n"
        + "\n".join(f"  {n}: argv={a} err={e}" for n, a, e in incompatible))


def test_spatial_continuity_is_argparse_checked_and_compatible():
    """🔴 本 bug 定点锁：spatial_continuity 走 argparse 校验路径且 `--cluster <id>` 兼容。"""
    captured = _capture_cluster_tasks_argv()
    spatial = [cmd for cmd in captured
               if Path(cmd[1]).name == "spatial_continuity_scanner.py"]
    assert spatial, "spatial_continuity 未被 cluster 模式调度（接线丢失）"
    argv = spatial[0][2:]
    # audit_hub 传的是带值 --cluster（cluster_001），必须被接受
    assert "--cluster" in argv, argv
    ci = argv.index("--cluster")
    assert argv[ci + 1].startswith("cluster_"), f"--cluster 应带 cluster_id 值: {argv}"
    parser, _notes = build_parser_from_source(_SCRIPTS / "spatial_continuity_scanner.py")
    assert parser is not None, "spatial_continuity 应为 argparse-based"
    assert dry_run(parser, argv) is None, f"spatial_continuity argv 不兼容: {argv}"


def test_store_true_cluster_flag_would_be_caught():
    """元测试：校验器对『--cluster 是 store_true 却被传值』这类回退能红（防校验器空转）。"""
    src = (
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('draft')\n"
        "p.add_argument('--cluster', action='store_true')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        tmp = Path(fh.name)
    try:
        parser, _ = build_parser_from_source(tmp)
        assert parser is not None
        # 带值 --cluster cluster_001 应被 store_true parser 拒绝（复现原 bug）
        assert dry_run(parser, ["d.txt", "--cluster", "cluster_001"]) is not None
        # 不带值则兼容
        assert dry_run(parser, ["d.txt", "--cluster"]) is None
    finally:
        tmp.unlink(missing_ok=True)
