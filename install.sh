#!/usr/bin/env sh
# Install claude-chats as a single script in ~/.local/bin (no Python packaging needed).
set -eu
BIN="${PREFIX:-$HOME/.local/bin}"
URL="https://raw.githubusercontent.com/chanwaijye/claude-chats/main/src/claude_chats/cli.py"
mkdir -p "$BIN"
if [ -f "$(dirname "$0")/src/claude_chats/cli.py" ]; then
  cp "$(dirname "$0")/src/claude_chats/cli.py" "$BIN/claude-chats"
else
  curl -fsSL "$URL" -o "$BIN/claude-chats"
fi
chmod +x "$BIN/claude-chats"
ln -sf "$BIN/claude-chats" "$BIN/cchats"
echo "installed: $BIN/claude-chats (alias: cchats)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: add $BIN to your PATH" ;; esac
