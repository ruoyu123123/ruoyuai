#!/bin/bash
# Send notification with click-to-focus support + Bark push
# Usage: send_notify.sh <event_type>
# event_type: "stop" or "notification"

HOOK_DIR="${HOME}/.claude/hooks"
BARK_KEY="${BARK_KEY:-}"
BARK_URL="https://api.day.app/${BARK_KEY}"
NOTIFY_APP="${HOOK_DIR}/ClaudeNotify.app/Contents/MacOS/claude_notify"
FOCUS_SCRIPT="${HOOK_DIR}/focus_terminal.sh"

# Per-session key: TERM_SESSION_ID is unique per Terminal tab
SESSION_KEY="${TERM_SESSION_ID:-default}"

# Skip notifications from subagents/background processes (no TERM_SESSION_ID)
if [ "$SESSION_KEY" = "default" ]; then
  exit 0
fi

# Skip if no session name file (session_start.sh was never run for this tab)
if [ ! -f "/tmp/claude_session_name_${SESSION_KEY}" ]; then
  exit 0
fi

# Debounce: skip "stop" notifications if last one was < 5 minutes ago
# This prevents cron polling (every 1 min) from spamming notifications
DEBOUNCE_FILE="/tmp/claude_notify_last_${SESSION_KEY}"
NOW=$(date +%s)
if [ "$1" = "stop" ] && [ -f "$DEBOUNCE_FILE" ]; then
  LAST=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo "0")
  DIFF=$((NOW - LAST))
  if [ "$DIFF" -lt 300 ]; then
    exit 0
  fi
fi
echo "$NOW" > "$DEBOUNCE_FILE"

NAME=$(cat "/tmp/claude_session_name_${SESSION_KEY}" 2>/dev/null || echo "小瑶酱")
WINDOW_ID=$(cat "/tmp/claude_window_${SESSION_KEY}" 2>/dev/null)
EVENT_TYPE="$1"

if [ "$EVENT_TYPE" = "stop" ]; then
  TITLE="✅ ${NAME}完成了任务"
  BODY="快来看看结果吧"
elif [ "$EVENT_TYPE" = "notification" ]; then
  TITLE="🔔 ${NAME}等待你的回复"
  BODY="快回来看看吧"
else
  exit 0
fi

# All background, don't block the hook
# Mac notification with click-to-focus (auto-kill after 60s)
(
  "$NOTIFY_APP" "$TITLE" "$BODY" "$FOCUS_SCRIPT $WINDOW_ID" &
  PID=$!
  sleep 60
  kill $PID 2>/dev/null
) &>/dev/null &
disown

# Bark push to phone (use --data-urlencode to handle spaces/emoji in title/body)
/usr/bin/curl -s --max-time 5 -X POST "${BARK_URL}" \
  -d "title=${TITLE}" \
  -d "body=${BODY}" \
  > /dev/null 2>&1

# Exit immediately, don't wait
exit 0
