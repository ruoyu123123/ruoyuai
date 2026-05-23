#!/bin/bash
# PostToolUse Hook: 将工具调用显示为可读的一句话
# 写入 /tmp/cc-tool-action.json 供 statusline 读取
# 保留原始 description，不做中英翻译

CACHE="/tmp/cc-tool-action.json"

# 读取 stdin（限 10KB，避免巨大 tool_response 卡住）
INPUT=$(head -c 10240)

TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ -z "$TOOL" ] && exit 0

NOW=$(date +%s)

# 原子写入缓存（写 tmp 再 mv，防止 statusline 读到半截）
write_cache() {
  local tmp="${CACHE}.tmp.$$"
  jq -n --arg tool "$TOOL" --arg text "$1" --argjson time "$NOW" \
    '{tool: $tool, text: $text, time: $time}' > "$tmp" 2>/dev/null
  mv -f "$tmp" "$CACHE" 2>/dev/null
}

case "$TOOL" in
  Read)
    F=$(echo "$INPUT" | jq -r '.tool_input.file_path // "?"' 2>/dev/null)
    write_cache "📖 Read $(basename "$F")"
    ;;
  Edit)
    F=$(echo "$INPUT" | jq -r '.tool_input.file_path // "?"' 2>/dev/null)
    write_cache "✏️  Edit $(basename "$F")"
    ;;
  Write)
    F=$(echo "$INPUT" | jq -r '.tool_input.file_path // "?"' 2>/dev/null)
    write_cache "📝 Write $(basename "$F")"
    ;;
  Bash)
    DESC=$(echo "$INPUT" | jq -r '.tool_input.description // empty' 2>/dev/null)
    if [ -n "$DESC" ]; then
      write_cache "💻 ${DESC:0:60}"
    else
      CMD=$(echo "$INPUT" | jq -r '.tool_input.command // "?"' 2>/dev/null)
      write_cache "💻 ${CMD:0:60}"
    fi
    ;;
  Grep)
    P=$(echo "$INPUT" | jq -r '.tool_input.pattern // "?"' 2>/dev/null)
    write_cache "🔍 Grep: ${P:0:40}"
    ;;
  Glob)
    P=$(echo "$INPUT" | jq -r '.tool_input.pattern // "?"' 2>/dev/null)
    write_cache "📂 Glob: ${P:0:40}"
    ;;
  Agent)
    D=$(echo "$INPUT" | jq -r '.tool_input.description // "?"' 2>/dev/null)
    write_cache "🤖 Agent: ${D:0:45}"
    ;;
  WebSearch)
    Q=$(echo "$INPUT" | jq -r '.tool_input.query // "?"' 2>/dev/null)
    write_cache "🌐 Search: ${Q:0:40}"
    ;;
  WebFetch)
    U=$(echo "$INPUT" | jq -r '.tool_input.url // "?"' 2>/dev/null)
    write_cache "🌐 Fetch: ${U:0:50}"
    ;;
  Skill)
    S=$(echo "$INPUT" | jq -r '.tool_input.skill // "?"' 2>/dev/null)
    write_cache "⚡ Skill: $S"
    ;;
  ToolSearch)
    Q=$(echo "$INPUT" | jq -r '.tool_input.query // "?"' 2>/dev/null)
    write_cache "🔎 ToolSearch: ${Q:0:40}"
    ;;
  LSP)
    write_cache "🧠 LSP"
    ;;
  NotebookEdit)
    write_cache "📓 NotebookEdit"
    ;;
  Task*)
    write_cache "📋 $TOOL"
    ;;
  Cron*)
    write_cache "⏰ $TOOL"
    ;;
  EnterPlanMode)
    write_cache "📐 Enter Plan Mode"
    ;;
  ExitPlanMode)
    write_cache "📐 Exit Plan Mode"
    ;;
  AskUserQuestion)
    write_cache "❓ Waiting for user"
    ;;
  mcp__*)
    # MCP: 用 __ 分割取最后一段作为动作名
    ACTION_PART=$(echo "$TOOL" | awk -F'__' '{print $NF}' | tr '_' ' ')
    write_cache "🔌 ${ACTION_PART:0:40}"
    ;;
  *)
    write_cache "🔧 $TOOL"
    ;;
esac

exit 0
