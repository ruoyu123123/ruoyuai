#!/usr/bin/env python3
"""web_search_client.py 测试（BYOK search·全内存 keyring + mock httpx·绝不联网/碰真 CM·零依赖顶层）。

D1 research-web 骨架：BYOK search key 三级解析 + Tavily 封装 + key 未配降级 + err 脱敏。
key 全用假值·httpx 全 mock（不发真请求）·keyring 全内存（不碰真 Credential Manager）。
"""
import io
import os
import sys
from contextlib import redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
import web_search_client as wsc  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402


def _with_mem(fn):
    """注入内存后端 + 清 env 跑 fn，结束还原（防跨测试污染真 keyring / env）。"""
    orig = keyring.get_keyring()
    orig_env = os.environ.get("RUOYU_SEARCH_KEY")
    keyring.set_keyring(MemKeyring())
    os.environ.pop("RUOYU_SEARCH_KEY", None)
    try:
        fn()
    finally:
        keyring.set_keyring(orig)
        if orig_env is None:
            os.environ.pop("RUOYU_SEARCH_KEY", None)
        else:
            os.environ["RUOYU_SEARCH_KEY"] = orig_env


# ============ secrets_store search service（独立 namespace）============
def test_search_key_roundtrip():
    def body():
        assert ss.set_search_key("tavily", "tvly-ABC12345") is True
        assert ss.get_search_key("tavily") == "tvly-ABC12345"
        assert ss.has_search_key("tavily") is True
    _with_mem(body)


def test_search_key_separate_from_gen_model():
    """search key 独立 service·与 gen-model key 同名 username 也不串。"""
    def body():
        ss.set_api_key("tavily", "sk-GENMODEL")        # gen-model service
        ss.set_search_key("tavily", "tvly-SEARCH")      # search service·同 username
        assert ss.get_search_key("tavily") == "tvly-SEARCH"
        assert ss.get_api_key("tavily") == "sk-GENMODEL"   # 互不串（service 隔离）
    _with_mem(body)


def test_search_key_empty_is_delete():
    def body():
        ss.set_search_key("tavily", "tvly-X")
        assert ss.set_search_key("tavily", "  ") is True    # 空串→delete
        assert ss.get_search_key("tavily") is None
        assert ss.has_search_key("tavily") is False
    _with_mem(body)


# ============ _resolve_search_key 三级优先级 ============
def test_resolve_key_from_keyring():
    def body():
        ss.set_search_key("tavily", "tvly-KEYRING")
        assert wsc._resolve_search_key("tavily") == "tvly-KEYRING"
    _with_mem(body)


def test_resolve_key_from_env_when_keyring_empty():
    """keyring 无 → env RUOYU_SEARCH_KEY 兜底。"""
    def body():
        os.environ["RUOYU_SEARCH_KEY"] = "tvly-ENVKEY"
        assert wsc._resolve_search_key("tavily") == "tvly-ENVKEY"
    _with_mem(body)


def test_resolve_key_keyring_over_env():
    """keyring 优先 env（BYOK 主路径）。"""
    def body():
        ss.set_search_key("tavily", "tvly-KEYRING")
        os.environ["RUOYU_SEARCH_KEY"] = "tvly-ENV"
        assert wsc._resolve_search_key("tavily") == "tvly-KEYRING"
    _with_mem(body)


def test_resolve_key_missing_raises():
    """keyring + env 都无 → SearchKeyMissing（调用方降级静态模板）。"""
    def body():
        try:
            wsc._resolve_search_key("tavily")
            assert False, "应 raise SearchKeyMissing"
        except wsc.SearchKeyMissing:
            pass
    _with_mem(body)


def test_is_search_available():
    def body():
        assert wsc.is_search_available("tavily") is False   # 未配
        ss.set_search_key("tavily", "tvly-K")
        assert wsc.is_search_available("tavily") is True
    _with_mem(body)


# ============ search（mock httpx·不联网）============
class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None):
        return _FakeResp(self._data)


def _patch_httpx_client(factory):
    """替换 httpx.Client 为 factory（不联网）·返回还原函数。"""
    import httpx
    orig = httpx.Client
    httpx.Client = factory
    return lambda: setattr(httpx, "Client", orig)


def test_search_tavily_mock_returns_llm_ready():
    def body():
        ss.set_search_key("tavily", "tvly-K")
        restore = _patch_httpx_client(lambda *a, **k: _FakeClient({
            "results": [
                {"title": "T1", "url": "http://a", "content": "C1"},
                {"title": "T2", "url": "http://b", "content": "C2"},
            ]}))
        try:
            res = wsc.search("测试查询", max_results=5)
            assert len(res) == 2
            assert res[0] == {"title": "T1", "url": "http://a", "content": "C1"}
        finally:
            restore()
    _with_mem(body)


def test_search_empty_query_raises():
    def body():
        try:
            wsc.search("  ")
            assert False, "空 query 应 ValueError"
        except ValueError:
            pass
    _with_mem(body)


def test_search_unsupported_provider_raises():
    def body():
        try:
            wsc.search("q", provider="brave")
            assert False, "未支持 provider 应 ValueError"
        except ValueError:
            pass
    _with_mem(body)


def test_normalize_handles_missing_fields():
    res = wsc._normalize_tavily(
        {"results": [{"title": "only title"}, "not a dict", {}]}, 5)
    assert res[0] == {"title": "only title", "url": "", "content": ""}
    assert len(res) == 2                 # "not a dict" 跳过


def test_search_key_never_leaks_to_error():
    """网络错 → SearchError·err 字符串不含 key（脱敏 + key 本在 payload 不在 err）。"""
    def body():
        ss.set_search_key("tavily", "tvly-SECRETKEY999")
        import httpx

        class _ErrClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None):
                raise httpx.ConnectError("conn refused")

        restore = _patch_httpx_client(lambda *a, **k: _ErrClient())
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                try:
                    wsc.search("q")
                    assert False, "网络错应 raise SearchError"
                except wsc.SearchError as e:
                    assert "tvly-SECRETKEY999" not in str(e)
            assert "tvly-SECRETKEY999" not in buf.getvalue()
        finally:
            restore()
    _with_mem(body)


# ============ gather_research_context（多 query → gen-model 输入 block）============
def test_gather_research_context_multi_query():
    """多 query → 格式化 research context markdown（含来源计数 header + 防编造提示）。"""
    def body():
        ss.set_search_key("tavily", "tvly-K")
        restore = _patch_httpx_client(lambda *a, **k: _FakeClient({
            "results": [
                {"title": "T1", "url": "http://a", "content": "C1"},
                {"title": "T2", "url": "http://b", "content": "C2"},
            ]}))
        try:
            ctx, n = wsc.gather_research_context(["查询1", "查询2"], max_per_query=3)
            assert n == 4                        # 2 query × 2 results
            assert "联网调研结果" in ctx
            assert "查询1" in ctx and "查询2" in ctx
            assert "T1" in ctx and "http://a" in ctx
            assert "勿凭记忆编造" in ctx          # gen-model 防编造提示
        finally:
            restore()
    _with_mem(body)


def test_gather_research_empty_queries():
    def body():
        assert wsc.gather_research_context([]) == ("", 0)
        assert wsc.gather_research_context(["  ", ""]) == ("", 0)   # 全空白
    _with_mem(body)


def test_gather_research_key_missing_raises():
    """key 未配 → SearchKeyMissing 冒泡（调用方 catch 降级静态模板）。"""
    def body():
        try:
            wsc.gather_research_context(["查询"])
            assert False, "key 未配应 raise SearchKeyMissing"
        except wsc.SearchKeyMissing:
            pass
    _with_mem(body)


def test_gather_research_no_results():
    """搜索返回空 → ('', 0)（不崩·调用方降级）。"""
    def body():
        ss.set_search_key("tavily", "tvly-K")
        restore = _patch_httpx_client(lambda *a, **k: _FakeClient({"results": []}))
        try:
            assert wsc.gather_research_context(["查询"]) == ("", 0)
        finally:
            restore()
    _with_mem(body)


# ============ build_default_queries（确定性拼 queries·零依赖纯模板）============
def test_build_default_queries_inspiration():
    assert wsc.build_default_queries("inspiration", "克苏鲁灯塔") == [
        "克苏鲁灯塔 网文 爆款 设定", "克苏鲁灯塔 题材 灵感 趋势", "克苏鲁灯塔 小说 创意"]


def test_build_default_queries_per_task_type():
    assert wsc.build_default_queries("outline", "X")[0] == "X 剧情 走向 网文"
    assert wsc.build_default_queries("character", "X")[0] == "X 人物 设定"
    assert wsc.build_default_queries("fact_check", "X")[0] == "X 设定 考据"


def test_build_default_queries_unknown_type_falls_back():
    """未知 task_type 回退 inspiration 模板。"""
    assert wsc.build_default_queries("nonsense", "X")[0] == "X 网文 爆款 设定"


def test_build_default_queries_empty_topic():
    assert wsc.build_default_queries("inspiration", "") == []
    assert wsc.build_default_queries("inspiration", "  ") == []


def test_build_default_queries_max_cap():
    assert wsc.build_default_queries("inspiration", "X", max_queries=1) == ["X 网文 爆款 设定"]


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
