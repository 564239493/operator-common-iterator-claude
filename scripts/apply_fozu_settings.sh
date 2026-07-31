#!/usr/bin/env bash
# 将 ~/.claude/settings-fozu.json 的内容覆盖到 ~/.claude/settings.json
# 用法: bash scripts/apply_fozu_settings.sh
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-/c/Users/ttelab/.claude}"
SRC="$CLAUDE_DIR/settings-fozu.json"
DST="$CLAUDE_DIR/settings.json"

if [ ! -f "$SRC" ]; then
  echo "错误: 源文件不存在: $SRC" >&2
  exit 1
fi

# 覆盖前备份（带时间戳，避免多次运行丢失原始备份）
if [ -f "$DST" ]; then
  ts=$(date +%Y%m%d_%H%M%S)
  cp "$DST" "$DST.bak.$ts"
  echo "已备份: $DST -> $DST.bak.$ts"
fi

cp "$SRC" "$DST"
echo "已替换: $DST  (来源: $SRC)"
