#!/usr/bin/env python3
"""Port the Cursor pstack plugin into the Claude Code pack.

Usage: port.py <upstream-pstack-checkout> [pack-dir]

The pack directory defaults to claude/pack/ in this repository and holds what a
project installs under its .claude/ directory.

Builds the whole pack in a staging directory: copies upstream skills/ and
agents/, then applies every Cursor-to-Claude-Code substitution below. Each
substitution names the exact upstream text it replaces and fails loudly when
that text is missing or ambiguous, so a refresh against a newer upstream
cannot silently skip a mapping. The poteto-mode skill is then reordered so
compaction keeps its playbooks, and its Cursor reminder becomes two
generated settings hooks. Each principle skill's description is cut to its
first sentence, so the skill listing has room for every skill. Files the
pack adds on top of upstream (settings, the mode hooks, the Claude Code
reference, the read-only agent, setup-pstack) are written whole, each
playbook is also written as a static playbook-<name> skill so that a
Skill-tool load carries its steps across compaction, and the regression
tests for the pack's safety fixes are copied from added-skills/. The
staged pack is then validated, and only
a pack that passes replaces the installed skills, agents, mode hooks, and
settings in the pack directory and removes the retired skills an earlier
pack produced, which RETIRED_SKILLS recognizes by their contents. A failed
refresh leaves the installed pack exactly as it was.

The read-only agent's search hook, hooks/pstack-readonly-search.py, is
maintained by hand and is only checked for presence here.

Upstream pin: cursor/plugins, pstack 0.15.8, commit
a58628271271837ef5f386adca29c0812683a19a.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

TRANSCRIPT_DIR = (
    "Transcripts for this working directory live under `~/.claude/projects/<slug>/`, "
    "where `<slug>` is the working directory path with every character other than a letter or digit turned into `-` "
    "(so `/Users/you/my.proj` becomes `-Users-you-my-proj`)."
)
NO_GLOB = (
    "Do not glob across `~/.claude/projects/*/`. That crosses workspace boundaries and reads "
    "private chats from unrelated projects."
)
REJECT_ALIAS = (
    "If the Agent tool rejects a value, use the default and say so. If it rejects the "
    "default, use the closest available alias from its error message."
)
SKILL_CREATOR = (
    "the **skill-creator** skill (Anthropic's skill for authoring SKILL.md files, from "
    "the `anthropics/skills` repository, installed as a plugin or a synced skill)"
)
# Commands run from the repository root, so playbooks name scripts by their installed path.
POTETO_SCRIPTS = ".claude/skills/poteto-mode/scripts"
ADDED_SKILL_FILES = Path(__file__).resolve().parent / "added-skills"
DEFAULT_PACK = Path(__file__).resolve().parents[1] / "pack"

class RetiredSkill(NamedTuple):
    reason: str
    digests: frozenset[str]


# Skills an earlier pack produced that a refresh removes: the reason an installed copy must not stay, and the SHA-256 of
# each SKILL.md the pack shipped under the name (`git show <commit>:claude/pack/skills/<name>/SKILL.md | sha256sum`).
# A skill with that name and other contents is the project's own, so a refresh keeps it and lists it with the kept skills.
RETIRED_SKILLS = {
    "playbook": RetiredSkill(
        "it ran a shell command while it loaded, and Claude Code pastes the skill's arguments into that command as typed",
        frozenset(
            {
                "1bd332f725136378b4227414419bf17a396c27d1344f7d35218f914d892a9adf",  # f16c297
                "dc01de2e733a25ff5c11302ad5b8d21d096633dd6d8a44bf193c3e835ad829df",  # d3c6801
            }
        ),
    ),
}
SEARCH_TOOLS = "Glob and Grep when this session has them, otherwise `rg`, `grep`, or `find` the way your agent definition describes"


def pin(spawn: str) -> str:
    return f"Pass `model: sonnet` on {spawn}. Omitting `model` is wrong, because the call inherits the parent model."


def role_lines(subject: str, exception: str) -> str:
    return (
        f"{subject} a role line in the `~/.claude/rules/pstack-models.md` rule and a default. Set `model` to that line's value, "
        "or to the default if the rule or the line is missing. "
        f"Leave `model` unset when the value is `auto` or `inherit-parent`, except on {exception}. {REJECT_ALIAS}"
    )

# (relative path under .claude/skills, [(old, new), ...]); each old must occur exactly once.
SUBSTITUTIONS: dict[str, list[tuple[str, str]]] = {
    "poteto-mode/SKILL.md": [
        (
            "- About to `AskQuestion` on a",
            "- About to `AskUserQuestion` on a",
        ),
        (
            "Agent-facing prose also follows the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            f"Agent-facing prose also follows {SKILL_CREATOR} when it is installed, and the Claude Code skills reference (https://code.claude.com/docs/en/skills) otherwise.",
        ),
        (
            "- Before commit → the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`).",
            "- Before commit → Claude Code's bundled `simplify` skill (`/simplify`), the slop-strip.",
        ),
        (
            "- Shipping UI / IDE / CLI → the matching control skill. `cursor-team-kit` publishes `control-cli` (CLIs and TUIs) and `control-ui` (browser / Electron / web UIs).",
            "- Shipping UI / IDE / CLI → the matching control skill. Claude Code bundles `/run` and `/verify`, which build, launch, and drive the project's app (`/run-skill-generator` teaches them a new project), and the **create-verification-skill** skill generates a project `verify-<app>` skill for a surface they do not cover.",
        ),
        (
            "and not Cursor's built-in babysit skill, whose description matches the same words.",
            "and not any other babysit skill whose description matches the same words.",
        ),
        (
            ("**Use `subagent_type: \"poteto-agent\"` for any subagent you spawn inside a playbook step** (code-writing delegates, ad-hoc helpers). `/poteto-mode` and `poteto-agent` route through the same wrapper. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own `subagent_type` for diverse-model review. Respect what the skill prescribes, don't override to `poteto-agent`.\n"
            "\n"
            "**Defaults for every `Task` call.** `run_in_background: true`, agent mode (readonly strips MCP), file pointers not inlined context, explicit model per role (configurable via `/setup-pstack`. Defaults `grok-4.7-xhigh-fast` for code, `claude-opus-5-5-max` for prose and judgment). Code delegates tier by difficulty. The hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`claude-opus-5-5-max`), whether the task needs judgment on vague intent or is a precisely specified sequence of steps to execute to the letter. Trivial mechanical edits go to your fast code model. Per-role lines in the `/setup-pstack` rule override these defaults and the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). A role with no line keeps its default, and a role line of `inherit-parent` or `auto` runs that role on the parent chat model (omit Task `model`). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`.\n"),
            ("**Use `subagent_type: \"poteto-agent\"` on the `Agent` tool for any subagent you spawn inside a playbook step** (code-writing delegates, ad-hoc helpers). Read-only exploration and research inside a playbook use `pstack-readonly` with `model: sonnet`. `/poteto-mode` and `poteto-agent` route through the same wrapper. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own `subagent_type` for diverse-model review. Respect what the skill prescribes, don't override to `poteto-agent`.\n"
            "\n"
            "**Defaults for every `Agent` call.** Background by default (Claude Code needs no flag), `general-purpose` for read-write work and `pstack-readonly` when the skill says read-only (both keep MCP), file pointers not inlined context, explicit `model` per role (configurable via `/setup-pstack`). Defaults are `opus` for code, `opus` for prose and judgment, `fable` for the hardest tasks, and `sonnet` for explorers and swarm workers. Read-only and research spawns pass `model: sonnet`. Omitting `model` is wrong, because the call inherits the parent model. `pstack-readonly` also pins `model: sonnet` so a forgotten argument still stays off Opus. `poteto-agent` stays unpinned because it implements as well as explores. Code delegates tier by difficulty. The hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`fable`), whether the task needs judgment on vague intent or is a precisely specified sequence of steps to execute to the letter. Trivial mechanical edits go to your fast code model (`sonnet`). Per-role lines in the `/setup-pstack` rule override these defaults and the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). A role with no line keeps its default, and a role line of `inherit-parent` or `auto` runs that role on the parent chat model (omit `model` on the `Agent` call). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`. Reasoning budget is the effort level `/setup-pstack` writes into `.claude/settings.local.json`; every pack subagent inherits it because none pins `effort`, and the rule's `# budget` line records it.\n"),
        ),
        # port_poteto_mode() moves Playbooks above Writing the reply.
        (
            "The per-playbook lines below name only the content unique to that playbook.",
            "The per-playbook lines above name only the content unique to that playbook.",
        ),
        (
            "Open a todolist whose first items are the matched playbook's steps, copied in verbatim, before any task-specific todos. A step you choose not to do stays in the list with a one-line `skip: <reason>`. Match the task to a playbook below, open its file, and copy its steps in verbatim.",
            "Match the task to a playbook below and load it with the Skill tool as `playbook-<name>`, where `<name>` is the file's basename (`/playbook-feature` loads `playbooks/feature.md`). A Read of the file does not survive compaction; a Skill-tool load does. Then, before any other tool call, open the todolist: one `TaskCreate` call per playbook step, in order, with the step text copied in verbatim, before any task-specific todos. A step you choose not to do stays in the list with a one-line `skip: <reason>`.",
        ),
        (
            "on an explicit pause, going offline, a Cursor restart, or imminent context compaction.",
            "on an explicit pause, going offline, a Claude Code restart, or imminent context compaction.",
        ),
    ],
    "poteto-mode/playbooks/multi-phase-plan.md": [
        (
            "3. Explore in subagents with `subagent_type: \"poteto-agent\"` and an explicit model per the Subagents section (the **guard-the-context-window** principle skill). Each returns file pointers, conventions, test commands, and entry points. No inlined dumps.",
            f"3. Explore in subagents with `subagent_type: \"pstack-readonly\"`. {pin('this read-only spawn')} Each returns file pointers, conventions, test commands, and entry points, with no inlined dumps (the **guard-the-context-window** principle skill).",
        ),
        (
            "Unless the operator names a path, write the file under the agent store's `docs/`.",
            "Unless the operator names a path, write the file under the pstack store's `docs/` (`~/.claude/pstack/store/docs/`).",
        ),
        (
            "on the `swarm workers` model (default `grok-4.7-xhigh-fast`).",
            "on the `swarm workers` model (default `sonnet`).",
        ),
        (
            "**Control skill.** Pick it by surface. Browser, Electron, and web UIs use `control-ui` from `cursor-team-kit`. CLIs and TUIs use `control-cli` from `cursor-team-kit`.",
            "**Control skill.** Pick it by surface. Browser, Electron, and web UIs use `/verify` or the project's `verify-<app>` skill with a browser-driving tool. CLIs and TUIs use `/run` and `/verify`.",
        ),
        (
            "- [ ] Run `/deslop` before each commit and `/no-comments` before review.",
            "- [ ] Run `/simplify` before each commit and `/no-comments` before review.",
        ),
        (
            "Each live lane runs on its own cloud VM at the PR head. Drive through `control-ui` or `control-cli` from `cursor-team-kit`.",
            "Each live lane runs in its own worktree (`isolation: \"worktree\"`) or remote session at the PR head. Drive through `/run`, `/verify`, or the project's `verify-<app>` skill.",
        ),
        ("`node pstack/skills/poteto-mode/scripts/check-plan.mjs", "`node .claude/skills/poteto-mode/scripts/check-plan.mjs"),
        ("The program runs `pstack/skills/", "The program runs `.claude/skills/"),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/<execution playbook>.md`",
            "`git show origin/main:.claude/skills/poteto-mode/playbooks/<execution playbook>.md`",
        ),
        ("`git show origin/main:pstack/skills/swarm/SKILL.md`", "`git show origin/main:.claude/skills/swarm/SKILL.md`"),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`",
            "`git show origin/main:.claude/skills/poteto-mode/playbooks/opening-a-pr.md`",
        ),
        ("`git show origin/main:pstack/skills/<each other", "`git show origin/main:.claude/skills/<each other"),
        ("run the swarm per `pstack/skills/swarm/SKILL.md`", "run the swarm per `.claude/skills/swarm/SKILL.md`"),
        (
            "Which PRs get `pstack/skills/how/SKILL.md` and `pstack/skills/interrogate/SKILL.md`. The trail per `pstack/skills/show-me-your-work/SKILL.md`.",
            "Which PRs get `.claude/skills/how/SKILL.md` and `.claude/skills/interrogate/SKILL.md`. The trail per `.claude/skills/show-me-your-work/SKILL.md`.",
        ),
    ],
    "poteto-mode/playbooks/orchestrate.md": [
        (
            "- **Worker / verifier.** Always `environment: \"cloud\"` unless the task needs this machine: `control-ui` or `control-cli` runtime verification (from `cursor-team-kit`). Reading local transcripts under `agent-transcripts/`. Simulators and local IDE state. Auth that exists only here. Cloud agents cannot read the local store, so their briefs inline what they need or point at repo paths.",
            "- **Worker / verifier.** Always `isolation: \"worktree\"` (or `isolation: \"remote\"` when the account allows cloud sessions) unless the task needs this session's checkout: `/run` or `/verify` runtime verification against a running instance. Reading local transcripts under `~/.claude/projects/<slug>/`. Simulators and local IDE state. Auth that exists only here. Remote agents cannot read the local store, so their briefs inline what they need or point at repo paths.",
        ),
        (
            "Run a unit's verifier on a different model family from its worker.",
            "Run a unit's verifier on a different model alias from its worker.",
        ),
        (
            "Create `orchestrate/<project-slug>/` in the current agent's store (path in the system prompt).",
            "Create `orchestrate/<project-slug>/` in the pstack store (`~/.claude/pstack/store/`).",
        ),
        (
            "Verbatim paste is for cloud spawns and every resume.",
            "Verbatim paste is for remote spawns and every resume.",
        ),
        (
            "the cloud agent's status in the Cursor dashboard.",
            "the subagent's status in `/tasks`.",
        ),
        (
            "Agents are spawned, resumed, and drained only through the Task tool.",
            "Agents are spawned and drained only through the Agent tool, and resumed only through `SendMessage`.",
        ),
        (
            "State reads and writes go through `scripts/orch/orch.ts` at drain points,",
            f"State reads and writes go through `{POTETO_SCRIPTS}/orch/orch.ts` at drain points,",
        ),
        (
            "Use `bun scripts/orch/orch.ts` for bookkeeping, written below as `orch`,",
            f"Use `bun {POTETO_SCRIPTS}/orch/orch.ts --store ~/.claude/pstack/store/orchestrate/<project-slug>` from the repository root for bookkeeping, written below as `orch`,",
        ),
        (
            "a nested spawn has the full Task schema including `environment`",
            "a nested spawn has the full Agent schema including `isolation`",
        ),
        (
            "- After a Cursor restart: local agents are dead, cloud work is not.",
            "- After a Claude Code restart: local agents are dead, remote work is not.",
        ),
        (
            "reattach cloud work by PR and branch rather than agent id,",
            "reattach remote work by PR and branch rather than agent id,",
        ),
    ],
    "poteto-mode/playbooks/babysit.md": [
        (
            "This playbook replaces Cursor's built-in babysit skill for these requests, so do not route there even though its description matches the same words.",
            "This playbook owns these requests. Do not route to any other babysit skill even though its description matches the same words.",
        ),
        (
            "On GitHub, status comes from `scripts/watch-pr/watch-pr`.",
            f"On GitHub, status comes from `{POTETO_SCRIPTS}/watch-pr/watch-pr`, run from the repository root.",
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
            "your configured refactoring model (default `opus`)",
        ),
    ],
    "poteto-mode/playbooks/autopilot-full.md": [
        (
            "One Cursor cloud agent per PR owns build, the first push,",
            "One subagent in its own worktree (`isolation: \"worktree\"`, or `\"remote\"` when the account allows cloud sessions) per PR owns build, the first push,",
        ),
        (
            "a slop-strip (the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)), `/no-comments` (the **no-comments** skill), a rebase onto current trunk,",
            "a slop-strip (Claude Code's bundled `simplify` skill (`/simplify`)), `/no-comments` (the **no-comments** skill), a rebase onto current trunk,",
        ),
        (
            "(with the matching control skill, such as `control-cli` or `control-ui` from `cursor-team-kit`, or a named driver where none exists)",
            "(with the matching control skill, such as `/run`, `/verify`, or the project's `verify-<app>` skill, or a named driver where none exists)",
        ),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`",
            "`git show origin/main:.claude/skills/poteto-mode/playbooks/autopilot-full.md`",
        ),
    ],
    "poteto-mode/playbooks/autopilot-stack.md": [
        (
            "One Cursor cloud agent per PR owns its change end to end:",
            "One subagent in its own worktree (`isolation: \"worktree\"`, or `\"remote\"` when the account allows cloud sessions) per PR owns its change end to end:",
        ),
        (
            "a slop-strip (the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)), `/no-comments` (the **no-comments** skill), and babysit to green",
            "a slop-strip (Claude Code's bundled `simplify` skill (`/simplify`)), `/no-comments` (the **no-comments** skill), and babysit to green",
        ),
        (
            "`git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`",
            "`git show origin/main:.claude/skills/poteto-mode/playbooks/autopilot-stack.md`",
        ),
    ],
    "poteto-mode/playbooks/shipping.md": [
        (
            "With GitHub, use `scripts/watch-pr/watch-pr --queued-stack --stack-prs <bottom>`",
            f"With GitHub, use `{POTETO_SCRIPTS}/watch-pr/watch-pr --queued-stack --stack-prs <bottom>`",
        ),
        (
            "One subagent per PR, not batched, each a Cursor cloud agent, each exercising the real surface with the matching control skill (such as `control-ui` or `control-cli` from `cursor-team-kit`) against parent versus head.",
            "One subagent per PR, not batched, each in its own worktree (`isolation: \"worktree\"`), each exercising the real surface with the matching control skill (`/run`, `/verify`, or the project's `verify-<app>` skill) against parent versus head.",
        ),
    ],
    "poteto-mode/playbooks/opening-a-pr.md": [
        (
            "Multiple `Task` calls on the same branch each get their own worktree,",
            "Multiple `Agent` calls on the same branch each get their own worktree,",
        ),
        (
            "**PRs.** Run `/deslop` from `cursor-team-kit` over the diff before commit.",
            "**PRs.** Run Claude Code's `/simplify` over the diff before commit.",
        ),
        (
            "A subagent that opens a PR runs `interrogate`, `/deslop`, and `/no-comments`, and posts the URL.",
            "A subagent that opens a PR runs `interrogate`, `/simplify`, and `/no-comments`, and posts the URL.",
        ),
    ],
    "poteto-mode/playbooks/bug-fix.md": [
        (
            "Drive a long or stubborn hunt with Cursor's `/loop` command.",
            "Drive a long or stubborn hunt with Claude Code's `/loop` command.",
        ),
        (
            "using your configured bug-fix model (default `grok-4.7-xhigh-fast`)",
            "using your configured bug-fix model (default `opus`)",
        ),
    ],
    "poteto-mode/playbooks/feature.md": [
        (
            "using your configured feature model (default `grok-4.7-xhigh-fast`)",
            "using your configured feature model (default `opus`)",
        ),
    ],
    "poteto-mode/playbooks/perf-issue.md": [
        (
            "using your configured perf-issue model (default `grok-4.7-xhigh-fast`)",
            "using your configured perf-issue model (default `opus`)",
        ),
    ],
    "poteto-mode/playbooks/hillclimb.md": [
        (
            "using your configured hillclimb model (default `grok-4.7-xhigh-fast`)",
            "using your configured hillclimb model (default `opus`)",
        ),
    ],
    "poteto-mode/playbooks/autonomous-run.md": [
        (
            "Pick the wake mechanism using Cursor's `/loop` command (a built-in, not a pstack skill).",
            "Pick the wake mechanism using Claude Code's `/loop` command (a bundled skill, not a pstack skill).",
        ),
        (
            "Do not park reversible work for the human or use `AskQuestion`.",
            "Do not park reversible work for the human or use `AskUserQuestion`.",
        ),
    ],
    "poteto-mode/playbooks/session-pickup.md": [
        (
            "A local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path. Do not glob across `~/.cursor/projects/*/`, that crosses workspace boundaries and reads private chats from unrelated projects), a cloud-agent URL, or a pushed branch.",
            f"A local transcript under this workspace's transcript directory (`~/.claude/projects/<slug>/`, slug rule in the **poteto-mode** skill's `references/claude-code.md`. {NO_GLOB}), a remote session URL, or a pushed branch.",
        ),
        (
            "Parse a long transcript in a subagent and keep the reduced timeline in the main thread (the **principle-guard-the-context-window** skill).",
            f"Parse a long transcript in a `pstack-readonly` subagent. {pin('this read-only spawn')} Keep the reduced timeline in the main thread (the **principle-guard-the-context-window** skill).",
        ),
    ],
    "poteto-mode/playbooks/runtime-forensics.md": [
        (
            "Parse large artifacts in a subagent (the **guard-the-context-window** principle skill), keep the reduced finding in the main thread.",
            f"Parse large artifacts in a `pstack-readonly` subagent. {pin('this read-only spawn')} Keep the reduced finding in the main thread (the **guard-the-context-window** principle skill).",
        ),
    ],
    "poteto-mode/playbooks/trace-forensics.md": [
        (
            "Parse large artifacts in a subagent (the **principle-guard-the-context-window** skill) and keep the reduced finding in the main thread.",
            f"Parse large artifacts in a `pstack-readonly` subagent. {pin('this read-only spawn')} Keep the reduced finding in the main thread (the **principle-guard-the-context-window** skill).",
        ),
    ],
    "poteto-mode/playbooks/eval.md": [
        (
            "Read each candidate's local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names this path). Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
            f"Read each candidate's local transcript under this workspace's transcript directory (`~/.claude/projects/<slug>/`, slug rule in the **poteto-mode** skill's `references/claude-code.md`). {NO_GLOB}",
        ),
    ],
    "poteto-mode/playbooks/worktree-cleanup.md": [
        (
            "then run `scripts/worktree-audit.sh` (principle-build-the-lever).",
            f"then run `{POTETO_SCRIPTS}/worktree-audit.sh` from the repository root (principle-build-the-lever).",
        ),
        (
            "fan subagents out to read the transcripts and report whether the chat is pinned or ongoing and which worktrees it touches (principle-guard-the-context-window, transcripts are bulk).",
            f"fan `pstack-readonly` subagents out to read the transcripts and report whether the chat is pinned or ongoing and which worktrees it touches (principle-guard-the-context-window, transcripts are bulk). {pin('each read-only spawn')}",
        ),
        (
            "since a hand-typed `myrepo-worktrees/x` misses one that lives at `.cursor/worktrees/myrepo/x`",
            "since a hand-typed `myrepo-worktrees/x` misses one that lives at `.claude/worktrees/x`",
        ),
        (
            "`~/Library/Application Support/Cursor` (`state.vscdb.backup`, and `snapshots/roots/<root>` where a `<root>` named for a folder you opened as a workspace balloons),",
            "`~/.claude/projects/<slug>/` (old session transcripts and their `subagents/` directories balloon),",
        ),
        (
            "`scratch:N` is untracked throwaway, safe to drop, but name the files. Per Autonomy, clean and merged and not-in-use proceeds. `wip` and in-use pause.",
            "`untracked:N` (`hold-untracked`) is N untracked files, which removal deletes too, so name the files and get the same decision. `wip:N,untracked:M` has both, so show the diff and name the files. `hold-unreachable` is a detached HEAD whose commits no branch or tag contains, so removal strands them; show `git log` and get a decision. Per Autonomy, clean and merged and not-in-use proceeds. `wip`, `untracked`, `hold-unreachable`, and in-use pause.",
        ),
        (
            "Per path, `git worktree remove --force <path>`. If the dir survives on ignored build artifacts, `rm -rf` it, then `git worktree prune`. Branch refs survive, so no commits are lost.",
            "Per path, `git worktree remove <path>`, never `--force`. If git refuses, the worktree has uncommitted or untracked work, so take it back to step 4. `git worktree remove` already deletes ignored build output. Then `git worktree prune`. Branch refs survive, so a removed worktree's branch commits are not lost.",
        ),
    ],
    "poteto-mode/playbooks/authoring-a-skill.md": [
        (
            "1. Use the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            f"1. Use {SKILL_CREATOR} when it is installed. Otherwise follow the Claude Code skills reference (https://code.claude.com/docs/en/skills) and check the result with `claude plugin validate .claude/skills`.",
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
            role_lines("Each spawn below names", "the explorer spawn in Step 2a"),
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `how explorer` line, default `grok-4.7-xhigh-fast`\n- `readonly`: `true`\n",
            f"- `subagent_type`: `pstack-readonly`\n- `model`: `sonnet`, or the `how explorer` line when that line names a different alias. {pin('this read-only spawn')}\n",
        ),
        (
            "Spawn one Task subagent that explores and explains in one pass:\n\n- `subagent_type`: `generalPurpose`\n- `model`: the `how explainer` line, default `claude-opus-5-5-max`\n- `readonly`: `true`\n",
            "Spawn one `Agent` subagent that explores and explains in one pass:\n\n- `subagent_type`: `pstack-readonly`\n- `model`: the `how explainer` line, default `opus`\n",
        ),
        (
            "spawn one Task subagent to synthesize their findings into one explanation:\n\n- `subagent_type`: `generalPurpose`\n- `model`: the `how explainer` line, default `claude-opus-5-5-max`\n- `readonly`: `true`\n",
            "spawn one `Agent` subagent to synthesize their findings into one explanation:\n\n- `subagent_type`: `pstack-readonly`\n- `model`: the `how explainer` line, default `opus`\n",
        ),
    ],
    "how/references/explorer-prompt.md": [
        (
            "Use Glob to find directories and files, Grep to find key symbols, Read to understand the actual implementation.",
            f"Search ({SEARCH_TOOLS}) to find directories, files, and key symbols, and Read to understand the actual implementation.",
        ),
    ],
    "how/references/explainer-prompt.md": [
        (
            "Use Read, Grep, and Glob as needed.",
            f"Use Read and search ({SEARCH_TOOLS}) as needed.",
        ),
    ],
    "why/SKILL.md": [
        (
            "Each spawn below names a role line in the `pstack-models.mdc` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            role_lines("Each spawn below names", "the investigator spawn in Step 3"),
        ),
        (
            "Before spawning investigators, list the available MCPs from the Cursor environment. Use the available-tools map when present. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers.",
            "Before spawning investigators, list the MCP servers connected to this session: the `mcp__<server>__<tool>` tools in your tool list, the deferred ones `ToolSearch` can load, and the output of `claude mcp list`.",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `why investigators` line, default `grok-4.7-xhigh-fast`\n- `readonly`: `false` (agent mode). **Do not use readonly/Ask mode.** It strips MCP access, which disables MCP-backed investigators entirely. Investigators still shouldn't write anything.",
            f"- `subagent_type`: `general-purpose`\n- `model`: `sonnet`, or the `why investigators` line when that line names a different alias. {pin('this research spawn')}\n- Investigators need their MCP tools, which every subagent keeps in Claude Code. They still shouldn't write anything.",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the `why synthesizer` line, default `claude-opus-5-5-max`\n- `readonly`: `false` (agent mode). The synthesizer's quality check spot-verifies citations, which can require MCP access. Readonly/Ask mode strips MCPs and defeats that.",
            "- `subagent_type`: `general-purpose`\n- `model`: the `why synthesizer` line, default `opus`\n- The synthesizer's quality check spot-verifies citations, which can require its MCP tools.",
        ),
    ],
    "interrogate/SKILL.md": [
        (
            "Launch all reviewers in a single message using the Task tool. Use the `interrogate reviewers` line in `~/.cursor/rules/pstack-models.mdc`,",
            "Launch all reviewers in a single message using the Agent tool. Use the `interrogate reviewers` line in `~/.claude/rules/pstack-models.md`,",
        ),
        (
            "| Reviewer A | `claude-opus-5-5-max` |\n| Reviewer B | `gpt-5.6-sol-max` |\n| Reviewer C | `grok-4.7-xhigh-fast` |",
            "| Reviewer A | `fable` |\n| Reviewer B | `opus` |\n| Reviewer C | `sonnet` |",
        ),
        (
            "- `subagent_type`: `generalPurpose`\n- `model`: the configured `interrogate reviewers` entry, or the table default with no configured line. For an `auto` or `inherit-parent` entry, omit `model` so that reviewer runs on the parent model.\n- `readonly`: `true`\n",
            "- `subagent_type`: `pstack-readonly`\n- `model`: the configured `interrogate reviewers` entry, or the table default with no configured line. For an `auto` or `inherit-parent` entry, omit `model` so that reviewer runs on the parent model.\n",
        ),
        (
            "If the Task tool rejects a configured entry, run that reviewer on the table default of its family and say so. Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`. With no family match, use Reviewer A's default. If it rejects a table default, check the valid slugs in the Task tool's error message, pick the closest equivalent (prefer the highest-reasoning tier of the same family), spawn with it, and open a separate PR to update the default table. Do not block the review on the slug issue. Never treat an alias entry as a rejected slug or apply either fallback to it.",
            "If the Agent tool rejects a configured entry, run that reviewer on the table default of its seat and say so. If it rejects a table default, check the valid values in the Agent tool's error message, pick the closest equivalent (prefer the highest-capability tier), spawn with it, and open a separate PR to update the default table. Do not block the review on the model issue. Never treat an `auto` or `inherit-parent` entry as a rejected value or apply either fallback to it.",
        ),
    ],
    "interrogate/references/rubric.md": [
        (
            "Use the tools available to you (Read, Grep, Glob) to explore.",
            f"Use the tools available to you, Read and search ({SEARCH_TOOLS}), to explore.",
        ),
    ],
    "arena/SKILL.md": [
        (
            "Use the `arena runners` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, default to one each on `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`. An `auto` or `inherit-parent` entry in this line or the cross-judge line means the parent model, so omit `model` for it. If the Task tool rejects a configured entry, run that seat on its family's default and say so. Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`. With no family match, use `claude-opus-5-5-max`. If it rejects a default, use the closest valid slug of the same family from its error message.",
            "Use the `arena runners` line in `~/.claude/rules/pstack-models.md`. If the rule or that line is missing, default to one each on `fable`, `opus`, `sonnet`. An `auto` or `inherit-parent` entry in this line or the cross-judge line means the parent model, so omit `model` for it. If the Agent tool rejects a configured entry, run that seat on its default and say so. With no matching seat, use `opus`. If it rejects a default, use the closest available alias from its error message.",
        ),
        (
            "Spawn all N subagents in one message with `run_in_background: true`, each with the task,",
            "Spawn all N subagents in one message (`subagent_type: poteto-agent`, background by default), each with the task,",
        ),
        (
            "choose one model from the `arena cross-judge pool` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, choose from `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`. Prefer a different model family from the parent's. Spawn one readonly judge subagent on that model.",
            "choose one model from the `arena cross-judge pool` line in `~/.claude/rules/pstack-models.md`. If the rule or that line is missing, choose from `fable`, `opus`, `sonnet`. Prefer a different model alias from the parent's. Spawn one `pstack-readonly` judge subagent on that model.",
        ),
    ],
    "architect/SKILL.md": [
        (
            "Take the runners from the `architect runners` line in the `pstack-models.mdc` rule, in place of the `arena runners` line. If the rule or that line is missing, use `claude-opus-5-5-max`, `gpt-5.6-sol-max`, `grok-4.7-xhigh-fast`.",
            "Take the runners from the `architect runners` line in the `~/.claude/rules/pstack-models.md` rule, in place of the `arena runners` line. If the rule or that line is missing, use `fable`, `opus`, `sonnet`.",
        ),
    ],
    "swarm/SKILL.md": [
        (
            "N is total workers, not the cloud concurrency limit.",
            "N is total workers, not the concurrent subagent limit.",
        ),
        (
            "Pick the worker model from the `swarm workers` line in `~/.cursor/rules/pstack-models.mdc`. If the rule or that line is missing, use `grok-4.7-xhigh-fast`. For `auto` or `inherit-parent`, omit `model` so the workers run on the parent model. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            f"Pick the worker model from the `swarm workers` line in `~/.claude/rules/pstack-models.md`. If the rule or that line is missing, pass `model: sonnet`. {pin('these speed spawns')} Omit `model` only when that role line is explicitly `auto` or `inherit-parent`. {REJECT_ALIAS}",
        ),
        (
            "Spawn all N workers in one message with `subagent_type: generalPurpose`, `environment: \"cloud\"`, `run_in_background: true`, and the step 4 model, left unset for `auto` or `inherit-parent`. Use `environment: \"local\"` only when the worker needs access to something on the user's computer.\n\nWhen a worker must start from a non-default pushed branch, pass `cloud_base_branch`.",
            "Spawn all N workers in one message with `subagent_type: general-purpose`, `isolation: \"worktree\"` (each worker gets its own checkout; use `isolation: \"remote\"` instead when the account allows cloud sessions and the worker needs nothing from this machine), and the step 4 model, left unset for `auto` or `inherit-parent`. Omit `isolation` only when the worker needs this session's checkout or something else on the user's computer.\n\nWhen a worker must start from a non-default pushed branch, name the branch in its brief. The worker runs `git fetch origin <branch> && git checkout <branch>` inside its worktree before anything else.",
        ),
    ],
    "reflect/SKILL.md": [
        (
            ("The parent finds its own transcript file before fanning out. The system prompt names the active workspace's `agent-transcripts/` directory. Use that path. Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.\n"
            "\n"
            "```bash\n"
            "ls -t <agent-transcripts>/*.jsonl <agent-transcripts>/*/*.jsonl <agent-transcripts>/*/subagents/*.jsonl 2>/dev/null | head -10\n"
            "```\n"
            "\n"
            "Three transcript layouts: legacy flat (`<id>.jsonl`), current nested (`<id>/<id>.jsonl`), and subagent (`<parent>/subagents/<child>.jsonl`).\n"
            "\n"
            "For each candidate, read the first JSONL line and check that `message.content[0].text` contains the conversation's opening user prompt."),
            (f"The parent finds its own transcript file before fanning out. {TRANSCRIPT_DIR} The current session's id is in `$CLAUDE_CODE_SESSION_ID`. Use only that directory. {NO_GLOB}\n"
            "\n"
            "```bash\n"
            "echo \"$CLAUDE_CODE_SESSION_ID\"\n"
            "ls -t ~/.claude/projects/<slug>/*.jsonl ~/.claude/projects/<slug>/*/subagents/*.jsonl 2>/dev/null | head -10\n"
            "```\n"
            "\n"
            "Two transcript layouts: session (`<id>.jsonl`) and subagent (`<parent>/subagents/agent-<child>.jsonl`).\n"
            "\n"
            "For each candidate, read the first JSONL line whose `type` is `user` and check that its `message.content` (a string, or a list whose first block carries `text`) contains the conversation's opening user prompt."),
        ),
        (
            "One message, three `Task` calls, `subagent_type: generalPurpose`, with `model` set as below, agent mode (`readonly: false`). Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript). Readonly strips MCPs.",
            "One message, three `Agent` calls, `subagent_type: general-purpose`, with `model` set as below. Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), which every subagent keeps in Claude Code.",
        ),
        (
            "Each reviewer and the synthesizer name a role line in the `pstack-models.mdc` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the Task tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
            role_lines("Each reviewer and the synthesizer name", "the Tooling reviewer"),
        ),
        (
            "| Judgment | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-max` | `references/judgment-reviewer.md` |\n| Tooling | `reflect tooling` | `gpt-5.6-sol-max` | `references/tooling-reviewer.md` |\n| Divergent | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-max` | `references/divergent-reviewer.md` |",
            f"| Judgment | `reflect judgment, divergent, synthesizer` | `opus` | `references/judgment-reviewer.md` |\n| Tooling | `reflect tooling` | `sonnet`. {pin('this research spawn')} | `references/tooling-reviewer.md` |\n| Divergent | `reflect judgment, divergent, synthesizer` | `opus` | `references/divergent-reviewer.md` |",
        ),
        (
            "Reviewers return findings in the `Task` response body.",
            "Reviewers return findings in the `Agent` response body.",
        ),
        (
            "One `Task` call, `subagent_type: generalPurpose`, with `model` from the `reflect judgment, divergent, synthesizer` line (default `claude-opus-5-5-max`), agent mode (`readonly: false`). The synthesizer's quality check includes spot-verifying citations, which can require MCP access. Readonly strips MCPs.",
            "One `Agent` call, `subagent_type: general-purpose`, with `model` from the `reflect judgment, divergent, synthesizer` line (default `opus`). The synthesizer's quality check includes spot-verifying citations, which can require MCP access, which the subagent keeps.",
        ),
        (
            ("- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Cursor's built-in `create-skill` skill and run its draft / test / iterate loop.\n"
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and run its description-optimization loop.\n"
            "- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.\n"
            "\n"
            "If your environment ships a SKILL.md validator, run it on every touched skill before declaring done. Skip this step if it doesn't."),
            ("- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the `skill-creator` skill (Anthropic's skill for authoring SKILL.md files) and run its draft / test / iterate loop.\n"
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `skill-creator` and run its description-optimization loop.\n"
            "- `new skill via skill-creator: <kebab-name>`: hand creation to `skill-creator`. Do not invent the shape ad hoc.\n"
            "\n"
            "Run `claude plugin validate` on the directory of every touched skill before declaring done."),
        ),
    ],
    "reflect/references/synthesizer.md": [
        (
            "- Existing-skill-first: propose `new skill via create-skill:` only when",
            "- Existing-skill-first: propose `new skill via skill-creator:` only when",
        ),
        (
            "| <new pattern, no existing skill is a real home> | <draft a new skill via create-skill> | <new skill via create-skill: <kebab-name>> |",
            "| <new pattern, no existing skill is a real home> | <draft a new skill via skill-creator> | <new skill via skill-creator: <kebab-name>> |",
        ),
    ],
    "reflect/references/tooling-reviewer.md": [
        (
            "- Tool calls (Shell, Grep, MCP, etc.)",
            "- Tool calls (Bash, Grep, MCP, etc.)",
        ),
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `Read` tool calls against any `SKILL.md` file (project `.claude/skills/`, user-level `~/.claude/skills/`, or plugin-installed paths under `~/.claude/plugins/`)\n- `Agent` prompts that name a skill path",
        ),
    ],
    "reflect/references/judgment-reviewer.md": [
        (
            "- Tool calls (Shell, Grep, MCP, etc.)",
            "- Tool calls (Bash, Grep, MCP, etc.)",
        ),
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `Read` tool calls against any `SKILL.md` file (project `.claude/skills/`, user-level `~/.claude/skills/`, or plugin-installed paths under `~/.claude/plugins/`)\n- `Agent` prompts that name a skill path",
        ),
    ],
    "reflect/references/divergent-reviewer.md": [
        (
            "- Tool calls (Shell, Grep, MCP, etc.)",
            "- Tool calls (Bash, Grep, MCP, etc.)",
        ),
        (
            "- `Read` tool calls against any `SKILL.md` file (workspace `.cursor/skills/`, user-level `~/.cursor/skills/`, or plugin-installed paths under `~/.cursor/plugins/`)\n- `Task` prompts that name a skill path",
            "- `Read` tool calls against any `SKILL.md` file (project `.claude/skills/`, user-level `~/.claude/skills/`, or plugin-installed paths under `~/.claude/plugins/`)\n- `Agent` prompts that name a skill path",
        ),
    ],
    "no-comments/SKILL.md": [
        (
            "1. Spawn `Task` with `subagent_type: \"Comment Sicko\"`. Pass the scope. Do not restate its rules.",
            "1. Spawn `Agent` with `subagent_type: \"comment-sicko\"`. Pass the scope. Do not restate its rules.",
        ),
    ],
    "recall/SKILL.md": [
        (
            "Transcripts live at `~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>.jsonl`, where `<slug>` is the workspace path with the leading slash dropped and each \"/\" turned into \"-\" (so `/Users/you/proj` becomes `Users-you-proj`). Every line is one chat message.",
            "Transcripts live at `~/.claude/projects/<slug>/<uuid>.jsonl`, where `<slug>` is the working directory path with every character other than a letter or digit turned into `-` (so `/Users/you/my.proj` becomes `-Users-you-my-proj`). Subagent transcripts sit beside them at `<uuid>/subagents/agent-<id>.jsonl`. Every line is one record. Chat messages are the lines whose `type` is `user` or `assistant`.",
        ),
    ],
    "show-me-your-work/SKILL.md": [
        (
            "Use the helper `scripts/log.sh <logfile>",
            "Use the helper `.claude/skills/show-me-your-work/scripts/log.sh <logfile>",
        ),
        (
            "Read this run's transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path). Don't glob across `~/.cursor/projects/*/`. That reads unrelated private chats.",
            "Read this run's transcript, `$CLAUDE_CODE_SESSION_ID.jsonl` under this workspace's transcript directory (`~/.claude/projects/<slug>/`, slug rule in the **poteto-mode** skill's `references/claude-code.md`). Don't glob across `~/.claude/projects/*/`. That reads unrelated private chats.",
        ),
    ],
    "automate-me/SKILL.md": [
        (
            "Drafts or revises a personal -mode skill via create-skill + unslop, optionally pulling fresh evidence from recent transcripts.",
            "Drafts or revises a personal -mode skill via skill-creator + unslop, optionally pulling fresh evidence from recent transcripts.",
        ),
        (
            "This skill orchestrates three others: an inline mining pass (see step 1), Cursor's built-in `create-skill` (authoring), and the **unslop** skill (prose discipline).",
            "This skill orchestrates three others: an inline mining pass (see step 1), the `skill-creator` skill (Anthropic's skill for authoring SKILL.md files, installed as a plugin or a synced skill), and the **unslop** skill (prose discipline).",
        ),
        (
            "Look recursively for `.cursor/skills/**/*-mode/SKILL.md` and `~/.cursor/skills/*-mode/SKILL.md` matching the user's handle. Mode skills can live in a personal category directory (`.cursor/skills/<handle>/`), not only at the top level. If one exists, confirm intent with `AskQuestion` (unless they already said \"update my skill\" or similar):",
            "Look recursively for `.claude/skills/**/*-mode/SKILL.md` and `~/.claude/skills/*-mode/SKILL.md` matching the user's handle. Mode skills can live in a personal category directory (`.claude/skills/<handle>/`), not only at the top level. If one exists, confirm intent with `AskUserQuestion` (unless they already said \"update my skill\" or similar):",
        ),
        (
            "Locate the active workspace's transcripts before fanning out. The system prompt names the workspace's `agent-transcripts/` directory. Use only that path. Don't glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
            f"Locate the active workspace's transcripts before fanning out. {TRANSCRIPT_DIR} Use only that path. {NO_GLOB}",
        ),
        (
            ("Mining misses intent that hasn't come up yet. Use the `AskQuestion` tool (structured multi-choice) rather than asking the user to type from scratch.\n"
            "\n"
            "Shape: one or two questions with 4-6 options each, `allow_multiple: true` for category questions."),
            ("Mining misses intent that hasn't come up yet. Use the `AskUserQuestion` tool (structured multi-choice) rather than asking the user to type from scratch.\n"
            "\n"
            "Shape: one or two questions with up to 4 options each (the tool's limit), `multiSelect: true` for category questions."),
        ),
        (
            ("Use Cursor's built-in `create-skill` skill to author the skill. Placement:\n"
            "\n"
            "- Path: preserve an existing mode skill's category. For a new mode, use `.cursor/skills/<handle>/<handle>-mode/SKILL.md` when the repo has an established personal category for that handle. Otherwise default to `.cursor/skills/<handle>-mode/SKILL.md` in the project (or `~/.cursor/skills/<handle>-mode/` if the user prefers a personal skill)."),
            ("Use the `skill-creator` skill to author the skill. Placement:\n"
            "\n"
            "- Path: preserve an existing mode skill's category. For a new mode, use `.claude/skills/<handle>/<handle>-mode/SKILL.md` when the repo has an established personal category for that handle. Otherwise default to `.claude/skills/<handle>-mode/SKILL.md` in the project (or `~/.claude/skills/<handle>-mode/` if the user prefers a personal skill)."),
        ),
        (
            "- Frontmatter formatting: follow `create-skill`'s YAML rules.",
            "- Frontmatter formatting: follow `skill-creator`'s YAML rules and the Claude Code skills reference.",
        ),
        (
            "Apply the **unslop** skill and `create-skill`'s writing guidelines to every line.",
            "Apply the **unslop** skill and `skill-creator`'s writing guidelines to every line.",
        ),
        (
            "A `create-skill`-style test/iterate benchmark loop isn't useful here.",
            "A `skill-creator`-style test/iterate benchmark loop isn't useful here.",
        ),
        (
            "- User wants a task-specific skill (not working conventions): `create-skill` alone, no mining required.",
            "- User wants a task-specific skill (not working conventions): `skill-creator` alone, no mining required.",
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
            "This skill generates that as a project-local skill (`.claude/skills/verify-<app>/`) tailored to the repo.",
        ),
        (
            "Write `.cursor/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it \u2014 without frontmatter the skill never registers)",
            "Write `.claude/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it; without frontmatter the skill never registers)",
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
            "Create `.claude/skills/verify-<app>/features/README.md` plus one file per user-facing feature",
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
            "(usually `.claude/skills/verify-*/`)",
        ),
    ],
    "make-bot-ui/SKILL.md": [
        (
            "name: Make Bot UI\n",
            "name: make-bot-ui\n",
        ),
        (
            "# How to make a bot UI\n\nBuild a page the user clicks.",
            "# How to make a bot UI\n\nThe webhook routine lives in Cursor's Automations. In Claude Code the `update_state` and `SendToUser` tools below do not exist, so ask the user to create the routine, copy its URL, and place the sender key in the server config themselves, then continue from **Host the page on this computer**.\n\nBuild a page the user clicks.",
        ),
        (
            "Store `{url, key}` in that UI's own directory.",
            "Store `{url, key, uiToken}` in that UI's own directory. `uiToken` is this UI's own caller token: at least 32 bytes from a secure random source, made for this UI alone, never the sender key.",
        ),
        (
            "Bind the server to `0.0.0.0:<port>`, not `127.0.0.1`. Tailscale peers cannot reach a localhost-only bind.\n",
            ("Bind the server only to this computer's Tailscale IPv4 address, the one `tailscale ip -4` prints, as `<100.x.x.x>:<port>`.\n"
            "If Tailscale is not up yet, finish **Put the page on the tailnet** first.\n"
            "Never bind `0.0.0.0`, which answers every network this computer is on, or `127.0.0.1`, which Tailscale peers cannot reach.\n"
            "The page is plain HTTP, so only the tailnet's WireGuard tunnel keeps the token and cookie below out of cleartext.\n"
            "Have the server read the address when it starts and exit if it cannot, rather than fall back to another bind.\n"
            "\n"
            "Other tailnet devices and other programs on this computer can still reach that address, so the sender key protects only the outbound call.\n"
            "Check every inbound request before the server does anything else:\n"
            "\n"
            "- The first visit carries `?token=<uiToken>`. On a match, set it as an `HttpOnly`, `SameSite=Strict` cookie and redirect to the same path without the query.\n"
            "- Every other request, the page and every button POST, must carry that cookie. Compare it with `uiToken` in constant time.\n"
            "- Reject a POST whose `Origin` header is not the page's own origin.\n"
            "- Answer `401` to any request that fails a check, and never call the webhook for it.\n"
            "- Do not log the token.\n"),
        ),
        (
            "Give the user both URLs:\n\n- `http://<hostname>.<tailnet>.ts.net:<port>`\n- `http://<100.x.x.x>:<port>`\n",
            "Give the user both URLs, and tell them to add `?token=` and the `uiToken` value from the UI's config on their first visit. Do not print the token in chat.\n\n- `http://<hostname>.<tailnet>.ts.net:<port>`\n- `http://<100.x.x.x>:<port>`\n",
        ),
        (
            "Probe `http://<100.x.x.x>:<port>/` and expect HTTP 200.",
            ("Probe `http://<100.x.x.x>:<port>/` without the token and expect HTTP 401.\n"
            "Probe it again with `?token=` and the token read from the config file inside the command, never typed out, and expect the redirect that sets the cookie.\n"
            "Probe `http://127.0.0.1:<port>/` and expect the connection to be refused, which shows the server is not listening on every interface."),
        ),
    ],
    "figure-it-out/SKILL.md": [],
}

# Substitutions inside scripts (same exact-match rule).
SCRIPT_SUBSTITUTIONS: dict[str, list[tuple[str, str]]] = {
    "poteto-mode/scripts/worktree-audit.sh": [
        (
            ("# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts.\n"
            "slug=$(printf '%s' \"$main_wt\" | sed 's#^/##; s#/#-#g')\n"
            "transcripts=\"$HOME/.cursor/projects/$slug/agent-transcripts\"\n"),
            ("# Transcripts dir: ~/.claude/projects/<slugified-path>. Claude Code turns\n"
            "# every character of the path other than a letter or digit into \"-\".\n"
            "slugify() { printf '%s' \"$1\" | sed 's#[^A-Za-z0-9]#-#g'; }\n"
            "transcripts=\"$HOME/.claude/projects/$(slugify \"$main_wt\")\"\n"),
        ),
        (
            ("\tif [ -d \"$transcripts\" ]; then\n"
            "\t\tf=$(rg -l -e \"${wt}/\" -e \"${wt}\\\"\" \"$transcripts\" 2>/dev/null \\\n"
            "\t\t\t| xargs stat -f '%m %N' 2>/dev/null | sort -rn | head -1)\n"
            "\t\tif [ -n \"$f\" ]; then last_ts=$(echo \"$f\" | awk '{print $1}')\n"
            "\t\t\tlast=$(date -r \"$last_ts\" '+%Y-%m-%d' 2>/dev/null); fi\n"
            "\tfi\n"),
            ("\twt_transcripts=\"$HOME/.claude/projects/$(slugify \"$wt\")\"\n"
            "\tf=$({ [ -d \"$transcripts\" ] && rg -l -e \"${wt}/\" -e \"${wt}\\\"\" \"$transcripts\" 2>/dev/null\n"
            "\t\t[ -d \"$wt_transcripts\" ] && find \"$wt_transcripts\" -name '*.jsonl' 2>/dev/null; } \\\n"
            "\t\t| while read -r t; do printf '%s %s\\n' \"$(mtime \"$t\")\" \"$t\"; done | sort -rn | head -1)\n"
            "\tif [ -n \"$f\" ]; then last_ts=$(echo \"$f\" | awk '{print $1}')\n"
            "\t\tlast=$(ymd \"$last_ts\"); fi\n"),
        ),
        (
            "now=$(date +%s)\n",
            ("now=$(date +%s)\n"
            "\n"
            "# GNU and BSD stat/date spell these differently; Claude Code runs on both.\n"
            "mtime() { stat -c '%Y' \"$1\" 2>/dev/null || stat -f '%m' \"$1\" 2>/dev/null; }\n"
            "ymd() { date -d \"@$1\" '+%Y-%m-%d' 2>/dev/null || date -r \"$1\" '+%Y-%m-%d' 2>/dev/null; }\n"),
        ),
        (
            "\t# Distinguish real WIP (tracked edits) from disposable untracked scratch.\n",
            "\t# Distinguish tracked edits from untracked files. Both are gone once the\n"
            "\t# worktree is removed; ignored build output is not listed here.\n",
        ),
        (
            "\t\tdirty=\"wip:$(printf '%s\\n' \"$porcelain\" | grep -cv '^??')\"\n"
            "\telse dirty=\"scratch:$(printf '%s\\n' \"$porcelain\" | grep -c '^??')\"; fi\n",
            "\t\tdirty=\"wip:$(printf '%s\\n' \"$porcelain\" | grep -cv '^??')\"\n"
            "\t\tuntracked=$(printf '%s\\n' \"$porcelain\" | grep -c '^??')\n"
            "\t\t[ \"$untracked\" -eq 0 ] || dirty=\"$dirty,untracked:$untracked\"\n"
            "\telse dirty=\"untracked:$(printf '%s\\n' \"$porcelain\" | grep -c '^??')\"; fi\n",
        ),
        (
            "\tcase \"$dirty\" in wip:*) bucket=hold-wip ;; *)\n"
            "\t\tcase \"$pr\" in *OPEN*) bucket=hold-open-pr ;; *)\n",
            "\t# A detached HEAD's commits live on no branch, so removing the worktree\n"
            "\t# strands them unless some ref still contains that commit.\n"
            "\tunreachable=no\n"
            "\t[ -z \"$branch\" ] && [ -z \"$(git -C \"$wt\" for-each-ref --contains \"$head\" --count=1 2>/dev/null)\" ] && unreachable=yes\n"
            "\n"
            "\tcase \"$dirty\" in wip:*) bucket=hold-wip ;; untracked:*) bucket=hold-untracked ;; *)\n"
            "\t\tif [ \"$unreachable\" = yes ]; then bucket=hold-unreachable\n"
            "\t\telse case \"$pr\" in *OPEN*) bucket=hold-open-pr ;; *)\n",
        ),
        (
            "\t\tesac ;;\n",
            "\t\tesac; fi ;;\n",
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
    ],
    "poteto-mode/scripts/orch/store.ts": [
        (
            "  mkdir,\n"
            "  open,\n",
            "  link,\n"
            "  mkdir,\n",
        ),
        (
            "  rm,\n",
            "  rm,\n"
            "  rmdir,\n",
        ),
        (
            "async function atomicWrite(path: string, contents: string): Promise<void> {\n",
            "async function atomicWrite(\n"
            "  path: string,\n"
            "  contents: string,\n"
            "  { exclusive = false } = {}\n"
            "): Promise<void> {\n",
        ),
        (
            "    await rename(temporary, path);\n",
            "    await (exclusive ? link : rename)(temporary, path);\n",
        ),
        (
            "  readonly force?: boolean;\n"
            "  readonly onLockStolen?: (holder: string) => void;\n",
            "",
        ),
        (
            "async function readPointers(\n"
            "  directory: string\n"
            "): Promise<readonly InboxPointer[]> {\n",
            "interface PointerFile {\n"
            "  readonly name: string;\n"
            "  readonly pointer: InboxPointer;\n"
            "}\n"
            "\n"
            "async function readPointers(\n"
            "  directory: string\n"
            "): Promise<readonly PointerFile[]> {\n",
        ),
        (
            "  const result: InboxPointer[] = [];\n",
            "  const result: PointerFile[] = [];\n",
        ),
        (
            "    result.push({\n"
            "      ts: row[0] ?? \"\",\n"
            "      agent: row[1] ?? \"\",\n"
            "      unit: row[2] ?? \"\",\n"
            "      status: row[3] ?? \"\",\n"
            "      report: row[4] ?? \"\",\n"
            "    });\n",
            "    result.push({\n"
            "      name: entry.name,\n"
            "      pointer: {\n"
            "        ts: row[0] ?? \"\",\n"
            "        agent: row[1] ?? \"\",\n"
            "        unit: row[2] ?? \"\",\n"
            "        status: row[3] ?? \"\",\n"
            "        report: row[4] ?? \"\",\n"
            "      },\n"
            "    });\n",
        ),
        (
            "async function acquireLock(\n",
            "async function replaceEmptyDirectory(\n"
            "  source: string,\n"
            "  target: string\n"
            "): Promise<boolean> {\n"
            "  try {\n"
            "    await rename(source, target);\n"
            "    return true;\n"
            "  } catch (error) {\n"
            "    const code = errorCode(error);\n"
            "    if (code === \"ENOTEMPTY\" || code === \"EEXIST\") return false;\n"
            "    throw error;\n"
            "  }\n"
            "}\n"
            "\n"
            "async function clearDeadOwners(guard: string): Promise<void> {\n"
            "  let entries: string[] = [];\n"
            "  try {\n"
            "    entries = await readdir(guard);\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) !== \"ENOENT\") throw error;\n"
            "  }\n"
            "  for (const entry of entries) {\n"
            "    const holder = entry.split(\".\")[0] ?? \"\";\n"
            "    if (!holderIsDead(holder))\n"
            "      throw new UserError(\n"
            "        `lock acquisition in progress by pid ${holder}; retry`\n"
            "      );\n"
            "    await rm(join(guard, entry), { force: true });\n"
            "  }\n"
            "}\n"
            "\n"
            "async function acquireGuard(store: string): Promise<() => Promise<void>> {\n"
            "  const guard = join(store, \".orch.lock-acquisition\");\n"
            "  const owner = `${process.pid}.${randomUUID()}`;\n"
            "  const staged = `${guard}-${owner}`;\n"
            "  await mkdir(staged);\n"
            "  try {\n"
            "    await writeFile(join(staged, owner), \"\");\n"
            "    for (let attempt = 0; attempt < 3; attempt++) {\n"
            "      if (await replaceEmptyDirectory(staged, guard)) {\n"
            "        return async (): Promise<void> => {\n"
            "          await unlink(join(guard, owner));\n"
            "          try {\n"
            "            await rmdir(guard);\n"
            "          } catch (error) {\n"
            "            const code = errorCode(error);\n"
            "            if (code !== \"ENOENT\" && code !== \"ENOTEMPTY\" && code !== \"EEXIST\")\n"
            "              throw error;\n"
            "          }\n"
            "        };\n"
            "      }\n"
            "      await clearDeadOwners(guard);\n"
            "    }\n"
            "    throw new UserError(\"lock acquisition in progress; retry\");\n"
            "  } finally {\n"
            "    await rm(staged, { recursive: true, force: true });\n"
            "  }\n"
            "}\n"
            "\n"
            "async function acquireLock(\n",
        ),
        (
            "  const pid = String(process.pid);\n"
            "  const create = async (): Promise<void> => {\n",
            "  const token = `${process.pid}\\n${randomUUID()}\\n`;\n"
            "  const releaseGuard = await acquireGuard(store);\n"
            "  try {\n"
            "    let contents: string | null = null;\n"
            "    try {\n"
            "      contents = await readFile(path, \"utf8\");\n"
            "    } catch (error) {\n"
            "      if (errorCode(error) !== \"ENOENT\") throw error;\n"
            "    }\n"
            "    if (contents !== null) {\n"
            "      const holder = contents.trim().split(\"\\n\")[0] || \"unknown\";\n"
            "      if (!holderIsDead(holder))\n"
            "        throw new UserError(`store lock held by pid ${holder}; a live or unknown owner cannot be stolen, even with --force`);\n"
            "      options.onStaleLock?.(holder);\n"
            "      await unlink(path);\n"
            "    }\n",
        ),
        (
            "    const handle = await open(path, \"wx\");\n"
            "    await handle.writeFile(`${pid}\\n`);\n"
            "    await handle.close();\n"
            "  };\n"
            "\n"
            "  const takeOver = async (): Promise<void> => {\n"
            "    await unlink(path);\n"
            "    try {\n"
            "      await create();\n"
            "    } catch (retryError) {\n"
            "      if (errorCode(retryError) === \"EEXIST\") {\n"
            "        const retryHolder =\n"
            "          (await readFile(path, \"utf8\")).trim() || \"unknown\";\n"
            "        throw new UserError(`store lock held by pid ${retryHolder}`);\n"
            "      }\n"
            "      throw retryError;\n"
            "    }\n",
            "    await atomicWrite(path, token, { exclusive: true });\n",
        ),
        (
            "  };\n"
            "\n"
            "  try {\n"
            "    await create();\n"
            "  } catch (error) {\n"
            "    if (errorCode(error) !== \"EEXIST\") {\n"
            "      throw error;\n"
            "    }\n"
            "    let holder = \"unknown\";\n"
            "    try {\n"
            "      holder = (await readFile(path, \"utf8\")).trim() || \"unknown\";\n"
            "    } catch {\n"
            "      holder = \"unknown\";\n"
            "    }\n"
            "    if (holderIsDead(holder)) {\n"
            "      options.onStaleLock?.(holder);\n"
            "      await takeOver();\n"
            "    } else if (options.force) {\n"
            "      options.onLockStolen?.(holder);\n"
            "      await takeOver();\n"
            "    } else {\n"
            "      throw new UserError(`store lock held by pid ${holder}`);\n"
            "    }\n",
            "  } finally {\n"
            "    await releaseGuard();\n",
        ),
        (
            "  }\n"
            "\n"
            "  return async (): Promise<void> => {\n",
            "  }\n"
            "  return async (): Promise<void> => {\n",
        ),
        (
            "      if ((await readFile(path, \"utf8\")).trim() === pid) {\n"
            "        await unlink(path);\n"
            "      }\n",
            "      if ((await readFile(path, \"utf8\")) === token) await unlink(path);\n",
        ),
        (
            "      if (errorCode(error) !== \"ENOENT\") {\n"
            "        throw error;\n"
            "      }\n",
            "      if (errorCode(error) !== \"ENOENT\") throw error;\n",
        ),
        (
            "        const rows = await readPointers(inbox);\n"
            "        const drained = join(\n"
            "          store,\n"
            "          `.inbox-drain-${process.pid}-${randomUUID()}`\n"
            "        );\n"
            "        await rename(inbox, drained);\n"
            "        try {\n"
            "          await mkdir(inbox);\n"
            "        } catch (error) {\n"
            "          await rename(drained, inbox);\n"
            "          throw error;\n"
            "        }\n"
            "        await rm(drained, { recursive: true, force: true });\n"
            "        return rows;\n",
            "        const files = await readPointers(inbox);\n"
            "        for (const { name } of files) {\n"
            "          await unlink(join(inbox, name));\n"
            "        }\n"
            "        return files.map(({ pointer }) => pointer);\n",
        ),
        (
            "        return readPointers(join(store, \"inbox\"));\n",
            "        return (await readPointers(join(store, \"inbox\"))).map(\n"
            "          ({ pointer }) => pointer\n"
            "        );\n",
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
description: Routing target for `/poteto-mode` and any request for poteto's style. Spawn a fresh `poteto-agent` for each new task, and resume one only in the strict cases that poteto-mode's Subagents section names. Starts with the `poteto-mode` skill preloaded in full, including its inline Principles index. Substituting `general-purpose` skips that text and drifts.
skills:
  - poteto-mode
background: true
---

# Poteto subagent

You are operating as poteto-mode's full agent style. The `poteto-mode` skill is preloaded above. Follow it in full, including its inline Principles index, before doing any work. Load a leaf `principle-*` skill with the Skill tool whenever you apply that principle, and load a playbook with `/playbook-<name>`.
""",
    "pstack-readonly.md": """---
name: pstack-readonly
description: Read-only explorer, explainer, judge, or reviewer for pstack workflows (how, arena cross-judge, interrogate). The Claude Code form of Cursor's `readonly: true` subagent. Reads code with Read and searches with rg, grep, find, and ls through a Read hook. Has no shell and no tool that writes files, runs commands, spawns or steers agents, or schedules work, and keeps MCP tools. Defaults to Sonnet so a caller that omits `model` does not inherit the parent. Pass `model` when the role line names a different alias.
model: sonnet
disallowedTools: Bash, PowerShell, Write, Edit, NotebookEdit, EnterWorktree, ExitWorktree, Monitor, Agent, Workflow, Skill, SendMessage, TaskCreate, TaskUpdate, TaskStop, CronCreate, CronDelete, ScheduleWakeup, RemoteTrigger, PushNotification, DesignSync, Artifact, ArtifactComments, ArtifactData
hooks:
  PreToolUse:
    - matcher: Read
      hooks:
        - type: command
          timeout: 90
          command: >-
            hook=.claude/hooks/pstack-readonly-search.py; f="$CLAUDE_PROJECT_DIR/$hook";
            [ -f "$f" ] || f="$(git rev-parse --show-toplevel 2>/dev/null)/$hook";
            [ -f "$f" ] || { echo "pstack-readonly: $hook is missing, so search is off" >&2; exit 1; };
            for py in python3 python; do
            if "$py" -c 'import sys; sys.exit(sys.version_info < (3, 8))' </dev/null 2>/dev/null;
            then exec "$py" "$f"; fi;
            done;
            echo 'pstack-readonly: search needs Python 3.8 or later on PATH' >&2; exit 1
background: true
---

# pstack read-only subagent

You are a read-only subagent. Answer the brief from the code, the transcripts, and the tools you can read with. You have no shell. You have no Skill tool and nothing is preloaded, so when a brief needs a skill's text it names the file (.claude/skills/<name>/SKILL.md) and you read it. To search, call Read with `file_path` set to `/.pstack-search/` followed by one `rg`, `grep`, `find`, or `ls` command, quoted as in a shell, for example `/.pstack-search/rg -n 'def main' src` or `/.pstack-search/rg --files -g '*.ts'`. A hook runs that command without a shell and returns its output as the file you read, so page through long results with `offset` and `limit`, and use each tool's own options in place of pipes and globs. Use Glob and Grep instead when this session has them. If such a read says the file does not exist, search is off in this session (Claude Code skips project hooks in an untrusted workspace), so say so instead of guessing paths. Never create, edit, or delete files, and never call a tool with side effects (no installs, no deploys, no network writes). If the brief needs a write, report that instead of doing it. Return findings as file pointers with line numbers, not inlined dumps.
""",
}

COMMENT_SICKO_FRONTMATTER = (
    "---\nname: Comment Sicko\ndescription: A deranged comment-hater that savors deletion and condemns workaround code.\n---\n",
    "---\nname: comment-sicko\ndescription: Comment Sicko. A deranged comment-hater that savors deletion and condemns workaround code.\nbackground: true\n---\n",
)
COMMENT_SICKO_HOWWHY = (
    "I run `/how`, `/why`, or both from the **how** and **why** skills on the named symbol or call.",
    "I run `/how`, `/why`, or both from the **how** and **why** skills (`.claude/skills/how/SKILL.md`, `.claude/skills/why/SKILL.md`) on the named symbol or call.",
)

SETUP_PSTACK = """---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget. Detects the models this Claude Code session can run subagents on and writes an always-applied personal rule that overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.claude/rules/pstack-models.md`, a personal rule Claude Code loads in every session, that sets pstack's model per role.

## Steps

### 1. Detect available models

Enumerate the model values you can pass to the `Agent` tool in this session. Its `model` parameter lists the aliases (`sonnet`, `opus`, `haiku`, `fable`). That is the dependable source. Confirm each alias with a probe: spawn one `pstack-readonly` subagent per alias, all in one message, with the prompt "reply ok", and drop any alias whose spawn fails or whose reply says the model is unavailable. Pass `model` on every probe. `pstack-readonly` pins `model: sonnet`, so a probe that omits `model` does not test the alias. Omitting `model` is wrong, because without that pin the call inherits the parent model. If you cannot detect any, ask the user to paste the aliases or full model IDs they have access to. Never write a value you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected values.

### 2. Load current state

The default role-to-model mapping is the rule shape shown in step 5 below. If `~/.claude/rules/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer `AskUserQuestion` over free text. Offer these four options with these exact labels, and name the current budget when the rule records one.

- `unlimited - keep max`
- `large - xhigh reasoning`
- `medium - high reasoning`
- `small - medium reasoning`

**(b) Apply it.** Build the working table from the skill defaults, and on a re-run keep any role you changed by alias, list, or `inherit-parent` and `auto`. In Claude Code the reasoning budget is the effort level, which every pack subagent inherits because none pins `effort`, so the budget does not change the model values. `unlimited` is effort `max`, `large` is `xhigh`, `medium` is `high`, and `small` is `medium`. Record the label and its effort on the `# budget` line. `inherit-parent` and `auto` do not change.

**(c) Show the roles and confirm.** Show every role with its model, marking any value not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent chat model, which keeps a session on whatever model the user picked) as the options. Prefer `AskUserQuestion` over free text. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it that differs from the parent's model when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real value written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen value is not available, stop and ask again.

### 5. Write the rule

Write `~/.claude/rules/pstack-models.md` with a `# budget` line carrying the chosen label and its target effort, and one line per role, using the same labels poteto-mode uses. No frontmatter, so the rule loads in every session. Overwrite the whole file so re-runs stay idempotent. Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent chat model (omit `model` on the Agent call). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited (max)
feature, refactoring: opus
bug-fix: opus
perf-issue: opus
hillclimb: opus
judgment and prose: opus
hardest tasks: fable
how explorer: sonnet
how explainer: opus
why investigators: sonnet
why synthesizer: opus
reflect tooling: sonnet
reflect judgment, divergent, synthesizer: opus
arena runners: fable, opus, sonnet
arena cross-judge pool: fable, opus, sonnet
swarm workers: sonnet
architect runners: fable, opus, sonnet
interrogate reviewers: fable, opus, sonnet
```

### 6. Apply the budget and confirm

Write the budget into the project's `.claude/settings.local.json`, creating the file when it is missing and keeping every other key. For `xhigh`, `high`, and `medium`, merge `"effortLevel": "<level>"` at the top level and remove any `CLAUDE_CODE_EFFORT_LEVEL` entry from its `env` object. For `max`, merge `"CLAUDE_CODE_EFFORT_LEVEL": "max"` into the `env` object and remove any top-level `effortLevel`, because Claude Code keeps `max` only for the current session unless the environment variable sets it. A project-level setting applies to every model, Opus 5.5 included, and to every subagent, and it loads in each new session for this project; `/effort` still changes the current session, and an organization cap still applies. Tell the user the rule and the setting were written and take effect in new sessions. Re-running this skill updates both.

### 7. Check the skill listing

Claude Code's skill listing shows each skill's `description` and `when_to_use` text, and its budget is 1% of the context window by default, about 8,000 characters at 200k.
The pack's `.claude/settings.json` sets `skillListingBudgetFraction` to `0.02`, about 16,000 characters.
Measure the project's skills with `cat .claude/skills/*/SKILL.md | grep -E '^(description|when_to_use):' | wc -c`, run the same command over `~/.claude/skills/*/SKILL.md` for the personal ones, and compare the sum of the two counts with the budget.
When the sum exceeds the budget, tell the user, point at the personal copies in `~/.claude/skills/` that duplicate pack skills (a personal skill shadows the project skill with the same name), and offer `SLASH_COMMAND_TOOL_CHAR_BUDGET` for a larger fixed budget.

### 8. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, `/run` and `/verify` already taught the project, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, invoke `/create-verification-skill` (resolves wherever pstack is installed: project or personal skills). On no, move on without pushing.
"""

SETTINGS_JSON = r"""{
  "env": {
    "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"
  },
  "skillListingBudgetFraction": 0.02,
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/poteto-mode-reminder.sh\"",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "sh \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/poteto-mode-compact.sh\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
"""

# poteto-mode's H2 sections in the order the port writes them. Compaction keeps only the start of a skill, so
# Playbooks follows Non-negotiables, and Subagents goes last because the compaction hook re-supplies the last section.
SECTION_ORDER = ("Non-negotiables", "Playbooks", "Principles", "Autonomy", "Writing the reply", "Comments", "Subagents")
PORT_NOTE = (
    "This is the Claude Code port. Load a named skill or playbook with the Skill tool rather than by reading its file, "
    "because only a Skill-tool load survives compaction (`/playbook-<name>` loads `playbooks/<name>.md`). "
    "`references/claude-code.md` maps Cursor's mechanisms to Claude Code, and relative paths resolve under `.claude/skills/poteto-mode/`."
)
REMINDER_SUFFIX = "Apply means the Skill tool, /poteto-mode. When its text is already in context, stay in it instead of invoking it again."
RECOVERY_NOTE = (
    "Context was compacted. If /poteto-mode was applied in this session, do this before any other action. "
    "1. Run TaskList and continue from the first open playbook step. "
    "2. If the matched playbook's steps are not in context, load them again with the Skill tool: /playbook-<name>. "
    "3. The re-attached poteto-mode text is cut at a fixed length and its final section is reproduced below in full, "
    "so use this copy of that section. "
    f'If the re-attached text ends before its "## {SECTION_ORDER[-1]}" heading, Read .claude/skills/poteto-mode/SKILL.md '
    "in full instead; invoking the skill again does not re-append it."
)
FINAL_SECTION_RULE = "--- poteto-mode, final section (reproduced because compaction cuts it) ---"
# Claude Code caps a hook's additionalContext at 10,000 characters. Past that it hands the model a file path and a
# 2,000-character preview instead, which would drop the reproduced final section.
HOOK_CONTEXT_MAX = 10_000
HOOK_HEADER = "#!/bin/sh\n# Generated by claude/port/port.py; edit the generator, not this file.\n"
# After compaction Claude Code re-attaches each skill's latest Skill-tool load, a `Base directory for this skill` header
# plus the body, and cuts it to its first 19,900 characters and a 100-character marker once it passes 20,000.
COMPACT_SKILL_CHARS = 20_000
COMPACT_MARKER_CHARS = 100
COMPACT_HEADER_ALLOWANCE = 300
COMPACT_KEPT = COMPACT_SKILL_CHARS - COMPACT_MARKER_CHARS - COMPACT_HEADER_ALLOWANCE
# The mode's Playbooks section has to end this early in the body, well inside what compaction keeps.
PLAYBOOKS_END_MAX = 12_000
# Each playbook is also a skill, because compaction re-attaches a Skill-tool load and nothing of a file that was read.
# The skill carries the playbook's text itself. Claude Code runs a skill's !`command` while the skill loads, with the
# arguments pasted in as typed and no permission prompt, so a loader that handed the playbook name to a shell ran
# whatever an apostrophe in the name let in, and no quoting could close that.
PLAYBOOK_SKILL_PREFIX = "playbook-"
PLAYBOOK_TITLE = re.compile(r"^### (.+)\n")
PLAYBOOK_SKILL = """---
name: {prefix}{name}
description: {description}
---

The {title} playbook, from .claude/skills/poteto-mode/playbooks/{name}.md. Relative paths below resolve under .claude/skills/poteto-mode/.

{text}"""
PLAYBOOK_DESCRIPTION = "The {title} playbook of poteto-mode, loaded through the Skill tool so its steps survive compaction."
# A shell command Claude Code runs while a skill loads: !`...` at a line start or after whitespace, or a fenced block
# opened with ```!. A `!` after any other character, as in a table cell quoting the operator, stays text.
SHELL_COMMAND = re.compile(r"(?:^|(?<=\s))!`|^```!", re.MULTILINE)

CLAUDE_CODE_REFERENCE = f"""# Claude Code environment

This is the Claude Code port of the Cursor pstack plugin. Cursor's mechanisms map as follows.

- **Mode and reminder.** Cursor's sticky mode and per-turn reminder are two hooks the pack's `.claude/settings.json` installs: `UserPromptSubmit` repeats the reminder on every turn, and `SessionStart` on `compact` re-supplies the mode's final section and the recovery steps after auto-compaction. `/poteto-mode` loads the mode once, and it stays in effect for the rest of the task.
- **Compaction.** Auto-compaction keeps the first 20,000 characters of each Skill-tool load and nothing of a file you read, so load skills and playbooks with the Skill tool (`/playbook-<name>` for a playbook). The todolist survives compaction, and `TaskList` is the first call after it.
- **Skills.** `.claude/skills/<name>/SKILL.md`. Every pstack skill is in your skill listing. When this mode or a playbook names a skill (**how**, `/unslop`, a `principle-*`), load it with the Skill tool. The user can also type `/<name>`. A `pstack-readonly` subagent has no Skill tool, so a brief that needs a skill's text names the file for it to read. Relative paths in the mode's text (`playbooks/feature.md`, `references/bugbot-triage.md`, `scripts/`) resolve under `.claude/skills/poteto-mode/`. Shell commands run from the repository root, so the playbooks spell script paths out in full (`{POTETO_SCRIPTS}/...`).
- **Subagents.** The `Agent` tool with `subagent_type`. `poteto-agent`, `comment-sicko`, and `pstack-readonly` are pstack's own, in `.claude/agents/`. `poteto-agent` starts with this mode preloaded, so arena runners and in-playbook delegates begin inside it. `general-purpose` is Claude Code's read-write agent. `pstack-readonly` is Cursor's `readonly: true`: no shell, no file writes, none of the tools that run commands, spawn or steer agents, or schedule work (`Monitor`, `Agent`, `Workflow`, `Skill`, `SendMessage`, and the task and cron tools), search through a `Read` hook that runs only `rg`, `grep`, `find`, and `ls`, MCP kept, and its frontmatter pins `model: sonnet`. An explicit `model` on the Agent call still selects the role (an explainer or a judge on `opus` or `fable`). Subagents run in the background, so Cursor's `run_in_background: true` needs no flag. `isolation: "worktree"` gives a subagent its own checkout in place of Cursor's `environment: "cloud"`, and `isolation: "remote"` runs it in a cloud session when the account allows it. Resume or message an existing subagent with `SendMessage`, and only in the strict cases that the Subagents section names.
- **Models.** The values the `Agent` tool accepts: the aliases `fable`, `opus`, `sonnet`, and `haiku`, or a full model ID. The reasoning budget is the effort level `/setup-pstack` writes into the project's `.claude/settings.local.json`; it applies to every model, and every pack subagent inherits it because none pins `effort`. An explicit `--effort`, `/effort`, or `CLAUDE_CODE_EFFORT_LEVEL` wins for that session. The model rule `/setup-pstack` writes is `~/.claude/rules/pstack-models.md`.
- **Todolist.** The task list (`TaskCreate`, `TaskUpdate`, `TaskList`). The pack's `.claude/settings.json` turns those tools on for every session in this repository. If they are missing, the settings were not merged; fix that rather than keeping the list in the reply.
- **Commands.** Questions to the human use `AskUserQuestion`. `/loop` is Claude Code's own. `/simplify` is Claude Code's slop-strip, in place of `/deslop`. `/run` and `/verify`, or a project `verify-<app>` skill, drive the real app in place of `control-cli` and `control-ui`. `/tasks` shows background subagents in place of the Cursor dashboard.
- **Transcripts.** {TRANSCRIPT_DIR} The current session is `$CLAUDE_CODE_SESSION_ID.jsonl` there, and its subagents are under `<session>/subagents/`. Never read another project's directory unless asked.
- **Store.** The pstack store is `~/.claude/pstack/store/`. Playbooks that write outside the repository (Orchestrate, Multi-phase plan) write there.
- **Review bots.** Bugbot, Greptile, and the agentic security review are GitHub-side and unchanged. Cursor Automations have no Claude Code equivalent.
"""

# Text a finished port must not contain: Cursor paths, tools, and model slugs, a forced worktree removal, and the em dash this repo bans.
LEFTOVERS = (
    "pstack-models.mdc",
    ".cursor/",
    "generalPurpose",
    "`Task`",
    "Task tool",
    "AskQuestion",
    "create-skill",
    "cursor-team-kit",
    "agent-transcripts",
    "grok-4.7",
    "gpt-5.6",
    "claude-opus-5-5-max",
    "worktree remove --force",
    "\u2014",
)
CURSOR_SKILL_KEYS = {"mode", "icon", "color", "reminder"}
# Upstream hides nearly every skill from the model; the port lets agents load all of them through the Skill tool.
DROPPED_SKILL_KEYS = CURSOR_SKILL_KEYS | {"disable-model-invocation"}
DISABLE_MODEL_INVOCATION = re.compile(r"^disable-model-invocation:.*\n", re.MULTILINE)
# A backticked command that starts at a skill's scripts/ directory breaks when run from the repository root.
RELATIVE_SCRIPT = re.compile(r"`(?:bun |node )?scripts/[^`\s]")
SEARCH_HOOK = Path("hooks") / "pstack-readonly-search.py"
# The skill listing shows every skill's description, so each principle keeps only its first sentence, which says
# when to apply it. A first sentence outside these bounds cannot stand alone as the description.
PRINCIPLE_SENTENCE_MIN = 20
PRINCIPLE_DESCRIPTION_MAX = 160
DESCRIPTION_VALUE = re.compile(r"^description:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


class RollbackFailed(Exception):
    pass


def apply(path: Path, pairs: list[tuple[str, str]], name: str) -> int:
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{name}: expected exactly one match, found {count} for:\n{old[:160]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return len(pairs)


def skill_body(text: str) -> str:
    """The text after a skill's frontmatter."""
    return text[text.find("\n---\n", 3) + 5:]


def split_sections(body: str) -> tuple[str, list[str]]:
    """Split a skill body into the text before its first H2 heading and its H2 sections, each up to the next heading."""
    intro, *sections = re.split(r"^(?=## )", body, flags=re.MULTILINE)
    return intro, sections


def heading(section: str) -> str:
    return section.partition("\n")[0][3:].strip()


def port_poteto_mode(path: Path) -> str:
    """Rewrite the mode so compaction keeps its playbooks, and return the upstream reminder for the mode hooks."""
    rel = "skills/poteto-mode/SKILL.md"
    text = path.read_text(encoding="utf-8")
    keys = frontmatter(text) or {}
    for key in ("description", "reminder"):
        if not keys.get(key):
            raise SystemExit(f"{rel}: upstream frontmatter lost {key}")
    intro, sections = split_sections(skill_body(text))
    names = [heading(section) for section in sections]
    missing = [name for name in SECTION_ORDER if name not in names]
    unexpected = [name for i, name in enumerate(names) if name not in SECTION_ORDER or name in names[:i]]
    if missing or unexpected:
        raise SystemExit(
            f"{rel}: upstream H2 sections changed (missing {missing}, unexpected {unexpected}); "
            "update SECTION_ORDER in port.py so every section has its place"
        )
    top, title, rest = intro.partition("# Poteto mode\n")
    if not title:
        raise SystemExit(f"{rel}: upstream lost the # Poteto mode heading that the port note follows")
    rest = rest.lstrip("\n")
    by_name = dict(zip(names, sections))
    ordered = "\n\n".join(by_name[name].rstrip("\n") for name in SECTION_ORDER)
    path.write_text(
        f"---\nname: poteto-mode\ndescription: {keys['description']}\n---\n{top}{title}\n{PORT_NOTE}\n\n{rest}{ordered}\n",
        encoding="utf-8",
    )
    return keys["reminder"]


def hook_script(event: str, context: str) -> str:
    """A POSIX sh hook that hands `context` to the model as the event's additional context."""
    if len(context) > HOOK_CONTEXT_MAX:
        raise SystemExit(
            f"the {event} hook's additional context is {len(context)} characters, over {HOOK_CONTEXT_MAX}, "
            "so Claude Code would hand the model a file path and a 2,000-character preview instead of the text"
        )
    output = json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})
    return f"{HOOK_HEADER}cat <<'EOF'\n{output}\nEOF\n"


def unquote(value: str) -> tuple[str, str]:
    """Split a frontmatter value into its surrounding double quote, if it has one, and the text inside."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return '"', value[1:-1]
    return "", value


def cut_principle_description(md: Path) -> None:
    """Cut a principle skill's description to its first sentence, quoted the way upstream quotes it."""
    rel = f"skills/{md.parent.name}/SKILL.md"
    text = md.read_text(encoding="utf-8")
    match = DESCRIPTION_VALUE.search(text, 0, text.find("\n---\n", 3) + 1)
    if not match:
        raise SystemExit(f"{rel}: no description to cut to its first sentence")
    quote, value = unquote(match.group(1))
    end = SENTENCE_END.search(value)
    if not end:
        raise SystemExit(f"{rel}: the description has no sentence end to cut it at")
    sentence = value[: end.end()]
    if not PRINCIPLE_SENTENCE_MIN <= len(sentence) <= PRINCIPLE_DESCRIPTION_MAX:
        raise SystemExit(
            f"{rel}: the description's first sentence is {len(sentence)} characters, outside "
            f"{PRINCIPLE_SENTENCE_MIN} to {PRINCIPLE_DESCRIPTION_MAX}, so it cannot stand alone as the description"
        )
    md.write_text(f"{text[:match.start(1)]}{quote}{sentence}{quote}{text[match.end(1):]}", encoding="utf-8")


def playbook_skills(mode: Path, skills: Path) -> None:
    """Write each playbook as a static skill, so a Skill-tool load carries its steps across compaction."""
    for md in sorted((mode.parent / "playbooks").glob("*.md")):
        rel = f"skills/poteto-mode/playbooks/{md.name}"
        text = md.read_text(encoding="utf-8")
        title = PLAYBOOK_TITLE.match(text)
        if not title:
            raise SystemExit(f"{rel}: no ### title on the first line to describe the {PLAYBOOK_SKILL_PREFIX}{md.stem} skill")
        skill = skills / f"{PLAYBOOK_SKILL_PREFIX}{md.stem}"
        if skill.exists():
            raise SystemExit(f"skills/{skill.name}: upstream now ships a skill of this name, so the playbook cannot become it")
        skill.mkdir()
        description = json.dumps(PLAYBOOK_DESCRIPTION.format(title=title.group(1).strip()))
        (skill / "SKILL.md").write_text(
            PLAYBOOK_SKILL.format(
                prefix=PLAYBOOK_SKILL_PREFIX, name=md.stem, title=title.group(1).strip(), description=description, text=text
            ),
            encoding="utf-8",
        )


def build(upstream: Path, pack: Path) -> int:
    """Write the complete ported pack under `pack` and return the number of substitutions applied."""
    skills = pack / "skills"
    agents = pack / "agents"
    shutil.copytree(upstream / "skills", skills)
    agents.mkdir()
    for f in (upstream / "agents").glob("*.md"):
        shutil.copy2(f, agents / f.name)

    applied = 0
    for rel, pairs in {**SUBSTITUTIONS, **SCRIPT_SUBSTITUTIONS}.items():
        applied += apply(skills / rel, pairs, f"skills/{rel}")
    mode = skills / "poteto-mode" / "SKILL.md"
    reminder = port_poteto_mode(mode)
    for md in skills.glob("*/SKILL.md"):
        text = md.read_text(encoding="utf-8")
        end = text.find("\n---\n", 3) + 1
        md.write_text(DISABLE_MODEL_INVOCATION.sub("", text[:end]) + text[end:], encoding="utf-8")
    for md in sorted(skills.glob("principle-*/SKILL.md")):
        cut_principle_description(md)
    playbook_skills(mode, skills)
    applied += apply(agents / "comment-sicko.md", [COMMENT_SICKO_FRONTMATTER, COMMENT_SICKO_HOWWHY], "agents/comment-sicko.md")
    for name, body in AGENTS.items():
        (agents / name).write_text(body, encoding="utf-8")
    for src in sorted(ADDED_SKILL_FILES.rglob("*")):
        if src.is_file():
            dest = skills / src.relative_to(ADDED_SKILL_FILES)
            if dest.exists():
                raise SystemExit(f"{dest}: upstream now ships this file; port it with a substitution instead")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    (mode.parent / "references").mkdir(exist_ok=True)
    (mode.parent / "references" / "claude-code.md").write_text(CLAUDE_CODE_REFERENCE, encoding="utf-8")
    _, sections = split_sections(skill_body(mode.read_text(encoding="utf-8")))
    final_section = sections[-1].rstrip("\n")
    mode_hooks = {
        "poteto-mode-reminder.sh": hook_script("UserPromptSubmit", f"{reminder} {REMINDER_SUFFIX}"),
        "poteto-mode-compact.sh": hook_script(
            "SessionStart", f"{RECOVERY_NOTE}\n\n{FINAL_SECTION_RULE}\n\n{final_section}\n\n{reminder}"
        ),
    }
    (pack / "hooks").mkdir()
    for name, script in mode_hooks.items():
        (pack / "hooks" / name).write_text(script, encoding="utf-8")
        (pack / "hooks" / name).chmod(0o755)
    (skills / "setup-pstack").mkdir(exist_ok=True)
    (skills / "setup-pstack" / "SKILL.md").write_text(SETUP_PSTACK, encoding="utf-8")
    (pack / "settings.json").write_text(SETTINGS_JSON, encoding="utf-8")
    return applied


def frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end < 0:
        return None
    keys = {}
    for line in text[4:end].splitlines():
        match = re.match(r"([A-Za-z][\w-]*):(.*)", line)
        if match:
            keys[match.group(1)] = match.group(2).strip()
    return keys


def mode_layout_problems(mode: Path) -> list[str]:
    """Check that compaction keeps the mode's Playbooks section and cuts the mode no earlier than its last section."""
    rel = "skills/poteto-mode/SKILL.md"
    intro, sections = split_sections(skill_body(mode.read_text(encoding="utf-8")))
    names = [heading(section) for section in sections]
    if tuple(names) != SECTION_ORDER:
        return [f"{rel}: H2 sections are {names}, not {list(SECTION_ORDER)}"]
    spans, start = {}, len(intro)
    for name, section in zip(names, sections):
        spans[name] = (start, start + len(section))
        start += len(section)
    problems = []
    if spans["Playbooks"][1] > PLAYBOOKS_END_MAX:
        problems.append(
            f"{rel}: the Playbooks section ends at body offset {spans['Playbooks'][1]}, past {PLAYBOOKS_END_MAX}, "
            "so compaction could cut the playbooks"
        )
    if spans[names[-1]][0] > COMPACT_KEPT:
        problems.append(
            f"{rel}: the last section, {names[-1]}, starts at body offset {spans[names[-1]][0]}, past {COMPACT_KEPT}, "
            "so compaction cuts text before it that the compaction hook does not re-supply"
        )
    return problems


def validate(pack: Path, target: Path) -> None:
    """Refuse a staged pack that is not a complete Claude Code port."""
    problems = []
    for kind, pattern, forbidden in (("skills", "*/SKILL.md", DROPPED_SKILL_KEYS), ("agents", "*.md", {"is_background"})):
        for md in sorted((pack / kind).glob(pattern)):
            name = md.parent.name if kind == "skills" else md.stem
            keys = frontmatter(md.read_text(encoding="utf-8"))
            if keys is None:
                problems.append(f"{kind}/{name}: no frontmatter")
            elif keys.get("name") != name or not keys.get("description"):
                problems.append(f"{kind}/{name}: frontmatter needs name: {name} and a description")
            elif forbidden & keys.keys():
                problems.append(f"{kind}/{name}: keys the port drops {sorted(forbidden & keys.keys())}")
            elif kind == "agents" and "effort" in keys:
                problems.append(f"{kind}/{name}: pins effort, so the budget /setup-pstack writes would not reach it")
    for md in sorted((pack / "skills").glob("principle-*/SKILL.md")):
        _, description = unquote((frontmatter(md.read_text(encoding="utf-8")) or {}).get("description", ""))
        if len(description) > PRINCIPLE_DESCRIPTION_MAX:
            problems.append(
                f"skills/{md.parent.name}: description is {len(description)} characters, over {PRINCIPLE_DESCRIPTION_MAX}"
            )
    for skill in sorted((pack / "skills").iterdir()):
        if not (skill / "SKILL.md").is_file():
            problems.append(f"skills/{skill.name}: no SKILL.md")
            continue
        if skill.name in RETIRED_SKILLS:
            problems.append(f"skills/{skill.name}: a retired name, which a refresh removes; drop it from RETIRED_SKILLS to produce it again")
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        if SHELL_COMMAND.search(text):
            problems.append(
                f"skills/{skill.name}: runs a shell command while it loads, and Claude Code pastes the skill's arguments "
                "into that command as typed, so the pack keeps every skill static"
            )
        if skill.name.startswith(PLAYBOOK_SKILL_PREFIX) and len(skill_body(text)) > COMPACT_KEPT:
            problems.append(
                f"skills/{skill.name}: the body is {len(skill_body(text))} characters, past {COMPACT_KEPT}, "
                "so compaction would cut its steps"
            )
    for md in sorted(pack.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        rel = md.relative_to(pack)
        problems += [f"{rel}: leftover {token!r}" for token in LEFTOVERS if token in text]
        if match := RELATIVE_SCRIPT.search(text):
            problems.append(f"{rel}: script path relative to the skill directory: {match.group(0)}")
    problems += mode_layout_problems(pack / "skills" / "poteto-mode" / "SKILL.md")
    if not (target / SEARCH_HOOK).is_file():
        problems.append(f"{SEARCH_HOOK} is missing, and the pstack-readonly agent searches through it")
    if problems:
        raise SystemExit("refusing to install the refreshed pack:\n  " + "\n  ".join(problems))


def retired_skills(target: Path) -> list[str]:
    """The retired skills installed under `target`, recognized by the contents an earlier pack produced."""
    found = []
    for name, retired in sorted(RETIRED_SKILLS.items()):
        skill = target / "skills" / name / "SKILL.md"
        if skill.is_file() and hashlib.sha256(skill.read_bytes()).hexdigest() in retired.digests:
            found.append(name)
    return found


def install(pack: Path, target: Path, replaced: Path, retired: list[str]) -> None:
    """Move every staged entry into `target` and the `retired` skills out of it, restoring the replaced entries if any step fails."""
    entries = [Path("skills") / name for name in retired] + [Path("settings.json")]
    entries += [Path(kind) / entry.name for kind in ("skills", "agents", "hooks") for entry in sorted((pack / kind).iterdir())]
    started: list[Path] = []
    try:
        for rel in entries:
            destination, old = target / rel, replaced / rel
            started.append(rel)
            if destination.exists() or destination.is_symlink():
                old.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(old)
            if (pack / rel).exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                (pack / rel).rename(destination)
    except BaseException:
        try:
            for rel in reversed(started):
                destination, old = target / rel, replaced / rel
                if not (pack / rel).exists() and (destination.exists() or destination.is_symlink()):
                    destination.rename(pack / rel)
                if old.exists() or old.is_symlink():
                    old.rename(destination)
        except BaseException as error:
            raise RollbackFailed(f"could not restore the installed pack: {error}") from error
        raise


def port(upstream: Path, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".pstack-port-", dir=target))
    pack = work / "pack"
    try:
        applied = build(upstream, pack)
        validate(pack, target)
        staged = {entry.name for entry in (pack / "skills").iterdir()}
        retired = retired_skills(target)
        install(pack, target, work / "replaced", retired)
    except RollbackFailed as error:
        raise SystemExit(f"{error}. The replaced entries are kept in {work / 'replaced'}.") from error
    except BaseException as error:
        shutil.rmtree(work, ignore_errors=True)
        raise SystemExit(f"{error}\nThe installed pack under {target} was not changed.") from error
    shutil.rmtree(work)
    for name in retired:
        print(f"removed skills/{name}, which an earlier pack produced: {RETIRED_SKILLS[name].reason}")
    extra = sorted({entry.name for entry in (target / "skills").iterdir()} - staged)
    if extra:
        print(f"kept skills this refresh did not produce (delete any that upstream removed): {', '.join(extra)}")
    return applied


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    target = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else DEFAULT_PACK
    applied = port(Path(sys.argv[1]).resolve(), target)
    print(f"ported {applied} substitutions into {target}")


if __name__ == "__main__":
    main()
