# claude-chats

A console tool to **preview** your Claude Code chat history, **predict which chats are useless** (mostly by size), and **delete** them safely. Deleted chats go to a trash folder so you can restore them.

It uses only the Python standard library and runs on Linux and macOS.

```
[ ]   1 ✗ ec90ef0a  2026-09-13 20:41    267B    0p  (no prompt)
[x]   2 ✗ 10d6a673  2026-09-01 23:24    1.8K    0p  (no prompt)
[ ]   3   2ed75dd6  2026-09-14 20:57  228.0K    1p  res
[ ]   4   87866082  2026-09-13 20:19    2.0M   12p  TensorRT FoundationPose review
 /home/me/projects/app  |  useless: no prompts typed; no replies; tiny (<10.0K)
```

## Where the chats live

Claude Code writes each session to `~/.claude/projects/<project>/<session-id>.jsonl`. A session can also have data in these places:

- `~/.claude/projects/<project>/<session-id>/` (subagent logs and large tool results)
- `~/.claude/file-history/<session-id>/`
- `~/.claude/session-env/<session-id>/`
- `~/.claude/todos/<session-id>*`

When you delete a chat, all of these are removed together. If `CLAUDE_CONFIG_DIR` is set, it is used instead of `~/.claude`.

## Install

Any of these works:

```sh
# 1. uv (recommended)
uv tool install git+https://github.com/chanwaijye/claude-chats

# 2. pipx
pipx install git+https://github.com/chanwaijye/claude-chats

# 3. single-file script into ~/.local/bin (only needs python3)
curl -fsSL https://raw.githubusercontent.com/chanwaijye/claude-chats/main/install.sh | sh
```

All three install two commands: `claude-chats` and the shorter alias `cchats`.

## Usage

```sh
cchats                      # interactive browser
cchats list                 # table of all chats (newest first)
cchats list -s size -u      # only the useless/maybe chats, smallest first
cchats show 2ed75dd6        # read a transcript (any unique id prefix works)
cchats rm 10d6 ec90         # move chats to the trash (asks first)
cchats clean -n             # dry run: what would be removed as useless
cchats clean                # trash every chat predicted useless
cchats clean --include-maybe --tiny 20 --small 100
cchats trash list           # what's in the trash
cchats trash restore 10d6   # put a chat back
cchats trash empty          # delete the trash permanently
```

Flags you can use with `rm` and `clean`: `-n/--dry-run`, `-y/--yes` (don't ask), `--purge` (delete permanently instead of using the trash), and `--force` (see below).

### Keys in the interactive browser

| Key | Action |
|---|---|
| `↑ ↓` / `j k`, PgUp/PgDn, `g G` | move |
| `Enter` | preview the transcript (`n`/`p` jump between your prompts, `q` goes back) |
| `Space` | mark or unmark a chat |
| `a` | mark every chat predicted useless |
| `d` | move the marked chats (or the current one) to the trash |
| `f` | show only useless/maybe chats, or show all again |
| `s` | change the sort: date, size, number of prompts |
| `q` | quit |

## How it decides a chat is useless

| Verdict | Rule |
|---|---|
| ✗ **useless** | smaller than `--tiny` (default 10 KB), **or** you typed no prompts, **or** Claude never replied |
| ? **maybe** | smaller than `--small` (default 50 KB) **and** only one prompt |
| keep | everything else |

The size of a chat file tracks how much work happened in it. Chats that were opened and then abandoned, cancelled `/resume` pickers, and remote-control stubs are usually just a few KB. Real sessions are usually hundreds of KB or more.

## Safety

- Deleting moves files to `~/.local/share/claude-chats/trash/` by default. Nothing is removed permanently unless you use `--purge` or `trash empty`.
- Chats changed in the last 10 minutes are skipped, since they may be open in a running session. Use `--force` to delete them anyway.
- The tool never reads or changes anything outside the chat files listed above.

## Uninstall

```sh
uv tool uninstall claude-chats          # if installed with uv
pipx uninstall claude-chats             # if installed with pipx
rm ~/.local/bin/claude-chats ~/.local/bin/cchats   # if installed with install.sh
```

You can also run `./uninstall.sh`, which handles all three. Add `--purge-trash` to also delete trashed chats. Without it the trash is kept at `~/.local/share/claude-chats/`, so uninstalling never loses a chat you might want back.

## License

MIT
