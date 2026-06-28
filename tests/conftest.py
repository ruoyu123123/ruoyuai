# 顶层 conftest（rootdir=tests）—— 只做 sys.path 注入（项目根 + core/scripts）。
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)
