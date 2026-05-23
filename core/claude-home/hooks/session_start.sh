#!/bin/bash
# Per-session key: TERM_SESSION_ID is unique per Terminal tab
SESSION_KEY="${TERM_SESSION_ID:-default}"

# Skip subagent/background processes
if [ "$SESSION_KEY" = "default" ]; then
  exit 0
fi

# Default session name, no dialog
echo "小瑶酱" > "/tmp/claude_session_name_${SESSION_KEY}"

# Save Terminal window ID for notification click-to-focus
WINDOW_ID=$(/usr/bin/osascript -e 'tell application "Terminal" to get id of front window' 2>/dev/null)
if [ -n "$WINDOW_ID" ]; then
  echo "$WINDOW_ID" > "/tmp/claude_window_${SESSION_KEY}"
fi
