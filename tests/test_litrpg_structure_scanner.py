"""litrpg_structure_scanner.py 确定性回归测试（零 LLM / 零联网）。

聚焦尚未被 tests/test_genre_packs.py 覆盖的核心确定性逻辑：
- `cjk()` 中日韩字符计数（含 ASCII / 数字 / 标点不计入的边界）。
- 短文本旁路：total < 500 CJK → 直接 PASS + 空 metrics（不跑探针）。
- `stat_sparse` 分支：有面板标记但 panel_per_1k < STAT_FLOOR → flag stat_sparse（非 no_system_panel）。
- metrics 计算：panel_hits / panel_per_1k / digit_runs（连续数字串计数）真实数值。
- verdict 映射：无 violation→PASS / 有 violation→FAIL_MINOR；gate_level 恒 advisory。
- `main()` CLI：含 sys.exit，走 subprocess 跑真 CLI——
    PASS→0 / FAIL_MINOR(有 violation)→1 / 路径不存在→2。

test_genre_packs.py 已覆盖：gate_level=advisory 契约 / no_system_panel flag / 有面板 PASS，
本文件不重复，专攻短文本旁路、stat_sparse 中间分支、metrics 数值与三档退出码。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import litrpg_structure_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "litrpg_structure_scanner.py"


# ──────────────────────────────────────────────────────────────────────────
# cjk() 计数边界
# ──────────────────────────────────────────────────────────────────────────
def test_cjk_counts_only_han():
    # 5 个汉字 + 数字/字母/标点/空格 都不计入
    assert mod.cjk("升级了") == 3
    assert mod.cjk("HP100 MP50!?。") == 0
    assert mod.cjk("等级 LV 99 提升") == 4  # 等 级 提 升
    assert mod.cjk("") == 0


# ──────────────────────────────────────────────────────────────────────────
# 短文本旁路：total < 500 → 直接 PASS，不跑探针
# ──────────────────────────────────────────────────────────────────────────
def test_short_text_bypasses_probes():
    # 完全无面板标记，但 < 500 CJK → 仍 PASS（不触发 no_system_panel）
    txt = "他走在路上" * 10  # 50 CJK
    r = mod.scan(txt)
    assert mod.cjk(txt) < 500
    assert r["verdict"] == "PASS"
    assert r["violations"] == []
    assert r["violations_count"] == 0
    assert r["metrics"] == {}           # 短文本不算 metrics
    assert r["gate_level"] == "advisory"
    assert r["scanner"] == "litrpg_structure"


def test_just_below_threshold_is_short():
    # 恰好 499 CJK 仍走短文本旁路
    txt = "字" * 499
    assert mod.cjk(txt) == 499
    r = mod.scan(txt)
    assert r["verdict"] == "PASS"
    assert r["metrics"] == {}


# ──────────────────────────────────────────────────────────────────────────
# stat_sparse 中间分支：有面板标记但密度过低
# ──────────────────────────────────────────────────────────────────────────
def test_stat_sparse_when_panel_present_but_sparse():
    # 2000+ CJK，仅 1 个面板标记 → panel_per_1k(≈0.42) 低于 STAT_FLOOR(0.5)
    body = "他平静地走在长长的道路上面慢慢思考着许多寻常普通的日常琐事情景象当中。"  # 无 PANEL_MARKERS
    txt = body * 70 + "副本"   # 注入恰好 1 个面板标记 '副本'
    r = mod.scan(txt)
    assert mod.cjk(txt) >= 500
    assert r["metrics"]["panel_hits"] == 1
    assert r["metrics"]["panel_per_1k"] < mod.STAT_FLOOR
    kinds = [v["kind"] for v in r["violations"]]
    assert kinds == ["stat_sparse"], r["metrics"]
    assert r["verdict"] == "FAIL_MINOR"
    # stat_sparse violation 携带 floor 与 panel_per_1k
    sv = r["violations"][0]
    assert sv["floor"] == mod.STAT_FLOOR
    assert sv["panel_per_1k"] == r["metrics"]["panel_per_1k"]
    assert sv["severity"] == "minor"


def test_no_panel_takes_precedence_over_sparse():
    # 0 面板标记 → 命中 no_system_panel 而非 stat_sparse（elif 分支互斥）
    body = "他慢慢地走在那条空旷无人的小巷里面想着遥远模糊的往昔回忆里景象事。"
    txt = body * 30  # 无任何 PANEL_MARKERS
    r = mod.scan(txt)
    assert r["metrics"]["panel_hits"] == 0
    kinds = [v["kind"] for v in r["violations"]]
    assert kinds == ["no_system_panel"]
    assert len(r["violations"]) == 1  # 互斥：绝不同时报 stat_sparse


# ──────────────────────────────────────────────────────────────────────────
# 面板充足 → PASS + metrics 数值正确
# ──────────────────────────────────────────────────────────────────────────
def test_dense_panel_passes_and_metrics():
    txt = "【系统提示】等级提升！属性 经验值 副本面板状态栏技能栏 HP MP LV。" * 25
    r = mod.scan(txt)
    assert r["verdict"] == "PASS"
    assert r["violations"] == []
    assert r["metrics"]["panel_per_1k"] >= mod.STAT_FLOOR
    assert r["metrics"]["panel_hits"] > 0


def test_digit_runs_metric_counts_number_groups():
    # digit_runs = 连续数字串个数（\d{1,}），非单数字。需 >=500 CJK 才算 metrics。
    filler = "字" * 600
    txt = filler + " 100 200 3" + " 【系统】"  # 3 个数字串：100 / 200 / 3
    r = mod.scan(txt)
    assert r["metrics"]["digit_runs"] == 3, r["metrics"]


# ──────────────────────────────────────────────────────────────────────────
# main() CLI 真子进程：三档退出码
# ──────────────────────────────────────────────────────────────────────────
class _Proc:
    """轻量替身：returncode + 已用 UTF-8 解码的 stdout/stderr。"""
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_cli(draft_path: Path):
    # 强制子进程用 UTF-8 输出（Windows 控制台默认 GBK 会把 CJK JSON 写成乱码）。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, env=env,
    )
    return _Proc(proc.returncode,
                 proc.stdout.decode("utf-8", errors="replace"),
                 proc.stderr.decode("utf-8", errors="replace"))


def test_cli_exit0_on_pass():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "draft.txt"
        f.write_text("【系统提示】等级提升属性经验值副本面板状态栏。" * 25,
                     encoding="utf-8")
        proc = _run_cli(f)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["verdict"] == "PASS"
        assert out["file"] == str(f)


def test_cli_exit1_on_fail_minor():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "draft.txt"
        # 长文本无面板 → FAIL_MINOR → exit 1
        f.write_text("他慢慢走在空旷无人的小巷里想着遥远往昔回忆景象。" * 40,
                     encoding="utf-8")
        proc = _run_cli(f)
        assert proc.returncode == 1, proc.stderr
        out = json.loads(proc.stdout)
        assert out["verdict"] == "FAIL_MINOR"
        assert any(v["kind"] == "no_system_panel" for v in out["violations"])


def test_cli_exit2_on_missing_path():
    proc = _run_cli(Path("/no/such/litrpg/draft/__missing__.txt"))
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "路径不存在" in proc.stdout
