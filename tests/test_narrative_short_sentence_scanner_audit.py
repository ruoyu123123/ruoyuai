"""narrative_short_sentence_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 两处确定性 advisory 检测器健壮性修复
（北极星⑤：advisory 检测·不干涉创作判断；relax-only 不收紧 → 不引入漏报）：

  · [L50] detect_annotation_zone：
      (1) 去掉绑定某本书世界观专名的 marker（ZF-/观测员档案/ZF档案/——ZF），
          系统须对任意作者通用 → 这些 token 不再制造笔注排除区。
      (2) 加位置守卫——笔注是章末块，只在后 40%（且至少最后 3 段）内找首个标记，
          避免正文中段子串命中（如『翻开档案标号』）静默把整条章尾排除出扫描。
      守护点：①中段 marker 不再吞章尾（违规仍被检出）②章尾真笔注照常排除
      ③ZF-/观测员 等已去绑 token 不再生成排除区。

  · [L85] scan_chapter 作者基线 relax-only（对齐兄弟 prose_rhythm_scanner._author_baseline）：
      作者档 quantitative.sentence_length.mean < 22（短句碎切作者，如惊悚乐园 16.8）→
      period 触发阈值由 3 放宽到 5（relax-only，绝不收紧）。
      author_sent_mean 缺省（None）时 period_floor=3，行为与改前完全一致。
      守护点：①无基线时行为不变（不引入漏报/误报）②短句作者基线下 3-4 句号短段被放过
      ③_author_sentence_mean 读对字段、缺/坏档安全返回 None ④main() --project 端到端生效。

注：fix_patch 中提到的 audit_hub.py call-site（传 --project/--style）属 scanner 外·非本文件，
不在本回归范围；本测试只钉 narrative_short_sentence_scanner.py 自身的健壮性与向后兼容。

跑法：PYTHONIOENCODING=utf-8 python tests/test_narrative_short_sentence_scanner_audit.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import narrative_short_sentence_scanner as nsss  # noqa: E402

_MODULE_PATH = _SCRIPTS / "narrative_short_sentence_scanner.py"


# ============================================================
# [L50] detect_annotation_zone — 去绑书 token + 位置守卫
# ============================================================

def test_mid_text_marker_does_not_swallow_tail():
    """[L50 核心] 正文中段出现『档案标号』子串 → 旧代码 set(range(2,len)) 把后续整章吞掉静默漏检；
    修复后位置守卫只看章尾区 → 中段 marker 不生成排除区，后段违规照常检出。"""
    paras = [
        "第一段普通叙述内容这里写满十五个汉字以上方便参与扫描判定逻辑哈哈。",
        "第二段也是普通叙述这里再写一些字让它超过十五个汉字门槛参与扫描就好了。",
        "他翻开档案标号那一页。看了一眼。皱起眉头。合上了本子。",   # 2 中段 marker
        "第四段继续写普通的叙述内容凑够字数让扫描器把它当作正文段落处理就行。",
        "第五段还是普通叙述这里写满十五个汉字以上让它进入扫描的判定流程中去。",
        "他站起身。推开门。走了出去。没有回头。",                   # 5 中段 marker 之后的违规
        "第七段普通叙述内容这里再补一些汉字凑过十五字门槛参与正文扫描判定。",
        "第八段普通叙述内容继续补字让它顺利进入扫描器的正文段落判定环节里。",
        "第九段普通叙述内容这里写够十五个汉字以上方便扫描器识别为正文段落。",
        "第十段普通叙述内容这里也写够十五个汉字让扫描器正常处理这一段落。",
    ]
    zone = nsss.detect_annotation_zone(paras)
    assert zone == set(), f"中段 marker 不应生成排除区，实际={sorted(zone)}"
    text = "\n".join(paras)
    r = nsss.scan_chapter(text)
    lines = {v["line"] for v in r["violations"]}
    # 旧代码：annotation_start=2 → index 2 起全排除 → lines 为空（漏检）。
    assert 3 in lines, f"中段 marker 后的违规(line3)应被检出，实际 lines={sorted(lines)}"
    assert 6 in lines, f"中段 marker 后的违规(line6)应被检出，实际 lines={sorted(lines)}"


def test_tail_marker_still_excluded():
    """章尾区真笔注（样本编号/档案标号）照常被识别为排除区并跳过扫描。"""
    paras = [
        "正文第一段这里写满十五个汉字以上让扫描器把它当作正文段落来处理就好。",
        "正文第二段这里再补一些汉字凑过十五字门槛进入扫描判定的流程中去吧。",
        "正文第三段普通叙述内容继续凑够十五个汉字以上方便扫描器识别正文。",
        "正文第四段普通叙述这里写满十五个汉字门槛让它顺利进入扫描判定。",
        "样本编号A-001。观测完毕。归档处理。封存待查。",   # 4 章尾笔注起点（短句形态）
        "档案标号续记。无异常。",                           # 5 笔注内
    ]
    zone = nsss.detect_annotation_zone(paras)
    assert zone == {4, 5}, f"章尾笔注区应为 {{4,5}}，实际={sorted(zone)}"
    r = nsss.scan_chapter("\n".join(paras))
    # 笔注区被排除 → 笔注里的短句形态不计违规。
    assert r["violations_count"] == 0, f"章尾笔注应被排除，实际 violations={r['violations']}"
    assert r["annotation_paras"] == 2


def test_debound_book_specific_markers_no_zone():
    """[L50] 已去绑的书专名 token（ZF-/观测员档案/ZF档案/——ZF）不再生成任何排除区。"""
    # 即便放在章尾，这些 token 也不应被识别为笔注 marker。
    paras = [
        "正文第一段这里写满十五个汉字以上让扫描器把它当作正文段落处理。",
        "正文第二段这里再补一些汉字凑过十五字门槛进入扫描判定流程。",
        "正文第三段普通叙述内容继续凑够十五个汉字以上方便识别。",
        "ZF-007 观测员档案记录在此。已确认。封存。归位完毕。",  # 去绑 token（即便在章尾）
    ]
    zone = nsss.detect_annotation_zone(paras)
    assert zone == set(), f"去绑 token 不应生成排除区，实际={sorted(zone)}"


def test_empty_paras_safe():
    """空段落列表 → 空排除区，不崩。"""
    assert nsss.detect_annotation_zone([]) == set()


def test_short_chapter_tail_guard_at_least_last_3():
    """章节很短（<5 段）时位置守卫至少覆盖最后 3 段：尾段 marker 仍被识别。"""
    paras = [
        "正文第一段这里写满十五个汉字以上让扫描器当作正文段落处理就行。",
        "正文第二段这里补足十五个汉字门槛进入扫描判定的流程里面去吧。",
        "样本编号Z-9。结束。",  # 末段笔注
    ]
    zone = nsss.detect_annotation_zone(paras)
    assert 2 in zone, f"末段 marker 应被识别，实际={sorted(zone)}"


# ============================================================
# [L85] scan_chapter 作者基线 relax-only
# ============================================================

# 4 句号、逗号 0、cjk<80 的叙述段：floor=3 触发，floor=5 放过。
_FOUR_PERIOD_PARA = "他走进屋子。灯还亮着。桌上有杯茶。窗户开着。"


def test_no_baseline_behaves_as_before():
    """author_sent_mean 缺省（None）→ period_floor=3，行为与改前一致：4 句号短段触发。"""
    r = nsss.scan_chapter(_FOUR_PERIOD_PARA)
    assert r["verdict"] != "PASS", "无基线时 4 句号短段应照旧触发"
    assert r["violations_count"] == 1


def test_short_sentence_author_baseline_relaxes():
    """[L85 核心] 作者档句长均值 < 22（短句碎切作者）→ floor 放宽到 5 → 4 句号短段被放过。"""
    r = nsss.scan_chapter(_FOUR_PERIOD_PARA, author_sent_mean=16.8)
    assert r["verdict"] == "PASS", f"短句作者基线下 4 句号应被放过，实际 {r['violations']}"
    assert r["violations_count"] == 0


def test_relax_only_not_tighten_for_long_sentence_author():
    """relax-only：长句作者（均值 ≥ 22）→ floor 仍是 3（不收紧），行为同无基线。"""
    r = nsss.scan_chapter(_FOUR_PERIOD_PARA, author_sent_mean=31.0)
    assert r["verdict"] != "PASS", "长句作者基线不应收紧或额外放宽，应与默认一致"
    assert r["violations_count"] == 1


def test_relaxed_floor_still_catches_5_periods():
    """放宽到 floor=5 后，≥5 句号的极端碎切短段仍被检出（relax 不是关检测）。"""
    para = "他来了。他走了。他坐下。他起身。他离开。"  # 5 句号
    r = nsss.scan_chapter(para, author_sent_mean=16.8)
    assert r["verdict"] != "PASS", "floor=5 下 5 句号短段仍应触发"
    assert r["violations"][0]["period"] >= 5


# ============================================================
# _author_sentence_mean — 读对字段 + 缺/坏档安全
# ============================================================

def _write_style(d, payload) -> Path:
    p = Path(d) / "作者风格.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_author_mean_reads_correct_field():
    """读 quantitative.sentence_length.mean。"""
    with tempfile.TemporaryDirectory() as d:
        sp = _write_style(d, {"quantitative": {"sentence_length": {"mean": 16.8}}})
        assert nsss._author_sentence_mean(str(sp), None) == 16.8


def test_author_mean_project_dir_fallback():
    """传 project 时从 _数据库/作者风格.json 读。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True)
        (db / "作者风格.json").write_text(
            json.dumps({"quantitative": {"sentence_length": {"mean": 19.5}}}, ensure_ascii=False),
            encoding="utf-8")
        assert nsss._author_sentence_mean(None, str(d)) == 19.5


def test_author_mean_project_dir_final_variant():
    """无 作者风格.json 时回落 作者风格_FINAL.json。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        db.mkdir(parents=True)
        (db / "作者风格_FINAL.json").write_text(
            json.dumps({"quantitative": {"sentence_length": {"mean": 21.0}}}, ensure_ascii=False),
            encoding="utf-8")
        assert nsss._author_sentence_mean(None, str(d)) == 21.0


def test_author_mean_missing_returns_none():
    """都不存在 → None（沿用通用兜底）。"""
    assert nsss._author_sentence_mean(None, None) is None
    with tempfile.TemporaryDirectory() as d:
        assert nsss._author_sentence_mean(str(Path(d) / "no.json"), str(d)) is None


def test_author_mean_malformed_returns_none():
    """坏 JSON / 字段缺失 / mean 非数字 → None，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert nsss._author_sentence_mean(str(bad), None) is None
        sp = _write_style(d, {"quantitative": {"sentence_length": {"mean": "短"}}})
        assert nsss._author_sentence_mean(str(sp), None) is None
        sp2 = _write_style(d, {"quantitative": {}})
        assert nsss._author_sentence_mean(str(sp2), None) is None


# ============================================================
# main() 子进程端到端：向后兼容 + --project relax-only
# ============================================================

def _run(args: list) -> tuple:
    import os
    env = {**dict(os.environ), "PYTHONIOENCODING": "utf-8"}
    cp = subprocess.run(
        [sys.executable, str(_MODULE_PATH), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    out = {}
    if cp.stdout.strip():
        try:
            out = json.loads(cp.stdout)
        except Exception:
            pass
    return cp.returncode, (cp.stderr or ""), out


def test_main_backward_compatible_single_arg():
    """[向后兼容] 只传章节文件（现 audit_hub call-site 的形态）→ 不崩、floor=3 触发。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "body.txt"
        f.write_text(_FOUR_PERIOD_PARA, encoding="utf-8")
        rc, err, out = _run([str(f)])
        assert "Traceback" not in err, err
        assert rc == 1, f"4 句号短段应触发 → exit 1，实际 rc={rc}, err={err}"
        assert out.get("verdict") != "PASS"


def test_main_project_arg_relaxes_end_to_end():
    """[L85 端到端] 传 --project 且作者档为短句作者 → relax 生效 → exit 0 PASS。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "作者风格.json").write_text(
            json.dumps({"quantitative": {"sentence_length": {"mean": 16.8}}}, ensure_ascii=False),
            encoding="utf-8")
        f = proj / "body.txt"
        f.write_text(_FOUR_PERIOD_PARA, encoding="utf-8")
        rc, err, out = _run([str(f), "--project", str(proj)])
        assert "Traceback" not in err, err
        assert rc == 0, f"短句作者基线下应 PASS → exit 0，实际 rc={rc}, err={err}, out={out}"
        assert out.get("verdict") == "PASS"


def test_main_unknown_args_ignored():
    """传未知/多余参数不影响解析（容错），仍正常输出 JSON。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "body.txt"
        f.write_text("普通叙述一句话这里写满十五个汉字以上方便扫描器处理它。", encoding="utf-8")
        rc, err, out = _run([str(f), "--whatever", "x"])
        assert "Traceback" not in err, err
        assert out.get("scanner") == "narrative_short_sentence_overuse"


def test_main_missing_file_exits_2():
    """文件不存在 → exit 2（沿用原契约）。"""
    rc, err, out = _run([str(Path(tempfile.gettempdir()) / "definitely_no_such_file_xyz.txt")])
    assert rc == 2


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
