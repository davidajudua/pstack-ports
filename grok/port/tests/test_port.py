import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

PORT = Path(__file__).resolve().parents[1] / "port.py"
spec = importlib.util.spec_from_file_location("pstack_port", PORT)
port = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port)


class DropModelInvocationTest(unittest.TestCase):
    def test_last_frontmatter_line_is_removed_and_body_keeps_the_phrase(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        skill = tmp / "foo"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: foo\ndescription: Foo.\ndisable-model-invocation: true\n---\n\n"
            "A new mode keeps `disable-model-invocation: true` only when the author asks.\n",
            encoding="utf-8",
        )
        port.drop_model_invocation_block(tmp)
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        front, body = text.split("---", 2)[1:]
        self.assertNotIn("disable-model-invocation: true", front)
        self.assertIn("disable-model-invocation: true", body)
        self.assertTrue(text.startswith("---\nname: foo\n"))

    def test_spaced_or_quoted_true_is_removed(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        skill = tmp / "foo"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            '---\nname: foo\ndescription: Foo.\ndisable-model-invocation:  "true"\n---\n\nBody.\n',
            encoding="utf-8",
        )
        port.drop_model_invocation_block(tmp)
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        self.assertNotIn("disable-model-invocation", front)
