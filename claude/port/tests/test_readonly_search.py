import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOK = Path(__file__).resolve().parents[2] / "pack" / "hooks" / "pstack-readonly-search.py"
spec = importlib.util.spec_from_file_location("pstack_readonly_search", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def run_hook(payload: dict | str) -> subprocess.CompletedProcess:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=text, capture_output=True, text=True, check=False)


def read(command: str, cwd: Path, **extra: object) -> dict:
    return {"tool_name": "Read", "cwd": str(cwd), "tool_input": {"file_path": hook.PREFIX + command, **extra}}


class Checks(unittest.TestCase):
    def test_read_only_searches_pass(self) -> None:
        for command in [
            "rg -n 'def main' scripts",
            "rg --files -g '*.md' .claude/skills",
            "rg --pretty --pcre2 'foo$' .",
            "grep -rn TaskCreate .claude/skills/poteto-mode",
            "grep -rln -e needle -- docs",
            "find . -name '*.py' -not -path './.git/*'",
            "find .claude -type f ! -name '*.md'",
            "ls -la .claude/agents",
        ]:
            with self.subTest(command=command):
                hook.check(hook.shlex.split(command))

    def test_refused(self) -> None:
        for command in [
            "touch x",
            "cat .env",
            "git log",
            "sh -c 'rg foo'",
            "/usr/bin/rg foo",
            "find . -name x -delete",
            "find . -exec rm {} ;",
            "find . -execdir sh -c x ;",
            "find . -fprint out.txt",
            "rg --pre cat foo",
            "rg --pre=cat foo",
            "rg --pre-glob '*.pdf' foo",
            "rg --hostname-bin=sh foo",
            "rg --hostname foo",
            "rg -z foo",
            "rg --search-zip foo",
            "grep --filter=sh foo .",
            "grep --fil=sh foo .",
            "grep --pager=less foo .",
            "grep --view=vim foo .",
            "grep --save-config",
            "grep --config=x foo",
            "grep ---x foo",
            "grep -rnQ foo .",
            "",
        ]:
            with self.subTest(command=command), self.assertRaises(hook.Refused):
                hook.check(hook.shlex.split(command))


class HookProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "mod.py").write_text("def locate_needle():\n    pass\n", encoding="utf-8")

    def results(self, completed: subprocess.CompletedProcess) -> tuple[dict, str]:
        self.assertEqual(completed.returncode, 0, completed.stderr)
        decision = json.loads(completed.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "allow")
        path = Path(decision["updatedInput"]["file_path"])
        self.addCleanup(path.unlink)
        return decision["updatedInput"], path.read_text(encoding="utf-8")

    def test_ordinary_read_passes_through(self) -> None:
        completed = run_hook({"tool_name": "Read", "cwd": str(self.tmp), "tool_input": {"file_path": str(self.tmp / "src" / "mod.py")}})
        self.assertEqual((completed.returncode, completed.stdout), (0, ""))

    def test_search_redirects_the_read_to_its_output(self) -> None:
        updated, text = self.results(run_hook(read("grep -rn locate_needle src", self.tmp, offset=1, limit=50)))
        self.assertEqual((updated["offset"], updated["limit"]), (1, 50))
        self.assertIn("$ grep -rn locate_needle src\n", text)
        self.assertIn("mod.py:1:def locate_needle():", text)
        self.assertIn("[exit status 0]", text)

    def test_find_lists_files(self) -> None:
        _, text = self.results(run_hook(read("find src -name '*.py'", self.tmp)))
        self.assertIn("src/mod.py", text)

    def test_refused_search_blocks_the_read(self) -> None:
        completed = run_hook(read("find src -delete", self.tmp))
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Refused find -delete", completed.stderr)
        self.assertTrue((self.tmp / "src" / "mod.py").exists())

    def python(self, code: str) -> tuple[str, str]:
        return hook.run([sys.executable, "-c", code], sys.executable, str(self.tmp), dict(os.environ))

    def test_output_is_cut_while_the_search_runs(self) -> None:
        body, footer = self.python("import sys\nwhile True: sys.stdout.write('x' * 65536)")
        self.assertEqual(len(body), hook.OUTPUT_LIMIT)
        self.assertEqual(footer, f"[output cut at {hook.OUTPUT_LIMIT} bytes; narrow the search]")

    def test_slow_search_is_stopped(self) -> None:
        with mock.patch.object(hook, "TIMEOUT_SECONDS", 0.5):
            body, footer = self.python("import sys, time\nprint('partial', flush=True)\ntime.sleep(30)")
        self.assertEqual(body, "partial\n")
        self.assertEqual(footer, "[stopped after 0.5 seconds; narrow the search]")

    def test_sweep_deletes_only_stale_results(self) -> None:
        stale, fresh, other = (self.tmp / name for name in [f"{hook.RESULTS}old.txt", f"{hook.RESULTS}new.txt", "pstack-search-old.txt"])
        for path in (stale, fresh, other):
            path.write_text("x", encoding="utf-8")
        hour_ago = hook.time.time() - hook.STALE_SECONDS - 1
        for path in (stale, other):
            os.utime(path, (hour_ago, hour_ago))
        with mock.patch.object(hook.tempfile, "gettempdir", return_value=str(self.tmp)):
            hook.sweep()
        self.assertEqual([p.exists() for p in (stale, fresh, other)], [False, True, True])

    def test_unreadable_payload_fails_closed(self) -> None:
        for payload in ["", "not json", json.dumps({"tool_input": 1})]:
            with self.subTest(payload=payload):
                self.assertEqual(run_hook(payload).returncode, 2)


if __name__ == "__main__":
    unittest.main()
