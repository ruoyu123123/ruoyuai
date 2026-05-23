#!/usr/bin/env python3
"""
[DEPRECATED] deepseek_fixer.py — 已重命名为 gen_fixer.py（Gen-Model 抽象层重构，2026-05-19）

本文件保留为兼容 shim，转发到 gen_fixer.py。
计划移除日期：2026-07-19（约 2 月兼容期）

新增 mode：validator-repair / voice-fix（配合 novel-validator-checker / novel-voice-checker agent）

请尽快迁移调用方到 gen_fixer.py：
  python core/scripts/gen_fixer.py ...

模型配置不再用 DEEPSEEK_* 字段，改用 GEN_MODEL_ACTIVE + GEN__<name>__* 多 profile。
跑 `python core/scripts/gen_model.py list / switch / show` 管理。
"""
import os
import subprocess
import sys

if __name__ == '__main__':
    print(
        "[DEPRECATED] deepseek_fixer.py -> gen_fixer.py "
        "(removal: 2026-07-19)\n"
        "  请迁移到 'python core/scripts/gen_fixer.py ...'",
        file=sys.stderr,
    )
    gen_fixer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'gen_fixer.py')
    sys.exit(subprocess.run(
        [sys.executable, gen_fixer_path, *sys.argv[1:]],
        cwd=os.getcwd(),
    ).returncode)
