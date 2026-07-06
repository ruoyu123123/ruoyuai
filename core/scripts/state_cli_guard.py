"""Internal guard for cluster-save-state child scripts."""
from __future__ import annotations

import os
import sys

ENV_NAME = "RUOYUAI_CLUSTER_STATE_INTERNAL"


def internal_env() -> dict[str, str]:
    env = os.environ.copy()
    env[ENV_NAME] = "1"
    return env


def is_internal() -> bool:
    return os.environ.get(ENV_NAME) == "1"


def require_internal(script_name: str) -> None:
    if is_internal():
        return
    print(
        f"[FATAL] {script_name} 是 /cluster-save-state 内部单章子步骤，"
        "禁止直接作为公开状态写入口运行；请通过 /cluster-save-state --cluster 调用。",
        file=sys.stderr,
    )
    sys.exit(2)
