"""Run the Codex pack's worktree-audit.sh against a real repository and check its buckets."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[2] / "pack"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class WorktreeAudit:
    script: Path

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        self.wt = self.root / "repo-merged"
        git("init", "--bare", str(origin), cwd=self.root)
        git("init", str(self.repo), cwd=self.root)
        (self.repo / "a.txt").write_text("a\n")
        git("add", "a.txt", cwd=self.repo)
        git("commit", "-m", "a", cwd=self.repo)
        git("remote", "add", "origin", str(origin), cwd=self.repo)
        git("push", "origin", "main", cwd=self.repo)
        git("worktree", "add", "-b", "merged", str(self.wt), cwd=self.repo)

    def row(self) -> tuple[str, str]:
        env = {**os.environ, "HOME": str(self.root), "GH_TOKEN": ""}
        out = subprocess.run(
            ["bash", str(self.script), str(self.repo)], env=env, check=True, capture_output=True, text=True
        ).stdout
        [row] = [r for r in (line.split("\t") for line in out.splitlines()[1:]) if r[8] == str(self.wt)]
        return row[3], row[7]

    def test_clean_merged_worktree_is_safe(self) -> None:
        self.assertEqual(self.row(), ("clean", "safe"))

    def test_untracked_files_hold_the_worktree(self) -> None:
        (self.wt / "notes.txt").write_text("only copy\n")
        self.assertEqual(self.row(), ("untracked:1", "hold-untracked"))

    def test_tracked_edits_still_report_untracked_files(self) -> None:
        (self.wt / "a.txt").write_text("edited\n")
        (self.wt / "notes.txt").write_text("only copy\n")
        (self.wt / "todo.txt").write_text("only copy\n")
        self.assertEqual(self.row(), ("wip:1,untracked:2", "hold-wip"))

    def test_ignored_build_output_does_not_hold_the_worktree(self) -> None:
        excludes = self.root / "excludes"
        excludes.write_text("build/\n")
        git("config", "core.excludesFile", str(excludes), cwd=self.repo)
        (self.wt / "build").mkdir()
        (self.wt / "build" / "out.o").write_text("artifact\n")
        self.assertEqual(self.row(), ("clean", "safe"))

    def test_detached_commit_no_ref_contains_holds_the_worktree(self) -> None:
        git("checkout", "--detach", cwd=self.wt)
        (self.wt / "b.txt").write_text("b\n")
        git("add", "b.txt", cwd=self.wt)
        git("commit", "-m", "b", cwd=self.wt)
        self.assertEqual(self.row(), ("clean", "hold-unreachable"))

    def test_detached_head_on_a_branch_commit_is_safe(self) -> None:
        git("checkout", "--detach", cwd=self.wt)
        self.assertEqual(self.row(), ("clean", "safe"))


class CodexWorktreeAuditTest(WorktreeAudit, unittest.TestCase):
    script = PACK / "skills/poteto-mode/scripts/worktree-audit.sh"


if __name__ == "__main__":
    unittest.main()
