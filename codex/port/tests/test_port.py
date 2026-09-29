import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PORT_DIR = Path(__file__).resolve().parents[1]


class PortTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scripts = self.root / 'port'
        scripts.mkdir()
        for item in ('port.py', 'session-entry.md'):
            shutil.copyfile(PORT_DIR / item, scripts / item)
        shutil.copytree(PORT_DIR / 'overrides', scripts / 'overrides')
        self.source = self.root / 'pstack'
        (self.source / 'skills' / 'poteto-mode' / 'references').mkdir(parents=True)
        (self.source / 'skills' / 'poteto-mode' / 'references' / 'guide.md').write_text('Guide.\n')
        (self.source / 'skills' / 'poteto-mode' / 'SKILL.md').write_text(
            '---\nname: poteto-mode\ndescription: Mode.\n---\n\n# Poteto mode\n')
        (self.source / 'agents').mkdir()
        (self.source / 'agents' / 'poteto-agent.md').write_text('Role.\n')
        (self.source / 'LICENSE').write_text('License.\n')
        self.manifest = scripts / 'source-manifest.json'
        self.write_manifest()
        self.port = scripts / 'port.py'

    def write_manifest(self):
        pinned = {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for directory in ('skills', 'agents') for p in (self.source / directory).rglob('*') if p.is_file()}
        self.manifest.write_text(json.dumps({'sha256': pinned}))

    def run_port(self, *args):
        return subprocess.run([sys.executable, str(self.port), str(self.source), *args],
                              cwd=self.root, capture_output=True, text=True)

    def test_port_defaults_to_the_pack_beside_the_generator(self):
        result = self.run_port()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ported 1 skills', result.stdout)
        mode = self.root / 'pack' / 'skills' / 'poteto-mode'
        self.assertIn('## Start the Codex session', (mode / 'SKILL.md').read_text())
        self.assertEqual((mode / 'agents' / 'openai.yaml').read_text(), 'policy:\n  allow_implicit_invocation: true\n')
        self.assertEqual((mode / 'references' / 'agents' / 'poteto-agent.md').read_text(), 'Role.\n')
        self.assertEqual((self.root / 'pack' / 'PSTACK-LICENSE').read_text(), 'License.\n')

    def test_port_writes_to_the_requested_output(self):
        output = self.root / 'elsewhere'
        result = self.run_port('--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((output / 'skills' / 'poteto-mode' / 'SKILL.md').is_file())
        self.assertFalse((self.root / 'pack').exists())

    def test_drifted_source_is_refused(self):
        (self.source / 'skills' / 'poteto-mode' / 'references' / 'guide.md').write_text('Changed.\n')
        result = self.run_port()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source differs from pinned PStack archive', result.stderr)
        self.assertFalse((self.root / 'pack').exists())


if __name__ == '__main__':
    unittest.main()
