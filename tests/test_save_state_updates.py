"""save_state_updates.py 确定性回归测试（零 LLM / 零联网）。

被测脚本 = cluster-save-state step 9 的 5-in-1 update wrapper。它本身**不打 LLM**，
确定性逻辑有四块，本组全部真 import 真调用钉死：

1. `get_cluster_chapter_range`：从 事件簇.json 解析 cluster 的 chapter_range
   —— list 形态 [a,b] / str 形态 "a-b" / cluster_key 归一化命中 / 文件缺失 /
   查不到 → 返回 []。
   ⚠️ 注意：本脚本是**本地手写解析**（区别于 save_state_evaluators.py 那份委托
   cluster_lookup 的同名函数），故无 blueprint 兜底——这正是要钉死的真实行为。

2. `run_one_module`：脚本不存在 → (False, "脚本不存在...")；存在 → subprocess 跑真
   子脚本、按 returncode 判成败、args_spec 按 args_map 正确展开。
   —— 用一个临时「假子脚本」+ monkeypatch SCRIPT_DIR 驱动，全程真 subprocess、
   不碰任何 LLM 子模块。

3. `run_updates_for_chapter`：`only` 过滤 / 代表章 vs 非代表章对 CLUSTER_ONCE_MODULES
   的跳过（H4 修复：declarative/offscreen 只在首章跑一次，防 delta 乘 N 倍）。

4. `main` 退出码（subprocess 跑真 CLI）：缺 _数据库 → exit 2；cluster 查不到 → exit 2。
   —— 这两条 FATAL 路径在调用任何子模块前就返回，故跑真 CLI 不会触发 LLM。

约定：零依赖 / 仅标准库 / test_* 无参数 / 失败 raise AssertionError。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state_updates as mod  # noqa: E402

_TARGET = _SCRIPTS / "save_state_updates.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path, clusters_obj) -> Path:
    """造 <tmp>/<书>/_数据库/事件簇.json，返回项目根。"""
    proj = tmp / "测试书"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps(clusters_obj, ensure_ascii=False), encoding="utf-8")
    return proj


def _write_fake_submodule(script_dir: Path, name: str, rc: int = 0,
                          out: str = "OK") -> None:
    """写一个临时『假子脚本』：打印收到的 argv（除自身），按指定 rc 退出。

    用来在不碰真 LLM 子模块的前提下驱动 run_one_module / run_updates_for_chapter
    的真实 subprocess 调用路径。
    """
    body = (
        "import sys\n"
        f"print({out!r} + ' argv=' + repr(sys.argv[1:]))\n"
        f"sys.exit({rc})\n"
    )
    (script_dir / name).write_text(body, encoding="utf-8")


def _call_quiet(fn, *args, **kwargs):
    """调被测函数并把它打印的进度（含 ✓/中文）吞进 UTF-8 缓冲，避免 GBK 控制台
    UnicodeEncodeError 干扰断言。这只重定向 stdout（纯 I/O 副作用），不 mock 任何
    被测逻辑——返回值仍是函数真实结果。"""
    buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    with contextlib.redirect_stdout(buf):
        return fn(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────
# 1. get_cluster_chapter_range —— 纯解析
# ──────────────────────────────────────────────────────────────────────────
def test_chapter_range_list_form():
    """[a,b] 形态展开为闭区间 [a..b]。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ]})
        assert mod.get_cluster_chapter_range(proj, "cluster_001") == [1, 2, 3]
        assert mod.get_cluster_chapter_range(proj, "cluster_002") == [4, 5, 6, 7]


def test_chapter_range_str_form():
    """字符串 "a-b" 形态也要正确解析为闭区间。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_005", "chapter_range": "10-12"},
        ]})
        assert mod.get_cluster_chapter_range(proj, "cluster_005") == [10, 11, 12]


def test_chapter_range_normalized_key_matching():
    """🔴 2026-06-17 bug-hunt 修后：get_cluster_chapter_range 委托权威 cluster_lookup
    （北极星① 唯一权威反查），享**数值归一化**——短 id / 裸数字也命中。

    （原本地实现用纯字符串相等无归一化·与 cluster_lookup 分歧·bug-hunt 证明该分歧致 save-state
    在 blueprint-only 状态 FATAL exit2 卡死管线·已对齐权威。）
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_002", "chapter_range": [4, 5]},
        ]})
        assert mod.get_cluster_chapter_range(proj, "cluster_002") == [4, 5]
        assert mod.get_cluster_chapter_range(proj, "002") == [4, 5]
        # 数值归一化（补零）→ 短 id / 裸数字现在也命中（对齐 cluster_lookup）
        assert mod.get_cluster_chapter_range(proj, "cluster_2") == [4, 5]
        assert mod.get_cluster_chapter_range(proj, "2") == [4, 5]


def test_chapter_range_not_found_and_missing_file():
    """查不到的 cluster → []；事件簇.json 缺失 → []（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        ]})
        assert mod.get_cluster_chapter_range(proj, "cluster_099") == []
        # 删掉文件 → 缺失分支返回 []
        (proj / "_数据库" / "事件簇.json").unlink()
        assert mod.get_cluster_chapter_range(proj, "cluster_001") == []


def test_chapter_range_blueprint_fallback():
    """🔴 2026-06-17 bug-hunt 修后：事件簇.json 里 cluster 无 chapter_range（fluid v27 splitter
    未回填）但 进度.json.cluster_blueprint 有时，get_cluster_chapter_range **委托 cluster_lookup
    读 blueprint 兜底**返回区间（不再返 []）。

    这是修复的核心：原本地实现无兜底返 [] → cmd_apply/save_state_updates 在 blueprint-only 状态
    FATAL exit2 卡死管线（而兄弟 evaluators 走 cluster_lookup 有兜底正常推进·三脚本权威源不一致）。
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_002"},  # 事件簇 无 chapter_range
        ]})
        (proj / "_数据库" / "进度.json").write_text(
            json.dumps({"cluster_blueprint": {"cluster_002": {"chapter_range": [4, 6]}}},
                       ensure_ascii=False), encoding="utf-8")
        # blueprint 兜底 → [4,5,6]（对齐 evaluators·根治三脚本不一致）
        assert mod.get_cluster_chapter_range(proj, "cluster_002") == [4, 5, 6]


# ──────────────────────────────────────────────────────────────────────────
# 2. run_one_module —— subprocess 调度 + args 展开
# ──────────────────────────────────────────────────────────────────────────
def test_run_one_module_script_missing():
    """子脚本文件不存在 → (False, '脚本不存在: ...')，不触发 subprocess。"""
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            mod.SCRIPT_DIR = Path(d)  # 空目录，无任何脚本
            ok, msg = mod.run_one_module(
                "offscreen", "offscreen_update.py", ["project", "chapter"],
                "/some/proj", 5)
            assert ok is False
            assert "脚本不存在" in msg
    finally:
        mod.SCRIPT_DIR = saved


def test_run_one_module_args_spec_expansion():
    """args_spec 按 args_map 展开：'ch'/'chapter' → str(ch)，'update' → 'update'，
    'project' → project。用假子脚本回显 argv 验证顺序与值。"""
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            mod.SCRIPT_DIR = sd
            _write_fake_submodule(sd, "fate_engine.py", rc=0, out="FATE")
            # fate 模块的 args_spec 是 ["project", "update", "chapter"]（三段式）
            ok, msg = mod.run_one_module(
                "fate_engine_update", "fate_engine.py",
                ["project", "update", "chapter"], "PROJX", 9)
            assert ok is True, msg
            # 假脚本回显 argv：应为 ['PROJX', 'update', '9']
            assert "'PROJX'" in msg and "'update'" in msg and "'9'" in msg, msg
    finally:
        mod.SCRIPT_DIR = saved


def test_run_one_module_nonzero_returncode_is_failure():
    """子脚本 returncode != 0 → run_one_module 判为失败，msg 带 rc=。"""
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            mod.SCRIPT_DIR = sd
            _write_fake_submodule(sd, "boom.py", rc=3, out="BOOM")
            ok, msg = mod.run_one_module(
                "x", "boom.py", ["project", "chapter"], "P", 1)
            assert ok is False
            assert "rc=3" in msg, msg
    finally:
        mod.SCRIPT_DIR = saved


# ──────────────────────────────────────────────────────────────────────────
# 3. run_updates_for_chapter —— only 过滤 + 代表章 H4 跳过
# ──────────────────────────────────────────────────────────────────────────
def test_only_filter_runs_subset():
    """only={'declarative'} → 只跑 declarative 一个模块，其余不出现在结果里。"""
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            mod.SCRIPT_DIR = sd
            # 只给 declarative 一个真脚本即可（其余被 only 过滤掉，不会调用）
            _write_fake_submodule(sd, "declarative_data_update.py")
            res = _call_quiet(mod.run_updates_for_chapter,
                              "P", 1, only={"declarative"}, is_representative_ch=True)
            assert set(res.keys()) == {"declarative"}, res
            assert res["declarative"]["ok"] is True
    finally:
        mod.SCRIPT_DIR = saved


def test_representative_ch_runs_cluster_once_modules():
    """代表章（is_representative_ch=True）：CLUSTER_ONCE_MODULES 真跑（不跳过）。"""
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            mod.SCRIPT_DIR = sd
            _write_fake_submodule(sd, "offscreen_update.py", out="OFF")
            res = _call_quiet(mod.run_updates_for_chapter,
                              "P", 1, only={"offscreen"}, is_representative_ch=True)
            assert res["offscreen"]["ok"] is True
            # 代表章真跑 → msg 来自假脚本输出，不是 'skipped' 文案
            assert "skipped" not in res["offscreen"]["msg"], res
            assert "OFF" in res["offscreen"]["msg"], res
    finally:
        mod.SCRIPT_DIR = saved


def test_non_representative_ch_skips_cluster_once_modules():
    """🔴 H4 核心：非代表章对 CLUSTER_ONCE_MODULES（offscreen/declarative）跳过，
    标记 ok=True + msg 含 'skipped'，绝不重放（防 relationship/faction delta 乘 N 倍）。

    同时验证非 CLUSTER_ONCE 模块（character_arc）在非代表章仍正常跑。
    """
    assert mod.CLUSTER_ONCE_MODULES == {"offscreen", "declarative"}
    saved = mod.SCRIPT_DIR
    try:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            mod.SCRIPT_DIR = sd
            _write_fake_submodule(sd, "offscreen_update.py", out="OFF")
            _write_fake_submodule(sd, "character_arc_update.py", out="ARC")
            res = _call_quiet(mod.run_updates_for_chapter,
                              "P", 2, only={"offscreen", "character_arc"},
                              is_representative_ch=False)
            # offscreen 是 cluster-once → 非代表章被跳过
            assert res["offscreen"]["ok"] is True
            assert "skipped" in res["offscreen"]["msg"], res
            # character_arc 非 cluster-once → 仍真跑
            assert res["character_arc"]["ok"] is True
            assert "ARC" in res["character_arc"]["msg"], res
    finally:
        mod.SCRIPT_DIR = saved


# ──────────────────────────────────────────────────────────────────────────
# 4. main 退出码（subprocess 真 CLI · FATAL 路径在调子模块前返回）
# ──────────────────────────────────────────────────────────────────────────
def _run_cli(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60)


def test_main_fatal_missing_database():
    """缺 _数据库 目录 → exit 2 + [FATAL]。"""
    with tempfile.TemporaryDirectory() as d:
        empty = Path(d) / "empty_proj"
        empty.mkdir()
        r = _run_cli(str(empty), "--cluster", "cluster_001")
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "FATAL" in (r.stderr + r.stdout)


def test_main_fatal_cluster_not_found():
    """有 _数据库 但 cluster 查不到 chapter_range → exit 2 + [FATAL]（在调子模块前返回）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        ]})
        r = _run_cli(str(proj), "--cluster", "cluster_099")
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "FATAL" in (r.stderr + r.stdout)


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    for n in sorted(g):
        if n.startswith("test_"):
            try:
                g[n]()
                print("OK", n)
            except Exception as e:  # noqa: BLE001
                print("FAIL", n, e)
                traceback.print_exc()
