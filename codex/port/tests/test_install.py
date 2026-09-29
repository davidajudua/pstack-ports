import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / 'install.py'
PACK = Path(__file__).resolve().parents[2] / 'pack' / 'skills'
SOURCE = PACK / 'poteto-mode'


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.target = self.home / '.agents' / 'skills' / 'poteto-mode'

    def run_install(self):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        return subprocess.run([sys.executable, str(INSTALL)], env=env, capture_output=True, text=True)

    def test_links_native_mode_into_fresh_home_and_is_idempotent(self):
        first = self.run_install()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.target.resolve(), SOURCE)
        self.assertTrue((self.target / 'SKILL.md').is_file())
        second = self.run_install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('already resolves', second.stdout)
        self.assertEqual(self.target.resolve(), SOURCE)

    def test_refuses_skills_link_into_another_checkout(self):
        shared = self.home / 'other-checkout' / '.agents' / 'skills'
        shared.mkdir(parents=True)
        (shared / 'existing.txt').write_text('untouched')
        self.target.parent.parent.mkdir(parents=True)
        self.target.parent.symlink_to(shared, target_is_directory=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(list(shared.iterdir()), [shared / 'existing.txt'])
        self.assertIn('real directory', result.stderr)

    def test_refuses_redirected_agents_parent(self):
        shared = self.home / 'shared-agents'
        shared.mkdir()
        (self.home / '.agents').symlink_to(shared, target_is_directory=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(list(shared.iterdir()), [])

    def test_registers_every_native_leaf(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for source in PACK.iterdir():
            self.assertEqual((self.target.parent / source.name).resolve(), source)
            self.assertTrue((self.target.parent / source.name / 'SKILL.md').is_file())
        self.assertEqual(len(list(self.target.parent.iterdir())), 47)

    def test_leaf_conflict_is_detected_before_any_links_change(self):
        leaf = self.target.parent / 'pstack-how'
        leaf.mkdir(parents=True)
        (leaf / 'SKILL.md').write_text('local skill')
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.target.exists())
        self.assertEqual((leaf / 'SKILL.md').read_text(), 'local skill')

    def test_replaces_stale_link(self):
        stale = self.home / 'old-checkout' / 'poteto-mode'
        stale.mkdir(parents=True)
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(stale, target_is_directory=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.resolve(), SOURCE)

    def test_relinks_link_to_another_valid_checkout(self):
        entry = self.home / 'worktree' / 'codex' / 'pack' / 'skills' / 'poteto-mode'
        entry.mkdir(parents=True)
        (entry / 'SKILL.md').write_text('native')
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(entry, target_is_directory=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.resolve(), SOURCE)

    def test_refuses_real_directory_even_when_home_has_native_pack(self):
        (self.home / '.codex' / 'skills' / 'poteto-mode').mkdir(parents=True)
        (self.home / '.codex' / 'skills' / 'poteto-mode' / 'SKILL.md').write_text('native')
        self.target.mkdir(parents=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.target.is_symlink())

    def test_refuses_leaf_links_when_user_skills_resolve_into_checkout(self):
        checkout_skills = PACK
        before = sorted(p.name for p in checkout_skills.iterdir())
        self.target.parent.parent.mkdir(parents=True)
        self.target.parent.symlink_to(checkout_skills, target_is_directory=True)
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertIn('real directory', result.stderr)
        self.assertEqual(sorted(p.name for p in checkout_skills.iterdir()), before)

    def test_refuses_to_replace_real_directory(self):
        self.target.mkdir(parents=True)
        (self.target / 'SKILL.md').write_text('local')
        result = self.run_install()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.target.is_symlink())
        self.assertEqual((self.target / 'SKILL.md').read_text(), 'local')


if __name__ == '__main__':
    unittest.main()
