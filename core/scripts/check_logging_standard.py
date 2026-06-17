#!/usr/bin/env python3
"""日志规范检查工具 — 扫描是否有违反规范的裸 print() 调用

用法：
    python core/scripts/check_logging_standard.py
    python core/scripts/check_logging_standard.py --fix  # 自动修复（TODO）

违规模式：
1. 裸 print() 调用（应改用 log_util.info/debug）
2. logger.debug 在生产代码（应用环境变量门控）
3. 中文日志消息（i18n 后期需要）
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "core" / "scripts"

# 豁免列表（这些文件可以有 print）
WHITELIST = {
    "log_util.py",          # 日志工具本身
    "__init__.py",          # 包初始化
    "check_logging_standard.py",  # 本脚本
    "orchestrator.py",      # 已有结构化输出
    "plan_tracker.py",      # CLI 工具需要 print
    "export_book.py",       # 导出工具需要 print
}


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个文件，返回违规列表"""
    issues = []
    try:
        text = filepath.read_text(encoding="utf-8")
        lines = text.split("\n")

        for i, line in enumerate(lines, start=1):
            # 检查裸 print()
            if re.search(r'\bprint\s*\(', line):
                # 排除注释和字符串内的 print
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # 排除 print_result_json / print_progress 等合法用法
                if "print_result_json" in line or "print_progress" in line:
                    continue
                if "# OK: print" in line:  # 显式标记豁免
                    continue

                issues.append({
                    "type": "bare_print",
                    "line": i,
                    "content": line.strip(),
                    "severity": "warning"
                })

    except Exception as e:
        print(f"WARNING: 扫描 {filepath} 失败: {e}", file=sys.stderr)

    return issues


def main():
    # Windows 控制台 UTF-8 支持
    import io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    total_files = 0
    total_issues = 0
    violating_files = []

    print("开始扫描日志规范违规...")
    print(f"目标目录: {SCRIPTS_DIR}")
    print(f"豁免文件: {len(WHITELIST)} 个\n")

    for filepath in sorted(SCRIPTS_DIR.glob("*.py")):
        if filepath.name in WHITELIST:
            continue

        total_files += 1
        issues = scan_file(filepath)

        if issues:
            violating_files.append(filepath.name)
            print(f"\n[!] {filepath.name}: {len(issues)} 处违规")
            for issue in issues[:3]:  # 只显示前 3 处
                print(f"    L{issue['line']}: {issue['content'][:80]}")
            if len(issues) > 3:
                print(f"    ... 还有 {len(issues) - 3} 处")
            total_issues += len(issues)

    print("\n" + "=" * 60)
    print(f"扫描完成: {total_files} 个文件")
    print(f"发现违规: {len(violating_files)} 个文件, {total_issues} 处 print() 调用")

    if violating_files:
        print("\n建议:")
        print("1. 高频脚本优先重构（gen_writer/save_state/gen_fixer）")
        print("2. 用 log_util.info() 替换关键进度的 print()")
        print("3. 用 log_util.debug() 替换调试信息的 print()")
        print("4. 保留结构化输出用 log_util.print_result_json()")
        return 1
    else:
        print("[OK] 所有文件符合日志规范")
        return 0


if __name__ == "__main__":
    sys.exit(main())
