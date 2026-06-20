# 顶层 conftest（rootdir=tests）—— 2026-06-20 GUI 删档后只剩 sys.path 注入。
# 原 pytest_plugins=['nicegui.testing.user_plugin'] 随 tests/gui/ 整目录一同删除。
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)
