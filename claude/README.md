# pstack for Claude Code

A Claude Code native port of [pstack](https://github.com/cursor/plugins/tree/ecc249f1e306fc64ddf83c7bed16cacf7c2239db/pstack) 0.15.5, Lauren Tan's Cursor plugin of engineering skills and playbooks.

## Install

The pack installs into a project's `.claude/` directory.
It must live there, because the skills run their helper scripts from `.claude/skills/poteto-mode/scripts/` and the read-only agent finds its hook at `$CLAUDE_PROJECT_DIR/.claude/hooks/`.

```sh
mkdir -p <project>/.claude
cp -R claude/pack/skills claude/pack/agents claude/pack/hooks <project>/.claude/
```

Then merge `claude/pack/settings.json` into `<project>/.claude/settings.json`.
It sets `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`, because the playbooks open a todolist first and some models have no task-list tools without it.

Personal skills in `~/.claude/skills/` win over project skills with the same name, so remove or rename any older pstack copy there.

The `poteto-mode` helper scripts need `bun`, and `worktree-audit.sh` needs `rg`, `gh`, and `jq`.

## Use

1. Run `/setup-pstack` once to pick a reasoning budget and per-role models.
   It writes `~/.claude/rules/pstack-models.md`.
2. Type `/poteto-mode` to start the mode for the session, as in Cursor.
   The mode matches the task to a playbook, opens a todolist with its steps, and reads the skills it needs.
   Nothing loads it by default.
3. Type `/<skill>` to run any skill directly, for example `/how`, `/why`, `/interrogate`, or `/reflect`.

## Layout

| Path | What it is |
|---|---|
| `pack/skills/` | All 47 upstream skills. `poteto-mode/` holds the mode, its 23 playbooks, references, and scripts. |
| `pack/agents/` | `poteto-agent` and `comment-sicko` from upstream, plus `pstack-readonly`, the Claude Code form of Cursor's `readonly: true` subagent. |
| `pack/hooks/pstack-readonly-search.py` | The `Read` hook `pstack-readonly` searches through. It is maintained by hand, not generated. |
| `pack/settings.json` | The todo-tools environment variable. |
| `port/port.py` | The generator. |
| `port/added-skills/` | Whole files the pack adds under `skills/`, such as its regression tests. |
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
| Model slugs with an effort suffix | Aliases `fable`, `opus`, `sonnet`, `haiku`, with effort from the session level |

Skill frontmatter keeps only Claude Code keys.
`disable-model-invocation: true` is dropped from every skill, so agents can load `poteto-mode` and its leaf skills through the Skill tool.
Upstream em dashes are rewritten.

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
upstream=$(scripts/fetch-upstream.sh)
python3 claude/port/port.py "$upstream"
(cd claude/port && python3 -m unittest discover -s tests)
```

`port.py <upstream-pstack> [pack-dir]` writes to `claude/pack/` by default.
It builds the pack in a staging directory, applies every substitution as an exact match, and stops on the first upstream text that drifted.
It then checks the staged pack for broken frontmatter, leftover Cursor paths, tools, and model slugs, a forced worktree removal, em dashes, and a missing search hook.
Only a pack that passes replaces the installed skills, agents, and `settings.json`, so a failed run changes nothing.
Fix a drifted entry in `port.py`, never the generated file.
