#!/usr/bin/env bash
# 若渝AI · macOS/Linux 启动入口
# 用法：cd 到任意目录后执行 path/to/ruoyuai/start.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(pwd)"

# -- Sync CLAUDE.md + commands to current working dir --
# 在仓库根目录内启动时跳过同步：agent 入口桩（23 行）绝不能覆盖仓库根的完整 CLAUDE.md
if [ "$PROJECT_DIR" != "$SCRIPT_DIR" ]; then
    if [ -f "$SCRIPT_DIR/core/claude-home/CLAUDE.md" ]; then
        cp -f "$SCRIPT_DIR/core/claude-home/CLAUDE.md" "$PROJECT_DIR/CLAUDE.md"
    fi
    mkdir -p "$PROJECT_DIR/.claude/commands"
    if [ -d "$SCRIPT_DIR/.claude/commands" ]; then
        cp -rf "$SCRIPT_DIR/.claude/commands/"*.md "$PROJECT_DIR/.claude/commands/" 2>/dev/null || true
    fi
fi

# -- Launch Claude Code (interactive) --
# 用户随便打个字就触发主菜单（CLAUDE.md 接管）
claude --dangerously-skip-permissions --append-system-prompt "Show main menu on start. Hard rules: writing must go through /cluster-write (cluster mode, no direct chapter generation), distillation runs in cluster mode, cluster-save-state must run all 14 steps, write directly after outline without asking user, auto-switch source on fetch failure without asking user." "$@"
