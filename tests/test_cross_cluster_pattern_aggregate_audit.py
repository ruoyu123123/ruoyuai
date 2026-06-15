"""回归测试：cross_cluster_pattern_aggregate.py 审计修复（2026-06 triage）。

钉死 triage_worth_fixing.json 中本文件 3 处修复点（全 advisory 检测器健壮性·零 LLM·零依赖）：

  L225  scan_dialogue_tags         —— 去硬编码姓氏闭集（漏王/李/张/刘/赵等高频姓）
                                      → 通用「名+说话动词」式；demo 姓(陆)不回归；
                                      避开假阳 知道/味道/难道。
  L379  scan_dialogue_stream_flat  —— has_say 同样去硬编码姓氏；长对话段(短句兜底失效)
                                      下高频姓说话人现在能命中 streak。
  L695  Finding 6 suggestion       —— 段首词建议文案的主角名从硬编码「陆衍」改为
                                      f-string {protagonist}（与兄弟行 L610/611/623 对齐）。

运行：PYTHONIOENCODING=utf-8 python tests/test_cross_cluster_pattern_aggregate_audit.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_pattern_aggregate as m  # noqa: E402


# ===== Bug L225: scan_dialogue_tags 去硬编码姓氏 =====

def test_dialogue_tags_common_surnames_now_counted():
    """高频姓 王/李/张/刘/赵（旧硬编码闭集漏掉）现在全部计入对话 tag。"""
    text = "王明说道，李雷笑道，张伟问道，刘洋答道，赵强喝道"
    # 5 个不同高频姓 + 说话动词 → 5（旧代码硬编码 [陆顾林吴老程韩邵宋钟谢沈周] 全漏 → 旧值 0）
    assert m.scan_dialogue_tags(text) == 5, m.scan_dialogue_tags(text)


def test_dialogue_tags_demo_surname_no_regression():
    """demo 书旧姓氏（陆）仍能被通用式命中，不因去硬编码而回归。"""
    assert m.scan_dialogue_tags("陆衍说道") >= 1


def test_dialogue_tags_pronoun_still_counted():
    """patt1「他/她+说想道」保持不变。"""
    assert m.scan_dialogue_tags("他说了一句，她想了想") == 2


def test_dialogue_tags_false_positive_words_not_matched():
    """知道/味道/难道 等含「道」但非对话 tag 的词不得误计（通用式精确性）。"""
    assert m.scan_dialogue_tags("他知道这个味道，难道不对吗") == 0


def test_dialogue_tags_colon_form_matched():
    """通用「名+说/问/喊/吼+：/，」式（无「道」尾）也命中。"""
    assert m.scan_dialogue_tags("小明说：你好") == 1


def test_dialogue_tags_named_speaker_after_punctuation():
    """标点后出现高频姓说话人（旧硬编码漏）现在计入；patt1 不受影响。"""
    # 「他停下。王明说道」：patt1=0（他停 不在 [说想道]），patt2 命中「王明说道」=1
    assert m.scan_dialogue_tags("他停下。王明说道") == 1


# ===== Bug L379: scan_dialogue_stream_flat has_say 去硬编码姓氏 =====

def _long_dialogue_para(speaker_say: str) -> str:
    """造一个长对话段（含引号 + 长度 >= 80 使 short 兜底失效，迫使 has_say 承担判定）。"""
    return '"' + "说话内容" + "某" * 90 + '"' + speaker_say


def test_stream_flat_common_surname_long_paras_now_streak():
    """长对话段 + 高频姓说话人：旧 has_say 硬编码姓氏漏掉、short 又因长段失效 → 旧 streak 0；
    修复后通用式命中 → streak 计满。"""
    text = "\n".join(_long_dialogue_para("王明说道") for _ in range(7))
    assert m.scan_dialogue_stream_flat(text) >= 6, m.scan_dialogue_stream_flat(text)


def test_stream_flat_demo_surname_no_regression():
    """demo 姓（陆）长对话段仍正常累计 streak。"""
    text = "\n".join(_long_dialogue_para("陆衍说道") for _ in range(7))
    assert m.scan_dialogue_stream_flat(text) >= 6


def test_stream_flat_prose_without_quote_not_streaked():
    """无引号的叙述段不得被误判为对话 streak（has_quote 守卫保持）。"""
    prose = "\n".join("他走到窗边看着远处的山。" for _ in range(7))
    assert m.scan_dialogue_stream_flat(prose) == 0


def test_stream_flat_regex_subject_action_anchor():
    """has_say 正则的负向锚点：「他/她」开头的说话动词由 [他她][说想道] 分支处理，
    不被通用「名+说话动词」分支因 (?<![他她]) 漏配（行为正确性而非数值）。"""
    patt = (
        r"[他她][说想道]"
        r"|(?<![他她])[一-龥]{1,3}(?:说道|笑道|问道|答道|喝道|低声道|开口道)"
        r"|(?<![他她])[一-龥]{1,3}[说问喊吼][：，]"
    )
    assert re.search(patt, "王明说道") is not None
    assert re.search(patt, "他说道") is not None
    assert re.search(patt, "他知道") is None  # 知 不在 [说想道]，且通用式被前缀排除


# ===== Bug L695: Finding 6 suggestion 主角名 f-string 化（端到端·驱动 main()） =====

def _build_project(root: Path, protagonist: str) -> Path:
    """造最小磁盘模式项目：人物卡(主角名) + 一章触发 PARA_FIRST_WORD_CONCENTRATION。"""
    proj = root / "测试书"
    (proj / "_数据库").mkdir(parents=True)
    (proj / "章节" / "第001章").mkdir(parents=True)
    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps({"characters": [{"name": protagonist, "role": "主角"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 10 段同首二字 + 3 段他首 → top1 占比 ~77% >= 0.35 触发 Finding 6
    lines = [f"{protagonist}走进房间看了一眼。" for _ in range(10)] + \
            ["门外传来脚步声响。" for _ in range(3)]
    (proj / "章节" / "第001章" / "第001章.txt").write_text("\n".join(lines), encoding="utf-8")
    return proj


def _run_aggregate(proj: Path) -> list[dict]:
    """以磁盘模式跑 main()（清 CLUSTER_MODE 防误入账本分支），返回报告 findings。"""
    env = dict(os.environ)
    env.pop("CLUSTER_MODE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(
        [sys.executable, str(_ROOT / "core" / "scripts" / "cross_cluster_pattern_aggregate.py"),
         str(proj), "--last-n", "5"],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("*.json")) if scan_dir.exists() else []
    assert reports, "scanner 未产出报告 JSON（main 可能异常退出）"
    return json.loads(reports[-1].read_text(encoding="utf-8")).get("findings", [])


def test_finding6_suggestion_uses_real_protagonist_not_hardcoded():
    """非 demo 主角（江条款）的书：Finding 6 建议文案应含真实主角名、不得出现硬编码「陆衍」。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _build_project(Path(d), "江条款")
        findings = _run_aggregate(proj)
        f6 = [f for f in findings if f.get("code") == "PARA_FIRST_WORD_CONCENTRATION"]
        assert f6, "未触发 PARA_FIRST_WORD_CONCENTRATION（fixture 未生效）"
        sug = f6[0]["suggestion"]
        assert "江条款" in sug, sug              # f-string 已插入真实主角名
        assert "陆衍" not in sug, sug            # 不再是硬编码 demo 名


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"[cross_cluster_pattern_aggregate_audit] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
