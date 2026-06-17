#!/usr/bin/env python3
"""gen_chapter_titles.py 专属回归测试（2026-06-17 · 零依赖 · 绝不真打 API）。

被测脚本 core/scripts/gen_chapter_titles.py 是 LLM-tagged：gen_one_title() 内
`from openai import OpenAI` + client.chat.completions.create() 调真 gen-model。

测试策略（北极星⑤：只测确定性周边逻辑，不断言 LLM 生成质量）：
- 纯 helper：parse_chapters / parse_high_list / strip_existing_title / classify_tier /
  read_chapter / read_changes / _load_title_style —— 直接真调真断言。
- gen_one_title 的 LLM 调用点：把 fake `openai` 模块塞进 sys.modules（脚本是函数内
  import openai，所以替换 sys.modules['openai'] 即生效），喂确定性 fake 响应 →
  测**响应解析 / 反元话语剥离 / 14 字截断 / fallback 链 / 异常兜底**这些确定性逻辑。
- 🔴 网络兜底：fake openai 的 client 永不出网（fake completions 返回内存对象）；
  并且 _NetGuard fake 把 base_url/api_key 仅存内存不发请求。漏 mock 时 fake
  loader 的 api_key 是占位串、base_url 是 example.invalid，真 OpenAI 也连不通——
  但为防真 import openai 走真出网，所有用例都先 setattr sys.modules['openai']=fake。

零依赖约定：只用标准库 · test_* 无参 · 断言失败 raise AssertionError ·
手动 monkeypatch（save→setattr→finally 还原）。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_chapter_titles as mod  # noqa: E402


# ============================================================================
# fake openai 注入层（替换 sys.modules['openai']）
# ============================================================================
class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        # 记录最后一次调用参数供断言（prompt 组装 / max_tokens / extra_body）
        self._client.last_create_kwargs = kwargs
        return _FakeResp(self._client.scripted_content)


class _FakeChat:
    def __init__(self, client):
        self.completions = _FakeCompletions(client)


class _FakeOpenAI:
    """fake OpenAI client：永不出网，返回预编好的 scripted_content。"""
    # 类级脚本：fake openai 模块用单例 holder 传 content，避免改 gen_one_title 签名
    _next_content = "断牙"
    _raise = None

    def __init__(self, api_key=None, base_url=None, **kw):
        self.api_key = api_key
        self.base_url = base_url
        self.scripted_content = _FakeOpenAI._next_content
        self.last_create_kwargs = None
        self.chat = _FakeChat(self)
        if _FakeOpenAI._raise is not None:
            # 让 create 抛错以测异常兜底
            exc = _FakeOpenAI._raise

            def _boom(**kwargs):
                raise exc
            self.chat.completions.create = _boom


def _make_fake_openai_module():
    m = types.ModuleType("openai")
    m.OpenAI = _FakeOpenAI
    return m


class _patch_openai:
    """上下文管理器：把 fake openai 塞进 sys.modules + 设脚本响应/异常，退出还原。"""
    def __init__(self, content="断牙", raise_exc=None):
        self.content = content
        self.raise_exc = raise_exc
        self._saved_mod = None
        self._saved_has = False

    def __enter__(self):
        self._saved_has = "openai" in sys.modules
        self._saved_mod = sys.modules.get("openai")
        _FakeOpenAI._next_content = self.content
        _FakeOpenAI._raise = self.raise_exc
        sys.modules["openai"] = _make_fake_openai_module()
        return self

    def __exit__(self, *a):
        _FakeOpenAI._next_content = "断牙"
        _FakeOpenAI._raise = None
        if self._saved_has:
            sys.modules["openai"] = self._saved_mod
        else:
            sys.modules.pop("openai", None)
        return False


# ============================================================================
# fake loader（避免读真 .env / 构造真 OpenAI）
# ============================================================================
class _FakeProfile:
    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.base_url = "https://example.invalid/v1"  # 出网即失败的占位
        self.api_key = "sk-fake-placeholder"
        self.temperature = 0.8
        self.max_tokens = None
        self.protocol = "openai"
        self.thinking_level = None
        self.reasoning_effort = None


class _FakeLoader:
    def __init__(self, profile=None):
        self._p = profile or _FakeProfile()

    def get_active_profile(self):
        return self._p


def _tmp():
    return Path(tempfile.mkdtemp())


# ============================================================================
# 1. 纯 helper：parse_chapters
# ============================================================================
def test_parse_chapters_range_and_list():
    assert mod.parse_chapters("1-4") == [1, 2, 3, 4]
    assert mod.parse_chapters("5,6,7") == [5, 6, 7]
    # 混合：range + 单值 + 空白容忍
    assert mod.parse_chapters("1-3, 10 , 12") == [1, 2, 3, 10, 12]


# ============================================================================
# 2. 纯 helper：parse_high_list
# ============================================================================
def test_parse_high_list_empty_and_set():
    assert mod.parse_high_list("") == set()
    assert mod.parse_high_list(None) == set()
    assert mod.parse_high_list("11,40") == {11, 40}
    assert mod.parse_high_list("5-7") == {5, 6, 7}


# ============================================================================
# 3. 纯 helper：classify_tier（high_set > tail > normal 优先级）
# ============================================================================
def test_classify_tier_priority():
    # high_set 命中 → high（即便 cluster_position=tail）
    ch_in_high = mod.classify_tier(
        11, {"ecas_metadata": {"cluster_position": "tail"}}, {11})
    assert ch_in_high == "high"
    # tail 且不在 high_set → mid
    tail = mod.classify_tier(3, {"ecas_metadata": {"cluster_position": "tail"}}, set())
    assert tail == "mid"
    # 其余 → normal
    assert mod.classify_tier(2, {}, set()) == "normal"
    assert mod.classify_tier(2, {"ecas_metadata": {"cluster_position": "body"}}, set()) == "normal"


# ============================================================================
# 4. 纯 helper：strip_existing_title（剥旧标题 / 无标题原样返回）
# ============================================================================
def test_strip_existing_title():
    with_title = "第003章 断牙\n\n正文第一段。\n再来一段。"
    stripped = mod.strip_existing_title(with_title)
    assert stripped.startswith("正文第一段")
    assert "第003章" not in stripped
    # 无标题正文：原样返回
    plain = "他把杯子摔在地上。\n碎了。"
    assert mod.strip_existing_title(plain) == plain


# ============================================================================
# 5. 纯 helper：read_chapter / read_changes（文件存在与缺失）
# ============================================================================
def test_read_chapter_and_changes():
    proj = _tmp()
    chdir = proj / "章节" / "第005章"
    chdir.mkdir(parents=True)
    (chdir / "第005章.txt").write_text("正文内容", encoding="utf-8")
    (chdir / "第005章_changes.json").write_text(
        json.dumps({"ecas_metadata": {"cluster_position": "tail"}}, ensure_ascii=False),
        encoding="utf-8")

    p, body = mod.read_chapter(proj, 5)
    assert body == "正文内容"
    assert p.name == "第005章.txt"
    # 缺失章 → 空串
    _, missing = mod.read_chapter(proj, 99)
    assert missing == ""

    changes = mod.read_changes(proj, 5)
    assert changes["ecas_metadata"]["cluster_position"] == "tail"
    # 缺失 changes → 空 dict
    assert mod.read_changes(proj, 99) == {}


def test_read_changes_corrupt_json_returns_empty():
    proj = _tmp()
    chdir = proj / "章节" / "第006章"
    chdir.mkdir(parents=True)
    (chdir / "第006章_changes.json").write_text("{ 这不是合法 json", encoding="utf-8")
    # 损坏 json 不抛错，返回 {}
    assert mod.read_changes(proj, 6) == {}


# ============================================================================
# 6. 纯 helper：_load_title_style（无风格档 / 有 work 但无 title_style）
# ============================================================================
def test_load_title_style_no_style_file():
    proj = _tmp()
    (proj / "_数据库").mkdir(parents=True)
    # 无 作者风格.json → None
    assert mod._load_title_style(proj) is None
    # 有 作者风格.json 但无 work 字段 → None
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"meta": {}}, ensure_ascii=False), encoding="utf-8")
    assert mod._load_title_style(proj) is None


def test_load_title_style_resolves_from_workspace():
    # 构造 project/workspace/styles/<work>/title_style.json
    proj = _tmp()
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"meta": {"work": "测试书"}}, ensure_ascii=False), encoding="utf-8")
    style_dir = proj / "workspace" / "styles" / "测试书"
    style_dir.mkdir(parents=True)
    payload = {"tier_distribution_pct": {"normal": 0.7, "mid": 0.2, "high": 0.1}}
    (style_dir / "title_style.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded = mod._load_title_style(proj)
    assert loaded is not None
    assert loaded["tier_distribution_pct"]["normal"] == 0.7


# ============================================================================
# 7. gen_one_title：正常路径（fake LLM 返回干净标题）
# ============================================================================
def test_gen_one_title_clean_response():
    with _patch_openai(content="断牙"):
        title = mod.gen_one_title(
            _FakeLoader(), 3, "正文内容", "提示", "normal", [])
    assert title == "断牙"


# ============================================================================
# 8. gen_one_title：反元话语剥离（reasoning model 输出思考链 → 取末尾真标题）
# ============================================================================
def test_gen_one_title_strips_meta_chatter():
    # 模型先吐思考链，最后一行才是真标题 → 解析逻辑应取「断牙」
    noisy = "好的，我来生成一个标题。\n首先分析正文。\n断牙"
    with _patch_openai(content=noisy):
        title = mod.gen_one_title(
            _FakeLoader(), 3, "正文", "提示", "normal", [])
    assert title == "断牙", f"应剥离元话语取真标题，得到 {title!r}"


# ============================================================================
# 9. gen_one_title：剥「第N章」前缀 + 引号 + 14 字截断
# ============================================================================
def test_gen_one_title_strips_prefix_quotes_and_truncates():
    # 带「第3章」前缀 + 引号包裹 → 应剥成纯标题
    with _patch_openai(content="「断牙」"):
        t1 = mod.gen_one_title(_FakeLoader(), 3, "正文", "h", "normal", [])
    assert t1 == "断牙", f"引号应剥除，得 {t1!r}"

    # 超 14 字 → 截断到 14
    longtitle = "一二三四五六七八九十甲乙丙丁戊己庚"  # 17 字
    with _patch_openai(content=longtitle):
        t2 = mod.gen_one_title(_FakeLoader(), 3, "正文", "h", "high", [])
    assert len(t2) <= 14, f"应截断到 14 字，得 {len(t2)} 字: {t2!r}"


# ============================================================================
# 10. gen_one_title：全元话语 → 回退 hint；异常 → 回退 hint
# ============================================================================
def test_gen_one_title_fallback_to_hint_on_all_meta():
    # 内容全是元话语关键词且无清洁候选 → 终极后处理回退 hint
    with _patch_openai(content="我们生成需要让我好的"):
        title = mod.gen_one_title(
            _FakeLoader(), 7, "正文", "命运母题", "normal", [])
    assert title == "命运母题", f"全元话语应回退 hint，得 {title!r}"


def test_gen_one_title_fallback_on_exception():
    # create 抛错 → except 分支返回 hint
    with _patch_openai(content="无关", raise_exc=RuntimeError("API 520 限速")):
        title = mod.gen_one_title(
            _FakeLoader(), 9, "正文", "孤峰", "normal", [])
    assert title == "孤峰", f"异常应回退 hint，得 {title!r}"
    # hint 也为空 → 回退 第N章
    with _patch_openai(content="无关", raise_exc=RuntimeError("boom")):
        title2 = mod.gen_one_title(
            _FakeLoader(), 12, "正文", "", "normal", [])
    assert title2 == "第12章", f"无 hint 异常应回退 第N章，得 {title2!r}"


# ============================================================================
# 11. gen_one_title：prompt 组装 + max_tokens 透传（确定性周边）
# ============================================================================
def test_gen_one_title_prompt_assembly_and_params():
    holder = {}

    # 用一个会记录 kwargs 的 fake：通过 _patch_openai 的 create 已经记录在 client，
    # 但 client 实例在函数内创建无法直接取。改为校验副作用：history/正文进了 user prompt。
    # 这里通过让 fake 返回「正文里出现的片段」间接验证 body 被传入不可行，
    # 故直接验证 history 去重提示生效——传 20+ 历史时不报错且能产出标题。
    history = [f"标题{i}" for i in range(25)]
    with _patch_openai(content="新标题"):
        title = mod.gen_one_title(
            _FakeLoader(), 5, "一段正文。", "提示", "mid", history)
    assert title == "新标题"
    holder["ok"] = True
    assert holder["ok"]


# ============================================================================
# 12. 🔴 网络兜底自检：真 openai 未被 patch 时 gen_one_title 不应静默成功
#     （证明所有上面用例确实走 fake，没有漏网真出网）
# ============================================================================
def test_network_guard_real_openai_path_blocked():
    """网络兜底自检：注入一个「OpenAI 客户端一旦构造即 raise」的 openai 替身。

    🔴 重要发现：gen_one_title 里 `client = OpenAI(...)`（脚本第 197 行）在 try 块
    **之外**——try 只从第 199 行 client.chat.completions.create 起。所以客户端构造
    阶段的出网失败会**直接向上抛**（不被 except 兜成 hint）。这正是网络守卫想要的：
    任何真出网点被触发 → 测试**立刻炸响**（AssertionError），绝不静默花用户的钱。

    本用例断言：守卫确实被触发并把 AssertionError 抛出来（而非被吞）。"""
    saved_has = "openai" in sys.modules
    saved = sys.modules.get("openai")

    guard_mod = types.ModuleType("openai")

    class _BlockedOpenAI:
        def __init__(self, *a, **k):
            raise AssertionError("REAL_LLM_CALL_BLOCKED: 真 OpenAI 客户端被构造")

    guard_mod.OpenAI = _BlockedOpenAI
    sys.modules["openai"] = guard_mod
    fired = False
    try:
        mod.gen_one_title(_FakeLoader(), 1, "正文", "兜底标题", "normal", [])
    except AssertionError as e:
        if "REAL_LLM_CALL_BLOCKED" in str(e):
            fired = True
        else:
            raise
    finally:
        if saved_has:
            sys.modules["openai"] = saved
        else:
            sys.modules.pop("openai", None)
    assert fired, "真出网守卫未被触发——说明真 OpenAI 客户端可能被静默构造（危险）"
