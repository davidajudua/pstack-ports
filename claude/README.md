# pstack for Claude Code

A Claude Code native port of [pstack](https://github.com/cursor/plugins/tree/a58628271271837ef5f386adca29c0812683a19a/pstack) 0.15.8, Lauren Tan's Cursor plugin of engineering skills and playbooks.

## Install

The pack installs into a project's `.claude/` directory.
It must live there, because the skills run their helper scripts from `.claude/skills/poteto-mode/scripts/`, and the read-only agent and the settings run their hooks from `$CLAUDE_PROJECT_DIR/.claude/hooks/`.

```sh
mkdir -p <project>/.claude
cp -R claude/pack/skills claude/pack/agents claude/pack/hooks <project>/.claude/
```

Then merge `claude/pack/settings.json` into `<project>/.claude/settings.json`.
It sets `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`, because the playbooks open a todolist first and some models have no task-list tools without it.
It also installs the two mode hooks: a per-turn reminder on `UserPromptSubmit`, and a compaction recovery on `SessionStart` with the `compact` matcher.
Interactive sessions run project hooks only after the workspace trust prompt is accepted.
The hooks live in settings because hooks in skill frontmatter neither fire on compaction nor survive `--resume`.

Personal skills in `~/.claude/skills/` win over project skills with the same name, so remove or rename any older pstack copy there.

The `poteto-mode` helper scripts need `bun`, and `worktree-audit.sh` needs `rg`, `gh`, and `jq`.

## Use

1. Run `/setup-pstack` once to pick a reasoning budget and per-role models.
   It writes `~/.claude/rules/pstack-models.md` and the project's effort setting in `.claude/settings.local.json`.
2. Type `/poteto-mode` to start the mode, as in Cursor.
   It stays in effect for the session through the per-turn reminder.
   The mode matches the task to a playbook, loads it with `/playbook <name>`, opens a todolist with its steps, and loads the skills it needs.
3. Type `/<skill>` to run any skill directly, for example `/how`, `/why`, `/interrogate`, or `/reflect`.

## Layout

| Path | What it is |
|---|---|
| `pack/skills/` | 51 skills: the 50 upstream skills, with `setup-pstack` rewritten for Claude Code, plus `playbook`, the `/playbook <name>` loader. `poteto-mode/` holds the mode, its 23 playbooks, references, and scripts. |
| `pack/agents/` | `poteto-agent` and `comment-sicko` from upstream, plus `pstack-readonly`, the Claude Code form of Cursor's `readonly: true` subagent. |
| `pack/hooks/` | `pstack-readonly-search.py`, the `Read` hook `pstack-readonly` searches through, which is maintained by hand. `poteto-mode-reminder.sh` and `poteto-mode-compact.sh`, the two mode hooks, which are generated. |
| `pack/settings.json` | The todo-tools environment variable and the two mode hooks. |
| `port/port.py` | The generator. |
| `port/added-skills/` | Whole files the pack adds under `skills/`: the `playbook` skill and the regression tests for the safety fixes. |
| `port/tests/` | Tests for the generator, the search hook, and the worktree audit. |

## Cursor to Claude Code mapping

| Cursor | Claude Code |
|---|---|
| `Task` tool, `subagent_type: generalPurpose` | `Agent` tool, `subagent_type: general-purpose` |
| `subagent_type: "Comment Sicko"` | `subagent_type: "comment-sicko"` |
| `readonly: true` | `subagent_type: pstack-readonly` |
| `run_in_background: true` | Default, since subagents run in the background |
| `environment: "cloud"` | `isolation: "worktree"`, or `isolation: "remote"` when the account allows cloud sessions |
| Resume an agent | `SendMessage` |
| `AskQuestion` (`allow_multiple`) | `AskUserQuestion` (`multiSelect`, at most four options) |
| `/deslop` from `cursor-team-kit` | Bundled `/simplify` |
| `control-cli`, `control-ui` | Bundled `/run` and `/verify`, or a project `verify-<app>` skill |
| Cursor's built-in `create-skill` | `skill-creator` |
| `~/.cursor/rules/pstack-models.mdc` | `~/.claude/rules/pstack-models.md` |
| `~/.cursor/projects/<slug>/agent-transcripts/` | `~/.claude/projects/<slug>/<session>.jsonl` |
| Model slugs with an effort suffix | Aliases `fable`, `opus`, `sonnet`, `haiku`, with the budget written to `.claude/settings.local.json` by `/setup-pstack` |
| `mode: true` with `reminder:` in skill frontmatter | `UserPromptSubmit` and `SessionStart` (`compact`) hooks in `.claude/settings.json` |
| Open the playbook file | `/playbook <name>` |

Skill frontmatter keeps only Claude Code keys.
`disable-model-invocation: true` is dropped from every skill, so agents can load `poteto-mode` and its leaf skills through the Skill tool.
Upstream em dashes are rewritten.

## Mode persistence

Auto-compaction keeps the first 20,000 characters of each skill loaded with the Skill tool, and nothing of a file read with `Read`.
That is why the mode loads a playbook with `/playbook <name>` instead of reading its file.
The port reorders the mode's sections for the same reason.
Playbooks comes right after Non-negotiables, so compaction keeps it, and Subagents comes last, because the compaction hook re-supplies the last section in full.
`port.py` refuses a pack whose Playbooks section ends more than 12,000 characters into the mode's body, or whose last heading starts after character 19,600, since compaction would then cut text that the hook does not re-supply.

## Read-only search

Claude Code can drop the Glob and Grep tools, and permission settings on an agent do not hold once the parent runs with `bypassPermissions`.
So `pstack-readonly` has no shell at all and searches through `Read` instead.
Its hook catches a `Read` of `/.pstack-search/<command>`, runs that one `rg`, `grep`, `find`, or `ls` command without a shell, and points the `Read` at the output.
The hook refuses every other program and every option that writes a file or runs another program, such as `find -exec` or `rg --pre`.
In an untrusted workspace the hook never runs, so search is off and the agent still cannot run anything.

## Safety fixes over upstream

- `watch-pr` never reports `READY` while GitHub reports the merge state `BLOCKED` or the review decision `REVIEW_REQUIRED`, and it pages through every review thread instead of the first 100.
- `orch` serializes lock acquisition and never takes the store lock from a live or unknown owner.
- `worktree-audit.sh` holds a worktree with untracked files (`hold-untracked`) or a detached HEAD whose commits no ref contains (`hold-unreachable`).
- The worktree-cleanup playbook removes a worktree with `git worktree remove`, never `--force`.

## Regenerate

```sh
upstream=$(scripts/fetch-upstream.sh claude)
python3 claude/port/port.py "$upstream"
(cd claude/port && python3 -m unittest discover -s tests)
```

`port.py <upstream-pstack> [pack-dir]` writes to `claude/pack/` by default.
It builds the pack in a staging directory, applies every substitution as an exact match, and stops on the first upstream text that drifted.
It then checks the staged pack for broken frontmatter, leftover Cursor paths, tools, and model slugs, a forced worktree removal, em dashes, and a missing search hook.
It also checks the mode's section layout against the compaction bounds in [Mode persistence](#mode-persistence).
Only a pack that passes replaces the installed skills, agents, mode hooks, and `settings.json`, so a failed run changes nothing.
Fix a drifted entry in `port.py`, never the generated file.
