#!/usr/bin/env sh
# Remove claude-chats. Pass --purge-trash to also delete chats sitting in its trash.
set -eu
BIN="${PREFIX:-$HOME/.local/bin}"
rm -f "$BIN/claude-chats" "$BIN/cchats"
command -v uv >/dev/null 2>&1 && uv tool uninstall claude-chats 2>/dev/null || true
command -v pipx >/dev/null 2>&1 && pipx uninstall claude-chats 2>/dev/null || true
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/claude-chats"
if [ "${1:-}" = "--purge-trash" ]; then
  rm -rf "$DATA"
  echo "removed claude-chats and its trash"
else
  echo "removed claude-chats"
  [ -d "$DATA" ] && echo "trash kept at $DATA (run with --purge-trash to delete it)"
fi
exit 0
