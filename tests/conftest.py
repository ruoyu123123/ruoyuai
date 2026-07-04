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

# 🔴 2026-07-04 W6-C 举一反三：content_backend_available() 是**文件存在性判定**（venv+infer
# 脚本+bge 模型目录三者俱在即 True），不是环境变量门控——本机三者俱在时它在测试环境不设任何
# 变量也返回 True，会让走内容后端的 active 测试真触发 venv 子进程（实测 263x 变慢 + 非确定性·
# w6s-zeroshot 发现①）。_NN_GATES 的清空机制对这种函数型门控完全不知情。根治：用其公开的 ckpt
# 覆盖钩子 RUOYU_CONTENT_EMBED_CKPT 指向不存在路径 → 函数体内 Path(ckpt).exists()=False → 内容
# 后端在测试里默认关闭（同 NN 门控「默认关·要用显式开」语义）。想测内容路径的测试自行
# monkeypatch content_backend_available→True + compute_content_embeddings_batch(假向量)（函数
# 替换优先于本 env）；真机测试自行 setenv 正确 ckpt 覆盖 sentinel。作用在函数体内部·不管消费方
# 怎么 import content_backend_available 都生效（比逐文件 monkeypatch 鲁棒·单点根治）。
_CONTENT_CKPT_ENV = "RUOYU_CONTENT_EMBED_CKPT"
_CONTENT_BACKEND_OFF_SENTINEL = str(_ROOT / ".nonexistent_content_ckpt_for_tests")


@pytest.fixture(autouse=True)
def _isolate_nn_gates():
    """每个测试前清空 NN 门控 + 关闭内容后端·测试后恢复进入时的值——杜绝跨测试残留污染 +
    杜绝函数型内容门控在测试里真触发子进程。"""
    saved = {g: os.environ.get(g) for g in _NN_GATES}
    saved_ckpt = os.environ.get(_CONTENT_CKPT_ENV)
    for g in _NN_GATES:
        os.environ.pop(g, None)
    os.environ[_CONTENT_CKPT_ENV] = _CONTENT_BACKEND_OFF_SENTINEL   # 内容后端默认关（见上）
    try:
        yield
    finally:
        for g, v in saved.items():
            if v is None:
                os.environ.pop(g, None)
            else:
                os.environ[g] = v
        if saved_ckpt is None:
            os.environ.pop(_CONTENT_CKPT_ENV, None)
        else:
            os.environ[_CONTENT_CKPT_ENV] = saved_ckpt
