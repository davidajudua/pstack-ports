"""A failed publish must leave the existing pack in place."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PORT = Path(__file__).resolve().parents[1] / "port.py"

import port  # noqa: E402
from port import replace_dir  # noqa: E402


class ReplaceDirTest(unittest.TestCase):
    def test_failed_swap_keeps_the_existing_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "skills"
            dest.mkdir()
            (dest / "keep.txt").write_text("old")
            with self.assertRaises(FileNotFoundError):
                replace_dir(root / "missing", dest)
            self.assertEqual((dest / "keep.txt").read_text(), "old")
            self.assertFalse((root / "skills.port-backup").exists())

    def test_successful_swap_replaces_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "skills"
            dest.mkdir()
            (dest / "keep.txt").write_text("old")
            src = root / "staged"
            src.mkdir()
            (src / "new.txt").write_text("new")
            backup = replace_dir(src, dest)
            self.assertEqual((dest / "new.txt").read_text(), "new")
            self.assertFalse((dest / "keep.txt").exists())
            self.assertIsNotNone(backup)
            assert backup is not None
            self.assertTrue((backup / "keep.txt").exists())

    def test_interrupted_backup_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup = root / "skills.port-backup"
            backup.mkdir()
            (backup / "old.txt").write_text("old")
            src = root / "staged"
            src.mkdir()
            (src / "new.txt").write_text("new")
            replace_dir(src, root / "skills")
            self.assertEqual((root / "skills" / "new.txt").read_text(), "new")
            self.assertEqual((root / "skills.port-backup" / "old.txt").read_text(), "old")


class DirtyPackTest(unittest.TestCase):
    """A refresh replaces the pack wholesale, so it must refuse to run over work git does not hold."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.pack = self.repo / "grok" / "pack"
        self.skill = self.pack / "skills" / "poteto-mode" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("committed\n")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "pack"]):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo, check=True
            )

    def port(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PORT), str(self.repo / "no-upstream"), str(self.pack)],
            capture_output=True,
            text=True,
        )

    def test_refuses_to_replace_uncommitted_edits(self) -> None:
        self.skill.write_text("hand edit\n")
        result = self.port()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace", result.stderr)
        self.assertIn("\nskills/poteto-mode/SKILL.md", result.stderr)
        self.assertEqual(self.skill.read_text(), "hand edit\n")

    def test_refuses_to_delete_untracked_skills(self) -> None:
        verify = self.pack / "skills" / "verify-app" / "SKILL.md"
        verify.parent.mkdir()
        verify.write_text("new\n")
        result = self.port()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to replace", result.stderr)
        self.assertIn("\nskills/verify-app/SKILL.md", result.stderr)
        self.assertEqual(verify.read_text(), "new\n")

    def ignore(self, *patterns: str) -> None:
        (self.repo / ".git" / "info" / "exclude").write_text("".join(f"{p}\n" for p in patterns))

    def refresh(self, ships: dict[str, str]) -> None:
        """Run the real refresh with a stand-in for the upstream build."""

        def build_pack(upstream: Path, staging: Path) -> int:
            for rel, text in {"skills/poteto-mode/SKILL.md": "refreshed\n", **ships}.items():
                (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                (staging / rel).write_text(text)
            for name in port.PACK_DIRS:
                (staging / name).mkdir(exist_ok=True)
            return 0

        argv = ["port.py", str(self.repo / "no-upstream"), str(self.pack)]
        with (
            mock.patch.object(port, "build_pack", build_pack),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            port.main()

    def test_refresh_keeps_ignored_local_files(self) -> None:
        self.ignore(".env", "node_modules/")
        secret = self.skill.parent / ".env"
        secret.write_text("TOKEN=local secret\n")
        patch = self.skill.parent / "scripts" / "node_modules" / "local-patch.js"
        patch.parent.mkdir(parents=True)
        patch.write_text("hand-written\n")
        self.refresh({})
        self.assertEqual(self.skill.read_text(), "refreshed\n")
        self.assertEqual(secret.read_text(), "TOKEN=local secret\n")
        self.assertEqual(patch.read_text(), "hand-written\n")

    def test_refuses_when_the_new_pack_ships_an_ignored_path(self) -> None:
        self.ignore(".env")
        secret = self.skill.parent / ".env"
        secret.write_text("TOKEN=local secret\n")
        with self.assertRaises(SystemExit) as refused:
            self.refresh({"skills/poteto-mode/.env": "shipped\n"})
        self.assertIn("refusing to replace", str(refused.exception))
        self.assertIn(" skills/poteto-mode/.env,", str(refused.exception))
        self.assertEqual(secret.read_text(), "TOKEN=local secret\n")
        self.assertEqual(self.skill.read_text(), "committed\n")

    def test_clean_pack_passes_the_check(self) -> None:
        result = self.port()
        self.assertNotIn("refusing to replace", result.stderr)


if __name__ == "__main__":
    unittest.main()
