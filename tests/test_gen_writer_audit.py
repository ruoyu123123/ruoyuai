"""gen_writer 审计修复回归测试（零依赖 · 文件尾 __main__ 直接跑）。

守护 2026-06-16 triage_worth_fixing 两个 gen_writer.py 修复：

  [Bug L1936/medium] enforce_short_paragraphs 反向 tighten 打碎作者签名复合长段。
    根因：函数读了作者 single（单句独行率）却没传进来 → 密实多句长段作者
    (single<0.5·如人生长恨 0.18 / 惊悚乐园) 被通用「一段一句」post-processor 拆碎，
    与系统自己的 _para_contract_line（single<0.5→写复合长段·relax-only）方针对着干。
    修：新增 author_single 参数 + single<0.5 时短路 no-op（不收紧·对齐段长契约）。

  [Bug L1303/low] expand 续写循环内联 _cjk lambda 用窄 CJK 区间 [一-鿿](U+4E00-U+9FFF)，
    漏 CJK 扩展 A [㐀-䶿](U+3400-U+4DBF)，与权威口径 chapter_io.count_cjk
    （含扩展 A·gate L1554/L1761 都走它）跨流水线不自洽。
    修：删 lambda，expand 路径全改用 cio.count_cjk。
"""
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import gen_writer as gw  # noqa: E402
import chapter_io as cio  # noqa: E402

# 一个超阈值的多句非对话段（3 个句末·无引号/面板保护）——基线行为下应被切成多段。
_LONG_MULTI = (
    "他推开门走进了那个昏暗的房间打量四周。"
    "墙上挂着一幅落满灰尘的旧画像。"
    "地上散落着许多被人撕碎的纸片。"
)


# ─────────────────────── Bug L1936：enforce_short_paragraphs 感知作者 single ───────────────────────

def test_enforce_short_paragraphs_signature_has_author_single():
    """函数签名必须接受 author_single 关键字参数（修复点·防签名回退）。"""
    import inspect
    params = inspect.signature(gw.enforce_short_paragraphs).parameters
    assert "author_single" in params, "enforce_short_paragraphs 必须新增 author_single 参数"
    assert params["author_single"].default is None, "author_single 默认 None（向后兼容·零回归）"


def test_dense_long_paragraph_author_skips_chopping():
    """核心修复：作者 single<0.5（密实多句长段型）→ 短路 no-op·不拆碎复合长段。"""
    out = gw.enforce_short_paragraphs(_LONG_MULTI, author_para_mean=20, author_single=0.18)
    assert out == _LONG_MULTI, "single<0.5 时必须原样返回（relax-only·对齐 _para_contract_line）"
    assert "\n\n" not in out, "密实长段作者不应被切出段落分隔"


def test_single_at_boundary_just_below_skips():
    """single 恰低于 0.5 阈值（0.49）→ 仍短路（边界·< 不是 <=）。"""
    out = gw.enforce_short_paragraphs(_LONG_MULTI, author_para_mean=20, author_single=0.49)
    assert out == _LONG_MULTI


def test_carved_sentence_author_still_chops():
    """碎句型作者 single>=0.5（如爽文 0.79）→ 守卫不触发·保留原切段行为（不回归）。"""
    out = gw.enforce_short_paragraphs(_LONG_MULTI, author_para_mean=20, author_single=0.79)
    assert out.count("\n\n") >= 2, "single>=0.5 时多句长段应照常按句末切段"
    assert out.replace("\n\n", "").replace("\n", "") == _LONG_MULTI, "只切段·一字不改"


def test_single_none_preserves_legacy_behavior():
    """author_single 缺省(None) → 旧行为完全不变（向后兼容·守 4 个既有调用）。"""
    out_none = gw.enforce_short_paragraphs(_LONG_MULTI, author_para_mean=20)
    out_explicit_none = gw.enforce_short_paragraphs(_LONG_MULTI, author_para_mean=20, author_single=None)
    assert out_none == out_explicit_none, "None 与缺省应行为一致"
    assert out_none.count("\n\n") >= 2, "缺 single 基线时维持原切段（不静默关掉检测）"


def test_dense_author_guard_does_not_touch_protected_segments():
    """密实作者短路时·对话/面板段当然也原样（短路在最前·不依赖 protected 逻辑）。"""
    dlg = "“你怎么会知道这扇门走不通，难道你提前来踩过点，还是说你有什么特殊的本事能一眼看穿这一切吗？”"
    assert gw.enforce_short_paragraphs(dlg, author_para_mean=20, author_single=0.1) == dlg


# ─────────────────────── Bug L1303：CJK 计数统一走 cio.count_cjk ───────────────────────

def test_inline_cjk_lambda_removed_from_expand_path():
    """回退守卫：expand 续写循环不得再出现窄区间内联 _cjk lambda（必须用 cio.count_cjk）。"""
    src = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "_cjk = lambda" not in src, "内联 _cjk lambda 必须删除（改用 cio.count_cjk）"
    # 窄区间签名（仅 BMP 主块·漏扩展 A）不应再作为 CJK 计数手段出现
    assert "'一' <= c <= '鿿'" not in src, "窄 CJK 区间不应再用于计数（与 chapter_io 权威口径冲突）"


def test_authoritative_count_cjk_covers_extension_a():
    """权威口径 cio.count_cjk 必须统计 CJK 扩展 A（U+3400-U+4DBF）——这是修复的语义差。

    旧内联 lambda 的窄区间 [一-鿿](U+4E00-U+9FFF) 对扩展 A 计 0，导致 expand gate
    与下游 L1554/L1761 宽口径不自洽。本测试钉死 expand 路径现在依赖的口径含扩展 A。
    """
    ext_a = "㐀䶿"  # 两个 CJK 扩展 A 字符（U+3400, U+4DBF）
    assert cio.count_cjk(ext_a) == 2, "cio.count_cjk 必须计入扩展 A 字符"
    # 与旧窄 lambda 对比：旧口径会漏（计 0），证明修复改变了 gate 行为方向（窄→宽·只多跑不少跑）
    old_narrow = sum(1 for c in ext_a if "一" <= c <= "鿿")
    assert old_narrow == 0, "旧窄区间确实漏扩展 A（修复消除该跨流水线分歧）"
    assert cio.count_cjk(ext_a) > old_narrow, "宽口径 >= 窄口径（修复方向：只让 expand 多跑·无内容丢失）"


def test_count_cjk_excludes_punct_and_ascii():
    """口径一致性附验：count_cjk 只数 CJK·不含标点/数字/字母（gate 语义稳定）。"""
    assert cio.count_cjk("他说：abc123，好。") == 3  # 他 说 好


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
    print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
    sys.exit(1 if fails else 0)
