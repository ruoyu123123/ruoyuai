#!/usr/bin/env python3
"""gen_model_loader.py 测试（多 profile 解析 + active/fallback 链 + reasoning_extra_body）。

覆盖所有生成脚本依赖的 profile 解析与 fallback。使用临时 `.env` 并严格恢复
`os.environ` 快照；GenModelLoader.__init__ 的 load_dotenv(override=True)
会把 env 注入 os.environ，测完必还原防污染真 .env / 其他测试（见 memory project_fake_llm_cli_e2e_harness）。
profile 名用 utgml_ 前缀避免与真实 keyring 条目碰撞（_resolve_api_key 会查 keyring）。
"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_model_loader as gml  # noqa: E402


@contextlib.contextmanager
def _env_snapshot():
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


_ENV_BODY = """\
GEN__utgml_a__MODEL=model-a
GEN__utgml_a__BASE_URL=https://a.example/v1
GEN__utgml_a__API_KEY=sk-aaa
GEN__utgml_a__TEMPERATURE=1.0
GEN__utgml_a__MAX_TOKENS=65536
GEN__utgml_a__REASONING_EFFORT=low
GEN__utgml_b__MODEL=model-b
GEN__utgml_b__BASE_URL=https://b.example/v1
GEN__utgml_b__API_KEY=sk-bbb
GEN__utgml_b__PROTOCOL=gemini
GEN__utgml_b__THINKING_LEVEL=high
GEN__utgml_c__MODEL=model-c
GEN__utgml_c__BASE_URL=https://c.example/v1
GEN__utgml_c__API_KEY=
GEN__utgml_d__MODEL=model-d
GEN__utgml_d__BASE_URL=https://d.example/v1
GEN__utgml_d__API_KEY=sk-ddd
"""


def _loader(tmp, active=None, fallback=None):
    """写临时 env + 构造 loader。active/fallback 手动设 os.environ（dotenv 无关·更鲁棒）。"""
    p = Path(tmp) / "t.env"
    p.write_text(_ENV_BODY, encoding="utf-8")
    # 先清掉可能从真 .env 残留的控制键，再按本测试设
    for k in ("GEN_MODEL_ACTIVE", "GEN_MODEL_FALLBACK_CHAIN"):
        os.environ.pop(k, None)
    if active is not None:
        os.environ["GEN_MODEL_ACTIVE"] = active
    if fallback is not None:
        os.environ["GEN_MODEL_FALLBACK_CHAIN"] = fallback
    ld = gml.GenModelLoader(env_path=p)
    # load_dotenv(override=True) 可能用 env 文件值覆盖；本 env 文件无 active/fallback 键 → 我们设的保留
    if active is not None:
        os.environ["GEN_MODEL_ACTIVE"] = active
    if fallback is not None:
        os.environ["GEN_MODEL_FALLBACK_CHAIN"] = fallback
    elif fallback is None:
        os.environ.pop("GEN_MODEL_FALLBACK_CHAIN", None)
    return ld


def test_parse_profiles_all_fields():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a")
        a = ld.get_profile("utgml_a")
        assert a is not None
        assert a.model == "model-a"
        assert a.base_url == "https://a.example/v1"
        assert a.api_key == "sk-aaa"
        assert a.temperature == 1.0
        assert a.max_tokens == 65536
        assert a.protocol == "openai"          # 默认
        assert a.reasoning_effort == "low"
        assert a.thinking_level is None


def test_protocol_and_thinking_level_parsed():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a")
        b = ld.get_profile("utgml_b")
        assert b.protocol == "gemini"
        assert b.thinking_level == "HIGH"       # upper 归一
        assert b.reasoning_effort is None


def test_max_tokens_none_when_absent():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a")
        d = ld.get_profile("utgml_d")
        assert d.max_tokens is None             # 缺 MAX_TOKENS → None（从 model_probe 读）


def test_active_profile_resolves():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a")
        assert ld.get_active_profile().name == "utgml_a"


def test_active_missing_raises():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active=None)
        os.environ.pop("GEN_MODEL_ACTIVE", None)   # 确保无 active
        try:
            ld.get_active_profile()
            assert False, "无 GEN_MODEL_ACTIVE 应抛 GenModelConfigError"
        except gml.GenModelConfigError:
            pass


def test_active_nonexistent_raises():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_ghost")
        try:
            ld.get_active_profile()
            assert False, "不存在的 active 应抛"
        except gml.GenModelConfigError:
            pass


def test_active_missing_key_raises():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_c")     # c 的 API_KEY 空
        try:
            ld.get_active_profile()
            assert False, "active 缺 key 应抛"
        except gml.GenModelConfigError:
            pass


def test_fallback_chain_parse():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a", fallback="utgml_b, utgml_c")
        assert ld.get_fallback_chain() == ["utgml_b", "utgml_c"]


def test_callable_profiles_filters_empty_key():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a", fallback="utgml_b, utgml_c")
        names = [p.name for p in ld.get_callable_profiles()]
        # active=a + b(有key) ; c 空 key 被过滤
        assert names == ["utgml_a", "utgml_b"], names


def test_reasoning_extra_body_independent_fields():
    with _env_snapshot(), tempfile.TemporaryDirectory() as tmp:
        ld = _loader(tmp, active="utgml_a")
        assert gml.reasoning_extra_body(ld.get_profile("utgml_a")) == {"reasoning_effort": "low"}
        assert gml.reasoning_extra_body(ld.get_profile("utgml_b")) == {"thinking_level": "HIGH"}
        assert gml.reasoning_extra_body(ld.get_profile("utgml_d")) == {}   # 都没 → 空 dict 不注入


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
