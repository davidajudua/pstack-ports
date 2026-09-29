import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CODEX = Path(__file__).resolve().parents[2]

# Stands in for port.py during verify --source: "regenerates" by copying the pristine pack.
FAITHFUL_PORT = """import argparse
import runpy
import shutil
from pathlib import Path
lib = runpy.run_path(str(Path(__file__).with_name("port_lib.py")))
globals().update({key: value for key, value in lib.items() if not key.startswith("_")})
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    shutil.copytree(args.source, args.output, dirs_exist_ok=True)
"""


class VerifyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.codex = self.root / 'codex'
        shutil.copytree(CODEX / 'port', self.codex / 'port', ignore=shutil.ignore_patterns('tests', '__pycache__'))
        shutil.copytree(CODEX / 'pack', self.codex / 'pack', ignore=shutil.ignore_patterns('node_modules'))
        port = self.codex / 'port' / 'port.py'
        shutil.copyfile(port, port.with_name('port_lib.py'))
        port.write_text(FAITHFUL_PORT)
        self.pristine = self.root / 'pristine'
        shutil.copytree(self.codex / 'pack', self.pristine)

    def verify(self, *args):
        return subprocess.run([sys.executable, str(self.codex / 'port' / 'verify.py'), *args],
                              capture_output=True, text=True)

    def test_committed_pack_passes(self):
        result = self.verify('--source', str(self.pristine))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('deterministic regeneration', result.stdout)

    def test_hand_edit_is_caught_by_regeneration(self):
        leaf = self.codex / 'pack' / 'skills' / 'pstack-how' / 'SKILL.md'
        leaf.write_text(leaf.read_text() + 'Hand edit.\n')
        self.assertEqual(self.verify().returncode, 0)
        result = self.verify('--source', str(self.pristine))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Committed pack differs from regeneration', result.stderr)

    def test_model_invocation_block_is_refused(self):
        leaf = self.codex / 'pack' / 'skills' / 'pstack-how' / 'SKILL.md'
        leaf.write_text(leaf.read_text().replace('\n---\n', '\ndisable-model-invocation: true\n---\n', 1))
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pstack-how', result.stderr)


if __name__ == '__main__':
    unittest.main()
