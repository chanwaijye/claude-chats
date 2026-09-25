#!/usr/bin/env python3
"""claude-chats: preview, triage and delete Claude Code chat sessions.

Sessions live in ~/.claude/projects/<project-slug>/<session-id>.jsonl.
Deleting moves a session (plus its sidecar data) into a trash folder so it
can be restored; `trash empty` removes it for good.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "0.4.0"

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_DIR / "projects"
if os.name == "nt":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
TRASH_DIR = DATA_DIR / "claude-chats" / "trash"
# Per-session sidecar locations, relative to CLAUDE_DIR.
SIDECARS = ("file-history/{sid}", "session-env/{sid}", "todos/{sid}*")

# Claude Code deletes sessions inactive longer than `cleanupPeriodDays` at startup.
DEFAULT_CLEANUP_DAYS = 30
if sys.platform == "darwin":
    MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
elif os.name == "nt":
    MANAGED_SETTINGS = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode/managed-settings.json"
else:
    MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")

ACTIVE_WINDOW_S = 10 * 60  # sessions touched this recently are treated as in use

# Useless-chat heuristics (tunable via CLI flags).
TINY_BYTES = 10 * 1024
SMALL_BYTES = 50 * 1024


# --------------------------------------------------------------------------- model


@dataclass
class Session:
    path: Path
    sid: str
    project: str
    size: int
    mtime: float
    title: str = ""
    first_prompt: str = ""
    prompts: int = 0
    replies: int = 0
    tool_calls: int = 0
    verdict: str = "keep"
    reasons: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return time.time() - self.mtime < ACTIVE_WINDOW_S

    @property
    def label(self) -> str:
        return self.title or self.first_prompt or "(no prompt)"

    def related_paths(self) -> list[Path]:
        paths = [self.path]
        folder = self.path.with_suffix("")
        if folder.is_dir():
            paths.append(folder)
        for pattern in SIDECARS:
            paths.extend(CLAUDE_DIR.glob(pattern.format(sid=self.sid)))
        return paths

    def total_size(self) -> int:
        return sum(_du(p) for p in self.related_paths())

    @property
    def expires(self) -> float | None:
        """When Claude Code's startup cleanup will delete this chat (None: never)."""
        days = cleanup_setting()[0]
        return None if days is None else self.mtime + days * 86400


_cleanup_cache: tuple | None = None


def cleanup_setting() -> tuple[int | None, str]:
    """Return (cleanupPeriodDays, where it came from). Managed settings win over user settings."""
    global _cleanup_cache
    if _cleanup_cache is None:
        _cleanup_cache = (DEFAULT_CLEANUP_DAYS, "Claude Code default")
        for src in (MANAGED_SETTINGS, CLAUDE_DIR / "settings.json"):
            try:
                val = json.loads(src.read_text(encoding="utf-8")).get("cleanupPeriodDays")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                if val is not None:
                    print(f"warning: ignoring invalid cleanupPeriodDays={val!r} in {src}", file=sys.stderr)
                continue
            _cleanup_cache = (int(val), str(src))
            break
    return _cleanup_cache


def set_cleanup_days(days: int) -> str:
    """Write cleanupPeriodDays to the user settings.json, keeping every other key."""
    global _cleanup_cache
    if days < 0:
        raise ValueError("days must be 0 or more")
    path = CLAUDE_DIR / "settings.json"
    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{path} is not valid JSON ({e}); fix it by hand first") from e
        if not isinstance(settings, dict):
            raise ValueError(f"{path} does not hold a JSON object; fix it by hand first")
    old = settings.get("cleanupPeriodDays")
    settings["cleanupPeriodDays"] = days
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".claude-chats.tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic: Claude Code never sees a half-written file
    _cleanup_cache = None
    msg = f"cleanupPeriodDays: {old if old is not None else 'unset'} -> {days}  ({path})"
    if cleanup_setting()[1] != str(path):
        msg += f"\nwarning: {cleanup_setting()[1]} overrides this; effective value is {cleanup_setting()[0]}"
    return msg


def cleanup_summary() -> str:
    days, src = cleanup_setting()
    if days == 0:
        return f"auto-clean: 0 days (all chats deleted at Claude Code startup; persistence off)  [{src}]"
    return f"auto-clean: chats inactive > {days} days are deleted at Claude Code startup  [{src}]"


def _du(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _text_of(content) -> str:
    """Extract plain text from a message content (str or list of blocks)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _is_human_prompt(rec: dict) -> bool:
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = rec.get("message", {}).get("content")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return False
    text = _text_of(content).strip()
    return bool(text) and not text.startswith(("<command-", "<local-command", "<system-reminder"))


def iter_records(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_session(path: Path) -> Session:
    st = path.stat()
    s = Session(path=path, sid=path.stem, project=path.parent.name, size=st.st_size, mtime=st.st_mtime)
    ai_title = custom_title = ""
    for rec in iter_records(path):
        t = rec.get("type")
        if t == "custom-title":
            custom_title = rec.get("customTitle") or rec.get("title") or custom_title
        elif t == "ai-title":
            ai_title = rec.get("aiTitle") or rec.get("title") or ai_title
        if rec.get("cwd") and s.project == path.parent.name:
            s.project = rec["cwd"]
        if _is_human_prompt(rec):
            s.prompts += 1
            if not s.first_prompt:
                s.first_prompt = " ".join(_text_of(rec["message"]["content"]).split())[:200]
        elif t == "assistant" and not rec.get("isSidechain"):
            for b in rec.get("message", {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                    s.replies += 1
                elif isinstance(b, dict) and b.get("type") == "tool_use":
                    s.tool_calls += 1
    s.title = custom_title or ai_title
    classify(s)
    return s


def classify(s: Session, tiny: int = TINY_BYTES, small: int = SMALL_BYTES) -> None:
    """Predict whether a chat is worth keeping, mainly from its size."""
    s.reasons = []
    if s.prompts == 0:
        s.reasons.append("no prompts typed")
    if s.replies == 0 and s.tool_calls == 0:
        s.reasons.append("no replies")
    if s.size < tiny:
        s.reasons.append(f"tiny (<{human(tiny)})")
    if s.reasons:
        s.verdict = "useless"
    elif s.size < small and s.prompts <= 1:
        s.verdict = "maybe"
        s.reasons.append(f"small (<{human(small)}), single prompt")
    else:
        s.verdict = "keep"


def discover(project: str | None = None) -> list[Session]:
    if not PROJECTS_DIR.is_dir():
        return []
    out = []
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        if project and project.lower() not in str(path.parent).lower():
            continue
        try:
            out.append(load_session(path))
        except OSError:
            continue
    return out


def find_sessions(sessions: list[Session], ids: list[str]) -> list[Session]:
    picked = []
    for prefix in ids:
        matches = [s for s in sessions if s.sid.startswith(prefix)]
        if not matches:
            sys.exit(f"no session matches '{prefix}'")
        if len(matches) > 1:
            sys.exit(f"'{prefix}' is ambiguous ({len(matches)} matches); use more characters")
        picked.append(matches[0])
    return picked


# --------------------------------------------------------------------------- delete / trash


def trash_sessions(sessions: list[Session], purge: bool = False) -> int:
    freed = 0
    batch = TRASH_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for s in sessions:
        for p in s.related_paths():
            size = _du(p)
            try:
                if purge:
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
                else:
                    dest = batch / p.relative_to(CLAUDE_DIR)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), dest)
            except OSError as e:  # e.g. file still open by Claude Code on Windows
                print(f"could not remove {p}: {e.strerror or e}", file=sys.stderr)
                continue
            freed += size
    if not purge and sessions:
        (batch / "manifest.json").write_text(
            json.dumps([{"sid": s.sid, "title": s.label, "project": s.project} for s in sessions], indent=2)
        )
    return freed


def trash_batches() -> list[Path]:
    return sorted(TRASH_DIR.glob("*/manifest.json")) if TRASH_DIR.is_dir() else []


def restore(sid_prefix: str) -> None:
    for manifest in trash_batches():
        batch = manifest.parent
        entries = json.loads(manifest.read_text())
        hits = [e for e in entries if e["sid"].startswith(sid_prefix)]
        if not hits:
            continue
        for e in hits:
            for src in list(batch.rglob(f"{e['sid']}*")):
                if not src.exists():
                    continue
                dest = CLAUDE_DIR / src.relative_to(batch)
                if dest.exists():
                    print(f"skip {dest} (already exists)")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), dest)
            print(f"restored {e['sid']}  {e['title'][:60]}")
        remaining = [e for e in entries if e not in hits]
        if remaining:
            manifest.write_text(json.dumps(remaining, indent=2))
        else:
            shutil.rmtree(batch)
        return
    sys.exit(f"'{sid_prefix}' not found in trash")


# --------------------------------------------------------------------------- rendering


def human(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def fmt_expiry(s: Session) -> str:
    if s.expires is None:
        return "never"
    left = (s.expires - time.time()) / 86400
    when = fmt_time(s.expires)
    return f"{when} (overdue, next Claude Code start)" if left <= 0 else f"{when} ({left:.0f}d left)"


def transcript(s: Session, width: int = 100, show_tools: bool = True) -> list[str]:
    lines = [
        f"Session : {s.sid}",
        f"Project : {s.project}",
        f"Title   : {s.label}",
        f"Updated : {fmt_time(s.mtime)}   Size: {human(s.size)}   "
        f"Prompts: {s.prompts}   Replies: {s.replies}   Tool calls: {s.tool_calls}",
        f"Verdict : {s.verdict}" + (f"  ({'; '.join(s.reasons)})" if s.reasons else ""),
        f"Expires : {fmt_expiry(s)}   ({cleanup_summary().split('  [')[0]})",
        "─" * min(width, 100),
    ]

    def add(prefix: str, text: str) -> None:
        for i, para in enumerate(text.strip().splitlines() or [""]):
            wrapped = textwrap.wrap(para, width - 4) or [""]
            for j, w in enumerate(wrapped):
                lines.append((prefix if i == 0 and j == 0 else "    ") + w)

    for rec in iter_records(s.path):
        if rec.get("isSidechain"):
            continue
        if _is_human_prompt(rec):
            lines.append("")
            add("▶ You: ", _text_of(rec["message"]["content"]))
        elif rec.get("type") == "assistant":
            for b in rec.get("message", {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    lines.append("")
                    add("◀ Claude: ", b["text"])
                elif b.get("type") == "tool_use" and show_tools:
                    arg = json.dumps(b.get("input", {}), ensure_ascii=False)
                    lines.append(f"    ⚙ {b.get('name')} {arg[:width - 12]}")
    return lines


def table_row(i: int, s: Session, width: int) -> str:
    mark = {"useless": "✗", "maybe": "?", "keep": " "}[s.verdict]
    left = f"{i:>3} {mark} {s.sid[:8]}  {fmt_time(s.mtime)}  {human(s.size):>6}  {s.prompts:>3}p  "
    return (left + s.label)[:width]


# --------------------------------------------------------------------------- commands


def sort_sessions(sessions: list[Session], key: str) -> list[Session]:
    keyfn = {"date": lambda s: -s.mtime, "size": lambda s: s.size, "prompts": lambda s: s.prompts}[key]
    return sorted(sessions, key=keyfn)


def cmd_list(args) -> None:
    sessions = sort_sessions(discover(args.project), args.sort)
    for s in sessions:
        classify(s, args.tiny * 1024, args.small * 1024)
    if args.useless:
        sessions = [s for s in sessions if s.verdict in ("useless", "maybe")]
    if args.json:
        print(json.dumps([
            {"sid": s.sid, "project": s.project, "title": s.label, "size": s.size,
             "updated": fmt_time(s.mtime), "prompts": s.prompts, "replies": s.replies,
             "verdict": s.verdict, "reasons": s.reasons,
             "expires": fmt_time(s.expires) if s.expires is not None else None}
            for s in sessions], indent=2))
        return
    width = shutil.get_terminal_size().columns
    print(f"{'#':>3}   {'ID':8}  {'UPDATED':16}  {'SIZE':>6}  {'PR':>4}  TITLE")
    for i, s in enumerate(sessions, 1):
        print(table_row(i, s, width))
    counts = {v: sum(s.verdict == v for s in sessions) for v in ("useless", "maybe", "keep")}
    print(f"\n{len(sessions)} chats, {human(sum(s.size for s in sessions))}  |  "
          f"✗ useless {counts['useless']}  ? maybe {counts['maybe']}  keep {counts['keep']}")
    print(cleanup_summary())


def cmd_status(args) -> None:
    if args.set is not None:
        _set_days(args)
        return
    days, src = cleanup_setting()
    sessions = discover(args.project)
    now = time.time()
    print(f"Source        : {src}")
    if days == 0:
        print("Cleanup days  : 0  (Claude Code deletes all chats at startup and stops saving new ones)")
    else:
        print(f"Cleanup days  : {days}" + ("  (default)" if src == "Claude Code default" else ""))
        cutoff = now - days * 86400
        print(f"Cutoff        : chats last active before {fmt_time(cutoff)} are removed at next Claude Code start")
    due = [s for s in sessions if s.expires is not None and s.expires <= now]
    week = [s for s in sessions if s.expires is not None and now < s.expires <= now + 7 * 86400]
    print(f"Chats         : {len(sessions)} ({human(sum(x.size for x in sessions))})")
    print(f"Overdue       : {len(due)} ({human(sum(x.size for x in due))})  — deleted at next Claude Code start")
    print(f"Next 7 days   : {len(week)} ({human(sum(x.size for x in week))})")
    upcoming = sorted((s for s in sessions if s.expires is not None), key=lambda s: s.expires)
    if upcoming and args.verbose:
        print("\nEXPIRES                         ID        SIZE  TITLE")
        width = shutil.get_terminal_size().columns
        for s in upcoming:
            print(f"{fmt_expiry(s):30}  {s.sid[:8]}  {human(s.size):>6}  {s.label}"[:width])
    elif upcoming:
        s = upcoming[0]
        print(f"Next expiry   : {fmt_expiry(s)}  {s.sid[:8]}  {s.label[:50]}")
    print("\nChange it with: claude-chats status --set DAYS")


def _set_days(args) -> None:
    days = args.set
    if days < 0:
        sys.exit("days must be 0 or more")
    now = time.time()
    doomed = [] if days == 0 else [s for s in discover() if s.mtime + days * 86400 <= now]
    if days == 0:
        warn = "0 makes Claude Code delete ALL chats at its next start and stop saving new ones."
    elif doomed:
        warn = (f"{len(doomed)} chat(s) ({human(sum(s.size for s in doomed))}) are older than {days} days "
                "and will be deleted at the next Claude Code start.")
    else:
        warn = ""
    if warn:
        print(warn)
        if not args.yes and not confirm(f"set cleanupPeriodDays to {days}?"):
            print("aborted")
            return
    try:
        print(set_cleanup_days(days))
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    print("takes effect the next time Claude Code starts")


def cmd_show(args) -> None:
    (s,) = find_sessions(discover(), [args.id])
    width = shutil.get_terminal_size().columns
    out = "\n".join(transcript(s, width, show_tools=not args.no_tools))
    if sys.stdout.isatty() and not args.no_pager:
        import pydoc
        pydoc.pager(out)
    else:
        print(out)


def confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _delete(targets: list[Session], args) -> None:
    skipped = [s for s in targets if s.active and not args.force]
    targets = [s for s in targets if s not in skipped]
    for s in skipped:
        print(f"skip {s.sid[:8]} (modified <{ACTIVE_WINDOW_S // 60} min ago, may be in use; --force to override)")
    if not targets:
        print("nothing to delete")
        return
    for s in targets:
        print(f"  {s.sid[:8]}  {human(s.total_size()):>6}  {s.label[:70]}")
    action = "PERMANENTLY delete" if args.purge else "move to trash"
    if args.dry_run:
        print(f"(dry run) would {action} {len(targets)} chat(s)")
        return
    if not args.yes and not confirm(f"{action} {len(targets)} chat(s)?"):
        print("aborted")
        return
    freed = trash_sessions(targets, purge=args.purge)
    where = "deleted" if args.purge else f"moved to {TRASH_DIR}"
    print(f"{len(targets)} chat(s) {where}, {human(freed)} freed")


def cmd_rm(args) -> None:
    _delete(find_sessions(discover(), args.ids), args)


def cmd_clean(args) -> None:
    sessions = discover(args.project)
    levels = ("useless", "maybe") if args.include_maybe else ("useless",)
    targets = []
    for s in sort_sessions(sessions, "size"):
        classify(s, args.tiny * 1024, args.small * 1024)
        if s.verdict in levels:
            targets.append(s)
    _delete(targets, args)


def cmd_trash(args) -> None:
    if args.action == "list":
        batches = trash_batches()
        if not batches:
            print("trash is empty")
        for m in batches:
            size = _du(m.parent)
            for e in json.loads(m.read_text()):
                print(f"{m.parent.name}  {e['sid'][:8]}  {e['title'][:70]}")
            print(f"  └ batch size {human(size)}")
    elif args.action == "empty":
        if not TRASH_DIR.exists():
            print("trash is empty")
            return
        size = _du(TRASH_DIR)
        if args.yes or confirm(f"permanently delete trash ({human(size)})?"):
            shutil.rmtree(TRASH_DIR)
            print(f"trash emptied, {human(size)} freed")
    elif args.action == "restore":
        if not args.id:
            sys.exit("usage: claude-chats trash restore <session-id-prefix>")
        restore(args.id)


# --------------------------------------------------------------------------- TUI

HELP = ("↑↓/jk move  PgUp/PgDn  Enter preview  Space mark  a mark useless  "
        "d delete marked  f filter  s sort  c cleanup days  q quit")


def tui(stdscr, args) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    for i, c in enumerate((curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_CYAN, curses.COLOR_GREEN), 1):
        curses.init_pair(i, c, -1)
    RED, YEL, CYAN, GRN = (curses.color_pair(i) for i in range(1, 5))

    all_sessions = discover(args.project)
    sorts = ["date", "size", "prompts"]
    sort_i, filt, cur, top = 0, False, 0, 0
    marked: set[str] = set()
    msg = ""

    def visible():
        ss = sort_sessions(all_sessions, sorts[sort_i])
        return [s for s in ss if s.verdict != "keep"] if filt else ss

    while True:
        rows = visible()
        h, w = stdscr.getmaxyx()
        body = h - 3
        cur = max(0, min(cur, len(rows) - 1))
        top = min(max(top, cur - body + 1), cur) if rows else 0
        stdscr.erase()
        total = sum(s.size for s in rows)
        header = (f" claude-chats  {len(rows)} chats {human(total)}  sort:{sorts[sort_i]}"
                  f"  filter:{'useless/maybe' if filt else 'all'}  marked:{len(marked)}"
                  f"  auto-clean:{cleanup_setting()[0]}d")
        stdscr.addnstr(0, 0, header.ljust(w), w - 1, curses.A_REVERSE)
        for row, s in enumerate(rows[top:top + body]):
            i = top + row
            attr = {"useless": RED, "maybe": YEL, "keep": 0}[s.verdict]
            if i == cur:
                attr |= curses.A_REVERSE
            box = "[x]" if s.sid in marked else "[ ]"
            stdscr.addnstr(row + 1, 0, f"{box} {table_row(i + 1, s, w - 5)}", w - 1, attr)
        if rows:
            s = rows[cur]
            info = (f" {s.project}  |  {s.verdict}: {'; '.join(s.reasons) or 'looks substantive'}"
                    f"  |  expires {fmt_expiry(s)}")
            stdscr.addnstr(h - 2, 0, info.ljust(w), w - 1, CYAN)
        stdscr.addnstr(h - 1, 0, (msg or HELP)[:w - 1], w - 1, GRN if msg else curses.A_DIM)
        msg = ""
        stdscr.refresh()

        k = stdscr.getch()
        if k in (ord("q"), 27):
            return
        elif k in (curses.KEY_DOWN, ord("j")):
            cur += 1
        elif k in (curses.KEY_UP, ord("k")):
            cur -= 1
        elif k == curses.KEY_NPAGE:
            cur += body
        elif k == curses.KEY_PPAGE:
            cur -= body
        elif k in (curses.KEY_HOME, ord("g")):
            cur = 0
        elif k in (curses.KEY_END, ord("G")):
            cur = len(rows) - 1
        elif k == ord("s"):
            sort_i = (sort_i + 1) % len(sorts)
        elif k == ord("c"):
            msg = edit_cleanup_days(stdscr, h, w, RED, all_sessions)
        elif k == ord("f"):
            filt, cur = not filt, 0
        elif k == ord(" ") and rows:
            marked ^= {rows[cur].sid}
            cur += 1
        elif k == ord("a"):
            marked |= {s.sid for s in rows if s.verdict == "useless" and not s.active}
            msg = f"marked all useless ({len(marked)} total)"
        elif k in (10, 13, curses.KEY_ENTER) and rows:
            pager(stdscr, transcript(rows[cur], w))
        elif k == ord("d"):
            targets = [s for s in all_sessions if s.sid in marked] or ([rows[cur]] if rows else [])
            targets = [s for s in targets if not s.active]
            if not targets:
                msg = "nothing to delete (active sessions are protected)"
                continue
            size = sum(s.total_size() for s in targets)
            prompt = f" Move {len(targets)} chat(s) ({human(size)}) to trash? y/N "
            stdscr.addnstr(h - 1, 0, prompt.ljust(w), w - 1, RED | curses.A_REVERSE)
            if stdscr.getch() in (ord("y"), ord("Y")):
                trash_sessions(targets)
                gone = {s.sid for s in targets}
                all_sessions = [s for s in all_sessions if s.sid not in gone]
                marked -= gone
                msg = f"moved {len(targets)} chat(s) to trash — restore with: claude-chats trash restore <id>"
            else:
                msg = "cancelled"


def edit_cleanup_days(stdscr, h: int, w: int, warn_attr, sessions: list[Session]) -> str:
    prompt = f" Auto-clean days (now {cleanup_setting()[0]}, blank cancels): "
    stdscr.addnstr(h - 1, 0, prompt.ljust(w), w - 1, curses.A_REVERSE)
    curses.echo()
    curses.curs_set(1)
    try:
        raw = stdscr.getstr(h - 1, min(len(prompt), w - 8), 6).decode(errors="replace").strip()
    finally:
        curses.noecho()
        curses.curs_set(0)
    if not raw:
        return "cancelled"
    if not raw.isdigit():
        return f"not a number: {raw}"
    days = int(raw)
    now = time.time()
    doomed = sessions if days == 0 else [s for s in sessions if s.mtime + days * 86400 <= now]
    if doomed:
        q = (" 0 deletes ALL chats at next Claude Code start! Sure? y/N " if days == 0 else
             f" {len(doomed)} chat(s) will be deleted at next Claude Code start. Set {days}? y/N ")
        stdscr.addnstr(h - 1, 0, q.ljust(w), w - 1, warn_attr | curses.A_REVERSE)
        if stdscr.getch() not in (ord("y"), ord("Y")):
            return "cancelled"
    try:
        return set_cleanup_days(days).replace("\n", "  ")
    except (ValueError, OSError) as e:
        return f"error: {e}"


def pager(stdscr, lines: list[str]) -> None:
    top = 0
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        for row, line in enumerate(lines[top:top + h - 1]):
            attr = curses.A_BOLD if line.startswith("▶") else 0
            stdscr.addnstr(row, 0, line, w - 1, attr)
        pct = min(100, int((top + h - 1) * 100 / max(1, len(lines))))
        stdscr.addnstr(h - 1, 0, f" {pct}%  ↑↓ PgUp/PgDn g/G scroll · n/p next/prev prompt · q back ".ljust(w),
                       w - 1, curses.A_REVERSE)
        stdscr.refresh()
        k = stdscr.getch()
        last = max(0, len(lines) - h + 1)
        if k in (ord("q"), 27, curses.KEY_LEFT):
            return
        elif k in (curses.KEY_DOWN, ord("j")):
            top = min(top + 1, last)
        elif k in (curses.KEY_UP, ord("k")):
            top = max(top - 1, 0)
        elif k in (curses.KEY_NPAGE, ord(" ")):
            top = min(top + h - 1, last)
        elif k == curses.KEY_PPAGE:
            top = max(top - h + 1, 0)
        elif k == ord("g"):
            top = 0
        elif k == ord("G"):
            top = last
        elif k == ord("n"):
            nxt = next((i for i in range(top + 1, len(lines)) if lines[i].startswith("▶")), top)
            top = min(nxt, last)
        elif k == ord("p"):
            prv = next((i for i in range(top - 1, -1, -1) if lines[i].startswith("▶")), 0)
            top = prv


def cmd_tui(args) -> None:
    # Imported lazily: Windows has no built-in curses (it comes from windows-curses),
    # and the non-interactive commands should work without it.
    global curses
    if not sys.stdout.isatty():
        sys.exit("the interactive browser needs a terminal; try `claude-chats list`")
    try:
        import curses
    except ImportError:
        sys.exit("the interactive browser needs curses; on Windows run `pip install windows-curses`\n"
                 "(or reinstall with uv/pipx, which adds it automatically)")
    curses.wrapper(tui, args)


# --------------------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-chats", description=__doc__.splitlines()[0])
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def heuristics(sp):
        sp.add_argument("--tiny", type=int, default=TINY_BYTES // 1024, metavar="KB",
                        help="chats smaller than this are useless (default %(default)s)")
        sp.add_argument("--small", type=int, default=SMALL_BYTES // 1024, metavar="KB",
                        help="single-prompt chats smaller than this are 'maybe' (default %(default)s)")

    def deleting(sp):
        sp.add_argument("-n", "--dry-run", action="store_true", help="show what would be deleted")
        sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
        sp.add_argument("--purge", action="store_true", help="delete permanently instead of trashing")
        sp.add_argument("--force", action="store_true", help="also delete chats modified in the last 10 min")

    sp = sub.add_parser("tui", help="interactive browser (default)")
    sp.add_argument("-p", "--project", help="only chats whose project path contains this text")
    sp.set_defaults(func=cmd_tui)

    sp = sub.add_parser("list", aliases=["ls"], help="list chats with size and verdict")
    sp.add_argument("-p", "--project", help="only chats whose project path contains this text")
    sp.add_argument("-s", "--sort", choices=["date", "size", "prompts"], default="date")
    sp.add_argument("-u", "--useless", action="store_true", help="only show useless/maybe chats")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    heuristics(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("status", help="show Claude Code's chat auto-cleanup setting and what it will delete")
    sp.add_argument("-p", "--project", help="only chats whose project path contains this text")
    sp.add_argument("-v", "--verbose", action="store_true", help="list every chat with its expiry date")
    sp.add_argument("--set", type=int, metavar="DAYS",
                    help="write cleanupPeriodDays to ~/.claude/settings.json (0 = delete everything!)")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("show", aliases=["view"], help="preview a chat transcript")
    sp.add_argument("id", help="session id or unique prefix")
    sp.add_argument("--no-tools", action="store_true", help="hide tool calls")
    sp.add_argument("--no-pager", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("rm", aliases=["delete"], help="delete chats by id")
    sp.add_argument("ids", nargs="+", metavar="id")
    deleting(sp)
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("clean", help="delete all chats predicted useless")
    sp.add_argument("-p", "--project", help="only chats whose project path contains this text")
    sp.add_argument("--include-maybe", action="store_true", help="also delete 'maybe' chats")
    heuristics(sp)
    deleting(sp)
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("trash", help="list, restore or empty the trash")
    sp.add_argument("action", choices=["list", "restore", "empty"])
    sp.add_argument("id", nargs="?")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(func=cmd_trash)
    return p


def main(argv: list[str] | None = None) -> None:
    # Windows consoles and redirected output may not be UTF-8; never crash on ✗ ▶ ─ etc.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        args = build_parser().parse_args(["tui", *(argv or sys.argv[1:])])
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
