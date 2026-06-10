#!/usr/bin/env python3
"""_smoke_inproc_target.py — frozen_smoke 路径1 进程内正例被试（带 main()）。

run_script_in_process 调的是 module.main()（非 __main__ 块），故此文件必须有 def main()。
内部跑纯 stdlib 断言（不读项目数据/文件）·return 0。须列入 spec hiddenimports（importlib
经 FrozenImporter 从 PYZ 找它·对抗审查 must_fix#3）。
"""
import sys


def main():
    import re

    def _norm(v):
        m = re.search(r"(\d+)", str(v))
        return f"cluster_{int(m.group(1)):03d}" if m else None

    assert _norm(6) == "cluster_006"
    assert _norm("cluster_2") == "cluster_002"
    assert _norm("cluster_006") == "cluster_006"
    print("inproc-ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
