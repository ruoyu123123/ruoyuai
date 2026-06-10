# 顶层 conftest（rootdir=tests）——pytest_plugins 必须在顶层 conftest，否则
# `python -m pytest tests/` 会因「非顶层 conftest 定义 pytest_plugins」硬错中断收集
# （pytest 9.x 把它从 deprecation 升为 collection error）。本文件取代原 tests/gui/conftest.py
# 里的 pytest_plugins（对抗审查 finding：那处破坏了 run_tests.py docstring 宣传的
# `pytest tests/` 调用方式）。
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

# NiceGUI 官方测试插件（User 夹具·无浏览器模拟 UI·tests/gui 用）。装在顶层 conftest
# 后对 tests/ 下所有 pytest 调用生效；nicegui 已装，对非 gui 测试无害。
pytest_plugins = ["nicegui.testing.user_plugin"]
