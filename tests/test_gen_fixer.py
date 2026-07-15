"""gen_fixer 回归测试。

gen_fixer 是 writer 之外第二个 gen-model 入口，且**原地覆写整章正文**（parse_and_apply），
截断/空响应后果比 gen_writer 写草稿更重 = 销毁已发布章节。call_gen_model 委托
llm_transport.generate() 做统一重试/续写/双协议分发（该机制本身的测试见
tests/test_llm_transport.py），本文件守护 wrapper 契约 + CJK 守恒校验：
  ① finish_reason 捕获 + 截断自动续写（fixer 专属续写文案）
  ② RateLimit 同 profile 重试
  ③ 空响应守卫（切 fallback / 全空 raise）
  ④ parse_and_apply CJK 守恒（整章 ±30% 超限拒绝覆写）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_fixer as gf
import gen_model_loader as gml


# ============ mock 基础设施（对齐 test_gen_writer 风格） ============
class _P:
    """mock profile。"""
    def __init__(self, max_tokens=None, model="m"):
        self.max_tokens = max_tokens
        self.model = model
        self.temperature = 0.8
        self.name = "mock"


def _profile(name, model="m"):
    p = _P(model=model)
    p.name = name
    p.api_key = "sk-test"
    p.base_url = "http://localhost/v1"
    return p


class _Loader:
    """mock loader：get_callable_profiles 返回给定 profile 列表。"""
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles


def _mock_client(chunks_spec, capture=None):
    """chunks_spec: [(content, finish_reason), ...]。"""
    class MockChoice:
        def __init__(s, c, fr):
            s.delta = type("D", (), {"content": c})()
            s.finish_reason = fr

    class MockChunk:
        def __init__(s, c, fr):
            s.choices = [MockChoice(c, fr)]

    class MockStream:
        def __iter__(s):
            return iter([MockChunk(c, fr) for c, fr in chunks_spec])

    class MockCompletions:
        def create(s, **kw):
            if capture is not None:
                capture["messages"] = kw["messages"]
            return MockStream()

    class MockClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": MockCompletions()})()

    return MockClient()


def _client_returning(chunks_spec):
    return _mock_client(chunks_spec)


def _patch_openai(mock_clients):
    """把 gen_fixer 用到的 OpenAI 工厂换成按构造顺序产出 mock client 的桩。

    返回 (restore_fn, captured_kwargs_list)。
    """
    import openai
    captured = []
    seq = list(mock_clients)

    def fake_openai(**kwargs):
        captured.append(kwargs)
        return seq[len(captured) - 1]

    orig = openai.OpenAI
    openai.OpenAI = fake_openai
    return (lambda: setattr(openai, "OpenAI", orig)), captured


# ============ resolve_max_tokens（沿用纯函数守护） ============
def test_resolve_max_tokens_explicit():
    mt, src = gf.resolve_max_tokens(_P(max_tokens=20000))
    assert mt == 20000 and src == "profile_explicit"


def test_resolve_max_tokens_default_fallback():
    mt, src = gf.resolve_max_tokens(_P(model="__nonexistent_model_xyz__"))
    assert mt == 16000


# ============ ① fixer 专属续写文案 + 截断自动续写 ============
def test_fixer_cont_msg_mentions_file_end_block():
    """fixer 续写文案必须提醒补全 ===FILE:.../===END=== 块（覆写半截会销毁已发布章节，
    不能沿用 gen_writer 的通用「补 CHANGES 块」续写文案）。"""
    msg = gf._fixer_cont_msg("length", 1)
    assert "截断" in msg
    assert "===END===" in msg


def test_call_gen_model_auto_continues_on_length():
    """② 截断自动续写：第一段 finish_reason=length → 触发续写，拼接完整正文。"""
    # 第一次返回截断（length），第二次（续写）返回 stop
    class TwoPhaseCompletions:
        def __init__(s):
            s.calls = 0

        def create(s, **kw):
            s.calls += 1
            if s.calls == 1:
                return _mock_client([("前半", "length")]).chat.completions.create(**kw)
            return _mock_client([("后半", "stop")]).chat.completions.create(**kw)

    class TwoPhaseClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": TwoPhaseCompletions()})()

    client = TwoPhaseClient()
    restore, _ = _patch_openai([client])
    try:
        text, used = gf.call_gen_model(_Loader([_profile("active")]), "sys", "usr")
    finally:
        restore()
    assert text == "前半后半"
    assert client.chat.completions.calls == 2  # 续写了 1 轮


# ============ ② 空响应守卫 ============
def test_call_gen_model_empty_response_switches_fallback():
    """④ 空响应不当成功 → 切 fallback（否则会拿空回复覆写整章）。"""
    empty_client = _client_returning([("", "stop")])
    good_client = _client_returning([("真修复正文", "stop")])
    restore, _ = _patch_openai([empty_client, good_client])
    try:
        text, used = gf.call_gen_model(
            _Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    finally:
        restore()
    assert text == "真修复正文"
    assert used.name == "fallback"


def test_call_gen_model_all_empty_raises():
    """④ active + fallback 全空 → raise GenModelExhaustedError（绝不返回空文本去覆写）。"""
    restore, _ = _patch_openai([
        _client_returning([("   ", "stop")]),
        _client_returning([("", "stop")]),
    ])
    raised = False
    try:
        gf.call_gen_model(_Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    except gml.GenModelExhaustedError:
        raised = True
    finally:
        restore()
    assert raised, "全链空响应必须 raise GenModelExhaustedError"


# ============ ③ 限流同 profile 重试 ============
class _FakeResp:
    status_code = 429
    headers = {}
    request = None


def test_call_gen_model_retries_same_profile_on_ratelimit():
    """③ 限流在同 profile 做有限重试（不一次就降级 fallback）。"""
    from openai import RateLimitError

    class FlakyCompletions:
        def __init__(s):
            s.calls = 0

        def create(s, **kw):
            s.calls += 1
            if s.calls <= 2:
                raise RateLimitError("rate limited", response=_FakeResp(), body=None)

            class Choice:
                def __init__(c):
                    c.delta = type("D", (), {"content": "重试成功修复正文"})()
                    c.finish_reason = "stop"

            class Chunk:
                def __init__(c):
                    c.choices = [Choice()]
            return iter([Chunk()])

    class FlakyClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": FlakyCompletions()})()

    flaky = FlakyClient()
    restore, _ = _patch_openai([flaky, _client_returning([("不该用到", "stop")])])
    import time as _t
    orig_sleep = _t.sleep
    _t.sleep = lambda *a, **k: None
    try:
        text, used = gf.call_gen_model(
            _Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    finally:
        _t.sleep = orig_sleep
        restore()
    assert text == "重试成功修复正文"
    assert used.name == "active"  # 同 profile 重试成功，没降级
    assert flaky.chat.completions.calls == 3  # 抛2次 + 成功1次


# ============ ④ parse_and_apply CJK 守恒校验 ============
def _reply_with_file(rel_path, body):
    return f"===FILE: {rel_path}===\n{body}\n===END===\n```json\n{{}}\n```"


def _make_chapter(root: Path, rel_path: str, body: str):
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_and_apply_accepts_within_tolerance():
    """⑤ 修复后字数在原文 ±30% 内 → 正常覆写。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "原始正文" * 10  # 40 CJK
        _make_chapter(root, rel, original)
        new_body = "修复后正文" * 8  # 40 CJK（同量级）
        reply = _reply_with_file(rel, new_body)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert len(written) == 1
        assert not rejected
        assert (root / rel).read_text(encoding="utf-8").strip() == new_body


def test_parse_and_apply_rejects_truncated_overwrite():
    """⑤ 核心：修复输出被截断（字数暴跌 > 30%）→ 拒绝覆写，保留原文不动。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "已发布的完整正文内容" * 20  # 200 CJK
        _make_chapter(root, rel, original)
        truncated = "只剩半截"  # 4 CJK，远低于 -30%
        reply = _reply_with_file(rel, truncated)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert not written, "截断输出不应覆写"
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "cjk_conservation_violation"
        # 原文必须原封不动
        assert (root / rel).read_text(encoding="utf-8") == original


def test_parse_and_apply_rejects_runaway_expansion():
    """⑤ 修复输出字数暴涨 > 30%（退化/重复输出）→ 同样拒绝覆写。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "原文" * 10  # 20 CJK
        _make_chapter(root, rel, original)
        runaway = "暴涨重复退化输出" * 20  # 160 CJK，远超 +30%
        reply = _reply_with_file(rel, runaway)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert not written
        assert len(rejected) == 1
        assert (root / rel).read_text(encoding="utf-8") == original


def test_word_count_mode_removed_and_conservation_has_no_bypass():
    """⑤ 回归锁（W3 旁路移除 2026-07-05）：--mode word-count 章级扩写旁路已删
    （v27 pending_tail 取代短章补字），parse_and_apply 的 CJK 守恒恒开、无豁免参数。"""
    import inspect
    src = inspect.getsource(gf)
    assert "word-count" not in src, "word-count 扩写模式不得复活（v27 pending_tail 取代）"
    assert not hasattr(gf, "build_word_count_prompt"), "build_word_count_prompt 应已删除"
    sig = inspect.signature(gf.parse_and_apply)
    assert "enforce_cjk_conservation" not in sig.parameters, "CJK 守恒不得留旁路开关"


def test_parse_and_apply_recovers_dropped_path_segment():
    """2026-06-02：LLM 自报 ===FILE: 路径漏段（如 cluster draft 漏「章节/」前缀）→ 旧版写到
    不存在路径崩 FileNotFoundError。修复后按 basename 回正到「修复前读过的已知文件」，
    写对位置、不在漏段路径误建文件。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/cluster_001_draft/cluster_001_draft.txt"  # 真实路径含「章节/」
        original = "原始草稿正文" * 10  # 60 CJK
        _make_chapter(root, rel, original)
        wrong_rel = "cluster_001_draft/cluster_001_draft.txt"  # LLM 漏了「章节/」
        new_body = "修复后草稿正文" * 9  # 同量级（过 CJK 守恒）
        reply = _reply_with_file(wrong_rel, new_body)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert len(written) == 1, "应按 basename 回正并写入，而非崩 FileNotFoundError"
        assert not rejected
        assert (root / rel).read_text(encoding="utf-8").strip() == new_body
        assert not (root / wrong_rel).exists(), "不应在漏段的错误路径误建文件"


def test_parse_and_apply_strips_backtick_wrapped_path():
    """2026-06-02：LLM 把 ===FILE: 路径用 markdown 反引号/引号包裹（`path`）→旧版 join 成带反引号
    非法路径 OSError 崩。修复后剥掉首尾反引号/引号再解析，正常写入。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/cluster_002_draft/cluster_002_draft.txt"
        original = "原始草稿正文" * 10
        _make_chapter(root, rel, original)
        abs_path = str(root / rel)
        new_body = "修复草稿正文" * 9
        reply = _reply_with_file("`" + abs_path + "`", new_body)  # 反引号包裹绝对路径
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert len(written) == 1, "反引号包裹路径应剥离后正常写入，而非 OSError"
        assert (root / rel).read_text(encoding="utf-8").strip() == new_body


def test_parse_and_apply_ambiguous_basename_not_recovered():
    """回正只在 basename 唯一匹配时生效——多个同名已读文件时不猜（避免写错文件）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel_a = "章节/cluster_001_draft/draft.txt"
        rel_b = "章节/cluster_002_draft/draft.txt"
        _make_chapter(root, rel_a, "原文A" * 10)
        _make_chapter(root, rel_b, "原文B" * 10)
        reply = _reply_with_file("draft.txt", "新正文" * 9)  # 漏段且 basename 二义
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel_a: "原文A" * 10, rel_b: "原文B" * 10})
        # basename 二义 → 不回正 → 该块写不进（不误伤任一文件）
        assert (root / rel_a).read_text(encoding="utf-8") == "原文A" * 10
        assert (root / rel_b).read_text(encoding="utf-8") == "原文B" * 10


def test_parse_and_apply_no_before_content_does_not_block():
    """守恒校验不误伤：没有 before_content（拿不到原文）时不阻断覆写（仅缺兜底，不能反而失败）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        _make_chapter(root, rel, "原文" * 50)
        reply = _reply_with_file(rel, "新")  # 字数差异巨大，但无 before 基线
        written, summary, rejected = gf.parse_and_apply(reply, root)
        assert len(written) == 1
        assert not rejected


# ============ ⑤ 路径基座单一口径（--files / --report-file / --brief 全部相对 --project） ============
# 真机 e2e：--files 相对 --project 解析、--report-file 却按 CWD 解析（双基座），
# 调用方连撞 2 次 file-not-found。锁死「同一脚本内所有路径参数同基座」。
import json as _json          # noqa: E402
import os as _os              # noqa: E402
import subprocess as _sp      # noqa: E402

_GEN_FIXER = Path(__file__).resolve().parents[1] / "core" / "scripts" / "gen_fixer.py"


def _mk_fixer_project(td: Path):
    """造一个最小 cluster 项目：草稿 + changes.json + reflector 报告（都在项目内）。"""
    draft_dir = td / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "cluster_001_draft.txt").write_text("原始正文段落。" * 20, encoding="utf-8")
    (draft_dir / "cluster_001_changes.json").write_text(
        _json.dumps({"self_eval": {"ecas_metadata": {"cluster_id": "cluster_001",
                                                     "cjk_actual": 1}}},
                    ensure_ascii=False), encoding="utf-8")
    qdir = td / "章节" / "_quality"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "reflector_R1.json").write_text(
        _json.dumps({"issues": [{"id": "RR_R1_001", "severity": "major"}]},
                    ensure_ascii=False), encoding="utf-8")
    return td


def _run_fixer(args, cwd):
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return _sp.run([sys.executable, str(_GEN_FIXER)] + args, cwd=str(cwd),
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=env, timeout=120)


def test_report_file_resolves_against_project_not_cwd():
    """--report-file 与 --files 同基座：项目内相对路径必须解析得到（CWD 在别处也能跑）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        other_cwd = Path(td) / "elsewhere"
        other_cwd.mkdir()
        r = _run_fixer(["--project", str(proj), "--mode", "comprehensive",
                        "--report-file", "章节/_quality/reflector_R1.json",
                        "--files", "章节/cluster_001_draft/cluster_001_draft.txt",
                        "--dry-run"], cwd=other_cwd)
        assert r.returncode == 0, f"项目相对 --report-file 应解析成功\nstderr={r.stderr}"
        assert "FileNotFoundError" not in r.stderr


def test_report_file_missing_is_loud_fatal_not_traceback():
    """--report-file 真不存在 → [FATAL] + exit 2（不降级、不裸崩 FileNotFoundError）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        r = _run_fixer(["--project", str(proj), "--mode", "comprehensive",
                        "--report-file", "章节/_quality/不存在.json",
                        "--files", "章节/cluster_001_draft/cluster_001_draft.txt",
                        "--dry-run"], cwd=proj)
        assert r.returncode == 2, f"缺 report 必须 exit 2，实际 {r.returncode}"
        assert "[FATAL" in r.stderr
        assert "Traceback" not in r.stderr, "契约错误必须响亮报错，不能裸崩 traceback"


def test_no_cwd_relative_path_arg_remains():
    """回归锁：源码里不得再出现 CWD 基座的裸 Path(args.<path>) 读取（双基座根因）。"""
    src = _GEN_FIXER.read_text(encoding="utf-8")
    assert "Path(args.report_file).read_text" not in src
    # 三个路径参数都必须过 is_absolute() → project_root 解析
    for arg in ("args.report_file", "args.brief"):
        assert f"{arg}" in src
    assert src.count("is_absolute()") >= 4


# ============ ⑥ 改稿后 changes.json 字数遥测回写（changes_io 单一真理源） ============
def test_run_scanners_forces_utf8_child_env():
    """子进程 scanner 必须强制 PYTHONIOENCODING=utf-8（GBK 环境下 CJK stdout 会 mojibake）。"""
    src = _GEN_FIXER.read_text(encoding="utf-8")
    assert "PYTHONIOENCODING" in src and "PYTHONUTF8" in src


def test_main_syncs_changes_cjk_after_fix(monkeypatch):
    """核心回归锁：gen_fixer 改稿落盘后必须回写 changes.json 的 cjk_actual。

    真机实测 desync（changes 12055 vs 草稿 12004）→ writer_truth_check 判 writer 说谎
    → cluster-save-state step 3 阻断。此处锁死 main() 走 changes_io 回写。
    """
    import changes_io  # noqa: E402
    import writer_truth_check as wtc  # noqa: E402

    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        rel = "章节/cluster_001_draft/cluster_001_draft.txt"
        fixed_body = "修复后的正文段落。" * 18  # 与原文同量级（过 CJK 守恒）

        monkeypatch.setattr(gf, "GenModelLoader", lambda *a, **k: _FakeLoader())
        monkeypatch.setattr(gf, "call_gen_model",
                            lambda loader, system, user: (
                                _reply_with_file(rel, fixed_body), _profile("mock")))
        monkeypatch.setattr(gf, "run_scanners", lambda paths: {})
        monkeypatch.setattr(sys, "argv", [
            "gen_fixer.py", "--project", str(proj), "--mode", "polish",
            "--files", rel, "--instructions", "把第 3 段收紧"])

        gf.main()

        changes = _json.loads(
            (proj / "章节" / "cluster_001_draft" / "cluster_001_changes.json")
            .read_text(encoding="utf-8"))
        meta = changes["self_eval"]["ecas_metadata"]
        real = changes_io.count_cjk((proj / rel).read_text(encoding="utf-8"))
        assert meta["cjk_actual"] == real, "改稿后 cjk_actual 必须对齐新草稿真值"
        assert meta["word_count_cjk"] == real
        # end-to-end：writer_truth_check 不再报 cjk 说谎 → save-state 放行
        rep = wtc.truth_check_cluster(proj, "cluster_001")
        assert rep["lie_count"] == 0, rep["lies_detected"]


class _FakeLoader:
    """main() 里只用到 get_active_profile / get_fallback_chain（call_gen_model 已被打桩）。"""
    env_path = "(fake)"

    def get_active_profile(self):
        return _profile("mock")

    def get_fallback_chain(self):
        return []


# ============ ⑦ checker brief 统一契约（version=2 + draft_path·cluster-only） ============
def _mk_brief(proj: Path, payload: dict, name="cluster_001_voice.json") -> str:
    bdir = proj / "_数据库" / ".checker_briefs"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / name).write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return f"_数据库/.checker_briefs/{name}"


def test_voice_fix_accepts_v2_draft_path_brief():
    """voice-checker 真机形态 brief（version=2 + carrier=cluster + draft_path）→ voice-fix 可跑通（dry-run）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        rel = _mk_brief(proj, {
            "version": 2, "carrier": "cluster", "cluster_id": "cluster_001",
            "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt",
            "checker": "novel-voice-checker",
            "violations": [{"line_start": 1, "line_end": 1, "original": "原始正文段落。",
                            "issue": "voice_drift", "fix_hint": "按角色短句改写"}],
        })
        r = _run_fixer(["--project", str(proj), "--mode", "voice-fix",
                        "--brief", rel, "--dry-run"], cwd=proj)
        assert r.returncode == 0, f"v2+draft_path brief 必须可跑\nstderr={r.stderr}"
        assert "cluster_001_draft.txt" in r.stdout, "prompt 必须引用 cluster 草稿路径"


def test_brief_missing_draft_path_rejected_loud():
    """brief 缺 draft_path（含旧 chapter_path 残留形态）→ exit 3 响亮报错，不静默降级。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        rel = _mk_brief(proj, {
            "version": 2, "carrier": "cluster", "cluster_id": "cluster_001",
            "chapter_path": "章节/cluster_001_draft/cluster_001_draft.txt",  # 旧口径
            "violations": [],
        })
        r = _run_fixer(["--project", str(proj), "--mode", "voice-fix",
                        "--brief", rel, "--dry-run"], cwd=proj)
        assert r.returncode == 3, f"缺 draft_path 必须 exit 3，实际 {r.returncode}"
        assert "draft_path" in r.stderr


def test_brief_version_1_rejected_with_regenerate_hint():
    """version=1 旧 brief → exit 3，报错指向重产 brief（统一契约 version=2）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_fixer_project(Path(td) / "proj")
        rel = _mk_brief(proj, {
            "version": 1, "cluster_id": "cluster_001",
            "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt",
            "violations": [],
        }, name="cluster_001_validator.json")
        r = _run_fixer(["--project", str(proj), "--mode", "validator-repair",
                        "--brief", rel, "--dry-run"], cwd=proj)
        assert r.returncode == 3, f"version=1 必须 exit 3，实际 {r.returncode}"
        assert "期望 2" in r.stderr


def test_brief_prompts_consume_draft_path_and_no_dual_caliber():
    """回归锁：两个 brief prompt 都读 draft_path；gen_fixer 源码与 checker agent 文档零 chapter_path 残留。"""
    brief = {"version": 2, "carrier": "cluster", "cluster_id": "cluster_001",
             "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt", "violations": []}
    for fn in (gf.build_voice_fix_prompt, gf.build_validator_repair_prompt):
        _, user = fn(brief, "正文")
        assert "cluster_001_draft.txt" in user, f"{fn.__name__} 必须引用 brief.draft_path"
    # 源码级双口径锁
    src = _GEN_FIXER.read_text(encoding="utf-8")
    assert "chapter_path" not in src, "gen_fixer 不得残留 chapter_path 旧章节口径"
    root = Path(__file__).resolve().parents[1]
    for agent in ("novel-voice-checker.md", "novel-validator-checker.md"):
        doc = (root / ".claude" / "agents" / agent).read_text(encoding="utf-8")
        assert "draft_path" in doc, f"{agent} brief schema 必须用 draft_path"
        assert "chapter_path" not in doc, f"{agent} 不得残留 chapter_path 旧口径"


def test_parse_and_apply_tolerates_missing_end_marker(tmp_path):
    """回归锁：LLM 漏 ===END=== 用 ``` fence + JSON 总结收尾（真机 cluster_002 reflow 实撞）
    → 按 fence/JSON 边界兜底解析；CJK 守恒闸仍生效。"""
    import gen_fixer as gf
    draft_rel = "章节/cluster_009_draft/cluster_009_draft.txt"
    original = "旧段落一。\n\n旧段落二，字数与新稿相当维持守恒。\n"
    target = tmp_path / draft_rel
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")
    reply = (
        "```\n"
        f"===FILE: {draft_rel}===\n"
        "新段落一。\n\n新段落二，字数与旧稿相当维持守恒。\n"
        "```\n"
        '{\n  "p50_after": 84.3\n}\n'
    )
    written, summary, rejected = gf.parse_and_apply(
        reply, tmp_path, {draft_rel: original})
    assert len(written) == 1 and str(target) in str(written[0].get("path"))
    assert rejected == []
    assert "新段落二" in target.read_text(encoding="utf-8")
