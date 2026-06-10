#!/usr/bin/env python3
"""judge_runner.py 测试（程序驱动 M2）——纯 mock，不打真 API。

覆盖四条硬契约：作者档注入存续 / 重试只针对结构破损 / failure_policy block-soft 分级 /
适配头(代读+free_notes)。外加 frontmatter 剥离、双载体落盘、CLI 注册表完整性。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import judge_runner as jr  # noqa: E402
import llm_transport as lt  # noqa: E402
from gen_model_loader import Profile  # noqa: E402


def _profile(name="p1"):
    return Profile(name=name, model="m", base_url="https://x.test/v1",
                   api_key="sk-test", temperature=0.8, max_tokens=None)


def _mk_gen(script):
    """script: list[str]——每次 generate() 返回下一个文本（模拟 LLM 回复）。"""
    calls = []

    def fn(profiles, system, user, **kw):
        calls.append({"system": system, "user": user, "kw": kw})
        if not script:
            raise lt.TransportExhausted([("p1", "script 耗尽")])
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return lt.GenResult(text=item, profile=_profile(), finish_reason="stop")

    return fn, calls


def _setup_project(tmp, with_author_profile=True):
    proj = Path(tmp) / "proj"
    (proj / "_数据库").mkdir(parents=True)
    if with_author_profile:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"句长均值": 31, "段落均长": 52}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _setup_agents(tmp):
    """临时 agents 目录（不依赖真实 .claude/agents 内容变化）。"""
    d = Path(tmp) / "agents"
    d.mkdir()
    for spec_name in jr.AGENT_SPECS:
        (d / f"{spec_name}.md").write_text(
            f"---\nname: {spec_name}\ntools: Read, Write\n---\n\n"
            f"你是 {spec_name}。Read 草稿后输出 JSON。", encoding="utf-8")
    return d


# ============ 注册表完整性 ============
def test_registry_covers_8_agents():
    assert len(jr.AGENT_SPECS) == 8
    # block 级 = 喂状态机的三个（对抗审查定调）
    blocks = {n for n, s in jr.AGENT_SPECS.items() if s.failure_policy == "block"}
    assert blocks == {"novel-summarizer", "novel-foreshadower", "novel-outline-planner"}


def test_registry_style_judges_need_author_profile():
    for n in ("novel-voice-checker", "novel-validator-checker",
              "novel-outline-planner", "novel-reading-reflector"):
        assert jr.AGENT_SPECS[n].needs_author_profile, n


def test_real_agent_md_files_exist():
    """注册表 8 个 agent 在真实 .claude/agents/ 都有定义（researcher 也在）。"""
    for n in jr.AGENT_SPECS:
        assert (jr.AGENTS_DIR / f"{n}.md").exists(), f"缺 agent 定义: {n}.md"


# ============ frontmatter / prompt 装配 ============
def test_load_agent_prompt_strips_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        d = _setup_agents(tmp)
        text = jr.load_agent_system_prompt("novel-summarizer", agents_dir=d)
        assert "tools:" not in text and "你是 novel-summarizer" in text


def test_assemble_user_prompt_reads_files_fully():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "draft.txt"
        f.write_text("正文" * 5000, encoding="utf-8")  # 1万字不截断
        u = jr.assemble_user_prompt({"CLUSTER_ID": "cluster_001"}, [("草稿", f)])
        assert "CLUSTER_ID: cluster_001" in u
        assert u.count("正文") == 5000  # 全量注入（feedback_no_token_saving）


def test_assemble_user_prompt_missing_file_noted():
    u = jr.assemble_user_prompt({}, [("缺失", Path("Z:/不存在.txt"))])
    assert "文件读取失败" in u


# ============ 硬契约 1：作者档注入存续 ============
def test_author_profile_injected_for_style_judge():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        fn, calls = _mk_gen(['```json\n{"violations": []}\n```'])
        out = jr.run_judge("novel-voice-checker", proj, params={"CLUSTER_ID": "001"},
                           agents_dir=agents, _generate_fn=fn)
        assert out.ok and not out.author_profile_missing
        assert "作者风格档（第一权威" in calls[0]["system"]
        assert '"句长均值": 31' in calls[0]["system"]


def test_author_profile_missing_guard_not_generic_fallback():
    """档缺失 → 注入「不得输出风格 finding」守卫 + 报告标记——绝不退回通用规则。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_project(tmp, with_author_profile=False)
        agents = _setup_agents(tmp)
        fn, calls = _mk_gen(['```json\n{"violations": []}\n```'])
        out = jr.run_judge("novel-voice-checker", proj, params={},
                           agents_dir=agents, _generate_fn=fn)
        assert out.author_profile_missing
        assert "不得输出任何风格/文笔/节奏类 finding" in calls[0]["system"]
        assert out.data["_author_profile_missing"] is True


def test_non_style_judge_skips_author_profile():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        fn, calls = _mk_gen(['```json\n{"cluster_id": "c1", "summary": "s", '
                             '"emotion": {"value": 0}}\n```'])
        out = jr.run_judge("novel-summarizer", proj, params={},
                           agents_dir=agents, _generate_fn=fn)
        assert out.ok and not out.author_profile_missing
        assert "作者风格档（第一权威" not in calls[0]["system"]


def test_corrupt_author_profile_treated_as_missing():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_project(tmp, with_author_profile=False)
        (proj / "_数据库" / "作者风格.json").write_text("{半截", encoding="utf-8")
        assert jr.build_author_profile_block(proj) is None


# ============ 硬契约 2：重试只针对结构破损 ============
def test_format_retry_on_parse_failure_then_success():
    fn, calls = _mk_gen(["这不是 JSON", '```json\n{"entries": []}\n```'])
    data, retries, _ = jr.judge_call([_profile()], "s", "u",
                                     required_keys=("entries",), _generate_fn=fn)
    assert data == {"entries": []} and retries == 1
    assert "格式重试提示" in calls[1]["user"]  # 重试带强化提示


def test_format_retry_on_missing_required_key():
    fn, calls = _mk_gen(['{"wrong_key": 1}', '{"entries": [1]}'])
    data, retries, _ = jr.judge_call([_profile()], "s", "u",
                                     required_keys=("entries",), _generate_fn=fn)
    assert data == {"entries": [1]} and retries == 1
    assert "顶层缺键" in calls[1]["user"]


def test_no_retry_on_valid_json_whatever_content():
    """判断内容「不符预期」绝不触发重试——合法 JSON + 键齐 = 一次过（北极星⑤）。"""
    weird = '{"entries": "完全自由发挥的字符串而非数组", "free_notes": "作者档独有维度"}'
    fn, calls = _mk_gen([weird])
    data, retries, _ = jr.judge_call([_profile()], "s", "u",
                                     required_keys=("entries",), _generate_fn=fn)
    assert retries == 0 and len(calls) == 1
    assert data["free_notes"] == "作者档独有维度"


def test_format_retry_capped():
    fn, calls = _mk_gen(["垃圾1", "垃圾2", "垃圾3"])
    data, retries, _ = jr.judge_call([_profile()], "s", "u",
                                     required_keys=("k",), max_format_retries=2,
                                     _generate_fn=fn)
    assert data.get("_parse_failed") and len(calls) == 3  # 1 原始 + 2 重试


# ============ 硬契约 3：failure_policy 分级 ============
def test_block_judge_raises_on_structure_failure():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        fn, _ = _mk_gen(["垃圾", "垃圾", "垃圾"])
        try:
            jr.run_judge("novel-summarizer", proj, params={},
                         agents_dir=agents, _generate_fn=fn)
            assert False, "block 级结构失败必须抛 JudgeBlockedError"
        except jr.JudgeBlockedError as e:
            assert "不可静默透传" in str(e)


def test_soft_judge_degrades_not_raises():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        fn, _ = _mk_gen(["垃圾", "垃圾", "垃圾"])
        out = jr.run_judge("novel-reflector", proj, params={},
                           agents_dir=agents, _generate_fn=fn,
                           output_path=Path(tmp) / "out.json")
        assert out.degraded and not out.ok
        saved = json.loads((Path(tmp) / "out.json").read_text(encoding="utf-8"))
        assert saved["_degraded"] is True  # 降级也落盘留痕·不静默消失


def test_block_judge_raises_on_transport_exhausted():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)

        def fn(*a, **k):
            raise lt.TransportExhausted([("p1", "down")])

        try:
            jr.run_judge("novel-foreshadower", proj, params={},
                         agents_dir=agents, _generate_fn=fn)
            assert False
        except jr.JudgeBlockedError:
            pass


def test_soft_judge_survives_transport_exhausted():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)

        def fn(*a, **k):
            raise lt.TransportExhausted([("p1", "down")])

        out = jr.run_judge("novel-voice-checker", proj, params={},
                           agents_dir=agents, _generate_fn=fn)
        assert out.degraded and out.data.get("_transport_exhausted")


# ============ 硬契约 4 + 适配头 ============
def test_adapter_header_in_system():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        fn, calls = _mk_gen(['{"entries": []}'])
        jr.run_judge("novel-reflector", proj, params={}, agents_dir=agents,
                     _generate_fn=fn)
        s = calls[0]["system"]
        assert "无法 Read/Write 文件" in s and "free_notes" in s


def test_judge_call_uses_json_mode_and_low_temp():
    fn, calls = _mk_gen(['{"k": 1}'])
    jr.judge_call([_profile()], "s", "u", _generate_fn=fn)
    kw = calls[0]["kw"]
    assert kw["response_format_json"] is True
    assert kw["temperature"] == jr.JUDGE_TEMPERATURE


# ============ 落盘（双载体） ============
def test_voice_checker_dual_output():
    with tempfile.TemporaryDirectory() as tmp:
        proj, agents = _setup_project(tmp), _setup_agents(tmp)
        reply = json.dumps({"violations": [], "judge_report": {"judge_id": "vc",
                                                               "overall_grade": "A"}},
                           ensure_ascii=False)
        fn, _ = _mk_gen([reply])
        brief = Path(tmp) / "brief.json"
        jrpt = Path(tmp) / "judge.json"
        out = jr.run_judge("novel-voice-checker", proj, params={},
                           agents_dir=agents, _generate_fn=fn,
                           output_path=brief, secondary_output_path=jrpt)
        assert out.ok
        assert json.loads(brief.read_text(encoding="utf-8"))["violations"] == []
        assert json.loads(jrpt.read_text(encoding="utf-8"))["overall_grade"] == "A"


def test_unknown_agent_rejected():
    try:
        jr.run_judge("novel-nonexistent", ".", params={})
        assert False
    except KeyError:
        pass


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
