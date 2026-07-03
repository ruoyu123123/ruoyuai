# 顶层 conftest（rootdir=tests）—— sys.path 注入 + NN 门控环境隔离。
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


# 🔴 2026-06-30 NN 门控测试隔离（根治跨测试污染·举一反三）
# 背景：部分测试用裸 os.environ["RUOYU_NN_*"]=... 设门控·断言失败时未清理 → 残留污染
#       后续 emotion/VAD 测试（本机 ckpt 齐备时会真跑 NN·拿到非确定性值 → 假失败）。
# 本 autouse fixture 每个测试前清空所有 NN 门控·测试后恢复进入时的值——
# 无论测试用 monkeypatch 还是裸 os.environ·都杜绝跨测试残留（finally 兜底清理）。
_NN_GATES = (
    "RUOYU_NN_SURPRISAL", "RUOYU_NN_COHERENCE", "RUOYU_NN_VAD",
    "RUOYU_NN_COREF", "RUOYU_CHARACTER_NETWORK", "RUOYU_NN_NLI",
    "RUOYU_FEATURE_STORE", "RUOYU_DATA_FLYWHEEL", "RUOYU_MODEL_REGISTRY",
    "RUOYU_PREF_RANKER", "RUOYU_NN_DAEMON",
    # EMBED_BACKEND：W5 起创作入口 setdefault=ruoyu_style（字符串值型）。测试前必须清空——
    # 泄漏进测试会让语义路径真调 venv（daemon 有 pytest 守卫不会拉起，但会走 25s 子进程）。
    "EMBED_BACKEND",
)


@pytest.fixture(autouse=True)
def _isolate_nn_gates():
    """每个测试前清空 NN 门控·测试后恢复进入时的值——杜绝裸 os.environ 跨测试残留污染。"""
    saved = {g: os.environ.get(g) for g in _NN_GATES}
    for g in _NN_GATES:
        os.environ.pop(g, None)
    try:
        yield
    finally:
        for g, v in saved.items():
            if v is None:
                os.environ.pop(g, None)
            else:
                os.environ[g] = v
