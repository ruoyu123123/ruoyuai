"""writer system prompt 禁「主角」占位词泄漏正文回归守卫（G3 e2e fix #4 · 2026-06-23）。

钉死 G3 真 API e2e 抓出的沉浸感 bug：scene_storyboard 用「主角」占位指代（项目未定
主角名），writer 照搬把「主角顶着那张名叫多林的脸」「主角的脑海里」写进正文。

修复 = gen_writer system prompt 常驻硬铁律加 H5「占位代号零泄漏」：正文里指代人物只能用
具体角色名 / 第三人称代词 / 身份称谓，**正文出现『主角』二字即破例失败**。

守护点：
  · 源码级（永远跑）：gen_writer.py 含 H5 指令文本 + 关键短语
  · build_prompt 级（e2e 项目在 worktree 时跑）：真 system 段含 H5 指令
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "scripts"))
import gen_writer as gw  # noqa: E402

_GW_SRC = (_REPO / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")


def test_h5_directive_in_source():
    """源码级守卫：H5 占位代号零泄漏铁律必须在 writer prompt 里。"""
    assert "H5" in _GW_SRC, "H5 铁律标号丢失"
    assert "占位代号零泄漏" in _GW_SRC, "H5 标题丢失"
    # 关键禁令：正文禁出现「主角」+ 给出替代方案（角色名/代词/身份称谓）
    assert "主角" in _GW_SRC and "占位代号" in _GW_SRC
    assert "第三人称代词" in _GW_SRC, "未给代词替代方案"
    assert "破例失败" in _GW_SRC, "未把『主角』泄漏标为破例失败"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            fn = globals()[nm]
            mark = getattr(fn, "pytestmark", [])
            skip = any(getattr(m, "name", "") == "skipif" and m.args and m.args[0]
                       for m in (mark if isinstance(mark, list) else [mark]))
            if skip:
                print(f"  [SKIP] {nm}")
                continue
            try:
                fn()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
