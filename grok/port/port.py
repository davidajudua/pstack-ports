#!/usr/bin/env python3
"""Port the Cursor pstack plugin into the Grok CLI pack.

Usage: port.py <upstream-pstack-checkout> [pack-dir]

The pack directory defaults to grok/pack/ in this repository and holds what a
project installs under its .grok/ directory.

Copies upstream skills/ and agents/ into skills/ and agents/ of the pack,
leaving out the skills the pack does not ship, then applies
every Cursor-to-Grok-CLI substitution below. Each substitution names the exact
upstream text it replaces and fails loudly when that text is missing or
ambiguous, so a refresh against a newer upstream cannot silently skip a
mapping. Files the pack adds on top of upstream (the pstack-agents workflow,
the agent frontmatter, setup-pstack, and added-skills/) are written whole.

The refresh swaps the pack's skills, agents, and workflows wholesale, so
it refuses to run while git reports uncommitted or untracked files in them, and
copies their ignored files (installed dependencies, local config) into the new
pack, refusing when the new pack ships a file at one of those paths.

Upstream pin: cursor/plugins, pstack 0.15.5, commit
ecc249f1e306fc64ddf83c7bed16cacf7c2239db.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXCLUDED_SKILLS = ("tdd", "teach")
PACK_DIRS = ("skills", "agents", "workflows")
# Whole files the pack adds under skills/, mirrored at the same relative paths.
ADDED_SKILL_FILES = Path(__file__).resolve().parent / "added-skills"
DEFAULT_PACK = Path(__file__).resolve().parents[1] / "pack"

SESSIONS_DIR = (
    "Grok CLI sessions for this working directory live under `~/.grok/sessions/<cwd-key>/<session-id>/`, "
    "where `<cwd-key>` is the working directory path URL-encoded with every `/` as `%2F` "
    "(`python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.getcwd(), safe=\"\"))'`). "
    "A key longer than 255 bytes is a slug plus a hash instead, with the original path in that directory's `.cwd` file."
)
NO_GLOB = (
    "Do not glob across `~/.grok/sessions/*/`. That crosses workspace boundaries and reads "
    "private chats from unrelated projects."
)
PSTACK_AGENTS = (
    "the `pstack-agents` workflow. Only the top-level session has the `workflow` tool, and the parent owns workflow launches. "
    "Before spawning, check whether you are a subagent: if you are, do not call the `workflow` tool. "
    "Spawn each entry with `spawn_subagent` instead, passing `model` only when its live schema lists it "
    "and `isolation: \"worktree\"` for `worktree: true`. "
    "For `readonly: true`, open the prompt with an explicit read-only instruction: read and search only, no file edits, "
    "no state-changing commands. Then collect each child's output with `get_command_or_subagent_output`. "
    "The top-level session calls the `workflow` tool with `source: {type: \"name\", name: \"pstack-agents\"}` "
    "and `args: {agents: [...]}`, one `{label, prompt, model, readonly, worktree}` entry per subagent. "
    "The run is backgrounded and reports each entry's `label`, `success`, and `output` when it completes"
)
WORKFLOW_REF = (
    "the `pstack-agents` workflow (a subagent uses `spawn_subagent` instead; "
    "see the **poteto-mode** skill's Model and read-only spawns)"
)
REJECT_MODEL = (
    "If the run or spawn fails on a model value, redo that entry with `model` removed so it runs on the session "
    "model, and say so."
)
CREATE_SKILL = "Grok CLI's bundled **create-skill** skill (`/create-skill`)"
NESTING = (
    "Cursor nests subagents to depth 3. Grok CLI nests to `[subagents] max_depth` in `~/.grok/config.toml`, "
    "which `/setup-pstack` sets to 3. "
    "When a spawn still fails with a depth-limit error, do that step yourself in your own context."
)
ONLY_RULE = (
    "`~/.grok/rules/pstack-models.md` is the only pstack model rule in Grok CLI. "
    "Ignore any `pstack-models` rule loaded from `~/.cursor/rules/` or `~/.claude/rules/`."
)
SLOP_STRIP = "the slop-strip (Grok CLI has no `/deslop`; see the **poteto-mode** skill's Grok CLI environment section)"
CONTROL = (
    "the project's `verify-<app>` skill (the **create-verification-skill** skill generates one), "
    "or the surface driven directly: the shell for CLIs and TUIs, a browser-driving CLI or MCP server for web UIs"
)

# (relative path under .grok/skills, [(old, new), ...]); each old must occur exactly once.
SUBSTITUTIONS: dict[str, list[tuple[str, str]]] = {
    "poteto-mode/SKILL.md": [
        (
            "name: Poteto Mode\n"
            "description: poteto's agent style for concise, detailed responses, deliberate subagents, unslopped prose, simple code, and verified work. Use for poteto, /poteto-mode, or requests to work in this style.\n"
            "disable-model-invocation: true\n"
            "mode: true\n"
            "icon: crown\n"
            "color: yellow\n"
            "reminder: New task? Playbook match or rigor needed -> apply /poteto-mode. Casual turn or user opts out -> don't.\n",
            "name: poteto-mode\n"
            "description: poteto's agent style for concise, detailed responses, deliberate subagents, unslopped prose, simple code, and verified work. Use for poteto, /poteto-mode, or requests to work in this style.\n",
        ),
        (
            "- About to `AskQuestion` on a",
            "- About to `ask_user_question` on a",
        ),
        (
            "Agent-facing prose also follows the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            f"Agent-facing prose also follows {CREATE_SKILL}.",
        ),
        (
            "- Before commit → the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`).",
            f"- Before commit → {SLOP_STRIP}.",
        ),
        (
            "- Shipping UI / IDE / CLI → the matching control skill. `cursor-team-kit` publishes `control-cli` (CLIs and TUIs) and `control-ui` (browser / Electron / web UIs).",
            f"- Shipping UI / IDE / CLI → the matching control skill: {CONTROL}.",
        ),
        (
            "and not Cursor's built-in babysit skill, whose description matches the same words.",
            "and not Grok CLI's bundled `pr-babysit` skill, whose description matches the same words.",
        ),
        (
            "**Use `subagent_type: \"poteto-agent\"` for any subagent you spawn inside a playbook step** (code-writing delegates, ad-hoc helpers). `/poteto-mode` and `poteto-agent` route through the same wrapper. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own `subagent_type` for diverse-model review. Respect what the skill prescribes, don't override to `poteto-agent`.\n"
            "\n"
            "**Defaults for every `Task` call.** `run_in_background: true`, agent mode (readonly strips MCP), file pointers not inlined context, explicit model per role (configurable via `/setup-pstack`. Defaults `grok-4.7-xhigh-fast` for code, `claude-opus-5-5-max` for prose and judgment). Code delegates tier by difficulty. The hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`claude-opus-5-5-max`), whether the task needs judgment on vague intent or is a precisely specified sequence of steps to execute to the letter. Trivial mechanical edits go to your fast code model. Per-role lines in the `/setup-pstack` rule override these defaults and the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). A role with no line keeps its default, and a role line of `inherit-parent` or `auto` runs that role on the parent chat model (omit Task `model`). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`.\n",
            "**Every subagent you spawn inside a playbook step runs as `poteto-agent`** (code-writing delegates, ad-hoc helpers). Its prompt opens with `Read .grok/agents/poteto-agent.md in full and act as that agent.` `/poteto-mode` and `poteto-agent` route through the same wrapper. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) prescribe their own spawns for diverse-model review. Respect what the skill prescribes, don't override to `poteto-agent`.\n"
            "\n"
            "**Defaults for every spawn.** Background (`background: true`, the `spawn_subagent` default), agent mode rather than read-only, file pointers not inlined context, explicit model per role (configurable via `/setup-pstack`. Defaults `grok-4.7-build-fast` for code, `grok-4.7` for prose and judgment). A spawn whose role model differs from the session model, or that must be read-only, runs through " + WORKFLOW_REF + ". Every other spawn uses `spawn_subagent`, which runs on the session model unless its live schema lists `model`. Code delegates tier by difficulty. The hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`grok-4.7`), whether the task needs judgment on vague intent or is a precisely specified sequence of steps to execute to the letter. Trivial mechanical edits go to your fast code model (`grok-4.7-build-fast`). Per-role lines in the `/setup-pstack` rule (`~/.grok/rules/pstack-models.md`) override these defaults and the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). A role with no line keeps its default, and a role line of `inherit-parent` or `auto` runs that role on the parent chat model (omit `model`). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`. Reasoning budget is the session effort level (`/effort`); the rule's `# budget` line records the level `/setup-pstack` chose.\n",
        ),
        (
            "## Writing the reply\n",
            "## Grok CLI environment\n"
            "\n"
            "This is the Grok CLI port of the Cursor pstack plugin. The command is `/poteto-mode`, and nothing in this pack starts a playbook until it runs. An ordinary request, a school scan, and a bot job stay on their own instructions. Cursor's mechanisms map as follows.\n"
            "\n"
            "- **Skills.** `.grok/skills/<name>/SKILL.md`, which wins over a `.claude/skills/` or `.agents/skills/` skill of the same name. Every pstack skill is model-invocable, so the model can load it. When this mode or a playbook names a skill (**how**, `/unslop`, a `principle-*`), read that file in full and apply it. That read is the invocation. The user can also type `/<name>`. Relative paths in this file (`playbooks/feature.md`, `references/bugbot-triage.md`, `scripts/`) resolve under `.grok/skills/poteto-mode/`. The **tdd** and **teach** skills are this repository's own, in `.agents/skills/`.\n"
            "- **Subagents.** `spawn_subagent` takes `prompt`, `description`, `background`, `isolation`, `resume_from`, and `cwd`. It has no agent type or read-only field. Pass `model` only when the live schema lists it. " + NESTING + " pstack's agents are prompt files in `.grok/agents/`: a spawn as `poteto-agent` or `comment-sicko` opens its prompt with `Read .grok/agents/<name>.md in full and act as that agent.` Cursor's `run_in_background: true` is `background: true`, the default. Collect a background child with `get_command_or_subagent_output`, and resume it with `resume_from` rather than spawning a sibling. `isolation: \"worktree\"` gives a child its own checkout in place of Cursor's `environment: \"cloud\"`.\n"
            f"- **Model and read-only spawns.** A spawn that sets a model or Cursor's `readonly: true` runs through {PSTACK_AGENTS}. `readonly: true` on a top-level workflow entry runs the child in Grok CLI's `read-only` capability mode (read and search, no edits, no shell). `worktree: true` gives it a private worktree. Omit `model`, or pass `auto` or `inherit-parent`, to run on the session model. {REJECT_MODEL}\n"
            "- **Models.** `grok models` and `/model` list the slugs, such as `grok-4.7`, `grok-4.7-build-fast`, and `grok-4.6`. The reasoning budget is the session effort level (`/effort`). The model rule `/setup-pstack` writes is `~/.grok/rules/pstack-models.md`. Grok CLI loads it in every session, and it lives outside every repository. " + ONLY_RULE + "\n"
            "- **Todolist.** `todo_write`.\n"
            "- **Commands.** Questions to the human use `ask_user_question`. `/loop` and `/goal` are Grok CLI's own. Grok CLI has no `/deslop`. The slop-strip is a pass over your own diff that deletes what it added without need: narration comments, dead code, speculative abstractions, defensive checks internal types already rule out, and debug leftovers. In place of `control-cli` and `control-ui`, drive the real app through " + CONTROL + ". The tasks pane (`Ctrl+G`) and `/workflow runs` show background subagents and workflow runs in place of the Cursor dashboard.\n"
            f"- **Transcripts.** {SESSIONS_DIR} Each session is a directory whose `chat_history.jsonl` holds one message per line. Its `type` is `system`, `user`, `assistant`, `reasoning`, or `tool_result`, and the user's own prompt sits inside `<user_query>` tags. A subagent's session is a sibling directory, described by its parent's `subagents/<child-id>/meta.json`. `$GROK_SESSION_ID` names the current session when the shell sets it. Otherwise the current session is the newest directory whose `chat_history.jsonl` holds this conversation's opening prompt. Never read another project's sessions unless asked.\n"
            "- **Store.** The pstack store is `~/.grok/pstack/store/`. Playbooks that write outside the repository (Orchestrate, Multi-phase plan) write there.\n"
            "- **Review bots.** Bugbot, Greptile, and the agentic security review are GitHub-side and unchanged. Cursor Automations have no Grok CLI equivalent.\n"
            "\n"
            "## Writing the reply\n",
        ),
        (
            "on an explicit pause, going offline, a Cursor restart, or imminent context compaction.",
            "on an explicit pause, going offline, a Grok CLI restart, or imminent context compaction.",
        ),
    ],
    "poteto-mode/playbooks/multi-phase-plan.md": [
        (
            "3. Explore in subagents with `subagent_type: \"poteto-agent\"` and an explicit model per the Subagents section",
            "3. Explore in `poteto-agent` subagents with an explicit model per the Subagents section, through " + WORKFLOW_REF,
        ),
        (
            "Unless the operator names a path, write the file under the agent store's `docs/`.",
            "Unless the operator names a path, write the file under the pstack store's `docs/` (`~/.grok/pstack/store/docs/`).",
        ),
        (
            "on the `swarm workers` model (default `grok-4.7-xhigh-fast`).",
            "on the `swarm workers` model (default `grok-4.7-build-fast`).",
        ),
        (
            "**Control skill.** Pick it by surface. Browser, Electron, and web UIs use `control-ui` from `cursor-team-kit`. CLIs and TUIs use `control-cli` from `cursor-team-kit`.",
            "**Control skill.** Pick it by surface. Use the project's `verify-<app>` skill when one exists. Otherwise browser, Electron, and web UIs use a browser-driving CLI or MCP server, and CLIs and TUIs use the shell.",
        ),
        (
            "- [ ] Arm the 30-minute audit tick. In a local session, a real terminal `/loop`. In a cloud root, a cloud-sleeper wake chain.",
            "- [ ] Arm the 30-minute audit tick. A real `/loop 30m` with the tick prompt.",
        ),
        (
            "- [ ] Run `/deslop` before each commit and `/no-comments` before review.",
            "- [ ] Run the slop-strip before each commit and `/no-comments` before review.",
        ),
        (
            "Each live lane runs on its own cloud VM at the PR head. Drive through `control-ui` or `control-cli` from `cursor-team-kit`.",
            f"Each live lane runs in its own worktree (`isolation: \"worktree\"`) at the PR head. Drive through {CONTROL}.",
        ),
        ("`node pstack/skills/poteto-mode/scripts/check-plan.mjs", "`node .grok/skills/poteto-mode/scripts/check-plan.mjs"),
        ("The program runs `pstack/skills/", "The program runs `.grok/skills/"),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/<execution playbook>.md`",
            "`git show origin/main:.grok/skills/poteto-mode/playbooks/<execution playbook>.md`",
        ),
        ("`git show origin/main:pstack/skills/swarm/SKILL.md`", "`git show origin/main:.grok/skills/swarm/SKILL.md`"),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`",
            "`git show origin/main:.grok/skills/poteto-mode/playbooks/opening-a-pr.md`",
        ),
        ("`git show origin/main:pstack/skills/<each other", "`git show origin/main:.grok/skills/<each other"),
        ("run the swarm per `pstack/skills/swarm/SKILL.md`", "run the swarm per `.grok/skills/swarm/SKILL.md`"),
        (
            "Which PRs get `pstack/skills/how/SKILL.md` and `pstack/skills/interrogate/SKILL.md`. The trail per `pstack/skills/show-me-your-work/SKILL.md`.",
            "Which PRs get `.grok/skills/how/SKILL.md` and `.grok/skills/interrogate/SKILL.md`. The trail per `.grok/skills/show-me-your-work/SKILL.md`.",
        ),
    ],
    "poteto-mode/playbooks/orchestrate.md": [
        (
            "- **Worker / verifier.** Always `environment: \"cloud\"` unless the task needs this machine: `control-ui` or `control-cli` runtime verification (from `cursor-team-kit`). Reading local transcripts under `agent-transcripts/`. Simulators and local IDE state. Auth that exists only here. Cloud agents cannot read the local store, so their briefs inline what they need or point at repo paths.",
            "- **Worker / verifier.** Always `isolation: \"worktree\"` unless the task needs this session's checkout: runtime verification against a running instance. Reading local sessions under `~/.grok/sessions/`. Simulators and local IDE state. Auth that exists only here. Briefs inline what the worker needs from the local store or point at repo paths.",
        ),
        (
            "Run a unit's verifier on a different model family from its worker.",
            "Run a unit's verifier on a different model from its worker, through " + WORKFLOW_REF + ".",
        ),
        (
            "Create `orchestrate/<project-slug>/` in the current agent's store (path in the system prompt).",
            "Create `orchestrate/<project-slug>/` in the pstack store (`~/.grok/pstack/store/`).",
        ),
        (
            "Verbatim paste is for cloud spawns and every resume.",
            "Verbatim paste is for worktree spawns and every resume.",
        ),
        (
            "the cloud agent's status in the Cursor dashboard.",
            "the subagent's status in the tasks pane (`Ctrl+G`).",
        ),
        (
            "Agents are spawned, resumed, and drained only through the Task tool.",
            "Agents are spawned through `spawn_subagent` or the `pstack-agents` workflow, drained through `get_command_or_subagent_output` or the run's completion report, and resumed only through `spawn_subagent` with `resume_from`.",
        ),
        (
            "spawns its own workers and verifiers (nesting works to depth 3, and a nested spawn has the full Task schema including `environment`)",
            "spawns its own workers and verifiers. " + NESTING[:-1],
        ),
        (
            "- After a Cursor restart: local agents are dead, cloud work is not.",
            "- After a Grok CLI restart: running subagents are dead, pushed branches and PRs are not.",
        ),
        (
            "reattach cloud work by PR and branch rather than agent id,",
            "reattach pushed work by PR and branch rather than agent id,",
        ),
    ],
    "poteto-mode/playbooks/babysit.md": [
        (
            "This playbook replaces Cursor's built-in babysit skill for these requests, so do not route there even though its description matches the same words.",
            "This playbook replaces Grok CLI's bundled `pr-babysit` skill for these requests, so do not route there even though its description matches the same words.",
        ),
        (
            "   On GitHub, stop at `READY` for one PR (single or stack mode). Queued mode",
            "   On GitHub, stop at `READY` for one PR (single or stack mode). A `BLOCKER` with `merge-gate` reason `github-blocked` or `review-required` means checks are green and nothing is left to fix, but GitHub still blocks the merge on a required review, approval, or branch rule. That is the human's line from step 9. Report the PR as waiting on review or branch rules and stop the watcher. Do not rearm it and do not try to fix the gate. Queued mode",
        ),
        (
            "or a `WAITING`/`merge-queue` report or `COMPLETE` in queued mode. On Origin",
            "or a `WAITING`/`merge-queue` report or `COMPLETE` in queued mode, or a `github-blocked` or `review-required` merge gate in any mode. On Origin",
        ),
        (
            "After GitHub reports `READY`, a queued",
            "After GitHub reports `READY`, a `github-blocked` or `review-required` merge gate, a queued",
        ),
    ],
    "poteto-mode/playbooks/refactoring.md": [
        (
            "your configured refactoring model (default `grok-4.7-xhigh-fast`)",
            "your configured refactoring model (default `grok-4.7-build-fast`)",
        ),
    ],
    "poteto-mode/playbooks/autopilot-full.md": [
        (
            "One Cursor cloud agent per PR owns build, the first push,",
            NESTING + " One `poteto-agent` subagent in its own worktree (`isolation: \"worktree\"`) per PR owns build, the first push,",
        ),
        (
            "a slop-strip (the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)), `/no-comments` (the **no-comments** skill), a rebase onto current trunk,",
            f"{SLOP_STRIP}, `/no-comments` (the **no-comments** skill), a rebase onto current trunk,",
        ),
        (
            "(with the matching control skill, such as `control-cli` or `control-ui` from `cursor-team-kit`, or a named driver where none exists)",
            "(with the matching control skill, such as the project's `verify-<app>` skill, or a named driver where none exists)",
        ),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`",
            "`git show origin/main:.grok/skills/poteto-mode/playbooks/autopilot-full.md`",
        ),
    ],
    "poteto-mode/playbooks/autopilot-stack.md": [
        (
            "One Cursor cloud agent per PR owns its change end to end:",
            "One subagent in its own worktree (`isolation: \"worktree\"`) per PR owns its change end to end:",
        ),
        (
            "a slop-strip (the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)), `/no-comments` (the **no-comments** skill), and babysit to green",
            f"{SLOP_STRIP}, `/no-comments` (the **no-comments** skill), and babysit to green",
        ),
        (
            "A local root arms each tick as a real terminal `/loop`. The loop uses a monitored-shell 30-minute sleep and emits an output-notification sentinel. A cloud root uses the existing cloud-sleeper wake chain instead.",
            "The root arms each tick as a real `/loop 30m` carrying the tick prompt.",
        ),
        (
            "re-read this playbook from trunk with `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`,",
            "re-read this playbook from trunk with `git show origin/main:.grok/skills/poteto-mode/playbooks/autopilot-stack.md`,",
        ),
    ],
    "poteto-mode/playbooks/shipping.md": [
        (
            "One subagent per PR, not batched, each a Cursor cloud agent, each exercising the real surface with the matching control skill (such as `control-ui` or `control-cli` from `cursor-team-kit`) against parent versus head.",
            "One subagent per PR, not batched, each in its own worktree (`isolation: \"worktree\"`), each exercising the real surface with the matching control skill (such as the project's `verify-<app>` skill) against parent versus head.",
        ),
        (
            "Rebase the lowest verified branch onto the exact trunk tip when needed, push it, and retarget",
            "Rebase the lowest verified branch onto the exact trunk tip when needed, push it with `git push --force-with-lease=<branch>:<verdict head SHA> origin <branch>`, which refuses if anyone moved the remote branch after the verdict, and retarget",
        ),
    ],
    "poteto-mode/playbooks/opening-a-pr.md": [
        (
            "Multiple `Task` calls on the same branch each get their own worktree,",
            "Multiple `spawn_subagent` calls on the same branch each get their own worktree,",
        ),
        (
            "**PRs.** Run `/deslop` from `cursor-team-kit` over the diff before commit.",
            "**PRs.** Run the slop-strip over the diff before commit.",
        ),
        (
            "A subagent that opens a PR runs `interrogate`, `/deslop`, and `/no-comments`, and posts the URL.",
            "A subagent that opens a PR runs `interrogate`, the slop-strip, and `/no-comments`, and posts the URL.",
        ),
        (
            "or `git fetch && git reset --hard origin/<branch>` between them.",
            "or `git fetch && git reset --keep origin/<branch>` between them, run only when `git status --porcelain --untracked-files=no` is empty and `git log origin/<branch>..HEAD` shows nothing unpushed. Otherwise commit and push that work first. If `--keep` refuses because the branch now tracks a path held by a local untracked file, such as `decisions.tsv`, move that file aside first.",
        ),
    ],
    "poteto-mode/playbooks/bug-fix.md": [
        (
            "Drive a long or stubborn hunt with Cursor's `/loop` command.",
            "Drive a long or stubborn hunt with Grok CLI's `/loop` command.",
        ),
        (
            "using your configured bug-fix model (default `grok-4.7-xhigh-fast`)",
            "using your configured bug-fix model (default `grok-4.7-build-fast`)",
        ),
    ],
    "poteto-mode/playbooks/feature.md": [
        (
            "using your configured feature model (default `grok-4.7-xhigh-fast`)",
            "using your configured feature model (default `grok-4.7-build-fast`)",
        ),
    ],
    "poteto-mode/playbooks/perf-issue.md": [
        (
            "using your configured perf-issue model (default `grok-4.7-xhigh-fast`)",
            "using your configured perf-issue model (default `grok-4.7-build-fast`)",
        ),
    ],
    "poteto-mode/playbooks/hillclimb.md": [
        (
            "using your configured hillclimb model (default `grok-4.7-xhigh-fast`)",
            "using your configured hillclimb model (default `grok-4.7-build-fast`)",
        ),
    ],
    "poteto-mode/playbooks/autonomous-run.md": [
        (
            "Pick the wake mechanism using Cursor's `/loop` command (a built-in, not a pstack skill).",
            "Pick the wake mechanism using Grok CLI's `/loop` command (a built-in, not a pstack skill).",
        ),
        (
            "Do not park reversible work for the human or use `AskQuestion`.",
            "Do not park reversible work for the human or use `ask_user_question`.",
        ),
    ],
    "poteto-mode/playbooks/session-pickup.md": [
        (
            "A local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path. Do not glob across `~/.cursor/projects/*/`, that crosses workspace boundaries and reads private chats from unrelated projects), a cloud-agent URL, or a pushed branch.",
            f"A local Grok CLI session under this workspace's sessions directory (`~/.grok/sessions/<cwd-key>/<session-id>/chat_history.jsonl`, key rule in the **poteto-mode** skill's Grok CLI environment section. {NO_GLOB}), or a pushed branch.",
        ),
    ],
    "poteto-mode/playbooks/eval.md": [
        (
            "Read each candidate's local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names this path). Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
            f"Read each candidate's local session under this workspace's sessions directory (`~/.grok/sessions/<cwd-key>/`, key rule in the **poteto-mode** skill's Grok CLI environment section). {NO_GLOB}",
        ),
    ],
    "poteto-mode/playbooks/worktree-cleanup.md": [
        (
            "since a hand-typed `myrepo-worktrees/x` misses one that lives at `.cursor/worktrees/myrepo/x`",
            "since a hand-typed `myrepo-worktrees/x` misses one that Grok CLI created elsewhere (`grok worktree list` names those)",
        ),
        (
            "For every `verify-recent-chat` row, or anything you doubt,",
            "A `hold-live-session` row has a running Grok CLI session inside it, so it is in use and stays. For every `verify-recent-chat` row, or anything you doubt,",
        ),
        (
            "`~/Library/Application Support/Cursor` (`state.vscdb.backup`, and `snapshots/roots/<root>` where a `<root>` named for a folder you opened as a workspace balloons),",
            "`~/.grok/sessions/` (old sessions balloon; `grok du` shows what uses space),",
        ),
        (
            "`scratch:N` is untracked throwaway, safe to drop, but name the files. Per Autonomy, clean and merged and not-in-use proceeds. `wip` and in-use pause.",
            "`untracked:N` (`hold-untracked`) is N untracked files, which removal deletes too, so name the files and get the same decision. `ignored:N` (`hold-ignored`) is N ignored paths, such as `.env`, local config, or a build directory, which removal deletes too, so list them with `git status --porcelain --ignored` and get the same decision. A row with several kinds, such as `untracked:1,ignored:2`, needs each one resolved. `hold-unreachable` is a detached HEAD whose commits no branch or tag contains, so removal strands them; show `git log` and get a decision. Per Autonomy, clean and merged and not-in-use proceeds. `wip`, `untracked`, `ignored`, `hold-unreachable`, and in-use pause.",
        ),
        (
            "Per path, `git worktree remove --force <path>`. If the dir survives on ignored build artifacts, `rm -rf` it, then `git worktree prune`. Branch refs survive, so no commits are lost.",
            "Per path, `git worktree remove <path>`, never `--force`. If git refuses, the worktree has uncommitted or untracked work, so take it back to step 4. `git worktree remove` deletes ignored files without refusing, which is why step 4 pauses on `ignored:N`. Then `git worktree prune`. Branch refs survive, so a removed worktree's branch commits are not lost.",
        ),
    ],
    "poteto-mode/playbooks/authoring-a-skill.md": [
        (
            "1. Use the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            f"1. Use {CREATE_SKILL}. Check the result with `grok inspect`, which lists every skill Grok CLI loads.",
        ),
    ],
    "poteto-mode/references/bugbot-triage.md": [
        (
            "### Contract-test drift claims are cheaply verifiable \u2014 run the test first",
            "### Contract-test drift claims are cheaply verifiable, so run the test first",
        ),
        (
            "- Do not skip when: n/a \u2014 this is a verification shortcut, not a dismissal",
            "- Do not skip when: n/a. This is a verification shortcut, not a dismissal",
        ),
    ],
    "how/SKILL.md": [
        (
            "Each spawn below names a role line in the `pstack-models.mdc` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            f"Every spawn below is read-only, so it runs through {PSTACK_AGENTS}. Each spawn names a role line in the `~/.grok/rules/pstack-models.md` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. {REJECT_MODEL} {ONLY_RULE}",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `how explorer` line, default `grok-4.7-xhigh-fast`\n- `readonly`: `true`\n",
            "- `model`: the `how explorer` line, default `grok-4.7-build-fast`\n- `readonly`: `true`\n",
        ),
        (
            "Spawn one Task subagent that explores and explains in one pass:\n\n- `subagent_type`: `generalPurpose`\n- `model`: the `how explainer` line, default `claude-opus-5-5-max`\n- `readonly`: `true`\n",
            "Spawn one `pstack-agents` subagent that explores and explains in one pass:\n\n- `model`: the `how explainer` line, default `grok-4.7`\n- `readonly`: `true`\n",
        ),
        (
            "spawn one Task subagent to synthesize their findings into one explanation:\n\n- `subagent_type`: `generalPurpose`\n- `model`: the `how explainer` line, default `claude-opus-5-5-max`\n- `readonly`: `true`\n",
            "spawn one `pstack-agents` subagent to synthesize their findings into one explanation:\n\n- `model`: the `how explainer` line, default `grok-4.7`\n- `readonly`: `true`\n",
        ),
    ],
    "why/SKILL.md": [
        (
            "Each spawn below names a role line in the `pstack-models.mdc` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            f"Every spawn below sets a model, so it runs through {PSTACK_AGENTS}. Each spawn names a role line in the `~/.grok/rules/pstack-models.md` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. {REJECT_MODEL} {ONLY_RULE}",
        ),
        (
            "Before spawning investigators, list the available MCPs from the Cursor environment. Use the available-tools map when present. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers.",
            "Before spawning investigators, list the MCP servers connected to this session: the `MCP servers connected` reminder in your context, the tools `search_tool` finds, and the output of `grok mcp list`.",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `why investigators` line, default `grok-4.7-xhigh-fast`\n- `readonly`: `false` (agent mode). **Do not use readonly/Ask mode.** It strips MCP access, which disables MCP-backed investigators entirely. Investigators still shouldn't write anything.",
            "- `model`: the `why investigators` line, default `grok-4.7-build-fast`\n- `readonly`: `false` (agent mode). **Do not use read-only mode.** Investigators need their inherited MCP servers. They still shouldn't write anything.",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `why synthesizer` line, default `claude-opus-5-5-max`\n- `readonly`: `false` (agent mode). The synthesizer's quality check spot-verifies citations, which can require MCP access. Readonly/Ask mode strips MCPs and defeats that.",
            "- `model`: the `why synthesizer` line, default `grok-4.7`\n- `readonly`: `false` (agent mode). The synthesizer's quality check spot-verifies citations, which can require its MCP servers.",
        ),
    ],
    "interrogate/SKILL.md": [
        (
            "Launch all reviewers in a single message using the Task tool. Use the `interrogate reviewers` line in `~/.cursor/rules/pstack-models.mdc`,",
            f"{ONLY_RULE} Launch all reviewers in one run of {PSTACK_AGENTS}. Use the `interrogate reviewers` line in `~/.grok/rules/pstack-models.md`,",
        ),
        (
            "| Reviewer A | `claude-opus-5-5-max` |\n| Reviewer B | `gpt-5.6-sol-max` |\n| Reviewer C | `grok-4.7-xhigh-fast` |",
            "| Reviewer A | `grok-4.7` |\n| Reviewer B | `grok-4.6` |\n| Reviewer C | `grok-4.7-build-fast` |",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the configured `interrogate reviewers` entry, or the table default with no configured line. For an `auto` or `inherit-parent` entry, omit `model` so that reviewer runs on the parent model.\n- `readonly`: `true`\n",
            "- `model`: the configured `interrogate reviewers` entry, or the table default with no configured line. For an `auto` or `inherit-parent` entry, omit `model` so that reviewer runs on the parent model.\n- `readonly`: `true`\n",
        ),
        (
            "If the Task tool rejects a configured entry, run that reviewer on the table default of its family and say so. Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`. With no family match, use Reviewer A's default. If it rejects a table default, check the valid slugs in the Task tool's error message, pick the closest equivalent (prefer the highest-reasoning tier of the same family), spawn with it, and open a separate PR to update the default table. Do not block the review on the slug issue. Never treat an alias entry as a rejected slug or apply either fallback to it.",
            "If the run fails on a configured entry, run that reviewer on the table default of its seat and say so. If it fails on a table default, check the valid slugs with `grok models`, pick the closest equivalent (prefer the highest-capability model), spawn with it, and open a separate PR to update the default table. Do not block the review on the slug issue. Never treat an `auto` or `inherit-parent` entry as a rejected slug or apply either fallback to it.",
        ),
    ],
    "arena/SKILL.md": [
        (
            "Use the `arena runners` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, default to one each on `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`. An `auto` or `inherit-parent` entry in this line or the cross-judge line means the parent model, so omit `model` for it. If the Task tool rejects a configured entry, run that seat on its family's default and say so. Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`. With no family match, use `claude-opus-5-5-max`. If it rejects a default, use the closest valid slug of the same family from its error message.",
            f"Use the `arena runners` line in `~/.grok/rules/pstack-models.md`. If the rule or that line is missing, default to one each on `grok-4.7`, `grok-4.6`, `grok-4.7-build-fast`. An `auto` or `inherit-parent` entry in this line or the cross-judge line means the parent model, so omit `model` for it. If the run fails on a configured entry, run that seat on its default and say so. With no matching seat, use `grok-4.7`. {REJECT_MODEL} {ONLY_RULE}",
        ),
        (
            "Spawn all N subagents in one message with `run_in_background: true`, each with the task,",
            f"{NESTING} Spawn all N subagents in one run of {PSTACK_AGENTS}, each entry with the seat's `model`, `readonly: false`, and a prompt that opens with `Read .grok/agents/poteto-agent.md in full and act as that agent.`, then the task,",
        ),
        (
            "choose one model from the `arena cross-judge pool` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, choose from `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`. Prefer a different model family from the parent's. Spawn one readonly judge subagent on that model.",
            "choose one model from the `arena cross-judge pool` line in `~/.grok/rules/pstack-models.md`. If the rule or that line is missing, choose from `grok-4.7`, `grok-4.6`, `grok-4.7-build-fast`. Prefer a different model from the parent's. Spawn one judge subagent on that model through " + WORKFLOW_REF + " with `readonly: true`.",
        ),
    ],
    "architect/SKILL.md": [
        (
            "Take the runners from the `architect runners` line in the `pstack-models.mdc` rule, in place of the `arena runners` line. If the rule or that line is missing, use `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`.",
            f"Take the runners from the `architect runners` line in the `~/.grok/rules/pstack-models.md` rule, in place of the `arena runners` line. If the rule or that line is missing, use `grok-4.7`, `grok-4.6`, `grok-4.7-build-fast`. {ONLY_RULE}",
        ),
    ],
    "swarm/SKILL.md": [
        (
            "N is total workers, not the cloud concurrency limit.",
            "N is total workers, not the concurrent subagent limit.",
        ),
        (
            "Pick the worker model from the `swarm workers` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, use `grok-4.7-xhigh-fast`. For `auto` or `inherit-parent`, omit `model` so the workers run on the parent model. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            f"Pick the worker model from the `swarm workers` line in `~/.grok/rules/pstack-models.md`. If the rule or that line is missing, use `grok-4.7-build-fast`. For `auto` or `inherit-parent`, omit `model` so the workers run on the parent model. {REJECT_MODEL} {ONLY_RULE}",
        ),
        (
            "Spawn all N workers in one message with `subagent_type: generalPurpose`, `environment: \"cloud\"`, `run_in_background: true`, and the step 4 model, left unset for `auto` or `inherit-parent`. Use `environment: \"local\"` only when the worker needs access to something on the user's computer.\n\nWhen a worker must start from a non-default pushed branch, pass `cloud_base_branch`.",
            f"Spawn all N workers in one run of {PSTACK_AGENTS}, each entry with `readonly: false`, `worktree: true` (each worker gets its own checkout), and the step 4 model, left unset for `auto` or `inherit-parent`. Drop `worktree` only when the worker needs this session's checkout or something else on the user's computer.\n\nWhen a worker must start from a non-default pushed branch, name the branch in its brief. The worker runs `git fetch origin <branch> && git checkout <branch>` inside its worktree before anything else.",
        ),
    ],
    "reflect/SKILL.md": [
        (
            "The parent finds its own transcript file before fanning out. The system prompt names the active workspace's `agent-transcripts/` directory. Use that path. Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.\n"
            "\n"
            "```bash\n"
            "ls -t <agent-transcripts>/*.jsonl <agent-transcripts>/*/*.jsonl <agent-transcripts>/*/subagents/*.jsonl 2>/dev/null | head -10\n"
            "```\n"
            "\n"
            "Three transcript layouts: legacy flat (`<id>.jsonl`), current nested (`<id>/<id>.jsonl`), and subagent (`<parent>/subagents/<child>.jsonl`).\n"
            "\n"
            "For each candidate, read the first JSONL line and check that `message.content[0].text` contains the conversation's opening user prompt.",
            f"The parent finds its own transcript file before fanning out. {SESSIONS_DIR} `$GROK_SESSION_ID` names the current session when the shell sets it. Use only that directory. {NO_GLOB}\n"
            "\n"
            "```bash\n"
            "key=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.getcwd(), safe=\"\"))')\n"
            "echo \"${GROK_SESSION_ID:-}\"\n"
            "ls -td ~/.grok/sessions/\"$key\"/*/ 2>/dev/null | head -10\n"
            "```\n"
            "\n"
            "Each session is a directory whose `chat_history.jsonl` is its transcript. A subagent's session is a sibling directory, and its parent's `subagents/<child-id>/meta.json` names it.\n"
            "\n"
            "For each candidate, read the first `chat_history.jsonl` line whose `type` is `user` and whose text holds `<user_query>`, and check that it contains the conversation's opening user prompt.",
        ),
        (
            "One message, three `Task` calls, `subagent_type: generalPurpose`, with `model` set as below, agent mode (`readonly: false`). Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript). Readonly strips MCPs.",
            f"One run of {PSTACK_AGENTS}, three entries, with `model` set as below, agent mode (`readonly: false`). Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), and read-only mode is not guaranteed to keep it.",
        ),
        (
            "Each reviewer and the synthesizer name a role line in the `pstack-models.mdc` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            f"Each reviewer and the synthesizer name a role line in the `~/.grok/rules/pstack-models.md` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. {REJECT_MODEL} {ONLY_RULE}",
        ),
        (
            "| Judgment | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-max` | `references/judgment-reviewer.md` |\n| Tooling | `reflect tooling` | `gpt-5.6-sol-max` | `references/tooling-reviewer.md` |\n| Divergent | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-max` | `references/divergent-reviewer.md` |",
            "| Judgment | `reflect judgment, divergent, synthesizer` | `grok-4.7` | `references/judgment-reviewer.md` |\n| Tooling | `reflect tooling` | `grok-4.6` | `references/tooling-reviewer.md` |\n| Divergent | `reflect judgment, divergent, synthesizer` | `grok-4.7` | `references/divergent-reviewer.md` |",
        ),
        (
            "Reviewers return findings in the `Task` response body.",
            "Reviewers return findings in their entry's `output` in the run's completion report. A subagent caller collects them with `get_command_or_subagent_output` instead.",
        ),
        (
            "One `Task` call, `subagent_type: generalPurpose`, with `model` from the `reflect judgment, divergent, synthesizer` line (default `claude-opus-5-5-max`), agent mode (`readonly: false`). The synthesizer's quality check includes spot-verifying citations, which can require MCP access. Readonly strips MCPs.",
            "One run of " + WORKFLOW_REF + " with one entry, `model` from the `reflect judgment, divergent, synthesizer` line (default `grok-4.7`), agent mode (`readonly: false`). The synthesizer's quality check includes spot-verifying citations, which can require MCP access.",
        ),
        (
            "- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Cursor's built-in `create-skill` skill and run its draft / test / iterate loop.\n"
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and run its description-optimization loop.\n"
            "- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.\n"
            "\n"
            "If your environment ships a SKILL.md validator, run it on every touched skill before declaring done. Skip this step if it doesn't.",
            "- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Grok CLI's bundled `create-skill` skill and run its draft / test / iterate loop.\n"
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and tune the description until the skill triggers.\n"
            "- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.\n"
            "\n"
            "Run `grok inspect` before declaring done and check that every touched skill still loads.",
        ),
    ],
    "reflect/references/tooling-reviewer.md": [
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `read_file` tool calls against any `SKILL.md` file (project `.grok/skills/`, user-level `~/.grok/skills/`, or plugin-installed paths under `~/.grok/installed-plugins/`)\n- `spawn_subagent` or `pstack-agents` prompts that name a skill path",
        ),
    ],
    "reflect/references/judgment-reviewer.md": [
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `read_file` tool calls against any `SKILL.md` file (project `.grok/skills/`, user-level `~/.grok/skills/`, or plugin-installed paths under `~/.grok/installed-plugins/`)\n- `spawn_subagent` or `pstack-agents` prompts that name a skill path",
        ),
    ],
    "reflect/references/divergent-reviewer.md": [
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `read_file` tool calls against any `SKILL.md` file (project `.grok/skills/`, user-level `~/.grok/skills/`, or plugin-installed paths under `~/.grok/installed-plugins/`)\n- `spawn_subagent` or `pstack-agents` prompts that name a skill path",
        ),
    ],
    "no-comments/SKILL.md": [
        (
            "1. Spawn `Task` with `subagent_type: \"Comment Sicko\"`. Pass the scope. Do not restate its rules.",
            "1. Call `spawn_subagent` with `background: true` and a prompt that opens with `Read .grok/agents/comment-sicko.md in full and act as that agent.`, then the scope. Do not restate its rules. Collect its report with `get_command_or_subagent_output`. " + NESTING,
        ),
    ],
    "recall/SKILL.md": [
        (
            "Transcripts live at `~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>.jsonl`, where `<slug>` is the workspace path with the leading slash dropped and each \"/\" turned into \"-\" (so `/Users/you/proj` becomes `Users-you-proj`). Every line is one chat message.",
            "Transcripts live at `~/.grok/sessions/<cwd-key>/<uuid>/chat_history.jsonl`, where `<cwd-key>` is the workspace path URL-encoded with every `/` as `%2F` (so `/Users/you/proj` becomes `%2FUsers%2Fyou%2Fproj`). A key longer than 255 bytes is a slug plus a hash instead, with the original path in that directory's `.cwd` file. Every line is one chat message. Its `type` is `system`, `user`, `assistant`, `reasoning`, or `tool_result`, and the user's own prompt sits inside `<user_query>` tags. `grok sessions search <keyword>` searches session summaries and first prompts.",
        ),
    ],
    "show-me-your-work/SKILL.md": [
        (
            "Read this run's transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path). Don't glob across `~/.cursor/projects/*/`. That reads unrelated private chats.",
            "Read this run's transcript, `chat_history.jsonl` in this session's directory under `~/.grok/sessions/<cwd-key>/` (key rule and current-session rule in the **poteto-mode** skill's Grok CLI environment section). Don't glob across `~/.grok/sessions/*/`. That reads unrelated private chats.",
        ),
    ],
    "automate-me/SKILL.md": [
        (
            "Drafts or revises a personal -mode skill via create-skill + unslop, optionally pulling fresh evidence from recent transcripts.",
            "Drafts or revises a personal -mode skill via Grok CLI's create-skill + unslop, optionally pulling fresh evidence from recent transcripts.",
        ),
        (
            "This skill orchestrates three others: an inline mining pass (see step 1), Cursor's built-in `create-skill` (authoring), and the **unslop** skill (prose discipline).",
            "This skill orchestrates three others: an inline mining pass (see step 1), Grok CLI's bundled `create-skill` (authoring), and the **unslop** skill (prose discipline).",
        ),
        (
            "Look recursively for `.cursor/skills/**/*-mode/SKILL.md` and `~/.cursor/skills/*-mode/SKILL.md` matching the user's handle. Mode skills can live in a personal category directory (`.cursor/skills/<handle>/`), not only at the top level. If one exists, confirm intent with `AskQuestion` (unless they already said \"update my skill\" or similar):",
            "Look recursively for `.grok/skills/**/*-mode/SKILL.md` and `~/.grok/skills/*-mode/SKILL.md` matching the user's handle. Mode skills can live in a personal category directory (`.grok/skills/<handle>/`), not only at the top level. If one exists, confirm intent with `ask_user_question` (unless they already said \"update my skill\" or similar):",
        ),
        (
            "Locate the active workspace's transcripts before fanning out. The system prompt names the workspace's `agent-transcripts/` directory. Use only that path. Don't glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
            f"Locate the active workspace's transcripts before fanning out. {SESSIONS_DIR} Each session's transcript is its `chat_history.jsonl`. Use only that directory. {NO_GLOB}",
        ),
        (
            "Mining misses intent that hasn't come up yet. Use the `AskQuestion` tool (structured multi-choice) rather than asking the user to type from scratch.\n"
            "\n"
            "Shape: one or two questions with 4-6 options each, `allow_multiple: true` for category questions.",
            "Mining misses intent that hasn't come up yet. Use the `ask_user_question` tool (structured multi-choice) rather than asking the user to type from scratch.\n"
            "\n"
            "Shape: one or two questions with 4-6 options each, `multi_select: true` for category questions.",
        ),
        (
            "Use Cursor's built-in `create-skill` skill to author the skill. Placement:\n"
            "\n"
            "- Path: preserve an existing mode skill's category. For a new mode, use `.cursor/skills/<handle>/<handle>-mode/SKILL.md` when the repo has an established personal category for that handle. Otherwise default to `.cursor/skills/<handle>-mode/SKILL.md` in the project (or `~/.cursor/skills/<handle>-mode/` if the user prefers a personal skill).",
            "Use Grok CLI's bundled `create-skill` skill to author the skill. Placement:\n"
            "\n"
            "- Path: preserve an existing mode skill's category. For a new mode, use `.grok/skills/<handle>/<handle>-mode/SKILL.md` when the repo has an established personal category for that handle. Otherwise default to `.grok/skills/<handle>-mode/SKILL.md` in the project (or `~/.grok/skills/<handle>-mode/` if the user prefers a personal skill).",
        ),
    ],
    "create-verification-skill/SKILL.md": [
        (
            "description: \"Generate a project-local verification skill that drives your app the way a user does \u2014 any language, framework, or platform.",
            "description: \"Generate a project-local verification skill that drives your app the way a user does, in any language, framework, or platform.",
        ),
        (
            "Existing harnesses first \u2014 Playwright/Cypress specs,",
            "Existing harnesses first: Playwright/Cypress specs,",
        ),
        (
            "This skill generates that as a project-local skill (`.cursor/skills/verify-<app>/`) tailored to the repo.",
            "This skill generates that as a project-local skill (`.grok/skills/verify-<app>/`) tailored to the repo.",
        ),
        (
            "Write `.cursor/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it \u2014 without frontmatter the skill never registers)",
            "Write `.grok/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it; without frontmatter the skill never registers)",
        ),
        (
            "- **Doctor:** one read-only check that answers \"is this instance worth driving?\" \u2014 process up,",
            "- **Doctor:** one read-only check that answers \"is this instance worth driving?\": process up,",
        ),
        (
            "confirm the evidence still exists at the named location \u2014 a cleanup that eats the proof fails this step.",
            "confirm the evidence still exists at the named location. A cleanup that eats the proof fails this step.",
        ),
        (
            "Create `.cursor/skills/verify-<app>/features/README.md` plus one file per user-facing feature",
            "Create `.grok/skills/verify-<app>/features/README.md` plus one file per user-facing feature",
        ),
    ],
    "maintain-verification-skill/SKILL.md": [
        (
            "- **clean** \u2014 every feature got source and live coverage; nothing worth shipping. No branch, no PR.\n- **changed** \u2014 one PR ships proven doc, harness, or map corrections.\n- **blocked** \u2014 coverage could not finish or a proven fix could not ship safely. Say exactly what blocked it.",
            "- **clean**: every feature got source and live coverage; nothing worth shipping. No branch, no PR.\n- **changed**: one PR ships proven doc, harness, or map corrections.\n- **blocked**: coverage could not finish or a proven fix could not ship safely. Say exactly what blocked it.",
        ),
        (
            "missing from the map \u2014 require a concrete source path before calling one missing.",
            "missing from the map. Require a concrete source path before calling one missing.",
        ),
        (
            "follow the verification skill's own launch model \u2014 one long-lived instance",
            "follow the verification skill's own launch model: one long-lived instance",
        ),
        (
            "since it last did something surprising \u2014 doctor before first drive,",
            "since it last did something surprising, so doctor before first drive,",
        ),
        (
            "outlives that drive's usefulness \u2014 failed-iteration residue is cleaned",
            "outlives that drive's usefulness, so failed-iteration residue is cleaned",
        ),
        (
            "fix it under edit scope and retry once \u2014 restart whatever the fix invalidated, nothing more \u2014 before calling the pass `blocked`.",
            "fix it under edit scope and retry once (restart whatever the fix invalidated, nothing more) before calling the pass `blocked`.",
        ),
        (
            "after the last drive of the run \u2014 including those re-proofs \u2014 so nothing outlives the run",
            "after the last drive of the run, including those re-proofs, so nothing outlives the run",
        ),
        (
            "(usually `.cursor/skills/verify-*/`)",
            "(usually `.grok/skills/verify-*/`)",
        ),
    ],
    "make-bot-ui/SKILL.md": [
        (
            "name: Make Bot UI\n",
            "name: make-bot-ui\n",
        ),
        (
            "Store `{url, key}` in that UI's own directory.",
            "Store `{url, key, uiToken}` in that UI's own directory. `uiToken` is this UI's own caller token: at least 32 bytes from a secure random source, made for this UI alone, never the sender key.",
        ),
        (
            "Bind the server to `0.0.0.0:<port>`, not `127.0.0.1`. Tailscale peers cannot reach a localhost-only bind.\n",
            "Bind the server to `0.0.0.0:<port>`, not `127.0.0.1`. Tailscale peers cannot reach a localhost-only bind.\n"
            "\n"
            "That bind also answers every other network this computer is on, so the sender key protects only the outbound call. Check every inbound request before the server does anything else:\n"
            "\n"
            "- The first visit carries `?token=<uiToken>`. On a match, set it as an `HttpOnly`, `SameSite=Strict` cookie and redirect to the same path without the query.\n"
            "- Every other request, the page and every button POST, must carry that cookie. Compare it with `uiToken` in constant time.\n"
            "- Reject a POST whose `Origin` header is not the page's own origin.\n"
            "- Answer `401` to any request that fails a check, and never call the webhook for it.\n"
            "- Do not log the token.\n",
        ),
        (
            "Give the user both URLs:\n\n- `http://<hostname>.<tailnet>.ts.net:<port>`\n- `http://<100.x.x.x>:<port>`\n",
            "Give the user both URLs, and tell them to add `?token=` and the `uiToken` value from the UI's config on their first visit. Do not print the token in chat.\n\n- `http://<hostname>.<tailnet>.ts.net:<port>`\n- `http://<100.x.x.x>:<port>`\n",
        ),
        (
            "Probe `http://<100.x.x.x>:<port>/` and expect HTTP 200.",
            "Probe `http://<100.x.x.x>:<port>/` without the token and expect HTTP 401. Probe it again with `?token=` and the token read from the config file inside the command, never typed out, and expect the redirect that sets the cookie.",
        ),
        (
            "# How to make a bot UI\n\nBuild a page the user clicks.",
            "# How to make a bot UI\n\nThe webhook routine lives in Cursor's Automations. In Grok CLI the `update_state` and `SendToUser` tools below do not exist, so ask the user to create the routine, copy its URL, and place the sender key in the server config themselves, then continue from **Host the page on this computer**.\n\nBuild a page the user clicks.",
        ),
    ],
    "typescript-best-practices/SKILL.md": [
        (
            "paths: [\"**/*.ts\", \"**/*.tsx\"]\ndisable-model-invocation: true\n",
            "paths: [\"**/*.ts\", \"**/*.tsx\"]\n",
        ),
    ],
}

# Substitutions inside scripts (same exact-match rule).
SCRIPT_SUBSTITUTIONS: dict[str, list[tuple[str, str]]] = {
    "poteto-mode/scripts/worktree-audit.sh": [
        (
            "# state, uncommitted work, remote/PR state, and the most recent chat that\n"
            "# operated in it. Emits a table sorted by size with a suggested bucket. Never\n"
            "# deletes anything; deletion stays a human-gated step in the playbook.\n",
            "# state, uncommitted work, remote/PR state, a live Grok CLI session inside it,\n"
            "# and the most recent chat that operated in it. Emits a table sorted by size\n"
            "# with a suggested bucket. Never deletes anything; deletion stays a\n"
            "# human-gated step in the playbook.\n",
        ),
        (
            "\t--json number,state,headRefName 2>/dev/null > \"$prs\" || echo \"[]\" > \"$prs\"\n",
            "\t--json number,state,headRefName,headRefOid 2>/dev/null > \"$prs\" || echo \"[]\" > \"$prs\"\n",
        ),
        (
            "# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts.\n"
            "slug=$(printf '%s' \"$main_wt\" | sed 's#^/##; s#/#-#g')\n"
            "transcripts=\"$HOME/.cursor/projects/$slug/agent-transcripts\"\n",
            "# Grok CLI sessions: ~/.grok/sessions/<cwd-key>/<session-id>/, where <cwd-key>\n"
            "# is the session's working directory URL-encoded. A key over 255 bytes is a\n"
            "# slug plus hash instead, with the original path in the group's .cwd file.\n"
            "grok_home=\"${GROK_HOME:-$HOME/.grok}\"\n"
            "sessions=\"$grok_home/sessions\"\n"
            "cwd_key() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=\"\"))' \"$1\"; }\n"
            "transcripts=\"$sessions/$(cwd_key \"$main_wt\")\"\n"
            "\n"
            "# Session groups whose working directory is $1 or inside it.\n"
            "session_groups() {\n"
            "\tlocal key; key=$(cwd_key \"$1\")\n"
            "\tfor g in \"$sessions/$key\" \"$sessions/$key\"%2F*; do [ -d \"$g\" ] && echo \"$g\"; done\n"
            "\tfor c in \"$sessions\"/*/.cwd; do\n"
            "\t\t[ -f \"$c\" ] || continue\n"
            "\t\tcase \"$(cat \"$c\")\" in \"$1\" | \"$1\"/*) dirname \"$c\" ;; esac\n"
            "\tdone\n"
            "}\n"
            "\n"
            "# Live Grok CLI sessions whose working directory is $1 or inside it.\n"
            "live_sessions() {\n"
            "\t[ -f \"$grok_home/active_sessions.json\" ] || return 0\n"
            "\tjq -r --arg wt \"$1\" '.[] | select(.cwd == $wt or (.cwd | startswith($wt + \"/\"))) | .pid' \\\n"
            "\t\t\"$grok_home/active_sessions.json\" 2>/dev/null | while read -r pid; do\n"
            "\t\tkill -0 \"$pid\" 2>/dev/null && echo \"$pid\"\n"
            "\tdone\n"
            "}\n",
        ),
        (
            "now=$(date +%s)\n"
            "\n",
            "now=$(date +%s)\n"
            "\n"
            "# GNU and BSD stat/date spell these differently; Grok CLI runs on both.\n"
            "mtime() { stat -c '%Y' \"$1\" 2>/dev/null || stat -f '%m' \"$1\" 2>/dev/null; }\n"
            "ymd() { date -d \"@$1\" '+%Y-%m-%d' 2>/dev/null || date -r \"$1\" '+%Y-%m-%d' 2>/dev/null; }\n"
            "\n",
        ),
        (
            "\t# Distinguish real WIP (tracked edits) from disposable untracked scratch.\n"
            "\tporcelain=$(git -C \"$wt\" status --porcelain 2>/dev/null)\n"
            "\tif [ -z \"$porcelain\" ]; then dirty=clean\n"
            "\telif printf '%s\\n' \"$porcelain\" | grep -qv '^??'; then\n"
            "\t\tdirty=\"wip:$(printf '%s\\n' \"$porcelain\" | grep -cv '^??')\"\n"
            "\telse dirty=\"scratch:$(printf '%s\\n' \"$porcelain\" | grep -c '^??')\"; fi\n",
            "\t# Removing the worktree deletes tracked edits, untracked files, and ignored\n"
            "\t# files alike, even an ignored build directory someone wrote a file into,\n"
            "\t# so every kind present is listed.\n"
            "\tporcelain=$(git -C \"$wt\" status --porcelain --ignored=matching 2>/dev/null)\n"
            "\tdirty=\"\"\n"
            "\tfor kind in 'wip:^[^?!]' 'untracked:^??' 'ignored:^!!'; do\n"
            "\t\tn=$(printf '%s\\n' \"$porcelain\" | grep -c \"${kind#*:}\")\n"
            "\t\t[ \"$n\" -gt 0 ] && dirty=\"${dirty:+$dirty,}${kind%%:*}:$n\"\n"
            "\tdone\n"
            "\t[ -z \"$dirty\" ] && dirty=clean\n",
        ),
        (
            "\tpr=$([ -n \"$branch\" ] && jq -r --arg b \"$branch\" \\\n"
            "\t\t'.[] | select(.headRefName==$b) | \"#\\(.number)/\\(.state)\"' \"$prs\" 2>/dev/null | head -1)\n",
            "\tpr_line=$([ -n \"$branch\" ] && jq -r --arg b \"$branch\" \\\n"
            "\t\t'.[] | select(.headRefName==$b) | \"#\\(.number)/\\(.state) \\(.headRefOid // \"\")\"' \"$prs\" 2>/dev/null | head -1)\n"
            "\tpr=${pr_line%% *}\n"
            "\tpr_head=${pr_line#* }\n",
        ),
        (
            "\t[ -z \"$pr\" ] && pr=\"-\"\n"
            "\n",
            "\t[ -z \"$pr\" ] && pr=\"-\"\n"
            "\tpr_contains=no\n"
            "\tif [ -n \"$pr_head\" ] && [ \"$pr_head\" != \"$pr\" ]; then\n"
            "\t\tgit -C \"$wt\" merge-base --is-ancestor \"$head\" \"$pr_head\" 2>/dev/null && pr_contains=yes\n"
            "\tfi\n"
            "\n",
        ),
        (
            "\tif [ -d \"$transcripts\" ]; then\n"
            "\t\tf=$(rg -l -e \"${wt}/\" -e \"${wt}\\\"\" \"$transcripts\" 2>/dev/null \\\n"
            "\t\t\t| xargs stat -f '%m %N' 2>/dev/null | sort -rn | head -1)\n"
            "\t\tif [ -n \"$f\" ]; then last_ts=$(echo \"$f\" | awk '{print $1}')\n"
            "\t\t\tlast=$(date -r \"$last_ts\" '+%Y-%m-%d' 2>/dev/null); fi\n"
            "\tfi\n",
            "\tf=$({ [ -d \"$transcripts\" ] && grep -rlF -e \"${wt}/\" -e \"${wt}\\\"\" \"$transcripts\" 2>/dev/null\n"
            "\t\tsession_groups \"$wt\" | while read -r g; do find \"$g\" -type f 2>/dev/null; done; } \\\n"
            "\t\t| while read -r t; do printf '%s %s\\n' \"$(mtime \"$t\")\" \"$t\"; done | sort -rn | head -1)\n"
            "\tif [ -n \"$f\" ]; then last_ts=$(echo \"$f\" | awk '{print $1}')\n"
            "\t\tlast=$(ymd \"$last_ts\"); fi\n",
        ),
        (
            "\trecent=$([ \"$last_ts\" -gt 0 ] 2>/dev/null && [ $(( (now - last_ts) / 86400 )) -le 4 ] && echo yes || echo no)\n"
            "\n",
            "\trecent=$([ \"$last_ts\" -gt 0 ] 2>/dev/null && [ $(( (now - last_ts) / 86400 )) -le 4 ] && echo yes || echo no)\n"
            "\t[ -n \"$(live_sessions \"$wt\")\" ] && { live=yes; last=live; } || live=no\n"
            "\n",
        ),
        (
            "\tcase \"$dirty\" in wip:*) bucket=hold-wip ;; *)\n"
            "\t\tcase \"$pr\" in *OPEN*) bucket=hold-open-pr ;; *)\n",
            "\t# A detached HEAD's commits live on no branch, so removing the worktree\n"
            "\t# strands them unless some ref still contains that commit.\n"
            "\tunreachable=no\n"
            "\t[ -z \"$branch\" ] && [ -z \"$(git -C \"$wt\" for-each-ref --contains \"$head\" --count=1 2>/dev/null)\" ] && unreachable=yes\n"
            "\n"
            "\tif [ \"$live\" = yes ]; then bucket=hold-live-session\n"
            "\telse case \"$dirty\" in wip:*) bucket=hold-wip ;; untracked:*) bucket=hold-untracked ;; ignored:*) bucket=hold-ignored ;; *)\n"
            "\t\tif [ \"$unreachable\" = yes ]; then bucket=hold-unreachable\n"
            "\t\telse case \"$pr\" in *OPEN*) bucket=hold-open-pr ;; *)\n",
        ),
        (
            "\t\t\telif [ \"$merged\" = YES ] || [ \"$pr\" != \"-\" ]; then bucket=safe\n",
            "\t\t\telif [ \"$merged\" = YES ] || { [ \"${pr##*/}\" = MERGED ] && [ \"$pr_contains\" = yes ]; }; then bucket=safe\n",
        ),
        (
            "\t\tesac ;;\n"
            "\tesac\n",
            "\t\tesac; fi ;;\n"
            "\tesac; fi\n",
        ),
    ],
    "poteto-mode/scripts/orch/orch.test.ts": [
        (
            "  it(\"blocks a writer and steals the pid lock only with force\", async () => {\n"
            "    const { directory, store } = await initializedStore();\n"
            "    await store.close();\n"
            "    await writeFile(join(directory, \".orch.lock\"), `${process.pid}\\n`);\n"
            "\n"
            "    const blocked = useStore(directory);\n"
            "    await expect(\n"
            "      blocked.units.add({ id: \"u1\", track: \"build\" })\n"
            "    ).rejects.toThrow(`store lock held by pid ${process.pid}`);\n"
            "\n"
            "    const stolen: string[] = [];\n"
            "    const forced = useStore(directory, {\n"
            "      force: true,\n"
            "      onLockStolen: (holder) => stolen.push(holder),\n"
            "    });\n"
            "    expect(\n"
            "      await forced.units.add({ id: \"u1\", track: \"build\" })\n"
            "    ).toMatchObject({ id: \"u1\" });\n"
            "    expect(stolen).toEqual([String(process.pid)]);\n"
            "    await forced.close();\n"
            "    expect(await readdir(directory)).not.toContain(\".orch.lock\");\n"
            "  });\n"
            "\n",
            "",
        ),
    ],
    "poteto-mode/scripts/watch-pr/policy.test.ts": [
        (
            "      [\"BLOCKED\", \"PENDING\", \"allowed\"],\n",
            "      [\"BLOCKED\", \"PENDING\", \"refused\"],\n"
            "      [\"BLOCKED\", \"SUCCESS\", \"refused\"],\n"
            "      [\"BLOCKED\", null, \"refused\"],\n",
        ),
    ],
    "poteto-mode/scripts/watch-pr/policy.ts": [
        (
            "    if (args.headRollupState === \"ERROR\" || args.headRollupState === \"FAILURE\")\n"
            "      return {\n"
            "        kind: \"refused\",\n"
            "        mergeStateStatus: args.mergeStateStatus,\n"
            "        headRollupState: args.headRollupState,\n"
            "      };\n",
            "",
        ),
        (
            "      kind: \"allowed\",\n"
            "      basis: \"rollup\",\n",
            "      kind: \"refused\",\n",
        ),
        (
            "    }),\n"
            "  };\n"
            "}\n"
            "const AUTOMATION_TOKENS = [\n",
            "    }),\n"
            "  };\n"
            "}\n"
            "// A pending human Code Review Gate also holds the head rollup pending, so with\n"
            "// one present only a required check that has not passed yet means CI is still\n"
            "// running. When the requirements cannot be read, report the merge gate.\n"
            "async function rollupAwaitsCi(\n"
            "  reader: T.GitHubReader,\n"
            "  facts: T.PullRequestFacts,\n"
            "  checks: readonly T.Check[]\n"
            "): Promise<boolean> {\n"
            "  if (!checks.some((check) => check.kind === \"code-review-gate\")) return true;\n"
            "  const required = await reader.requiredCheckNames(\n"
            "    facts.context,\n"
            "    facts.baseRefName\n"
            "  );\n"
            "  const awaited = required?.filter((name) => name !== \"Code Review Gate\");\n"
            "  if (!awaited?.length) return false;\n"
            "  const passed = await reader.passedRequiredChecks(facts.context);\n"
            "  if (passed === null) return false;\n"
            "  return awaited.some((name) => !passed.includes(name));\n"
            "}\n"
            "const AUTOMATION_TOKENS = [\n",
        ),
        (
            "    else if (merge.github.kind === \"refused\")\n",
            "    else if (\n"
            "      merge.github.kind === \"refused\" &&\n"
            "      (merge.github.headRollupState === \"FAILURE\" ||\n"
            "        merge.github.headRollupState === \"ERROR\")\n"
            "    )\n",
        ),
        (
            "      ci = { ...base, kind: \"ci-pending\", failed: [], pending };\n"
            "    else\n",
            "      ci = { ...base, kind: \"ci-pending\", failed: [], pending };\n"
            "    else if (\n"
            "      merge.github.kind === \"refused\" &&\n"
            "      (merge.github.headRollupState === \"PENDING\" ||\n"
            "        merge.github.headRollupState === \"EXPECTED\") &&\n"
            "      (await rollupAwaitsCi(args.reader, facts, checks.checks))\n"
            "    )\n"
            "      ci = {\n"
            "        ...base,\n"
            "        kind: \"ci-pending\",\n"
            "        failed: [],\n"
            "        pending: [\n"
            "          {\n"
            "            kind: \"pending\",\n"
            "            name: \"head commit status\",\n"
            "            reportedState: merge.github.headRollupState,\n"
            "            description: \"\",\n"
            "            link: \"\",\n"
            "            workflow: \"\",\n"
            "          },\n"
            "        ],\n"
            "      };\n"
            "    else if (merge.github.kind === \"refused\")\n"
            "      ci = {\n"
            "        ...base,\n"
            "        kind: \"ci-github-blocked\",\n"
            "        failed: [],\n"
            "        pending: [],\n"
            "        github: merge.github,\n"
            "      };\n"
            "    else\n",
        ),
        (
            "  return row.facts.reviewDecision === \"CHANGES_REQUESTED\"\n"
            "    ? \"changes-requested\"\n",
            "  if (row.facts.reviewDecision === \"CHANGES_REQUESTED\")\n"
            "    return \"changes-requested\";\n"
            "  if (row.kind === \"open\" && row.ci.kind === \"ci-github-blocked\")\n"
            "    return \"github-blocked\";\n"
            "  return row.kind === \"open\" &&\n"
            "    row.ci.kind === \"ci-clean\" &&\n"
            "    row.facts.reviewDecision === \"REVIEW_REQUIRED\"\n"
            "    ? \"review-required\"\n",
        ),
        (
            "  if (reviewDecision === \"CHANGES_REQUESTED\") return null;\n",
            "  if (\n"
            "    reviewDecision === \"CHANGES_REQUESTED\" ||\n"
            "    reviewDecision === \"REVIEW_REQUIRED\"\n"
            "  )\n"
            "    return null;\n",
        ),
    ],
    "poteto-mode/scripts/watch-pr/fakes.test-helper.ts": [
        (
            "  readonly commitRollups?: readonly CommitRollup[];\n"
            "  readonly openPullRequests?: readonly OpenPullRequest[];\n",
            "  readonly commitRollups?: readonly CommitRollup[];\n"
            "  readonly requiredCheckNames?: readonly string[] | null;\n"
            "  readonly passedRequiredChecks?: readonly string[] | null;\n"
            "  readonly openPullRequests?: readonly OpenPullRequest[];\n",
        ),
        (
            "    },\n"
            "  };\n",
            "    },\n"
            "    async requiredCheckNames() {\n"
            "      calls.push(\"requiredCheckNames\");\n"
            "      return options.requiredCheckNames ?? null;\n"
            "    },\n"
            "    async passedRequiredChecks() {\n"
            "      calls.push(\"passedRequiredChecks\");\n"
            "      return options.passedRequiredChecks ?? null;\n"
            "    },\n"
            "  };\n",
        ),
    ],
    "poteto-mode/scripts/watch-pr/types.ts": [
        (
            "  readonly headRollupState: \"ERROR\" | \"FAILURE\";\n",
            "  readonly headRollupState: RollupState;\n",
        ),
        (
            "    }\n"
            "  | {\n"
            "      readonly kind: \"allowed\";\n"
            "      readonly basis: \"rollup\";\n"
            "      readonly mergeStateStatus: \"BLOCKED\";\n"
            "      readonly headRollupState: Exclude<RollupState, \"ERROR\" | \"FAILURE\">;\n",
            "",
        ),
        (
            "};\n"
            "export type CiPending = CiBase & {\n",
            "};\n"
            "export type CiGithubBlocked = CiBase & {\n"
            "  readonly kind: \"ci-github-blocked\";\n"
            "  readonly failed: readonly [];\n"
            "  readonly pending: readonly [];\n"
            "  readonly github: GitHubMergeRefusal;\n"
            "};\n"
            "export type CiPending = CiBase & {\n",
        ),
        (
            "export type CiState = CiFailing | CiGithubRejected | CiPending | CiClean;\n",
            "export type CiState =\n"
            "  | CiFailing\n"
            "  | CiGithubRejected\n"
            "  | CiGithubBlocked\n"
            "  | CiPending\n"
            "  | CiClean;\n",
        ),
        (
            "      readonly reviewDecision: Exclude<ReviewDecision, \"CHANGES_REQUESTED\">;\n",
            "      readonly reviewDecision: Exclude<\n"
            "        ReviewDecision,\n"
            "        \"CHANGES_REQUESTED\" | \"REVIEW_REQUIRED\"\n"
            "      >;\n",
        ),
        (
            "  | \"changes-requested\";\n",
            "  | \"changes-requested\"\n"
            "  | \"github-blocked\"\n"
            "  | \"review-required\";\n",
        ),
        (
            "  commitRollups(context: PrContext): Promise<readonly CommitRollup[]>;\n"
            "}\n",
            "  commitRollups(context: PrContext): Promise<readonly CommitRollup[]>;\n"
            "  // Null when the branch's rulesets or protection cannot be read.\n"
            "  requiredCheckNames(\n"
            "    repository: Repository,\n"
            "    branch: string\n"
            "  ): Promise<readonly string[] | null>;\n"
            "  // Head-commit checks GitHub counts toward a required check and reports as\n"
            "  // passed, matched by GitHub itself so an app-pinned requirement is honoured.\n"
            "  // Null when they cannot be read.\n"
            "  passedRequiredChecks(context: PrContext): Promise<readonly string[] | null>;\n"
            "}\n",
        ),
    ],
    "poteto-mode/scripts/watch-pr/render.ts": [
        (
            "      return `❌ GitHub reports failing checks${was}`;\n"
            "    default: {\n",
            "      return `❌ GitHub reports failing checks${was}`;\n"
            "    case \"ci-github-blocked\":\n"
            "      return `✅ checks, GitHub blocks merge${was}`;\n"
            "    default: {\n",
        ),
        (
            "            : \"resolve the changes-requested review before waiting for the merge queue\";\n",
            "            : blocker.reason === \"changes-requested\"\n"
            "              ? \"resolve the changes-requested review before waiting for the merge queue\"\n"
            "              : blocker.reason === \"review-required\"\n"
            "                ? \"checks are green; get the required review approved, and rearm only after it changes\"\n"
            "                : \"checks are green; wait for the required approval or branch rule GitHub reports as BLOCKED, and rearm only after it changes\";\n",
        ),
    ],
    "poteto-mode/scripts/watch-pr/github.ts": [
        (
            "  \"\\nquery ReviewThreads($owner: String!, $repo: String!, $pr: Int!) {\\n  repository(owner: $owner, name: $repo) {\\n    pullRequest(number: $pr) {\\n      reviewThreads(first: 100) {\\n        nodes {\\n          id\\n          isResolved\\n          comments(first: 10) {\\n            nodes {\\n              body\\n              createdAt\\n              path\\n              line\\n              author { login }\\n            }\\n          }\\n        }\\n      }\\n    }\\n  }\\n}\\n\";\n",
            "  \"\\nquery ReviewThreads($owner: String!, $repo: String!, $pr: Int!, $after: String) {\\n  repository(owner: $owner, name: $repo) {\\n    pullRequest(number: $pr) {\\n      reviewThreads(first: 100, after: $after) {\\n        pageInfo { hasNextPage endCursor }\\n        nodes {\\n          id\\n          isResolved\\n          comments(first: 10) {\\n            nodes {\\n              body\\n              createdAt\\n              path\\n              line\\n              author { login }\\n            }\\n          }\\n        }\\n      }\\n    }\\n  }\\n}\\n\";\n",
        ),
        (
            "  \"\\nquery PrCheckRollup($owner: String!, $repo: String!, $pr: Int!, $after: String) {\\n  repository(owner: $owner, name: $repo) {\\n    pullRequest(number: $pr) {\\n      commits(last: 1) {\\n        nodes {\\n          commit {\\n            statusCheckRollup {\\n              contexts(first: 100, after: $after) {\\n                pageInfo {\\n                  hasNextPage\\n                  endCursor\\n                }\\n                nodes {\\n                  __typename\\n                  ... on CheckRun {\\n                    name\\n                    status\\n                    conclusion\\n                    detailsUrl\\n                  }\\n                  ... on StatusContext {\\n                    context\\n                    state\\n                    targetUrl\\n                  }\\n                }\\n              }\\n            }\\n          }\\n        }\\n      }\\n    }\\n  }\\n}\\n\";\n"
            "\n",
            "  \"\\nquery PrCheckRollup($owner: String!, $repo: String!, $pr: Int!, $after: String) {\\n  repository(owner: $owner, name: $repo) {\\n    pullRequest(number: $pr) {\\n      commits(last: 1) {\\n        nodes {\\n          commit {\\n            statusCheckRollup {\\n              contexts(first: 100, after: $after) {\\n                pageInfo {\\n                  hasNextPage\\n                  endCursor\\n                }\\n                nodes {\\n                  __typename\\n                  ... on CheckRun {\\n                    name\\n                    status\\n                    conclusion\\n                    detailsUrl\\n                  }\\n                  ... on StatusContext {\\n                    context\\n                    state\\n                    targetUrl\\n                  }\\n                }\\n              }\\n            }\\n          }\\n        }\\n      }\\n    }\\n  }\\n}\\n\";\n"
            "export const PR_REQUIRED_CHECKS_QUERY =\n"
            "  \"\\nquery PrRequiredChecks($owner: String!, $repo: String!, $pr: Int!, $after: String) {\\n  repository(owner: $owner, name: $repo) {\\n    pullRequest(number: $pr) {\\n      commits(last: 1) {\\n        nodes {\\n          commit {\\n            statusCheckRollup {\\n              contexts(first: 100, after: $after) {\\n                pageInfo {\\n                  hasNextPage\\n                  endCursor\\n                }\\n                nodes {\\n                  __typename\\n                  ... on CheckRun {\\n                    name\\n                    status\\n                    conclusion\\n                    isRequired(pullRequestNumber: $pr)\\n                  }\\n                  ... on StatusContext {\\n                    context\\n                    state\\n                    isRequired(pullRequestNumber: $pr)\\n                  }\\n                }\\n              }\\n            }\\n          }\\n        }\\n      }\\n    }\\n  }\\n}\\n\";\n"
            "\n",
        ),
        (
            "\n"
            "export class GhGitHubReader implements T.GitHubReader {\n",
            "\n"
            "export function parseRequiredCheckNames(\n"
            "  rules: unknown,\n"
            "  branch: unknown\n"
            "): readonly string[] {\n"
            "  const names = new Set<string>();\n"
            "  const add = (check: unknown, path: string) =>\n"
            "    names.add(string(record(check, path).context, `${path}.context`));\n"
            "  for (const item of list(rules, \"branch rules\")) {\n"
            "    const rule = record(item, \"branch rule\");\n"
            "    if (rule.type === \"required_status_checks\")\n"
            "      for (const check of list(\n"
            "        at(rule, [\"parameters\", \"required_status_checks\"]),\n"
            "        \"branch rule.parameters.required_status_checks\"\n"
            "      ))\n"
            "        add(check, \"branch rule required check\");\n"
            "  }\n"
            "  const protection = record(branch, \"branch\").protection;\n"
            "  if (protection === undefined) return [...names];\n"
            "  const required = record(protection, \"branch.protection\")\n"
            "    .required_status_checks;\n"
            "  if (required === undefined) return [...names];\n"
            "  const classic = record(required, \"branch.protection.required_status_checks\");\n"
            "  for (const context of list(classic.contexts ?? [], \"required contexts\"))\n"
            "    names.add(string(context, \"required context\"));\n"
            "  for (const check of list(classic.checks ?? [], \"required checks\"))\n"
            "    add(check, \"required check\");\n"
            "  return [...names];\n"
            "}\n"
            "\n"
            "export class GhGitHubReader implements T.GitHubReader {\n",
        ),
        (
            "    return parseReviewThreads(\n"
            "      await runJson(graphqlArgs(REVIEW_THREADS_QUERY, context))\n"
            "    );\n",
            "    const nodes: unknown[] = [];\n"
            "    const cursors = new Set<string>();\n"
            "    let after: string | null = null;\n"
            "    do {\n"
            "      const argv = graphqlArgs(REVIEW_THREADS_QUERY, context);\n"
            "      if (after !== null) argv.push(\"-f\", `after=${after}`);\n"
            "      const value = await runJson(argv);\n"
            "      const connection = record(\n"
            "        at(value, [\"data\", \"repository\", \"pullRequest\", \"reviewThreads\"]),\n"
            "        \"reviewThreads\"\n"
            "      );\n"
            "      nodes.push(...list(connection.nodes, \"reviewThreads.nodes\"));\n"
            "      const page = record(connection.pageInfo, \"reviewThreads.pageInfo\");\n"
            "      if (typeof page.hasNextPage !== \"boolean\")\n"
            "        missing(\"reviewThreads.pageInfo.hasNextPage\", page.hasNextPage);\n"
            "      after = page.hasNextPage\n"
            "        ? string(page.endCursor, \"reviewThreads.pageInfo.endCursor\") : null;\n"
            "      if (after !== null) {\n"
            "        if (!after || cursors.has(after))\n"
            "          missing(\"reviewThreads pagination cursor\", after);\n"
            "        cursors.add(after);\n"
            "      }\n"
            "    } while (after !== null);\n"
            "    return parseReviewThreads({\n"
            "      data: { repository: { pullRequest: { reviewThreads: { nodes } } } },\n"
            "    });\n",
        ),
        (
            "      };\n"
            "    });\n"
            "  }\n"
            "}\n",
            "      };\n"
            "    });\n"
            "  }\n"
            "  async requiredCheckNames(\n"
            "    repository: T.Repository,\n"
            "    branch: string\n"
            "  ): Promise<readonly string[] | null> {\n"
            "    const base = `repos/${repository.owner}/${repository.repo}`;\n"
            "    const encoded = encodeURIComponent(branch);\n"
            "    try {\n"
            "      const [rules, protection] = await Promise.all([\n"
            "        runJson([\n"
            "          \"gh\",\n"
            "          \"api\",\n"
            "          `${base}/rules/branches/${encoded}?per_page=100`,\n"
            "        ]),\n"
            "        runJson([\"gh\", \"api\", `${base}/branches/${encoded}`]),\n"
            "      ]);\n"
            "      return parseRequiredCheckNames(rules, protection);\n"
            "    } catch (error) {\n"
            "      if (error instanceof WatcherQueryError) return null;\n"
            "      throw error;\n"
            "    }\n"
            "  }\n"
            "  async passedRequiredChecks(\n"
            "    context: T.PrContext\n"
            "  ): Promise<readonly string[] | null> {\n"
            "    const passed: string[] = [];\n"
            "    let after: string | null = null;\n"
            "    try {\n"
            "      do {\n"
            "        const argv = graphqlArgs(PR_REQUIRED_CHECKS_QUERY, context);\n"
            "        if (after !== null) argv.push(\"-f\", `after=${after}`);\n"
            "        const commits = list(\n"
            "          at(await runJson(argv), [\n"
            "            \"data\",\n"
            "            \"repository\",\n"
            "            \"pullRequest\",\n"
            "            \"commits\",\n"
            "            \"nodes\",\n"
            "          ]),\n"
            "          \"commits.nodes\"\n"
            "        );\n"
            "        const rollup =\n"
            "          commits.length === 0\n"
            "            ? null\n"
            "            : at(commits[commits.length - 1], [\"commit\", \"statusCheckRollup\"]);\n"
            "        if (rollup === null) return passed;\n"
            "        const contexts = record(at(rollup, [\"contexts\"]), \"contexts\");\n"
            "        for (const item of list(contexts.nodes, \"contexts.nodes\")) {\n"
            "          const node = record(item, \"required check context\");\n"
            "          if (node.isRequired !== true) continue;\n"
            "          if (\n"
            "            node.__typename === \"CheckRun\" &&\n"
            "            node.status === \"COMPLETED\" &&\n"
            "            [\"SUCCESS\", \"NEUTRAL\", \"SKIPPED\"].includes(String(node.conclusion))\n"
            "          )\n"
            "            passed.push(string(node.name, \"CheckRun.name\"));\n"
            "          if (node.__typename === \"StatusContext\" && node.state === \"SUCCESS\")\n"
            "            passed.push(string(node.context, \"StatusContext.context\"));\n"
            "        }\n"
            "        const page = record(contexts.pageInfo, \"contexts.pageInfo\");\n"
            "        after =\n"
            "          page.hasNextPage === true\n"
            "            ? string(page.endCursor, \"contexts.pageInfo.endCursor\")\n"
            "            : null;\n"
            "      } while (after !== null);\n"
            "      return passed;\n"
            "    } catch (error) {\n"
            "      if (error instanceof WatcherQueryError) return null;\n"
            "      throw error;\n"
            "    }\n"
            "  }\n"
            "}\n",
        ),
    ],
    "poteto-mode/scripts/orch/store.ts": [
        (
            "import { randomUUID } from \"node:crypto\";\n",
            "import { createHash, randomUUID } from \"node:crypto\";\n",
        ),
        (
            "  access,\n"
            "  mkdir,\n",
            "  access,\n"
            "  link,\n"
            "  mkdir,\n",
        ),
        (
            "  open,\n",
            "",
        ),
        (
            "  readonly force?: boolean;\n"
            "  readonly onLockStolen?: (holder: string) => void;\n",
            "",
        ),
        (
            "  const pid = String(process.pid);\n"
            "  const create = async (): Promise<void> => {\n"
            "    const handle = await open(path, \"wx\");\n"
            "    await handle.writeFile(`${pid}\\n`);\n"
            "    await handle.close();\n"
            "  };\n"
            "\n"
            "  const takeOver = async (): Promise<void> => {\n"
            "    await unlink(path);\n",
            "  const token = `${process.pid}\\n${randomUUID()}\\n`;\n"
            "  for (let attempt = 0; attempt < 3; attempt += 1) {\n"
            "    if (await createLock(path, token)) {\n"
            "      return () => unlinkIfContains(path, token);\n"
            "    }\n"
            "    let contents: string;\n",
        ),
        (
            "      await create();\n"
            "    } catch (retryError) {\n"
            "      if (errorCode(retryError) === \"EEXIST\") {\n"
            "        const retryHolder =\n"
            "          (await readFile(path, \"utf8\")).trim() || \"unknown\";\n"
            "        throw new UserError(`store lock held by pid ${retryHolder}`);\n"
            "      }\n"
            "      throw retryError;\n"
            "    }\n"
            "  };\n"
            "\n"
            "  try {\n"
            "    await create();\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) !== \"EEXIST\") {\n",
            "      contents = await readFile(path, \"utf8\");\n"
            "    } catch (error) {\n"
            "      if (errorCode(error) === \"ENOENT\") continue;\n",
        ),
        (
            "    let holder = \"unknown\";\n",
            "    const holder = contents.trim().split(\"\\n\")[0] || \"unknown\";\n"
            "    if (!holderIsDead(holder))\n"
            "      throw new UserError(`store lock held by pid ${holder}; a live or unknown owner cannot be stolen, even with --force`);\n"
            "    options.onStaleLock?.(holder);\n"
            "    await removeStale(path, contents, token);\n"
            "  }\n"
            "  throw new UserError(\"store lock kept changing hands; retry\");\n"
            "}\n"
            "\n"
            "// The lock appears with its owner already written, so a crash at any point\n"
            "// leaves either no lock or one whose dead owner a later writer can clear.\n"
            "async function createLock(path: string, token: string): Promise<boolean> {\n"
            "  const staged = `${path}.${randomUUID()}`;\n"
            "  await writeFile(staged, token, { flag: \"wx\" });\n"
            "  try {\n"
            "    await link(staged, path);\n"
            "    return true;\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) === \"EEXIST\") return false;\n"
            "    throw error;\n"
            "  } finally {\n"
            "    await rm(staged, { force: true });\n"
            "  }\n"
            "}\n"
            "\n"
            "// Removes `path` only while it still holds the dead owner's `stale` contents.\n"
            "// A claim named after those contents lets exactly one writer remove them, so\n"
            "// nobody can check them, lose a race, and then delete a live lock instead.\n"
            "// A claim left by a writer that died holding it is cleared the same way.\n"
            "async function removeStale(\n"
            "  path: string,\n"
            "  stale: string,\n"
            "  token: string\n"
            "): Promise<void> {\n"
            "  const digest = createHash(\"sha256\").update(stale).digest(\"hex\");\n"
            "  const claim = join(dirname(path), `${LOCK_FILE}.claim-${digest.slice(0, 32)}`);\n"
            "  if (await createLock(claim, token)) {\n",
        ),
        (
            "      holder = (await readFile(path, \"utf8\")).trim() || \"unknown\";\n"
            "    } catch {\n"
            "      holder = \"unknown\";\n",
            "      await unlinkIfContains(path, stale);\n"
            "    } finally {\n"
            "      await unlinkIfContains(claim, token);\n",
        ),
        (
            "    if (holderIsDead(holder)) {\n"
            "      options.onStaleLock?.(holder);\n"
            "      await takeOver();\n"
            "    } else if (options.force) {\n"
            "      options.onLockStolen?.(holder);\n"
            "      await takeOver();\n"
            "    } else {\n"
            "      throw new UserError(`store lock held by pid ${holder}`);\n"
            "    }\n",
            "    return;\n",
        ),
        (
            "  }\n"
            "\n",
            "  }\n"
            "  let claimant: string;\n"
            "  try {\n"
            "    claimant = await readFile(claim, \"utf8\");\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) === \"ENOENT\") return;\n"
            "    throw error;\n"
            "  }\n"
            "  if (holderIsDead(claimant.split(\"\\n\")[0] ?? \"\"))\n"
            "    await removeStale(claim, claimant, token);\n"
            "}\n"
            "\n",
        ),
        (
            "  return async (): Promise<void> => {\n"
            "    try {\n"
            "      if ((await readFile(path, \"utf8\")).trim() === pid) {\n"
            "        await unlink(path);\n"
            "      }\n"
            "    } catch (error) {\n"
            "      if (errorCode(error) !== \"ENOENT\") {\n"
            "        throw error;\n"
            "      }\n"
            "    }\n"
            "  };\n",
            "// Safe without a compare-and-delete because only the file's owner, or the one\n"
            "// writer holding the claim on a dead owner's contents, ever removes it.\n"
            "async function unlinkIfContains(path: string, contents: string): Promise<void> {\n"
            "  try {\n"
            "    if ((await readFile(path, \"utf8\")) === contents) await unlink(path);\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) !== \"ENOENT\") throw error;\n"
            "  }\n",
        ),
    ],
    "poteto-mode/scripts/orch/orch.ts": [
        (
            "    force: options.force,\n"
            "    onLockStolen: (holder) =>\n"
            "      io.stderr(`stealing store lock held by pid ${holder}\\n`),\n",
            "",
        ),
        (
            "    .option(\"--force\", \"steal an existing store lock\", false);\n",
            "    .option(\"--force\", \"legacy option; live or unknown lock owners are never stolen\", false);\n",
        ),
    ],
}

AGENTS = {
    "poteto-agent.md": """---
name: poteto-agent
description: Routing target for `/poteto-mode` and any request for poteto's style. Resume an existing `poteto-agent` with `resume_from` rather than spawning a sibling. Reads the `poteto-mode` skill's `SKILL.md` in full before any work, including its inline Principles index. Substituting a bare `general-purpose` prompt skips that read and drifts.
prompt_mode: full
model: inherit
permission_mode: default
agents_md: false
---

# Poteto subagent

You are operating as poteto-mode's full agent style. If the Poteto mode text is not in your context, read `.grok/skills/poteto-mode/SKILL.md` in full before doing any work, including its inline Principles index. Navigate to a leaf `principle-*` skill (`.grok/skills/principle-<name>/SKILL.md`) whenever you apply that principle.

""" + NESTING + """
""",
}

COMMENT_SICKO_FRONTMATTER = (
    "---\nname: Comment Sicko\ndescription: A deranged comment-hater that savors deletion and condemns workaround code.\n---\n",
    "---\nname: comment-sicko\ndescription: Comment Sicko. A deranged comment-hater that savors deletion and condemns workaround code.\nprompt_mode: full\nmodel: inherit\npermission_mode: default\nagents_md: false\n---\n",
)
COMMENT_SICKO_HOWWHY = (
    "I run `/how`, `/why`, or both from the **how** and **why** skills on the named symbol or call.",
    "I run `/how`, `/why`, or both from the **how** and **why** skills (`.grok/skills/how/SKILL.md`, `.grok/skills/why/SKILL.md`) on the named symbol or call. " + NESTING,
)

PSTACK_AGENTS_WORKFLOW = """let meta = #{
    name: "pstack-agents",
    description: "Run pstack subagents in parallel, each with its own model, read-only mode, and worktree choice",
    when_to_use: "A pstack skill spawns a subagent that sets a model or Cursor's readonly: true",
    phases: [ #{ title: "Agents" } ],
};

let entries = if args == () { () } else { args.agents };
if entries == () || type_of(entries) != "array" || entries.len() == 0 {
    pause("verification", "Pass args.agents: a list of { label, prompt, model, readonly, worktree }.");
}

phase("Agents");
let jobs = [];
for e in entries {
    let job = #{
        prompt: e.prompt,
        label: e.label,
        capability_mode: if e.readonly == true { "read-only" } else { "all" },
    };
    if e.model != () && e.model != "" && e.model != "auto" && e.model != "inherit-parent" {
        job.model = e.model;
    }
    if e.worktree == true {
        job.isolation_worktree = true;
    }
    jobs.push(job);
}
let results = parallel(jobs);

let report = [];
let i = 0;
for r in results {
    if r == () {
        report.push(#{ label: entries[i].label, success: false, output: "the agent did not start" });
    } else {
        report.push(#{ label: entries[i].label, agent_id: r.agent_id, success: r.success, output: r.output });
    }
    i += 1;
}
complete(#{ agents: report });
"""

SETUP_PSTACK = """---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget. Detects the models this Grok CLI session can run subagents on and writes an always-applied personal rule that overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.grok/rules/pstack-models.md`, a personal rule Grok CLI loads in every session, that sets pstack's model per role.
That path is Grok CLI's own. It lives outside every repository, so no commit picks it up. Never write Cursor's `~/.cursor/rules/pstack-models.mdc` or Claude Code's `~/.claude/rules/pstack-models.md` from here, and ignore both when reading current state.

## Steps

### 1. Detect available models

Run `grok models` and take the model slugs it lists (such as `grok-4.7`, `grok-4.7-build-fast`, `grok-4.6`). That is the dependable source. If it prints nothing usable, ask the user to paste the slugs they have access to. Never write a slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.

### 2. Load current state

The default role-to-model mapping is the rule shape shown in step 5 below. If `~/.grok/rules/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer `ask_user_question` over free text. Offer these four options with these exact labels, and name the current budget when the rule records one.

- `unlimited - keep max`
- `large - xhigh reasoning`
- `medium - high reasoning`
- `small - medium reasoning`

**(b) Apply it.** Build the working table from the skill defaults, and on a re-run keep any role you changed by slug, list, or `inherit-parent` and `auto`. In Grok CLI the reasoning budget is the session effort level, which subagents inherit, so the budget does not change the model values. `unlimited` and `large` are effort `xhigh`, `medium` is `high`, and `small` is `medium`. Record the label and its effort on the `# budget` line. `inherit-parent` and `auto` do not change.

**(c) Show the roles and confirm.** Show every role with its model, marking any slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent chat model, which keeps a session on whatever model the user picked) as the options. Prefer `ask_user_question` over free text. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it that differs from the parent's model when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen slug is not available, stop and ask again.

### 5. Write the rule

Write `~/.grok/rules/pstack-models.md` with a `# budget` line carrying the chosen label and its target effort, and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent chat model (omit `model` on the pstack-agents entry). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited (xhigh)
feature, refactoring: grok-4.7-build-fast
bug-fix: grok-4.7-build-fast
perf-issue: grok-4.7-build-fast
hillclimb: grok-4.7-build-fast
judgment and prose: grok-4.7
hardest tasks: grok-4.7
how explorer: grok-4.7-build-fast
how explainer: grok-4.7
why investigators: grok-4.7-build-fast
why synthesizer: grok-4.7
reflect tooling: grok-4.6
reflect judgment, divergent, synthesizer: grok-4.7
arena runners: grok-4.7, grok-4.6, grok-4.7-build-fast
arena cross-judge pool: grok-4.7, grok-4.6, grok-4.7-build-fast
swarm workers: grok-4.7-build-fast
architect runners: grok-4.7, grok-4.6, grok-4.7-build-fast
interrogate reviewers: grok-4.7, grok-4.6, grok-4.7-build-fast
```

### 6. Apply the budget and confirm

The budget's effort is a session setting. Tell the user to set it with `/effort <level>`, which you cannot type for them. Tell the user the rule was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Nesting depth

""" + NESTING + """ Read `[subagents] max_depth` in `~/.grok/config.toml`. When the key is missing or below 3, set `max_depth = 3` under `[subagents]`: merge that one key into the file, creating the table when it is missing and keeping every other key and table. Tell the user it applies to new sessions.

### 8. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, read `.grok/skills/create-verification-skill/SKILL.md` and follow it. On no, move on without pushing.
"""


MODEL_BLOCK = re.compile(r"^disable-model-invocation\s*:\s*[\"']?true[\"']?\s*$", re.IGNORECASE)


def drop_model_invocation_block(skills: Path) -> None:
    """Drop upstream `disable-model-invocation` from each skill frontmatter."""
    for md in skills.glob("*/SKILL.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 3)
        if end < 0:
            continue
        lines = text[:end].split("\n")
        if not any(MODEL_BLOCK.match(item) for item in lines):
            continue
        kept = [item for item in lines if not MODEL_BLOCK.match(item)]
        md.write_text("\n".join(kept) + text[end:], encoding="utf-8")
    for md in skills.glob("*/SKILL.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 3)
        if end < 0:
            raise SystemExit(f"{md}: skill frontmatter has no closing marker")
        if any(MODEL_BLOCK.match(item) for item in text[:end].split("\n")):
            raise SystemExit(f"{md}: disable-model-invocation still set")


def apply(path: Path, pairs: list[tuple[str, str]]) -> str:
    """Return the rewritten file. Callers write only after every file validates."""
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected exactly one match, found {count} for:\n{old[:160]!r}")
        text = text.replace(old, new)
    return text


def replace_dir(src: Path, dest: Path) -> Path | None:
    """Swap dest for src. Returns the backup of the previous dest, if there was one.

    A leftover ``*.port-backup`` is the only copy of a pack from a refresh that
    stopped after moving the old tree aside. Restore it when the destination is
    missing, and never delete it before the new tree is in place.
    """
    backup = dest.with_name(dest.name + ".port-backup")
    if backup.exists() and not dest.exists():
        backup.rename(dest)
    if backup.exists():
        preserved = dest.with_name(dest.name + ".port-backup-preserved")
        n = 1
        while preserved.exists():
            preserved = dest.with_name(f"{dest.name}.port-backup-preserved-{n}")
            n += 1
        backup.rename(preserved)
    if dest.exists():
        dest.rename(backup)
    try:
        src.rename(dest)
    except Exception:
        if backup.exists() and not dest.exists():
            backup.rename(dest)
        raise
    return backup if backup.exists() else None


def local_pack_files(pack: Path) -> tuple[list[str], list[str]]:
    """Pack paths git holds no copy of, relative to the pack: (uncommitted or untracked, ignored)."""
    dirs = [name for name in PACK_DIRS if (pack / name).exists()]
    if not dirs:
        return [], []
    prefix = subprocess.run(["git", "-C", str(pack), "rev-parse", "--show-prefix"], capture_output=True, text=True)
    result = subprocess.run(
        ["git", "-C", str(pack), "status", "--porcelain", "-z", "--untracked-files=all", "--ignored=matching", "--", *dirs],
        capture_output=True,
        text=True,
    )
    if prefix.returncode != 0 or result.returncode != 0:
        raise SystemExit(f"refusing to replace {pack}: git status failed there\n{prefix.stderr}{result.stderr}")
    root = prefix.stdout.strip()
    unsaved: list[str] = []
    ignored: list[str] = []
    entries = iter(result.stdout.split("\0"))
    for entry in entries:
        if not entry:
            continue
        (ignored if entry.startswith("!! ") else unsaved).append(entry[3:].rstrip("/").removeprefix(root))
        if "R" in entry[:2] or "C" in entry[:2]:
            next(entries, None)
    return unsaved, ignored


def carry_ignored(pack: Path, ignored: list[str], staging: Path) -> None:
    """Copy ignored pack files into the new pack so the swap keeps them."""
    for rel in ignored:
        src = pack / rel
        dest = staging / rel
        if dest.exists() or dest.is_symlink():
            raise SystemExit(f"refusing to replace {pack}: the new pack ships {rel}, which is ignored local work; move it first")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)


def build_pack(upstream: Path, staging: Path) -> int:
    skills = staging / "skills"
    agents = staging / "agents"
    workflows = staging / "workflows"
    for d in (skills, agents, workflows):
        d.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        upstream / "skills",
        skills,
        dirs_exist_ok=True,
        ignore=lambda src, names: [n for n in names if Path(src) == upstream / "skills" and n in EXCLUDED_SKILLS],
    )
    for f in (upstream / "agents").glob("*.md"):
        shutil.copy2(f, agents / f.name)

    applied = 0
    rewritten: list[tuple[Path, str]] = []
    for rel, pairs in {**SUBSTITUTIONS, **SCRIPT_SUBSTITUTIONS}.items():
        rewritten.append((skills / rel, apply(skills / rel, pairs)))
        applied += len(pairs)
    rewritten.append(
        (
            agents / "comment-sicko.md",
            apply(agents / "comment-sicko.md", [COMMENT_SICKO_FRONTMATTER, COMMENT_SICKO_HOWWHY]),
        )
    )
    applied += 2
    for path, text in rewritten:
        path.write_text(text, encoding="utf-8")
    for name, body in AGENTS.items():
        (agents / name).write_text(body, encoding="utf-8")
    for src in sorted(ADDED_SKILL_FILES.rglob("*")):
        if src.is_file():
            dest = skills / src.relative_to(ADDED_SKILL_FILES)
            if dest.exists():
                raise SystemExit(f"{dest}: upstream now ships this file; port it with a substitution instead")
            shutil.copy2(src, dest)

    (skills / "setup-pstack" / "SKILL.md").write_text(SETUP_PSTACK, encoding="utf-8")
    drop_model_invocation_block(skills)
    (workflows / "pstack-agents.rhai").write_text(PSTACK_AGENTS_WORKFLOW, encoding="utf-8")
    return applied


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    upstream = Path(sys.argv[1]).resolve()
    grok = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else DEFAULT_PACK
    grok.mkdir(parents=True, exist_ok=True)
    unsaved, ignored = local_pack_files(grok)
    if unsaved:
        raise SystemExit(
            f"refusing to replace {grok} over uncommitted or untracked files; commit or move them first:\n"
            + "\n".join(unsaved)
        )
    staging_root = Path(tempfile.mkdtemp(prefix=".grok-port-", dir=grok.parent))
    backups: list[Path] = []
    try:
        applied = build_pack(upstream, staging_root)
        carry_ignored(grok, ignored, staging_root)
        for name in PACK_DIRS:
            backup = replace_dir(staging_root / name, grok / name)
            if backup is not None:
                backups.append(backup)
    except Exception:
        for backup in reversed(backups):
            dest = backup.with_name(backup.name.removesuffix(".port-backup"))
            if dest.exists():
                shutil.rmtree(dest)
            backup.rename(dest)
        raise
    else:
        for backup in backups:
            shutil.rmtree(backup)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)

    print(f"ported {applied} substitutions into {grok}")


if __name__ == "__main__":
    main()
