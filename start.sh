#!/usr/bin/env bash
# 若渝AI · macOS/Linux 启动入口
# 用法：cd 到任意目录后执行 path/to/ruoyuai/start.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(pwd)"

# -- Sync CLAUDE.md + commands to current working dir --
if [ -f "$SCRIPT_DIR/core/claude-home/CLAUDE.md" ]; then
    cp -f "$SCRIPT_DIR/core/claude-home/CLAUDE.md" "$PROJECT_DIR/CLAUDE.md"
fi
mkdir -p "$PROJECT_DIR/.claude/commands"
if [ -d "$SCRIPT_DIR/core/claude-home/commands" ]; then
    cp -rf "$SCRIPT_DIR/core/claude-home/commands/"*.md "$PROJECT_DIR/.claude/commands/" 2>/dev/null || true
fi

# -- Launch Claude Code (interactive) --
# 用户随便打个字就触发主菜单（CLAUDE.md 接管）
claude --dangerously-skip-permissions --append-system-prompt "Show main menu on start. Hard rules: use Agent sub-tasks for writing chapters, use Agent sub-tasks for each chapter in distillation, save-state must run all 11 steps, write directly after outline without asking user, auto-switch source on fetch failure without asking user." "$@"
