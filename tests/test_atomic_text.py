"""atomic_write_text 回归测试 — 残余非原子写收编（2026-06-13）。

收编对象（崩溃/断电不留半截产物文件）：
  - atomic_json.atomic_write_text 新增（与 atomic_write_json 同核：tmp pid+uuid + fsync + os.replace）
  - plan_tracker._save_json     → atomic_write_text（序列化仍由 _json_dump_safe 权威产出）
  - gen_writer 草稿/changes     → atomic_write_text（源码级防回归·不 import 重模块）
  - chapter_splitter pending_tail → atomic_write_text（功能级）
  - chapter_io.write_body/write_changes → atomic_write_text（章节 txt 真正落盘点·功能级）

这组测试钉死「写后读回一致 / 已存在目标被原子替换 / tmp 不残留 / 崩溃不毁原文件」，
**不碰任何写作业务逻辑**。零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import atomic_json  # noqa: E402
import chapter_io as cio  # noqa: E402
import chapter_splitter  # noqa: E402
import plan_tracker  # noqa: E402


def _no_stray_tmp(dirpath: Path):
    """目录树内不得残留 .tmp（原子写应清理本次 tmp）。"""
    strays = [p.name for p in dirpath.rglob("*.tmp")]
    assert not strays, f"残留游离 tmp 文件: {strays}"


# ---------------------------------------------------------------- atomic_write_text 本体

def test_roundtrip_identical():
    """写后读回逐字节一致（多行中文 + 默认 utf-8 + 不偷加/偷删换行）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "章节" / "cluster_001_draft.txt"  # 父目录由原子写自建
        text = "第一章 雪夜\n\n“他来了。”\n林潜把杯子摔在地上……\n"
        atomic_json.atomic_write_text(p, text)
        assert p.read_text(encoding="utf-8") == text
        _no_stray_tmp(Path(td))


def test_overwrite_existing_target_replaced():
    """目标已存在 → 被原子替换为新内容（旧内容不残留、不拼接）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "draft.txt"
        atomic_json.atomic_write_text(p, "旧版正文" * 100)
        atomic_json.atomic_write_text(p, "新版\n")
        assert p.read_text(encoding="utf-8") == "新版\n"
        _no_stray_tmp(Path(td))


def test_encoding_param_respected():
    """encoding 参数生效（非 utf-8 也走同一原子路径）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "gbk.txt"
        atomic_json.atomic_write_text(p, "中文内容\n", encoding="gbk")
        # 文本模式写盘 \n→平台换行（Windows=\r\n）与裸 write_text 旧行为一致，比较时归一。
        assert p.read_bytes().decode("gbk").replace("\r\n", "\n") == "中文内容\n"
        _no_stray_tmp(Path(td))


def test_crash_mid_replace_preserves_original_and_cleans_tmp():
    """模拟 replace 阶段崩溃（os.replace 抛非 PermissionError）→ 异常向上抛、
    原文件原样无损、本次 tmp 被 finally 清理。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "draft.txt"
        original = "崩溃前的完整草稿\n"
        atomic_json.atomic_write_text(p, original)

        orig_replace = atomic_json.os.replace

        def _boom(src, dst):
            raise OSError("simulated kill mid-replace")

        atomic_json.os.replace = _boom
        try:
            try:
                atomic_json.atomic_write_text(p, "半截新内容")
                assert False, "应向上抛 OSError（不静默吞）"
            except OSError:
                pass
        finally:
            atomic_json.os.replace = orig_replace
        # 原子语义核心断言：目标完好、内容仍是崩溃前版本、tmp 不残留
        assert p.read_text(encoding="utf-8") == original
        _no_stray_tmp(Path(td))


def test_atomic_write_json_delegation_unchanged():
    """重构后 atomic_write_json（委托 atomic_write_text）行为不变：
    中文不转义 / indent=2 / 读回等值 / 无 tmp 残留。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "data.json"
        data = {"中文": "ok", "n": [1, 2]}
        atomic_json.atomic_write_json(p, data)
        raw = p.read_text(encoding="utf-8")
        assert json.loads(raw) == data
        assert "中文" in raw  # ensure_ascii=False 保持
        _no_stray_tmp(Path(td))


# ---------------------------------------------------------------- plan_tracker._save_json

def test_plan_tracker_save_json_routes_through_atomic_write_text():
    """_save_json 必须实际经 atomic_json.atomic_write_text 落盘（打桩确认调用路径），
    且序列化字节与旧实现一致（_json_dump_safe：ensure_ascii=False/indent=2/sort_keys=False）。"""
    called = {"n": 0, "target": None}
    orig = atomic_json.atomic_write_text

    def _spy(target, text, **kw):
        called["n"] += 1
        called["target"] = Path(target)
        return orig(target, text, **kw)

    atomic_json.atomic_write_text = _spy
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".plans" / "x_main_outline_20260613T000000.json"
            plan = {"plan_id": "x", "steps": [{"n": 1, "title": "走向卡"}], "z先": 1, "a后": 2}
            plan_tracker._save_json(p, plan)
            assert called["n"] == 1, "_save_json 未走 atomic_write_text（绕过了原子路径）"
            assert called["target"] == p
            raw = p.read_text(encoding="utf-8")
            assert raw == plan_tracker._json_dump_safe(plan), "落盘字节与 _json_dump_safe 不一致"
            assert json.loads(raw) == plan
            assert list(json.loads(raw).keys()) == list(plan.keys()), "sort_keys 行为变了"
            _no_stray_tmp(Path(td))
    finally:
        atomic_json.atomic_write_text = orig


def test_plan_tracker_serialize_self_check_preserved():
    """不可序列化 → 仍是 RuntimeError（_json_dump_safe 自检契约不变），且不产生任何文件。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        try:
            plan_tracker._save_json(p, {"bad": object()})
            assert False, "应抛 RuntimeError"
        except RuntimeError:
            pass
        assert not p.exists()
        _no_stray_tmp(Path(td))


def test_plan_tracker_no_bare_write_text_regression():
    """源码层防回归：plan_tracker 不得再出现 plan 落盘裸 path.write_text(text。"""
    src = Path(plan_tracker.__file__).read_text(encoding="utf-8")
    assert "path.write_text(text" not in src, "_save_json 回归成裸 write_text"


# ---------------------------------------------------------------- chapter_io（章节 txt 真正落盘点）

def test_chapter_io_write_body_atomic_and_content_unchanged():
    """write_body 行为不变性：标准嵌套路径 / rstrip+末尾单 \\n 口径不变 / 无 tmp 残留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = cio.write_body(root, 3, "第3章 还价\n\n正文段落。\n\n\n")
        assert p == root / "章节" / "第003章" / "第003章.txt"
        assert p.read_text(encoding="utf-8") == "第3章 还价\n\n正文段落。\n"
        # 覆盖重写（splitter 幂等重切）仍一致
        cio.write_body(root, 3, "改写后")
        assert p.read_text(encoding="utf-8") == "改写后\n"
        _no_stray_tmp(root)


def test_chapter_io_write_changes_atomic_valid_json():
    """write_changes：裸 factual 自动包装行为不变 + 合法 JSON + 无 tmp 残留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = cio.write_changes(root, 3, {"locked_facts": ["规则会还价"]})
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["factual"] == {"locked_facts": ["规则会还价"]}
        assert data["self_eval"] == {}
        _no_stray_tmp(root)


def test_chapter_io_crash_mid_write_preserves_old_chapter():
    """章节 txt 崩溃语义：replace 阶段被杀 → 旧章节完好（P1 半截章节=读者直接看到残文）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = cio.write_body(root, 7, "旧版第七章全文")
        orig_replace = atomic_json.os.replace

        def _boom(src, dst):
            raise OSError("simulated kill")

        atomic_json.os.replace = _boom
        try:
            try:
                cio.write_body(root, 7, "新版写到一半")
                assert False, "应向上抛 OSError"
            except OSError:
                pass
        finally:
            atomic_json.os.replace = orig_replace
        assert p.read_text(encoding="utf-8") == "旧版第七章全文\n"
        _no_stray_tmp(root)


# ---------------------------------------------------------------- chapter_splitter pending_tail

def test_splitter_pending_tail_atomic_and_content_unchanged():
    """_write_pending_tail 行为不变性：路径 / rstrip+末尾单 \\n 口径不变 / 无 tmp 残留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = chapter_splitter._write_pending_tail(root, "006", "末段不足三千字的尾巴\n\n")
        assert p == root / "章节" / "cluster_006_draft" / "cluster_006_pending_tail.txt"
        assert p.read_text(encoding="utf-8") == "末段不足三千字的尾巴\n"
        _no_stray_tmp(root)


def test_splitter_pending_tail_routes_through_atomic():
    """_write_pending_tail 必须实际调用 atomic_write_text（打桩确认调用路径）。"""
    called = {"n": 0}
    orig = chapter_splitter.atomic_write_text

    def _spy(target, text, **kw):
        called["n"] += 1
        return orig(target, text, **kw)

    chapter_splitter.atomic_write_text = _spy
    try:
        with tempfile.TemporaryDirectory() as td:
            chapter_splitter._write_pending_tail(Path(td), "001", "尾巴")
    finally:
        chapter_splitter.atomic_write_text = orig
    assert called["n"] == 1, "_write_pending_tail 未走 atomic_write_text"


# ---------------------------------------------------------------- gen_writer（源码级·不 import 重模块）

def test_gen_writer_no_bare_draft_write_text_regression():
    """源码层防回归：gen_writer 草稿/changes 落盘不得回归裸 write_text。
    （gen_writer import 链含 gen_model_loader/frozen_util，零依赖测试不实际 import。）"""
    src = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "draft_path.write_text" not in src, "草稿回归成裸 write_text"
    assert "changes_path.write_text" not in src, "changes.json 回归成裸 write_text"
    assert "atomic_write_text(draft_path" in src, "草稿未走 atomic_write_text"
    assert "atomic_write_text(changes_path" in src, "changes.json 未走 atomic_write_text"


def test_chapter_splitter_no_bare_product_write_text_regression():
    """源码层防回归：splitter 产物（pending_tail）不得回归裸 write_text；
    WAL/.pre_opening 属日志/临时写，允许保留裸写（收编边界钉死，防误扩/误收）。"""
    src = (_SCRIPTS / "chapter_splitter.py").read_text(encoding="utf-8")
    assert 'p.write_text(text.rstrip() + "\\n"' not in src, "pending_tail 回归成裸 write_text"
    assert "atomic_write_text(p, text.rstrip()" in src, "pending_tail 未走 atomic_write_text"


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
