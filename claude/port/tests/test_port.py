import importlib.util
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

PORT = Path(__file__).resolve().parents[1] / "port.py"
spec = importlib.util.spec_from_file_location("pstack_port", PORT)
port = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port)

FOO_UPSTREAM = "---\nname: foo\ndescription: Foo.\n---\n\nSpawn it with the Task tool.\n"
FOO_INSTALLED = "---\nname: foo\ndescription: Foo.\n---\n\nThe installed copy.\n"
FOO_SUBSTITUTION = {"foo/SKILL.md": [("Spawn it with the Task tool.", "Spawn it with the Agent tool.")]}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class PortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.upstream = self.tmp / "upstream"
        write(self.upstream / "skills" / "foo" / "SKILL.md", FOO_UPSTREAM)
        write(self.upstream / "skills" / "setup-pstack" / "SKILL.md", "Cursor setup.\n")
        write(
            self.upstream / "agents" / "comment-sicko.md",
            port.COMMENT_SICKO_FRONTMATTER[0] + "\n" + port.COMMENT_SICKO_HOWWHY[0] + "\n",
        )
        write(self.upstream / "agents" / "poteto-agent.md", "Cursor agent.\n")

        self.pack = self.tmp / "pack"
        write(self.pack / "hooks" / "pstack-readonly-search.py", "# search hook\n")
        write(self.pack / "skills" / "foo" / "SKILL.md", FOO_INSTALLED)
        write(self.pack / "skills" / "local-only" / "SKILL.md", "---\nname: local-only\ndescription: Mine.\n---\n")
        write(self.pack / "agents" / "pstack-readonly.md", "old agent\n")
        write(self.pack / "settings.json", "{}\n")
        write(self.pack / "settings.local.json", '{"local": true}\n')

        patches = [
            mock.patch.object(port, "SUBSTITUTIONS", dict(FOO_SUBSTITUTION)),
            mock.patch.object(port, "SCRIPT_SUBSTITUTIONS", {}),
            mock.patch.object(port, "ADDED_SKILL_FILES", self.tmp / "no-added-files"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_port(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            port.port(self.upstream, self.pack)
        return out.getvalue()

    def assertRefusedUntouched(self) -> None:
        before = snapshot(self.pack)
        with self.assertRaises(SystemExit) as refused:
            self.run_port()
        self.assertIn("was not changed", str(refused.exception))
        self.assertEqual(snapshot(self.pack), before)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])

    def test_drifted_upstream_leaves_installed_pack_untouched(self) -> None:
        write(self.upstream / "skills" / "foo" / "SKILL.md", FOO_UPSTREAM.replace("Task tool", "Task tool now"))
        self.assertRefusedUntouched()

    def test_leftover_cursor_text_is_refused(self) -> None:
        write(self.upstream / "skills" / "bar" / "SKILL.md", "---\nname: bar\ndescription: Bar.\n---\n\nsubagent_type: generalPurpose\n")
        self.assertRefusedUntouched()

    def test_relative_script_command_is_refused(self) -> None:
        write(self.upstream / "skills" / "bar" / "SKILL.md", "---\nname: bar\ndescription: Bar.\n---\n\nRun `bun scripts/orch/orch.ts`.\n")
        self.assertRefusedUntouched()

    def test_cursor_frontmatter_is_refused(self) -> None:
        write(self.upstream / "skills" / "bar" / "SKILL.md", "---\nname: bar\ndescription: Bar.\nmode: true\n---\n")
        self.assertRefusedUntouched()

    def test_skills_are_model_invocable(self) -> None:
        write(
            self.upstream / "skills" / "bar" / "SKILL.md",
            "---\nname: bar\ndescription: Bar.\ndisable-model-invocation: true\n---\n\nKeep disable-model-invocation: true in prose.\n",
        )
        self.run_port()
        self.assertEqual(
            (self.pack / "skills" / "bar" / "SKILL.md").read_text(encoding="utf-8"),
            "---\nname: bar\ndescription: Bar.\n---\n\nKeep disable-model-invocation: true in prose.\n",
        )

    def test_missing_search_hook_is_refused(self) -> None:
        (self.pack / "hooks" / "pstack-readonly-search.py").unlink()
        self.assertRefusedUntouched()

    def test_failed_move_rolls_back(self) -> None:
        real_rename = Path.rename
        calls = []

        def flaky_rename(path: Path, target: Path) -> Path:
            calls.append(path)
            if len(calls) == 4:
                raise OSError("disk went away")
            return real_rename(path, target)

        before = snapshot(self.pack)
        with mock.patch.object(Path, "rename", flaky_rename), self.assertRaises(SystemExit):
            self.run_port()
        self.assertEqual(snapshot(self.pack), before)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])

    def test_refresh_installs_pack_and_is_idempotent(self) -> None:
        out = self.run_port()
        self.assertIn("Spawn it with the Agent tool.", (self.pack / "skills" / "foo" / "SKILL.md").read_text())
        self.assertEqual((self.pack / "skills" / "setup-pstack" / "SKILL.md").read_text(), port.SETUP_PSTACK)
        self.assertEqual((self.pack / "agents" / "pstack-readonly.md").read_text(), port.AGENTS["pstack-readonly.md"])
        self.assertEqual((self.pack / "settings.json").read_text(), port.SETTINGS_JSON)
        self.assertEqual((self.pack / "settings.local.json").read_text(), '{"local": true}\n')
        self.assertTrue((self.pack / "skills" / "local-only" / "SKILL.md").is_file())
        self.assertIn("local-only", out)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])

        after_first = snapshot(self.pack)
        self.run_port()
        self.assertEqual(snapshot(self.pack), after_first)


if __name__ == "__main__":
    unittest.main()
