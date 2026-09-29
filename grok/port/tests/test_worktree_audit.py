"""Run the Grok pack's worktree-audit.sh against a real repository and check its buckets."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "pack/skills/poteto-mode/scripts/worktree-audit.sh"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class WorktreeAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.grok_home = root / "grok-home"
        (self.grok_home / "sessions").mkdir(parents=True)
        origin = root / "origin.git"
        self.repo = root / "repo"
        self.wt = root / "repo-merged"
        git("init", "--bare", str(origin), cwd=root)
        git("init", str(self.repo), cwd=root)
        (self.repo / "a.txt").write_text("a\n")
        git("add", "a.txt", cwd=self.repo)
        git("commit", "-m", "a", cwd=self.repo)
        git("remote", "add", "origin", str(origin), cwd=self.repo)
        git("push", "origin", "main", cwd=self.repo)
        git("worktree", "add", "-b", "merged", str(self.wt), cwd=self.repo)

    def row(self, extra_env: dict[str, str] | None = None) -> list[str]:
        env = {**os.environ, "HOME": str(self.grok_home.parent), "GROK_HOME": str(self.grok_home), "GH_TOKEN": ""}
        if extra_env:
            env.update(extra_env)
        out = subprocess.run(
            ["bash", str(SCRIPT), str(self.repo)], env=env, check=True, capture_output=True, text=True
        ).stdout
        rows = [line.split("\t") for line in out.splitlines()[1:]]
        [row] = [r for r in rows if r[8] == str(self.wt)]
        return row

    def bucket(self, extra_env: dict[str, str] | None = None) -> tuple[str, str]:
        row = self.row(extra_env)
        return row[6], row[7]

    def session_group(self, cwd: Path) -> Path:
        group = self.grok_home / "sessions" / urllib.parse.quote(str(cwd), safe="") / "s1"
        group.mkdir(parents=True)
        (group / "chat_history.jsonl").write_text("{}\n")
        return group

    def test_clean_merged_worktree_without_sessions_is_safe(self) -> None:
        self.assertEqual(self.bucket(), ("-", "safe"))

    def test_live_grok_session_in_worktree_holds_it(self) -> None:
        (self.grok_home / "active_sessions.json").write_text(
            json.dumps([{"session_id": "s1", "pid": os.getpid(), "cwd": str(self.wt / "src")}])
        )
        self.assertEqual(self.bucket(), ("live", "hold-live-session"))

    def test_dead_session_pid_does_not_hold_worktree(self) -> None:
        dead = subprocess.Popen(["true"])
        dead.wait()
        (self.grok_home / "active_sessions.json").write_text(
            json.dumps([{"session_id": "s1", "pid": dead.pid, "cwd": str(self.wt)}])
        )
        self.assertEqual(self.bucket(), ("-", "safe"))

    def test_recent_grok_session_in_worktree_needs_verification(self) -> None:
        self.session_group(self.wt)
        last, bucket = self.bucket()
        self.assertNotEqual(last, "-")
        self.assertEqual(bucket, "verify-recent-chat")

    def test_sibling_worktree_with_shared_prefix_is_not_matched(self) -> None:
        self.session_group(Path(str(self.wt) + "-r37"))
        self.assertEqual(self.bucket(), ("-", "safe"))

    def unmerged_pr_row(self, state: str, *, head_matches: bool) -> list[str]:
        parent = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.wt, text=True).strip()
        (self.wt / "b.txt").write_text("b\n")
        git("add", "b.txt", cwd=self.wt)
        git("commit", "-m", "b", cwd=self.wt)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.wt, text=True).strip()
        oid = head if head_matches else parent
        fake = self.grok_home.parent / "bin"
        fake.mkdir()
        gh = fake / "gh"
        pr = {"number": 9, "state": state, "headRefName": "merged", "headRefOid": oid}
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "fields = sys.argv[sys.argv.index('--json') + 1].split(',')\n"
            f"pr = {pr!r}\n"
            "print(json.dumps([{k: pr.get(k) for k in fields}]))\n"
        )
        gh.chmod(0o755)
        env = {**os.environ, "HOME": str(self.grok_home.parent), "GROK_HOME": str(self.grok_home), "GH_TOKEN": "", "PATH": f"{fake}:{os.environ['PATH']}"}
        out = subprocess.run(
            ["bash", str(SCRIPT), str(self.repo)], env=env, check=True, capture_output=True, text=True
        ).stdout
        rows = [line.split("\t") for line in out.splitlines()[1:]]
        [row] = [r for r in rows if r[8] == str(self.wt)]
        self.assertEqual(row[5], f"#9/{state}")
        self.assertEqual(row[2], "no")
        return row

    def test_closed_unmerged_pr_is_not_safe(self) -> None:
        self.assertEqual(self.unmerged_pr_row("CLOSED", head_matches=False)[7], "review")

    def test_reused_branch_ahead_of_merged_pr_is_not_safe(self) -> None:
        self.assertEqual(self.unmerged_pr_row("MERGED", head_matches=False)[7], "review")

    def test_merged_pr_at_the_worktree_head_is_safe(self) -> None:
        self.assertEqual(self.unmerged_pr_row("MERGED", head_matches=True)[7], "safe")

    def test_untracked_files_in_merged_worktree_hold_it(self) -> None:
        (self.wt / "decisions.tsv").write_text("kept out of git on purpose\n")
        self.assertEqual(self.bucket(), ("-", "hold-untracked"))

    def ignore(self, *patterns: str) -> None:
        git("config", "core.excludesFile", str(self.grok_home.parent / "ignore"), cwd=self.wt)
        (self.grok_home.parent / "ignore").write_text("".join(f"{p}\n" for p in patterns))

    def test_files_inside_ignored_build_directories_hold_merged_worktree(self) -> None:
        # A build directory can hold a hand-written file that no build restores.
        self.ignore("node_modules/", "dist/")
        (self.wt / "node_modules" / "left-pad").mkdir(parents=True)
        (self.wt / "node_modules" / "left-pad" / "index.js").write_text("hand patched\n")
        (self.wt / "dist").mkdir()
        (self.wt / "dist" / "notes.md").write_text("only copy\n")
        self.assertEqual(self.bucket(), ("-", "hold-ignored"))
        self.assertEqual(self.row()[3], "ignored:2")

    def test_ignored_user_files_hold_merged_worktree(self) -> None:
        self.ignore(".env")
        (self.wt / ".env").write_text("TOKEN=local secret\n")
        self.assertEqual(self.bucket(), ("-", "hold-ignored"))

    def test_mixed_local_files_list_every_kind(self) -> None:
        self.ignore(".env")
        (self.wt / ".env").write_text("TOKEN=local secret\n")
        (self.wt / "decisions.tsv").write_text("kept out of git on purpose\n")
        self.assertEqual(self.bucket(), ("-", "hold-untracked"))
        self.assertEqual(self.row()[3], "untracked:1,ignored:1")
        (self.wt / "a.txt").write_text("edited\n")
        self.assertEqual(self.bucket(), ("-", "hold-wip"))
        self.assertEqual(self.row()[3], "wip:1,untracked:1,ignored:1")

    def test_detached_commits_no_ref_reaches_hold_worktree(self) -> None:
        git("checkout", "--detach", cwd=self.wt)
        (self.wt / "b.txt").write_text("b\n")
        git("add", "b.txt", cwd=self.wt)
        git("commit", "-m", "b", cwd=self.wt)
        self.assertEqual(self.bucket(), ("-", "hold-unreachable"))

    def test_detached_head_on_main_is_safe(self) -> None:
        git("checkout", "--detach", cwd=self.wt)
        self.assertEqual(self.bucket(), ("-", "safe"))

    def test_main_repo_session_that_touched_worktree_needs_verification(self) -> None:
        group = self.session_group(self.repo)
        (group / "chat_history.jsonl").write_text(json.dumps({"content": f"cd {self.wt}/src"}) + "\n")
        self.assertEqual(self.bucket()[1], "verify-recent-chat")


if __name__ == "__main__":
    unittest.main()
