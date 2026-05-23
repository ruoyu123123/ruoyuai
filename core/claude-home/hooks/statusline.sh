#!/bin/bash
# Claude Code StatusLine: 最近操作 + 上下文 + 费用
# 读取 PostToolUse hook 写入的缓存 + stdin 状态 JSON

CACHE="/tmp/cc-tool-action.json"

# 读取 Claude Code 传入的状态 JSON
STDIN=$(cat)

# 提取关键指标
MODEL=$(echo "$STDIN" | jq -r '.model.display_name // "Claude"' 2>/dev/null)
CTX_PCT=$(echo "$STDIN" | jq -r '.context_window.used_percentage // 0' 2>/dev/null | cut -d. -f1)
COST=$(echo "$STDIN" | jq -r '.cost.total_cost_usd // 0' 2>/dev/null)

# 格式化费用（>= $0.01 显示 2 位小数，否则显示 4 位）
if [ "$COST" = "0" ] || [ "$COST" = "null" ]; then
  COST_STR=""
else
  COST_INT=$(echo "$COST" | awk '{printf "%d", $1 * 100}')
  if [ "$COST_INT" -ge 1 ] 2>/dev/null; then
    COST_STR=" │ \$$(printf '%.2f' "$COST")"
  else
    COST_STR=" │ \$$(printf '%.4f' "$COST")"
  fi
fi

# 读取最近操作
ACTION=""
if [ -f "$CACHE" ]; then
  ACTION=$(jq -r '.text // empty' "$CACHE" 2>/dev/null)
  ACTION_TIME=$(jq -r '.time // 0' "$CACHE" 2>/dev/null)
  NOW=$(date +%s)
  AGE=$((NOW - ACTION_TIME))

  # 超过 5 分钟不显示具体操作
  [ "$AGE" -gt 300 ] && ACTION=""
fi

# 输出状态行
if [ -n "$ACTION" ]; then
  echo "${ACTION} │ ${MODEL} │ ctx ${CTX_PCT}%${COST_STR}"
else
  echo "${MODEL} │ ctx ${CTX_PCT}%${COST_STR}"
fi
