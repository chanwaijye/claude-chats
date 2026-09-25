# claude-chats

A console tool to **preview** your Claude Code chat history, **predict which chats are useless** (mostly by size), and **delete** them safely. Deleted chats go to a trash folder so you can restore them.

It runs on **Linux, macOS and Windows** with Python 3.9 or newer. On Linux and macOS it uses only the standard library. On Windows it also installs `windows-curses` for the interactive browser.

[![test](https://github.com/chanwaijye/claude-chats/actions/workflows/test.yml/badge.svg)](https://github.com/chanwaijye/claude-chats/actions/workflows/test.yml)

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

When you delete a chat, all of these are removed together. If `CLAUDE_CONFIG_DIR` is set, it is used instead of `~/.claude`. On Windows, `~` means `%USERPROFILE%` (for example `C:\Users\you\.claude`).

## Install

### Step 1: install uv (skip if you already have it)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager. It also installs Python for you if needed.

**Linux / macOS**
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** (PowerShell)
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# or: winget install --id=astral-sh.uv -e
```

Open a new terminal afterwards, then check that it worked with `uv --version`.

### Step 2: install claude-chats

```sh
uv tool install git+https://github.com/chanwaijye/claude-chats
```

This works the same on Linux, macOS and Windows. It installs two commands: `claude-chats` and the shorter alias `cchats`. If your terminal then says the command isn't found, run `uv tool update-shell` and open a new terminal.

<details>
<summary>Other ways to install</summary>

```sh
# pipx (any OS)
pipx install git+https://github.com/chanwaijye/claude-chats

# single-file script into ~/.local/bin (Linux/macOS, needs only python3)
curl -fsSL https://raw.githubusercontent.com/chanwaijye/claude-chats/main/install.sh | sh
```
</details>

### Update

```sh
uv tool upgrade claude-chats
```

## Usage

```sh
cchats                      # interactive browser
cchats list                 # table of all chats (newest first)
cchats list -s size -u      # only the useless/maybe chats, smallest first
cchats status               # Claude Code's auto-cleanup setting and what it will delete
cchats status -v            # ...plus every chat's expiry date
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

## Claude Code's own auto-cleanup

Claude Code deletes chats that have been inactive for longer than `cleanupPeriodDays` (default 30) every time it starts. `cchats status` shows the current value, where it comes from (managed settings, then `~/.claude/settings.json`, else the default), the cutoff date, and how many chats are overdue or expire in the next 7 days. The expiry date also appears in `show`, in `list --json` (`expires`), and in the interactive browser's status line. To keep chats longer, set it in `~/.claude/settings.json`:

```json
{ "cleanupPeriodDays": 365 }
```

`0` makes Claude Code delete every chat at startup and stop saving new ones.

## Safety

- Deleting moves files to the trash by default: `~/.local/share/claude-chats/trash/` on Linux and macOS, `%LOCALAPPDATA%\claude-chats\trash\` on Windows. Nothing is removed permanently unless you use `--purge` or `trash empty`.
- Chats changed in the last 10 minutes are skipped, since they may be open in a running session. Use `--force` to delete them anyway. On Windows, a file that is still open can't be moved. The tool reports it and skips it.
- The tool never reads or changes anything outside the chat files listed above.

## Uninstall

**claude-chats**

```sh
uv tool uninstall claude-chats          # if installed with uv (any OS)
pipx uninstall claude-chats             # if installed with pipx
./uninstall.sh                          # Linux/macOS: handles every install method
```

Uninstalling keeps the trash, so you never lose a chat you might want back. To delete the trash too:

```sh
rm -rf ~/.local/share/claude-chats                        # Linux/macOS (or ./uninstall.sh --purge-trash)
Remove-Item -Recurse "$env:LOCALAPPDATA\claude-chats"     # Windows PowerShell
```

**uv itself** (only if you don't use it for anything else)

```sh
# Linux / macOS
uv cache clean
rm -rf "$(uv python dir)" "$(uv tool dir)"
rm ~/.local/bin/uv ~/.local/bin/uvx
```

```powershell
# Windows PowerShell
uv cache clean
Remove-Item -Recurse -Force "$(uv python dir)", "$(uv tool dir)"
Remove-Item "$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.local\bin\uvx.exe"
# or, if you installed it with winget: winget uninstall astral-sh.uv
```

## License

MIT
