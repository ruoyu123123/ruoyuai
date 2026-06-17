#!/usr/bin/env python3
"""model_probe.py 专属回归测试（纯 mock·绝不真打 API / 不真出网）。

被测脚本是 LLM-tagged：唯一真出网点是 probe_models_endpoint() 里的 requests.get
（探测中转站 /v1/models）。本测试 monkeypatch 掉 requests，喂 fake 响应，只测
确定性周边逻辑：

  · get_capabilities  —— 本地表精确匹配 / 前缀模糊匹配 / 保守默认 fallback
  · probe_models_endpoint —— 响应解析（200→models / 非200→error / 异常→error）
  · load_cache / save_cache —— JSON 往返 + 损坏文件容错
  · KNOWN_MODELS —— 已知模型表 schema 完整性

🔴 网络兜底：每个会触发 probe_models_endpoint 的用例都把 requests.get monkeypatch 成
「调用即受控 fake」；并额外提供 _net_guard 把真 requests.get raise，防漏 mock 真花钱。

零依赖约定：只用标准库·test_* 无参数·失败 raise AssertionError。
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts"))
import model_probe as mod  # noqa: E402


# ----------------------------------------------------------------------------
# 网络兜底：把任何真 requests.get 变成立即 raise（防漏 mock 真出网真花钱）
# ----------------------------------------------------------------------------
class _NetForbidden(Exception):
    pass


class _FakeResp:
    """模拟 requests.Response 的最小子集。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _install_requests_stub(get_impl):
    """把 model_probe 用到的 requests.get 换成 get_impl，返回还原器。

    model_probe 在函数内部 `import requests`，所以补丁打在真 requests 模块上即可
    （probe_models_endpoint 拿到的就是这个模块的 get）。
    """
    import requests as _real_requests
    saved = _real_requests.get
    _real_requests.get = get_impl
    return _real_requests, saved


def _net_guard():
    """安装一个「调用即 raise」的 requests.get 哨兵，返回 (module, saved)。"""
    def boom(*a, **k):
        raise _NetForbidden("真 requests.get 被调用——测试漏 mock！")
    return _install_requests_stub(boom)


# ============================================================================
# get_capabilities —— 最富确定性逻辑（精确 / 模糊 / 默认三条路径）
# ============================================================================
def test_get_capabilities_exact_match_returns_copy():
    """精确命中 KNOWN_MODELS：返回正确能力 + 必须是 copy（改返回值不污染原表）。"""
    caps = mod.get_capabilities("deepseek-v4-pro")
    assert caps["context_window"] == 1048576, caps
    assert caps["max_output_tokens"] == 384000, caps
    # 是 copy：篡改返回值不应改到 KNOWN_MODELS
    caps["context_window"] = -1
    assert mod.KNOWN_MODELS["deepseek-v4-pro"]["context_window"] == 1048576, \
        "get_capabilities 返回的不是 copy，污染了 KNOWN_MODELS"


def test_get_capabilities_prefix_fuzzy_match_appends_note():
    """前缀模糊匹配：未精确命中但以已知 id 开头 → 借用其能力 + 追加 [模糊匹配 X] 标注。"""
    caps = mod.get_capabilities("deepseek-v4-pro-2026-05-preview")
    assert caps["context_window"] == 1048576, caps
    assert "模糊匹配 deepseek-v4-pro" in caps["notes"], caps
    # 模糊匹配也必须是 copy，不污染原表 notes
    assert "模糊匹配" not in mod.KNOWN_MODELS["deepseek-v4-pro"].get("notes", ""), \
        "模糊匹配污染了 KNOWN_MODELS 的 notes"


def test_get_capabilities_fuzzy_handles_missing_notes_key():
    """模糊匹配分支用 .get('notes','') 兜底——即使源条目无 notes 也不 KeyError。

    deepseek-v3 条目没有 notes 字段，用它的前缀触发模糊匹配验证健壮性。
    """
    assert "notes" not in mod.KNOWN_MODELS["deepseek-v3"], "前提变了：deepseek-v3 现在有 notes"
    caps = mod.get_capabilities("deepseek-v3-0324-special")
    assert caps["context_window"] == 65536, caps
    assert "模糊匹配 deepseek-v3" in caps["notes"], caps


def test_get_capabilities_unknown_falls_back_to_conservative_default():
    """全不命中 → 保守默认（32768/8192/8000 + default_conservative source + 含模型名的提示）。"""
    caps = mod.get_capabilities("totally-unknown-model-xyz")
    assert caps["context_window"] == 32768, caps
    assert caps["max_output_tokens"] == 8192, caps
    assert caps["recommended_max_tokens_for_writing"] == 8000, caps
    assert caps["source"] == "default_conservative_unknown_model", caps
    assert "totally-unknown-model-xyz" in caps["notes"], caps


def test_get_capabilities_every_result_has_required_keys():
    """三条路径返回的 dict 都必须含下游 main() 直接索引的 4 个键（防 KeyError 崩 print）。"""
    required = ("context_window", "max_output_tokens",
                "recommended_max_tokens_for_writing", "source")
    samples = ["deepseek-chat", "deepseek-v4-flash-xyz", "no-such-model"]
    for mid in samples:
        caps = mod.get_capabilities(mid)
        for k in required:
            assert k in caps, f"{mid} 返回缺键 {k}: {caps}"


# ============================================================================
# probe_models_endpoint —— 唯一出网点·全程 mock requests
# ============================================================================
def test_probe_endpoint_200_parses_model_ids():
    """200 + 标准 OpenAI 列表结构 → reachable=True 且抽出 data[].id 列表。"""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResp(200, {"data": [{"id": "m-a"}, {"id": "m-b"}, {"id": "m-c"}]})

    realmod, saved = _install_requests_stub(fake_get)
    try:
        res = mod.probe_models_endpoint("sk-secret", "https://relay.test/v1")
    finally:
        realmod.get = saved

    assert res["reachable"] is True, res
    assert res["status_code"] == 200, res
    assert res["models_listed"] == ["m-a", "m-b", "m-c"], res
    # URL 由 base_url 去尾斜杠后拼 /models 构成
    assert captured["url"] == "https://relay.test/v1/models", captured["url"]
    # 鉴权头携带 api_key
    assert captured["headers"]["Authorization"] == "Bearer sk-secret", captured["headers"]
    assert captured["timeout"] == 15, captured["timeout"]


def test_probe_endpoint_strips_trailing_slash_on_base_url():
    """base_url 带尾斜杠时不能拼出 //models（rstrip 行为）。"""
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        return _FakeResp(200, {"data": []})

    realmod, saved = _install_requests_stub(fake_get)
    try:
        mod.probe_models_endpoint("k", "https://relay.test/v1/")
    finally:
        realmod.get = saved

    assert seen["url"] == "https://relay.test/v1/models", seen["url"]


def test_probe_endpoint_non_200_returns_error_branch():
    """非 200 → reachable=False + status_code 透传 + error 截断到 200 字符。"""
    long_body = "E" * 500

    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(401, text=long_body)

    realmod, saved = _install_requests_stub(fake_get)
    try:
        res = mod.probe_models_endpoint("k", "https://relay.test/v1")
    finally:
        realmod.get = saved

    assert res["reachable"] is False, res
    assert res["status_code"] == 401, res
    assert "error" in res, res
    assert len(res["error"]) == 200, ("error 未截断到 200", len(res["error"]))
    assert "models_listed" not in res, res


def test_probe_endpoint_exception_is_caught_into_error_dict():
    """requests.get 抛异常 → 不冒泡·吞成 {reachable:False, error:'类型: 消息'}。"""
    def fake_get(url, headers=None, timeout=None):
        raise ConnectionError("relay down")

    realmod, saved = _install_requests_stub(fake_get)
    try:
        res = mod.probe_models_endpoint("k", "https://relay.test/v1")
    finally:
        realmod.get = saved

    assert res["reachable"] is False, res
    assert "ConnectionError" in res["error"], res
    assert "relay down" in res["error"], res
    assert res["base_url"] == "https://relay.test/v1", res


def test_probe_endpoint_200_missing_data_key_yields_empty_list():
    """200 但 payload 无 data 键 → models_listed 为空列表（不崩）。"""
    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(200, {"object": "list"})  # 故意没有 data

    realmod, saved = _install_requests_stub(fake_get)
    try:
        res = mod.probe_models_endpoint("k", "https://relay.test/v1")
    finally:
        realmod.get = saved

    assert res["reachable"] is True, res
    assert res["models_listed"] == [], res


def test_net_guard_blocks_real_get_when_not_mocked():
    """兜底自证：装了 _net_guard 后再调 probe_models_endpoint，必走异常分支（绝不真出网）。"""
    realmod, saved = _net_guard()
    try:
        res = mod.probe_models_endpoint("k", "https://relay.test/v1")
    finally:
        realmod.get = saved
    # 真 get 被哨兵替换成 raise → 被 except 捕获成 error dict（reachable=False）
    assert res["reachable"] is False, res
    assert "_NetForbidden" in res["error"] or "NetForbidden" in res["error"], res


# ============================================================================
# load_cache / save_cache —— 缓存 JSON 往返 + 容错（重定向到临时目录）
# ============================================================================
def _with_temp_cache(fn):
    """把 get_cache_path 重定向到临时文件后执行 fn，再还原。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    cache_file = tmp / ".model_capabilities.json"
    saved = mod.get_cache_path
    mod.get_cache_path = lambda: cache_file
    try:
        return fn(cache_file)
    finally:
        mod.get_cache_path = saved
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_then_load_cache_roundtrip_with_unicode():
    """save_cache → load_cache 往返保真，含中文（ensure_ascii=False·utf-8）。"""
    payload = {
        "schema_version": "1.0",
        "model_capabilities": {"模型甲": {"context_window": 1048576, "notes": "中文备注"}},
    }

    def body(cache_file):
        mod.save_cache(payload)
        assert cache_file.exists(), "save_cache 没写出文件"
        raw = cache_file.read_text(encoding="utf-8")
        assert "模型甲" in raw, "中文被转义/丢失（ensure_ascii 应为 False）"
        loaded = mod.load_cache()
        assert loaded == payload, (loaded, payload)

    _with_temp_cache(body)


def test_load_cache_missing_file_returns_empty_dict():
    """缓存文件不存在 → 返回 {}（不崩，main --list 靠此判空）。"""
    def body(cache_file):
        assert not cache_file.exists()
        assert mod.load_cache() == {}, "缺文件时 load_cache 应返回空 dict"

    _with_temp_cache(body)


def test_load_cache_corrupt_json_tolerated_as_empty():
    """缓存文件是损坏 JSON → 吞异常返回 {}（不让脏缓存崩流水线）。"""
    def body(cache_file):
        cache_file.write_text("{ not valid json ::: ", encoding="utf-8")
        assert mod.load_cache() == {}, "损坏 JSON 应被容错成空 dict"

    _with_temp_cache(body)


# ============================================================================
# KNOWN_MODELS —— 已知模型表 schema 完整性（下游 main() 依赖这些字段格式化输出）
# ============================================================================
def test_known_models_table_schema_integrity():
    """每个已知模型必须含 main() step2 直接索引的整型能力字段 + source。"""
    int_keys = ("context_window", "max_output_tokens", "recommended_max_tokens_for_writing")
    assert len(mod.KNOWN_MODELS) >= 5, "已知模型表异常缩水"
    for mid, caps in mod.KNOWN_MODELS.items():
        for k in int_keys:
            assert k in caps, f"{mid} 缺 {k}"
            assert isinstance(caps[k], int), f"{mid}.{k} 不是 int（main 用 :, 格式化会崩）: {caps[k]!r}"
        assert isinstance(caps.get("source"), str) and caps["source"], f"{mid} 缺 source"


def test_get_cache_path_is_in_dotclaude_dir():
    """缓存路径落在 .claude 目录下、文件名固定（与读端 gen_* 约定一致）。"""
    p = mod.get_cache_path()
    assert p.name == ".model_capabilities.json", p
    assert p.parent.name == ".claude", p


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    failed = 0
    for name in sorted(g):
        if name.startswith("test_"):
            try:
                g[name]()
                print("OK", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, e)
                traceback.print_exc()
    sys.exit(1 if failed else 0)
