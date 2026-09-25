"""End-to-end tests against a fake ~/.claude directory. Run: python -m unittest"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
USELESS = "aaaa1111-0000-0000-0000-000000000000"
REAL = "bbbb2222-0000-0000-0000-000000000000"


def write_jsonl(path: Path, records, pad: int = 0) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
        fh.write(json.dumps({"type": "pad", "data": "x" * pad}) + "\n")
    old = time.time() - 86400  # older than the "in use" window
    os.utime(path, (old, old))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg, self.data = root / "cfg", root / "data"
        proj = self.cfg / "projects" / "-home-me-app"
        proj.mkdir(parents=True)
        write_jsonl(proj / f"{USELESS}.jsonl", [{"type": "mode", "mode": "normal"}])
        (proj / USELESS / "tool-results").mkdir(parents=True)
        (self.cfg / "file-history" / USELESS).mkdir(parents=True)
        (self.cfg / "file-history" / USELESS / "f").write_text("x")
        write_jsonl(proj / f"{REAL}.jsonl", [
            {"type": "ai-title", "aiTitle": "Fix the login bug ✓"},
            {"type": "user", "cwd": "/home/me/app", "message": {"role": "user", "content": "why does login fail?"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Let me look — the token expires."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "auth.py"}}]}},
            {"type": "user", "message": {"content": "fix it please"}},
        ], pad=60 * 1024)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, ok=True):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.cfg), XDG_DATA_HOME=str(self.data),
                   LOCALAPPDATA=str(self.data), PYTHONPATH=str(SRC), PYTHONIOENCODING="cp1252")
        p = subprocess.run([sys.executable, "-m", "claude_chats.cli", *args],
                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def trash(self) -> Path:
        return self.data / "claude-chats" / "trash"

    def test_list_classifies_by_size(self):
        out = json.loads(self.run_cli("list", "--json"))
        verdicts = {r["sid"]: r["verdict"] for r in out}
        self.assertEqual(verdicts, {USELESS: "useless", REAL: "keep"})
        self.assertIn("useless 1", self.run_cli("list"))  # plain table survives a cp1252 console

    def test_show_transcript(self):
        out = self.run_cli("show", "bbbb", "--no-pager")
        self.assertIn("why does login fail?", out)
        self.assertIn("Read", out)
        self.assertIn("/home/me/app", out)

    def test_clean_trash_and_restore(self):
        self.assertIn("would move", self.run_cli("clean", "-n"))
        self.run_cli("clean", "-y")
        self.assertFalse((self.cfg / "file-history" / USELESS).exists())
        self.assertTrue(any(self.trash().rglob(f"{USELESS}.jsonl")))
        self.assertIn(USELESS[:8], self.run_cli("trash", "list"))

        self.run_cli("trash", "restore", "aaaa")
        self.assertTrue((self.cfg / "projects" / "-home-me-app" / f"{USELESS}.jsonl").exists())
        self.assertTrue((self.cfg / "file-history" / USELESS / "f").exists())
        self.assertIn("empty", self.run_cli("trash", "list"))

    def test_rm_purge_and_active_protection(self):
        real = self.cfg / "projects" / "-home-me-app" / f"{REAL}.jsonl"
        os.utime(real)  # just modified -> treated as in use
        self.assertIn("skip", self.run_cli("rm", "bbbb", "-y"))
        self.assertTrue(real.exists())
        self.run_cli("rm", "bbbb", "-y", "--purge", "--force")
        self.assertFalse(real.exists())
        self.assertFalse(self.trash().exists())

    def test_cleanup_status(self):
        out = self.run_cli("status")
        self.assertIn("Cleanup days  : 30  (default)", out)
        self.assertIn("Overdue       : 0", out)
        (self.cfg / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 7}))
        out = self.run_cli("status", "-v")
        self.assertIn("Cleanup days  : 7", out)
        self.assertIn("settings.json", out)
        self.assertIn("6d left", out)
        rows = json.loads(self.run_cli("list", "--json"))
        self.assertTrue(all(r["expires"] for r in rows))
        self.assertIn("auto-clean", self.run_cli("list"))
        self.assertIn("Expires :", self.run_cli("show", "bbbb", "--no-pager"))
        (self.cfg / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 0}))
        self.assertIn("stops saving", self.run_cli("status"))

    def test_set_cleanup_days(self):
        settings = self.cfg / "settings.json"
        settings.write_text(json.dumps({"model": "opus", "cleanupPeriodDays": 30}))
        self.assertIn("30 -> 365", self.run_cli("status", "--set", "365"))
        self.assertEqual(json.loads(settings.read_text()), {"model": "opus", "cleanupPeriodDays": 365})
        self.assertIn("Cleanup days  : 365", self.run_cli("status"))

        # lowering below the chats' age, or 0, needs confirmation
        self.assertIn("aborted", self.run_cli("status", "--set", "1"))
        self.assertIn("aborted", self.run_cli("status", "--set", "0"))
        self.assertEqual(json.loads(settings.read_text())["cleanupPeriodDays"], 365)
        self.run_cli("status", "--set", "0", "-y")
        self.assertEqual(json.loads(settings.read_text())["cleanupPeriodDays"], 0)

        self.run_cli("status", "--set", "-5", ok=False)
        settings.write_text("{broken")
        self.run_cli("status", "--set", "10", ok=False)
        self.assertEqual(settings.read_text(), "{broken")

    def test_unknown_id_fails(self):
        self.run_cli("show", "zzzz", ok=False)


if __name__ == "__main__":
    unittest.main()
