import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
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

MODE_DESCRIPTION = "Poteto's working style. Use for /poteto-mode."
REMINDER = "New task? Playbook match or rigor needed -> apply /poteto-mode. Casual turn or user opts out -> don't."
# Upstream sentences that the real poteto-mode substitutions rewrite.
PER_PLAYBOOK = "The per-playbook lines below name only the content unique to that playbook."
PLAYBOOKS_LEAD = (
    "Open a todolist whose first items are the matched playbook's steps, copied in verbatim, before any task-specific "
    "todos. A step you choose not to do stays in the list with a one-line `skip: <reason>`. Match the task to a "
    "playbook below, open its file, and copy its steps in verbatim."
)
# One short paragraph per H2 section, in upstream order. Subagents carries backticks, quotes, and a `$` that the
# hook scripts must hand over unexpanded.
MODE_SECTIONS = {
    "Non-negotiables": "Verify every claim before you make it.",
    "Principles": "Each principle is a skill of its own.",
    "Autonomy": "Decide what you can observe yourself.",
    "Subagents": 'Brief each subagent in full: its `subagent_type`, "the goal", and paths under $HOME spelled out.',
    "Writing the reply": f"Lead with the answer. {PER_PLAYBOOK}",
    "Comments": "Keep comments rare and specific.",
    "Playbooks": PLAYBOOKS_LEAD,
}
FEATURE = "# Feature\n\n1. Read the code.\n"
OPENING_A_PR = "# Opening a PR\n\n1. Push the branch.\n"
PLAYBOOK_HEADER = (
    "Playbook {0}, from .claude/skills/poteto-mode/playbooks/{0}.md. "
    "Relative paths below resolve under .claude/skills/poteto-mode/.\n\n"
)
PLAYBOOK_USAGE = "Usage: /playbook <name>. Available playbooks: feature, opening-a-pr\n"
# The first sentence of a principle's description says when to apply it, and is all the port keeps.
PRINCIPLE_TRIGGER = "Apply when a foo needs care."


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def principle(name: str, description: str) -> str:
    """A principle SKILL.md whose frontmatter description is `description`, quotes included."""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nCheck each step.\n"


def first_sentence(length: int) -> str:
    """A first sentence of exactly `length` characters."""
    return "Apply when " + "x" * (length - 12) + "."


def poteto_mode(sections: dict[str, str], reminder: bool = True) -> str:
    """An upstream poteto-mode SKILL.md with Cursor's frontmatter and the given H2 sections."""
    keys = f"name: Poteto Mode\ndescription: {MODE_DESCRIPTION}\ndisable-model-invocation: true\nmode: true\nicon: crown\ncolor: yellow\n"
    if reminder:
        keys += f"reminder: {REMINDER}\n"
    body = "\n\n".join(f"## {name}\n\n{text}" for name, text in sections.items())
    return f"---\n{keys}---\n\n# Poteto mode\n\n{body}\n"


class PortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.upstream = self.tmp / "upstream"
        write(self.upstream / "skills" / "foo" / "SKILL.md", FOO_UPSTREAM)
        write(self.upstream / "skills" / "setup-pstack" / "SKILL.md", "Cursor setup.\n")
        self.mode = self.upstream / "skills" / "poteto-mode" / "SKILL.md"
        write(self.mode, poteto_mode(MODE_SECTIONS))
        write(self.upstream / "skills" / "poteto-mode" / "playbooks" / "feature.md", FEATURE)
        write(self.upstream / "skills" / "poteto-mode" / "playbooks" / "opening-a-pr.md", OPENING_A_PR)
        self.principle = self.upstream / "skills" / "principle-foo" / "SKILL.md"
        write(self.principle, principle("principle-foo", f'"{PRINCIPLE_TRIGGER} Foo work drifts without it."'))
        write(
            self.upstream / "agents" / "comment-sicko.md",
            port.COMMENT_SICKO_FRONTMATTER[0] + "\n" + port.COMMENT_SICKO_HOWWHY[0] + "\n",
        )
        write(self.upstream / "agents" / "poteto-agent.md", "Cursor agent.\n")
        self.added = self.tmp / "added-skills"
        shutil.copytree(port.ADDED_SKILL_FILES / "playbook", self.added / "playbook")

        self.project = self.tmp / "project"
        self.pack = self.project / ".claude"
        write(self.pack / "hooks" / "pstack-readonly-search.py", "# search hook\n")
        write(self.pack / "skills" / "foo" / "SKILL.md", FOO_INSTALLED)
        write(self.pack / "skills" / "local-only" / "SKILL.md", "---\nname: local-only\ndescription: Mine.\n---\n")
        write(self.pack / "agents" / "pstack-readonly.md", "old agent\n")
        write(self.pack / "settings.json", "{}\n")
        write(self.pack / "settings.local.json", '{"local": true}\n')

        self.mode_pairs = [
            pair for pair in port.SUBSTITUTIONS["poteto-mode/SKILL.md"] if pair[0] in (PER_PLAYBOOK, PLAYBOOKS_LEAD)
        ]
        self.assertEqual(len(self.mode_pairs), 2)
        patches = [
            mock.patch.object(port, "SUBSTITUTIONS", {**FOO_SUBSTITUTION, "poteto-mode/SKILL.md": self.mode_pairs}),
            mock.patch.object(port, "SCRIPT_SUBSTITUTIONS", {}),
            mock.patch.object(port, "ADDED_SKILL_FILES", self.added),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_port(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            port.port(self.upstream, self.pack)
        return out.getvalue()

    def assertRefusedUntouched(self) -> str:
        before = snapshot(self.pack)
        with self.assertRaises(SystemExit) as refused:
            self.run_port()
        self.assertIn("was not changed", str(refused.exception))
        self.assertEqual(snapshot(self.pack), before)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])
        return str(refused.exception)

    def assertMoveFailureRollsBack(self, fails) -> None:
        real_rename = Path.rename
        calls = []

        def flaky_rename(path: Path, target: Path) -> Path:
            calls.append(path)
            if fails(len(calls), path):
                raise OSError("disk went away")
            return real_rename(path, target)

        before = snapshot(self.pack)
        with mock.patch.object(Path, "rename", flaky_rename), self.assertRaises(SystemExit) as refused:
            self.run_port()
        self.assertIn("disk went away", str(refused.exception))
        self.assertEqual(snapshot(self.pack), before)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])

    def run_hook(self, name: str) -> dict[str, str]:
        script = self.pack / "hooks" / name
        self.assertTrue(os.access(script, os.X_OK), f"{name} is not executable")
        result = subprocess.run(["sh", str(script)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)["hookSpecificOutput"]

    def load_playbook(self, *args: str) -> str:
        script = self.pack / "skills" / "playbook" / "scripts" / "load.sh"
        return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, check=True).stdout

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
        self.assertMoveFailureRollsBack(lambda count, path: count == 4)

    def test_failed_mode_hook_move_rolls_back(self) -> None:
        # The reminder hook is the last entry installed, after the compaction hook beside it.
        self.assertMoveFailureRollsBack(lambda count, path: path.name == "poteto-mode-reminder.sh")

    def test_mode_is_reordered_for_compaction(self) -> None:
        self.run_port()
        text = (self.pack / "skills" / "poteto-mode" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(port.frontmatter(text), {"name": "poteto-mode", "description": MODE_DESCRIPTION})
        self.assertEqual(re.findall(r"^## (.+)$", text, re.MULTILINE), list(port.SECTION_ORDER))
        self.assertIn("The per-playbook lines above name only the content unique to that playbook.", text)
        self.assertIn("load it with the Skill tool: `/playbook <name>`", text)
        ported = dict(MODE_SECTIONS)
        for old, new in self.mode_pairs:
            ported = {name: paragraph.replace(old, new) for name, paragraph in ported.items()}
        body = "\n\n".join(f"## {name}\n\n{ported[name]}" for name in port.SECTION_ORDER)
        self.assertEqual(
            text,
            f"---\nname: poteto-mode\ndescription: {MODE_DESCRIPTION}\n---\n\n# Poteto mode\n\n{port.PORT_NOTE}\n\n{body}\n",
        )

    def test_changed_mode_sections_are_refused(self) -> None:
        renamed = {("Comment style" if name == "Comments" else name): text for name, text in MODE_SECTIONS.items()}
        extra = {**MODE_SECTIONS, "Examples": "One worked example."}
        for sections, change in (
            (renamed, "missing ['Comments'], unexpected ['Comment style']"),
            (extra, "missing [], unexpected ['Examples']"),
        ):
            with self.subTest(change):
                write(self.mode, poteto_mode(sections))
                message = self.assertRefusedUntouched()
                self.assertIn(change, message)
                self.assertIn("update SECTION_ORDER", message)

    def test_mode_without_reminder_is_refused(self) -> None:
        write(self.mode, poteto_mode(MODE_SECTIONS, reminder=False))
        self.assertIn("skills/poteto-mode/SKILL.md: upstream frontmatter lost reminder", self.assertRefusedUntouched())

    def test_mode_past_the_compaction_bounds_is_refused(self) -> None:
        for name, text, problem in (
            ("Playbooks", f"{PLAYBOOKS_LEAD}\n\n{'x' * port.PLAYBOOKS_END_MAX}", "the Playbooks section ends at body offset"),
            ("Principles", "x" * port.COMPACT_KEPT, "the last section, Subagents, starts at body offset"),
        ):
            with self.subTest(name):
                write(self.mode, poteto_mode({**MODE_SECTIONS, name: text}))
                self.assertIn(problem, self.assertRefusedUntouched())

    def test_mode_hooks_hand_the_reminder_and_the_final_section_to_the_model(self) -> None:
        self.run_port()
        reminder = self.run_hook("poteto-mode-reminder.sh")
        self.assertEqual(reminder["hookEventName"], "UserPromptSubmit")
        self.assertEqual(reminder["additionalContext"], f"{REMINDER} {port.REMINDER_SUFFIX}")
        compact = self.run_hook("poteto-mode-compact.sh")
        self.assertEqual(compact["hookEventName"], "SessionStart")
        self.assertIn("Run TaskList", compact["additionalContext"])
        self.assertEqual(
            compact["additionalContext"],
            f"{port.RECOVERY_NOTE}\n\n{port.FINAL_SECTION_RULE}\n\n## Subagents\n\n{MODE_SECTIONS['Subagents']}\n\n{REMINDER}",
        )

    def test_settings_run_the_installed_mode_hooks(self) -> None:
        self.run_port()
        text = (self.pack / "settings.json").read_text(encoding="utf-8")
        self.assertEqual(text, port.SETTINGS_JSON)
        settings = json.loads(text)
        self.assertEqual(settings["env"], {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"})
        self.assertEqual(sorted(settings["hooks"]), ["SessionStart", "UserPromptSubmit"])
        self.assertEqual(settings["hooks"]["SessionStart"][0]["matcher"], "compact")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}
        for event, [group] in settings["hooks"].items():
            [hook] = group["hooks"]
            with self.subTest(event):
                result = subprocess.run(
                    ["sh", "-c", hook["command"]], cwd=self.tmp, env=env, capture_output=True, text=True, check=True
                )
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], event)

    def test_playbook_loader_prints_the_named_playbook(self) -> None:
        self.run_port()
        skill = self.pack / "skills" / "playbook"
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(text, (self.added / "playbook" / "SKILL.md").read_text(encoding="utf-8"))
        # Claude Code substitutes the skill directory and the raw arguments into the line, then runs it.
        [command] = re.findall(r"^!`(.+)`$", text, re.MULTILINE)
        command = command.replace("${CLAUDE_SKILL_DIR}", str(skill))

        def run_line(arguments: str) -> str:
            line = command.replace("$ARGUMENTS", arguments)
            return subprocess.run(["sh", "-c", line], cwd=self.project, capture_output=True, text=True, check=True).stdout

        self.assertEqual(run_line("Opening a PR"), PLAYBOOK_HEADER.format("opening-a-pr") + OPENING_A_PR)
        for arguments in ("feature; echo INJECTED", "a$(echo SUB)b"):
            with self.subTest(arguments=arguments):
                self.assertEqual(run_line(arguments), PLAYBOOK_USAGE)

        self.assertEqual(self.load_playbook("feature"), PLAYBOOK_HEADER.format("feature") + FEATURE)
        for args in (["Opening a PR"], ["opening-a-pr.md"]):
            with self.subTest(args=args):
                self.assertEqual(self.load_playbook(*args), PLAYBOOK_HEADER.format("opening-a-pr") + OPENING_A_PR)
        # The last name reaches a file that exists, so only the character check refuses it.
        for args in (["nope"], [], ["../x"], ["../references/claude-code"]):
            with self.subTest(args=args):
                self.assertEqual(self.load_playbook(*args), PLAYBOOK_USAGE)

    def test_agent_that_pins_effort_is_refused(self) -> None:
        pinned = port.AGENTS["poteto-agent.md"].replace("\nbackground: true\n", "\nbackground: true\neffort: high\n")
        self.assertNotEqual(pinned, port.AGENTS["poteto-agent.md"])
        with mock.patch.dict(port.AGENTS, {"poteto-agent.md": pinned}):
            self.assertIn("agents/poteto-agent: pins effort", self.assertRefusedUntouched())

    def test_compact_hook_context_past_the_cap_is_refused(self) -> None:
        fixed = len(f"{port.RECOVERY_NOTE}\n\n{port.FINAL_SECTION_RULE}\n\n## Subagents\n\n\n\n{REMINDER}")
        write(self.mode, poteto_mode({**MODE_SECTIONS, "Subagents": "x" * (port.HOOK_CONTEXT_MAX - fixed)}))
        self.run_port()
        self.assertEqual(len(self.run_hook("poteto-mode-compact.sh")["additionalContext"]), port.HOOK_CONTEXT_MAX)
        write(self.mode, poteto_mode({**MODE_SECTIONS, "Subagents": "x" * (port.HOOK_CONTEXT_MAX - fixed + 1)}))
        self.assertIn(
            f"the SessionStart hook's additional context is {port.HOOK_CONTEXT_MAX + 1} characters",
            self.assertRefusedUntouched(),
        )

    def test_principle_description_is_cut_to_its_first_sentence(self) -> None:
        installed = self.pack / "skills" / "principle-foo" / "SKILL.md"
        self.run_port()
        self.assertEqual(installed.read_text(encoding="utf-8"), principle("principle-foo", f'"{PRINCIPLE_TRIGGER}"'))
        for upstream, cut in (
            ('"Apply when node.js code needs care. Then check it."', '"Apply when node.js code needs care."'),
            ('"Apply when a foo needs care! Then check it."', '"Apply when a foo needs care!"'),
            ("Does a foo need care? Apply this.", "Does a foo need care?"),
            (f'"{first_sentence(20)} Then check it."', f'"{first_sentence(20)}"'),
            (f'"{first_sentence(160)} Then check it."', f'"{first_sentence(160)}"'),
            (f'"{PRINCIPLE_TRIGGER}"', f'"{PRINCIPLE_TRIGGER}"'),
            ("Apply when debugging.", "Apply when debugging."),
        ):
            with self.subTest(upstream):
                write(self.principle, principle("principle-foo", upstream))
                self.run_port()
                self.assertEqual(installed.read_text(encoding="utf-8"), principle("principle-foo", cut))

    def test_principle_description_that_cannot_stand_alone_is_refused(self) -> None:
        for description, problem in (
            (f'"{first_sentence(161)} Then check it."', "the description's first sentence is 161 characters"),
            (f'"{first_sentence(19)} Then check it."', "the description's first sentence is 19 characters"),
            ('"Apply when the bar is raised and then"', "the description has no sentence end"),
        ):
            with self.subTest(problem):
                write(self.upstream / "skills" / "principle-bar" / "SKILL.md", principle("principle-bar", description))
                self.assertIn(f"skills/principle-bar/SKILL.md: {problem}", self.assertRefusedUntouched())

    def test_validate_refuses_a_principle_description_past_the_listing_bound(self) -> None:
        self.run_port()
        installed = self.pack / "skills" / "principle-foo" / "SKILL.md"
        write(installed, principle("principle-foo", f'"{first_sentence(160)}"'))
        port.validate(self.pack, self.pack)
        write(installed, principle("principle-foo", f'"{first_sentence(161)}"'))
        with self.assertRaises(SystemExit) as refused:
            port.validate(self.pack, self.pack)
        self.assertIn("skills/principle-foo: description is 161 characters, over 160", str(refused.exception))

    def test_claude_code_reference_is_installed(self) -> None:
        self.run_port()
        reference = (self.pack / "skills" / "poteto-mode" / "references" / "claude-code.md").read_text(encoding="utf-8")
        self.assertEqual(reference, port.CLAUDE_CODE_REFERENCE)
        self.assertIn("Mode and reminder", reference)

    def test_refresh_installs_pack_and_is_idempotent(self) -> None:
        out = self.run_port()
        self.assertIn("Spawn it with the Agent tool.", (self.pack / "skills" / "foo" / "SKILL.md").read_text())
        self.assertEqual((self.pack / "skills" / "setup-pstack" / "SKILL.md").read_text(), port.SETUP_PSTACK)
        self.assertEqual((self.pack / "agents" / "pstack-readonly.md").read_text(), port.AGENTS["pstack-readonly.md"])
        agent = (self.pack / "agents" / "poteto-agent.md").read_text(encoding="utf-8")
        self.assertEqual(agent, port.AGENTS["poteto-agent.md"])
        preloaded = re.findall(r"^  - (\S+)$", agent.split("\n---\n", 1)[0], re.MULTILINE)
        self.assertEqual(preloaded, ["poteto-mode"])
        for name in preloaded:
            self.assertTrue((self.pack / "skills" / name / "SKILL.md").is_file(), name)
        self.assertEqual((self.pack / "settings.json").read_text(), port.SETTINGS_JSON)
        settings = json.loads((self.pack / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(list(settings), ["env", "skillListingBudgetFraction", "hooks"])
        self.assertEqual(settings["skillListingBudgetFraction"], 0.02)
        self.assertEqual((self.pack / "settings.local.json").read_text(), '{"local": true}\n')
        self.assertEqual(
            sorted(p.name for p in (self.pack / "hooks").iterdir()),
            ["poteto-mode-compact.sh", "poteto-mode-reminder.sh", "pstack-readonly-search.py"],
        )
        self.assertEqual((self.pack / "hooks" / "pstack-readonly-search.py").read_text(), "# search hook\n")
        self.assertTrue((self.pack / "skills" / "local-only" / "SKILL.md").is_file())
        self.assertIn("local-only", out)
        self.assertEqual(list(self.pack.glob(".pstack-port-*")), [])

        after_first = snapshot(self.pack)
        self.run_port()
        self.assertEqual(snapshot(self.pack), after_first)


if __name__ == "__main__":
    unittest.main()
