# NiceGUI 官方测试插件（User 夹具·无浏览器模拟 UI·角度②）
# 本目录用 pytest 跑（python -m pytest tests/gui）——零依赖 runner 的 glob 不递归
# 子目录，不会误捡这里的 async 测试。
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest_plugins = ["nicegui.testing.user_plugin"]
