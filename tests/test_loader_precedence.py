#!/usr/bin/env python3
"""gen_model_loader api_key 三级优先级测试（BYOK·keyring > environ > .env）。

零依赖顶层（run_tests.py glob 得到）·内存 keyring·临时 .env·try/finally 清环境。
核心：① dev 零回归基线（现状逐字节）② keyring 赢 ③ environ 注入（.env 未定义时）
④ override=True 现实锁定（.env 定义时 environ 不覆盖）⑤ 全空抛错 ⑥ 缓存失效链。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
import gen_model_loader as gml  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402

SVC = ss.SERVICE


def _write_env(text: str) -> Path:
    f = Path(tempfile.mkstemp(suffix=".env")[1])
    f.write_text(text, encoding="utf-8")
    return f


def _clean_environ():
    for k in list(os.environ):
        if k.startswith("GEN__") or k in ("GEN_MODEL_ACTIVE", "GEN_MODEL_FALLBACK_CHAIN"):
            os.environ.pop(k, None)


def _run(env_text, *, keyring_kv=None, environ_kv=None, assert_fn):
    """统一夹具：内存 keyring + 临时 .env + 干净 environ → 新 loader → 断言 → 全还原。"""
    orig_kr = keyring.get_keyring()
    saved_environ = {k: v for k, v in os.environ.items()
                     if k.startswith("GEN__") or k.startswith("GEN_MODEL_")}
    env_file = _write_env(env_text)
    keyring.set_keyring(MemKeyring())
    try:
        _clean_environ()
        for k, v in (keyring_kv or {}).items():
            ss.set_api_key(k, v)
        for k, v in (environ_kv or {}).items():
            os.environ[k] = v
        gml.reset_default_loader()
        loader = gml.GenModelLoader(env_path=env_file)  # 显式路径绕单例
        assert_fn(loader)
    finally:
        keyring.set_keyring(orig_kr)
        _clean_environ()
        for k, v in saved_environ.items():
            os.environ[k] = v
        gml.reset_default_loader()
        try:
            env_file.unlink()
        except OSError:
            pass


# ① dev 零回归基线：仅 .env 有 key（keyring 空·environ 无）→ 逐字节现状
def test_case1_env_only_baseline():
    def chk(loader):
        p = loader.get_profile("t")
        assert p is not None and p.api_key == "ENVFILE_KEY"
    _run("GEN__t__MODEL=m\nGEN__t__API_KEY=ENVFILE_KEY\nGEN_MODEL_ACTIVE=t\n",
         assert_fn=chk)


# ② keyring 赢（keyring + .env 都有）
def test_case2_keyring_wins():
    def chk(loader):
        assert loader.get_profile("t").api_key == "KR_KEY"
    _run("GEN__t__MODEL=m\nGEN__t__API_KEY=ENVFILE_KEY\nGEN_MODEL_ACTIVE=t\n",
         keyring_kv={"t": "KR_KEY"}, assert_fn=chk)


# ③ environ 注入：.env **未定义** 该 key（无 GEN__t__API_KEY 行）+ environ 有 → environ 胜
def test_case3_environ_injection_when_env_absent():
    def chk(loader):
        assert loader.get_profile("t").api_key == "ENV_INJECTED"
    _run("GEN__t__MODEL=m\nGEN_MODEL_ACTIVE=t\n",   # 故意无 API_KEY 行
         environ_kv={"GEN__t__API_KEY": "ENV_INJECTED"}, assert_fn=chk)


# ④ override=True 现实锁定：.env **定义了** key + environ 有不同值 → .env 值胜
#    （load_dotenv override 把 .env 回灌 environ·锁住这条现实·防未来重构误「修」成意外）
def test_case4_env_file_defined_locks_over_environ():
    def chk(loader):
        # 由于显式 env_path + load_dotenv(override=True) 会用 .env 值刷 environ，
        # _resolve_api_key 的 environ 层读到的是被 .env 覆盖后的值 == .env 值。
        assert loader.get_profile("t").api_key == "ENVFILE_KEY"
    _run("GEN__t__MODEL=m\nGEN__t__API_KEY=ENVFILE_KEY\nGEN_MODEL_ACTIVE=t\n",
         environ_kv={"GEN__t__API_KEY": "SHELL_STALE"}, assert_fn=chk)


# ⑤ 三者全空 → api_key 空 + get_active_profile 抛 GenModelConfigError（语义不变）
def test_case5_all_empty_raises():
    def chk(loader):
        assert loader.get_profile("t").api_key == ""
        try:
            loader.get_active_profile()
            assert False, "缺 key 应抛 GenModelConfigError"
        except gml.GenModelConfigError as e:
            assert "API_KEY" in str(e)
    _run("GEN__t__MODEL=m\nGEN_MODEL_ACTIVE=t\n", assert_fn=chk)


# ⑥ keyring 后端故障 → 优雅降级到 .env（不崩）
def test_case6_keyring_fault_degrades_to_env():
    saved = ss._keyring
    ss._keyring = None        # 模拟 keyring 完全不可用
    try:
        def chk(loader):
            assert loader.get_profile("t").api_key == "ENVFILE_KEY"
        _run("GEN__t__MODEL=m\nGEN__t__API_KEY=ENVFILE_KEY\nGEN_MODEL_ACTIVE=t\n",
             assert_fn=chk)
    finally:
        ss._keyring = saved


# ⑦ 缓存失效链：set keyring 后不 reset → 旧缓存；reset 后新 loader 读到新 key（GUI 录入生效）
def test_case7_cache_invalidation_after_set():
    orig_kr = keyring.get_keyring()
    env_file = _write_env(
        "GEN__t__MODEL=m\nGEN__t__API_KEY=ENVFILE_KEY\nGEN_MODEL_ACTIVE=t\n")
    keyring.set_keyring(MemKeyring())
    saved_environ = {k: v for k, v in os.environ.items() if k.startswith("GEN")}
    try:
        _clean_environ()
        gml.reset_default_loader()
        loader1 = gml.GenModelLoader(env_path=env_file)
        assert loader1.get_profile("t").api_key == "ENVFILE_KEY"   # keyring 空 → .env
        ss.set_api_key("t", "NEW_KR_KEY")
        # 同一 loader 实例缓存未失效 → 仍旧值
        assert loader1.get_profile("t").api_key == "ENVFILE_KEY"
        # 新 loader（GUI save 后 reset_default_loader + 新构造）→ 读到 keyring 新 key
        gml.reset_default_loader()
        loader2 = gml.GenModelLoader(env_path=env_file)
        assert loader2.get_profile("t").api_key == "NEW_KR_KEY"
    finally:
        keyring.set_keyring(orig_kr)
        _clean_environ()
        for k, v in saved_environ.items():
            os.environ[k] = v
        gml.reset_default_loader()
        try:
            env_file.unlink()
        except OSError:
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
