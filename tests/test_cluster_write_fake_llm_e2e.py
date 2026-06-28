#!/usr/bin/env python3
"""cluster-write 写作主轨 fake-LLM 端到端集成测试（2026-06-17）。

不打真 gen-model API，用确定性 fake LLM 驱动**真 orchestrator CLI** 跑通 cluster-write
流水线——验证「driver 把各步串对了 + 真脚本（build_manifest/gen_writer/audit_hub/
chapter_splitter/gen_chapter_titles/split_cluster_changes）真跑出结构合法中间产物」这条
**确定性骨架**，绝不断言 fake 正文质量（北极星④/⑤ + 调研 §2.4 deterministic 集成线）。

方案锚点：workspace/_temp_research/mock_llm_cli_e2e_harness_plan.md（§3 注入层 B 混合 +
§4 夹具骨架 + §6 风险）。复用 tests/test_program_driven_e2e.py 的 _Sandbox。

🔴 强制安全前置（最高优先级）：跑任何 orchestrator 前装**网络层硬兜底**——所有真实出网点
被调用即抛 AssertionError(REAL_LLM_CALL_BLOCKED)。即使漏 patch 某高层 seam，也只会测试失败
报错，绝不花用户的钱。测试跑通后确认网络兜底全程未触发（所有 LLM 都走了 fake）。

三处 LLM seam（调研已确认·直接用）：
1. writer 正文 → monkeypatch gen_writer._stream_once（内部分发 gemini·一处覆盖两协议）。
2. judge 全栈 → monkeypatch judge_runner.lt.generate（judge_call: gen=_generate_fn or lt.generate·
   一次覆盖全部 8 judge）。
3. gen_writer 的 GenModelLoader → 替身（dummy profile·避免读真 .env / 构造真 OpenAI client）。

zero-dep 约定：tests/run_tests.py 用 importlib 直接调无参 test_*。本文件**不用 pytest fixture**——
用「手动 monkeypatch（保存原引用 → setattr → finally 还原）」上下文管理器，确保
`python tests/run_tests.py` 与 pytest 都能跑。
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc            # noqa: E402
import plan_tracker as pt             # noqa: E402
import judge_runner as jr             # noqa: E402
import llm_transport as lt            # noqa: E402
import gen_writer                     # noqa: E402
import gen_model_loader               # noqa: E402

REAL_PLANS = _ROOT / "core" / "claude-home" / "plans"


# ============================================================================
# 沙盒（复用 test_program_driven_e2e._Sandbox 形态：拷真实模板 + 重定向 plan 存储）
# ============================================================================
class _Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        for d in (self.templates, self.projects, self.tmp / "styles",
                  self.tmp / ".plans"):
            d.mkdir(parents=True)
        for f in REAL_PLANS.glob("*.plan.json"):
            (self.templates / f.name).write_text(
                f.read_text(encoding="utf-8"), encoding="utf-8")
        self.proj = self.projects / "冒烟书"
        (self.proj / "_数据库").mkdir(parents=True)
        self._saved = {}

    def __enter__(self):
        for k, v in [("TEMPLATES_DIR", self.templates),
                     ("PROJECTS_DIR", self.projects),
                     ("STYLES_DIR", self.tmp / "styles"),
                     ("GLOBAL_PLANS_DIR", self.tmp / ".plans"),
                     ("ATTEST_KEY_PATH", self.tmp / ".plans" / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)


# ============================================================================
# 网络层硬兜底（强制安全前置）：任何真实出网点被调用即抛错·绝不花钱
# ============================================================================
class _NetGuardTripped(AssertionError):
    pass


@contextmanager
def _network_hard_block():
    """monkeypatch 所有真实出网点 → 调用即 raise。覆盖：
    · urllib.request.urlopen（gemini 裸 urllib·gen_writer._stream_once_gemini + 通用）
    · llm_transport.stream_once（judge 真 transport 底层单次流式）
    · gen_writer._stream_once_gemini（writer gemini 真出网）
    · openai.OpenAI（writer/judge 构造真 client 即拦——连建都不让）
    若任一被触发 = 测试漏 patch 了某高层 seam，立即失败报错（绝不静默真打 API）。
    """
    import urllib.request
    saved = []

    def _save(mod, name):
        saved.append((mod, name, getattr(mod, name)))

    def _boom(where):
        def _f(*a, **k):
            raise _NetGuardTripped(f"REAL_LLM_CALL_BLOCKED: {where} 被真实调用"
                                   f"（fake seam 漏 patch → 会花用户的钱）")
        return _f

    _save(urllib.request, "urlopen")
    urllib.request.urlopen = _boom("urllib.request.urlopen")
    _save(lt, "stream_once")
    lt.stream_once = _boom("llm_transport.stream_once")
    _save(gen_writer, "_stream_once_gemini")
    gen_writer._stream_once_gemini = _boom("gen_writer._stream_once_gemini")
    # openai.OpenAI：构造 client **不**出网（writer call_gen_model 内构造但 _stream_once 已被
    # fake → .chat.completions.create 永不调用）。所以不拦构造（拦了会误伤 fake 路径），而是
    # 让构造出的 client 的 .chat.completions.create 真被调时才 boom——即「真出网才拦」。
    try:
        import openai
        _save(openai, "OpenAI")

        class _BoomClient:
            def __init__(self, *a, **k):
                pass

            class chat:  # noqa: N801
                class completions:  # noqa: N801
                    @staticmethod
                    def create(*a, **k):
                        raise _NetGuardTripped(
                            "REAL_LLM_CALL_BLOCKED: openai client.chat.completions."
                            "create 被真实调用（fake _stream_once 漏 patch）")
        openai.OpenAI = _BoomClient
    except ImportError:
        pass
    try:
        yield
    finally:
        for mod, name, orig in reversed(saved):
            setattr(mod, name, orig)


# ============================================================================
# fake LLM 替身
# ============================================================================
class _FakeProfile:
    """最小 Profile 替身（judge_call 取 result.profile.name；gen_writer 取 .model/.name/
    .api_key/.temperature/.max_tokens/.protocol）。openai 协议→ writer 不走 urllib。"""
    name = "fake-llm"
    model = "fake-model"
    base_url = "http://fake.invalid/v1"
    api_key = "FAKE_KEY_NOT_REAL"
    temperature = 0.8
    max_tokens = 16000
    protocol = "openai"
    thinking_level = None
    reasoning_effort = None


class _FakeLoader:
    """替 gen_writer.GenModelLoader：返回 dummy profile，绝不读真 .env / keyring。"""
    env_path = Path("<fake-loader-no-env>")

    def get_active_profile(self):
        return _FakeProfile()

    def get_callable_profiles(self):
        return [_FakeProfile()]

    def get_fallback_chain(self):
        return []


# writer 正文 fake：返回足够长的**契约合法**确定性正文（>12000 CJK 过 splitter 字数下限）。
# 🔴 2026-06-27 C04/C05 适配：fake 正文必须是 proper 短段落（每段 < 单段超长阈值 + 句末标点）
#   + changes.factual 非空——否则真 audit_hub（step3 in-process 真跑）命中 STYLE_单段超长 /
#   CHANGES_MISSING（hard_gate），新增的 C04 质量地板会（正确地）拦下这种带病 garbage 草稿。
#   旧 fake（"正文"*4000 单段 + 空 factual）正是 C04 该拦的形态——改为合法形态测确定性骨架。
def _fake_body(n_paras, *, start=0):
    """n 段**各不相同**的单句短段落（句末 。·远 < 单段超长阈值）。
    🔴 段落必须互不相同——gen_writer draft_sanitizer 会「整块去重」把重复段折叠掉
    （重复段会被压成 ~1 段·draft 塌缩成 120 CJK）。用段号嵌入保证唯一。"""
    return "\n".join(f"第{i}段正文在此推进剧情。" for i in range(start, start + n_paras))


_FAKE_BODY = _fake_body(1800)              # ~18000 CJK 唯一短段落（越过 freestyle 软下限 16000）


def _fake_stream_once(client, profile, system, user, max_tokens,
                      prior_assistant=None, cont_reason="length"):
    """替 gen_writer._stream_once。

    第一次调用返回主体正文；若 gen_writer 因 freestyle min_cjk 软下限触发 expand 续写
    （prior_assistant 非空），再补一段唯一短段落（不引入单段超长·不与主体重复被去重）。
    finish=stop 不触发续写。
    """
    if prior_assistant:
        return _fake_body(500, start=50000), "stop"
    changes = {"factual": {"_note": "冒烟测试·无真实事实变更"},
               "self_eval": {"waivers": []}}
    fenced = "\n\n```json\n" + json.dumps(changes, ensure_ascii=False) + "\n```"
    return _FAKE_BODY + fenced, "stop"


# judge fake：按 agent 返回满足该 spec.required_keys 的 JSON（block 级缺键会抛
# JudgeBlockedError 停流水线·见 judge_runner AGENT_SPECS）。
_JUDGE_JSON = {
    "novel-summarizer": {
        "cluster_id": "cluster_001", "summary": "冒烟摘要",
        "emotion": {"value": 2}},
    "novel-foreshadower": {
        "judge_id": "foreshadower", "specific_findings": {}},
    "novel-reflector": {"entries": []},
    "novel-reading-reflector": {"verdict": "pass", "new_issues_this_round": []},
    "novel-voice-checker": {
        "violations": [], "judge_report": {"judge_id": "vc"}},
    "novel-validator-checker": {"violations": []},
    "novel-outline-planner": {"candidates": [
        {"label": "走向A·主线推进", "_emergence_score": 0.9},
        {"label": "走向B·支线碰撞", "_emergence_score": 0.7}]},
}


def _make_fake_generate(call_log):
    def fake_generate(loader_or_profiles, system, user, *, label="llm", **kw):
        # label 形如 "judge:novel-summarizer"——抽 agent 名挑对应 JSON
        agent = label.split("judge:", 1)[-1] if "judge:" in label else ""
        call_log.append(agent or label)
        payload = _JUDGE_JSON.get(agent, {"free_notes": "", "verdict": "pass",
                                          "new_issues_this_round": []})
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return lt.GenResult(text=text, profile=_FakeProfile(), finish_reason="stop")
    return fake_generate


# ============================================================================
# UTF-8 IO 环境（in-process 跑真脚本必备·Windows GBK 控制台兼容）
# ============================================================================
@contextmanager
def _utf8_io():
    """in-process 跑的真脚本 print 直接走父进程 sys.stdout/stderr。Windows 控制台默认
    GBK → 脚本 print emoji/↩(↩)/中文符号会 UnicodeEncodeError 崩（split_cluster_changes
    实测）。生产 run_script_in_process 只在 sys.frozen 时 reconfigure（orchestrator.py:271）；
    dev 子进程路径靠 PYTHONUTF8 env（仅影响子进程·不影响已起的父进程 sys.stdout 编码）。
    测试 in-process 路径需手动把**父进程** stdout/stderr reconfigure 成 utf-8（不改生产码·
    只在测试进程内补 frozen 路径已有的同款环境位）。同时设 PYTHONUTF8 让真脚本再 spawn 的
    子 scanner 也 UTF-8 输出。
    """
    import os
    _env_keys = ("PYTHONIOENCODING", "PYTHONUTF8")
    saved_env = {k: os.environ.get(k) for k in _env_keys}
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    saved_enc = {}
    for nm in ("stdout", "stderr"):
        s = getattr(sys, nm, None)
        rec = getattr(s, "reconfigure", None)
        if rec is not None:
            saved_enc[nm] = (s.encoding, s.errors)
            try:
                rec(encoding="utf-8", errors="replace")
            except Exception:
                saved_enc.pop(nm, None)
    try:
        yield
    finally:
        for nm, (enc, err) in saved_enc.items():
            try:
                getattr(sys, nm).reconfigure(encoding=enc, errors=err)
            except Exception:
                pass
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _frozen_environ():
    """os.environ 全量快照/还原。

    🔴 关键隔离（2026-06-17 抓出的测试污染）：in-process 跑真管线时 gen_model_loader 会
    load_dotenv() 把真实 .env 的全部 GEN__*（含真 API key + GEN_MODEL_ACTIVE）灌进
    os.environ。若不还原 → 泄漏到顺序跑的后续测试（实测打挂 test_config_split 的
    dist-mode 断言：读到 GEN_MODEL_ACTIVE=<dev值> 而非 builtin 的 gemini_pro_preview）。
    本 CM 在每处 in-process 脚本执行后把 environ 精确还原到执行前。"""
    import os
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ============================================================================
# in-process runner（让 monkeypatch 对 gen_writer 等模块生效·不起子进程隔绝 patch）
# ============================================================================
def _inprocess_runner(cmd, *, repo_root=None, label=""):
    tokens = orc._strip_python_prefix(cmd)
    return orc.run_script_in_process(
        tokens, repo_root=repo_root or _ROOT, label=label)


# ============================================================================
# 种子项目：预置最小可用子系统让真脚本有输入（build_manifest preflight 过）
# ============================================================================
def _seed_min_subsystems(proj: Path):
    """① scaffold 全 34 子系统骨架 ② 覆写 事件簇.json（cluster_001 in_progress +
    chapter_range + scene_storyboard，status 用白名单 in_progress 非 active——
    memory feedback_build_manifest_cluster_brief_injection_gate）③ 人物卡补一角色。"""
    db = proj / "_数据库"
    # ① scaffold 34 骨架（in-process·与生产同路径）
    with _frozen_environ(), _utf8_io():
        rc = orc.run_script_in_process(
            ["core/scripts/scaffold_subsystems.py", "emit", "--db-dir", str(db)],
            repo_root=_ROOT, label="seed-scaffold")
    assert rc == 0, f"scaffold_subsystems emit 失败 rc={rc}"

    # ② 事件簇.json：cluster_001 必须 in_progress（白名单）+ chapter_range + scene_storyboard
    #    build_manifest.current_scene() 从 事件簇 fallback 取 scene → preflight 过
    shijianji = {
        "schema_version": "v2.cluster", "ecas_enabled": True,
        "v2_cluster_enabled": True,
        "clusters": [{
            "cluster_id": "cluster_001",
            "key": "001",
            "vol": 1,
            "status": "in_progress",            # ← 白名单值（非 active 孤儿值）
            "narrative_mode": "in_medias_res",
            "chapter_range": [1, 4],
            "title": "冒烟首块",
            "scope_summary": "主角在育新中学的第一夜，规则诡谈拉开序幕，强冲突开场倒叙引入。",
            "scene_storyboard": [
                {"ch": 1, "scene": 1, "title": "灾难开场",
                 "type": "悬疑", "characters": ["林默"],
                 "summary": "深夜广播响起，第一条规则被违反。"},
                {"ch": 1, "scene": 2, "title": "反转揭底",
                 "type": "悬疑", "characters": ["林默", "周雯"],
                 "summary": "回溯白天，主角如何踏入这所学校。"},
                {"ch": 1, "scene": 3, "title": "回到当下",
                 "type": "悬疑", "characters": ["林默"],
                 "summary": "接回开篇，悬念升级。"},
            ],
            "foreshadowing_to_plant": [],
        }],
    }
    (db / "事件簇.json").write_text(
        json.dumps(shijianji, ensure_ascii=False, indent=2), encoding="utf-8")

    # ③ 人物卡补一角色（preflight 只 warning·补上减少噪声 + 让 scene characters 命中）
    cards = {
        "schema_version": "v27",
        "characters": [
            {"id": "林默", "name": "林默", "role": "主角",
             "aliases": [], "voice_pack": {}},
            {"id": "周雯", "name": "周雯", "role": "配角",
             "aliases": [], "voice_pack": {}},
        ],
    }
    (db / "人物卡.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================================
# 测试主体
# ============================================================================
@contextmanager
def _writer_judge_fakes(judge_log):
    """同时安装 writer seam + judge seam + loader 替身（手动 save/restore·非 fixture）。"""
    saved = [
        (gen_writer, "_stream_once", gen_writer._stream_once),
        (gen_writer, "GenModelLoader", gen_writer.GenModelLoader),
        (jr.lt, "generate", jr.lt.generate),
    ]
    gen_writer._stream_once = _fake_stream_once
    gen_writer.GenModelLoader = _FakeLoader
    jr.lt.generate = _make_fake_generate(judge_log)
    try:
        yield
    finally:
        for mod, name, orig in saved:
            setattr(mod, name, orig)


def _run_cluster_write():
    """跑一次 cluster-write 真流水线·返回 (summary, judge_log)。

    三处 fake seam + 网络硬兜底 + UTF-8 IO 全装好·script_runner 走 in-process（让
    monkeypatch 对 gen_writer 生效）·judge_dispatch=None 用真 default_judge_dispatch。
    """
    judge_log = []
    import os
    saved_best_of_n = os.environ.get("BEST_OF_N")
    os.environ["BEST_OF_N"] = "1"   # 关 best-of-N 择优·走单稿直生（call_gen_model）
    try:
        with _frozen_environ(), _utf8_io(), _network_hard_block(), \
                _writer_judge_fakes(judge_log):
            summary = orc.run_command(
                "cluster-write", "冒烟书", key="001",
                script_runner=_inprocess_runner,
                judge_dispatch=None,        # 用真 default_judge_dispatch（judge 链真跑）
                pause_handler=None, auto_pilot=False,
                repo_root=_ROOT)
        return summary, judge_log
    finally:
        if saved_best_of_n is None:
            os.environ.pop("BEST_OF_N", None)
        else:
            os.environ["BEST_OF_N"] = saved_best_of_n


def test_cluster_write_fake_llm_full_pipeline():
    """cluster-write 7 步 fake-LLM 端到端：真脚本 in-process 跑 + fake LLM·
    断言确定性骨架（步数/end_plan/中间产物落盘）。绝不断言 fake 正文质量。"""
    with _Sandbox() as sb:
        _seed_min_subsystems(sb.proj)
        summary, judge_log = _run_cluster_write()

        # —— 骨架断言 1：end_plan 全绿 + 7 步全完成 ——
        assert summary.end_report.get("ok"), summary.end_report
        done = [o for o in summary.completed if o.status == "completed"]
        assert len(done) == 7, f"应 7 步全过·实际 {len(done)}: {summary.completed}"

        db = sb.proj / "_数据库"
        # —— 骨架断言 2：step1 build_manifest 真产 ch_001.json（真脚本·非 fake）——
        manifest = db / ".manifest" / "ch_001.json"
        assert manifest.exists(), "build_manifest 未产出 ch_001.json"
        mf = json.loads(manifest.read_text(encoding="utf-8"))
        assert mf.get("preflight", {}).get("passed"), \
            f"manifest preflight 应 passed: {mf.get('preflight')}"

        # —— 骨架断言 3：step2 gen_writer 真产 draft.txt + changes.json（writer seam 工作）——
        draft = (sb.proj / "章节" / "cluster_001_draft"
                 / "cluster_001_draft.txt")
        assert draft.exists(), "gen_writer 未产出 draft.txt"
        draft_txt = draft.read_text(encoding="utf-8")
        assert len(draft_txt) > 3000, f"draft 太短 {len(draft_txt)}"
        changes = (sb.proj / "章节" / "cluster_001_draft"
                   / "cluster_001_changes.json")
        assert changes.exists(), "gen_writer 未产出 changes.json"
        cj = json.loads(changes.read_text(encoding="utf-8"))
        assert "factual" in cj and "self_eval" in cj, \
            f"changes schema 破损: {list(cj)}"

        # —— 骨架断言 4：step3 audit_hub 真产 cluster audit 报告（真脚本·17 scanner）——
        audit = db / ".audit" / "cluster_001_audit.json"
        assert audit.exists(), "audit_hub 未产出 cluster_001_audit.json"
        json.loads(audit.read_text(encoding="utf-8"))  # 结构合法

        # —— 骨架断言 5：step6 splitter 真产 WAL·chapter_range 字段合法（data_flow 源）——
        wal = db / ".wal" / "splitter_cluster_001_decisions.json"
        assert wal.exists(), "chapter_splitter 未产出 splitter WAL"
        wj = json.loads(wal.read_text(encoding="utf-8"))
        assert "chapter_range" in wj, f"splitter WAL 缺 chapter_range: {list(wj)}"
        cr = wj["chapter_range"]
        assert isinstance(cr, list) and len(cr) == 2, f"chapter_range 形态错: {cr}"

        # —— 骨架断言 6：judge 链真跑（fake LLM·summarizer/foreshadower 等被派发）——
        assert "novel-reading-reflector" in judge_log, \
            f"reading-reflector 未派发: {judge_log}"
        assert {"novel-summarizer", "novel-foreshadower",
                "novel-reflector"} & set(judge_log), \
            f"step5 三 judge 至少一个应派发: {judge_log}"


def test_network_hard_block_trips_on_real_call():
    """元测试：网络兜底确实会拦真出网（证明兜底是活的·不是摆设）。
    若兜底失效，下面真打 urllib 会发请求/超时——此测试反而失败兜不住。"""
    import urllib.request
    with _network_hard_block():
        try:
            urllib.request.urlopen("http://should.never.be.called.invalid/x")
        except _NetGuardTripped as e:
            assert "REAL_LLM_CALL_BLOCKED" in str(e)
            return
        raise AssertionError("网络兜底未拦截 urlopen——兜底失效")


def test_seed_subsystems_preflight_passes():
    """种子项目能让 build_manifest preflight 过（抓 seed/schema 漂移早失败·
    不必跑完整 7 步即可定位种子问题）。"""
    with _Sandbox() as sb:
        _seed_min_subsystems(sb.proj)
        with _frozen_environ(), _utf8_io(), _network_hard_block():
            rc = orc.run_script_in_process(
                ["core/scripts/build_manifest.py", str(sb.proj), "1"],
                repo_root=_ROOT, label="seed-manifest")
        assert rc == 0, f"build_manifest rc={rc}（preflight 未过或脚本崩）"
        mf = json.loads((sb.proj / "_数据库" / ".manifest"
                         / "ch_001.json").read_text(encoding="utf-8"))
        assert mf["preflight"]["passed"], mf["preflight"]


def _short_fake_stream_once(client, profile, system, user, max_tokens,
                            prior_assistant=None, cont_reason="length"):
    """短稿 fake（~2300 CJK < 单章下限 3000）→ splitter 全退 pending_tail·0 章。
    expand 续写也只加一点点（模拟真模型「已无更多内容」·实测真 API 偶发）。
    🔴 2026-06-27 C04/C05 适配：同样用 proper 短段落 + 非空 factual（避免 STYLE_单段超长 /
    CHANGES_MISSING hard_gate 被 C04 地板拦下·短稿 pending_tail 是 v27 合法流程非 garbage）。"""
    if prior_assistant:
        return _fake_body(2, start=90000), "stop"
    changes = {"factual": {"_note": "短稿冒烟"}, "self_eval": {"waivers": []}}
    fenced = "\n\n```json\n" + json.dumps(changes, ensure_ascii=False) + "\n```"
    return _fake_body(230) + fenced, "stop"


@contextmanager
def _short_writer_judge_fakes(judge_log):
    saved = [
        (gen_writer, "_stream_once", gen_writer._stream_once),
        (gen_writer, "GenModelLoader", gen_writer.GenModelLoader),
        (jr.lt, "generate", jr.lt.generate),
    ]
    gen_writer._stream_once = _short_fake_stream_once
    gen_writer.GenModelLoader = _FakeLoader
    jr.lt.generate = _make_fake_generate(judge_log)
    try:
        yield
    finally:
        for mod, name, orig in saved:
            setattr(mod, name, orig)


def test_cluster_write_short_draft_goes_to_pending_tail():
    """🔴 回归（2026-06-17 真 API e2e 抓出 + 修）：真模型偶发产短稿（整稿 < 单章下限 3000）→
    splitter 切 0 章·全退 pending_tail（v27 合法流程）→ gen_chapter_titles / split_cluster_changes
    必须优雅 no-op（非崩溃 / 非 advisory-exit-1）·cluster-write 7 步完整跑通·内容退 pending_tail
    等下个 cluster 拼接。

    曾 bug：① gen_chapter_titles 收 `--chapters []` → parse_chapters 的 int("[]") 崩 rc=3；
    ② split_cluster_changes 0 切的「无 range 可回填」误判 advisory → exit 1 → 整条管线卡死。
    fake 永远产 8000 CJK 掩盖了它·真 gen-model 产 2580 CJK 短稿才暴露。"""
    judge_log = []
    with _Sandbox() as sb:
        _seed_min_subsystems(sb.proj)
        saved_bon = os.environ.get("BEST_OF_N")
        os.environ["BEST_OF_N"] = "1"
        try:
            with _frozen_environ(), _utf8_io(), _network_hard_block(), \
                    _short_writer_judge_fakes(judge_log):
                summary = orc.run_command(
                    "cluster-write", "冒烟书", key="001",
                    script_runner=_inprocess_runner, judge_dispatch=None,
                    pause_handler=None, auto_pilot=False, repo_root=_ROOT)
        finally:
            if saved_bon is None:
                os.environ.pop("BEST_OF_N", None)
            else:
                os.environ["BEST_OF_N"] = saved_bon

        assert summary.end_report.get("ok"), f"短稿管线应完整跑通: {summary.end_report}"
        done = [o for o in summary.completed if o.status == "completed"]
        assert len(done) == 7, f"短稿应 7 步全过（曾卡死在 step6）·实际 {len(done)}: {summary.completed}"
        # 短稿退 pending_tail（不强切·等下 cluster 拼接）
        pt = sb.proj / "章节" / "cluster_001_draft" / "cluster_001_pending_tail.txt"
        assert pt.exists(), "短稿应退 pending_tail 等下 cluster 拼接"
        wal = sb.proj / "_数据库" / ".wal" / "splitter_cluster_001_decisions.json"
        wj = json.loads(wal.read_text(encoding="utf-8"))
        assert wj.get("chapters_split") == 0, f"短稿应切 0 章·实际 {wj.get('chapters_split')}"


import os  # noqa: E402  (短稿回归测试用·置文件级)


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
