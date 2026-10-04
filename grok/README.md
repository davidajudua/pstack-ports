# pstack for Grok CLI

A Grok CLI port of [pstack 0.15.5](https://github.com/cursor/plugins/tree/ecc249f1e306fc64ddf83c7bed16cacf7c2239db/pstack), Lauren Tan's Cursor plugin of engineering skills and playbooks.

## Install

The pack installs into a project's `.grok/` directory, because the skills name their helper scripts and workflow by that path.

```sh
mkdir -p <project>/.grok
cp -R grok/pack/skills grok/pack/agents grok/pack/workflows <project>/.grok/
```

Inside Grok, `.grok/skills/` wins over `.agents/skills/` for the same skill name.
Run `grok inspect` to confirm the skills and agents load.

## Use

1. Run `/setup-pstack` once.
   It writes `~/.grok/rules/pstack-models.md` and sets `[subagents] max_depth = 3` in `~/.grok/config.toml` when the key is missing or lower, since Cursor nests subagents to depth 3.
2. Type `/poteto-mode` to start the mode, the same invocation as Cursor.
   An ordinary request does not start a playbook.
3. Type `/<skill>` to run any skill directly.

## How it maps Cursor

- Cursor `Task` spawns become `spawn_subagent`.
- Spawns that set a model or Cursor's `readonly: true` run through `workflows/pstack-agents.rhai`, because `spawn_subagent` has no read-only field and takes `model` only when its live schema lists it.
  Only the top-level session has the `workflow` tool, so a subagent spawns those entries with `spawn_subagent` instead.
- Transcripts are Grok CLI sessions under `~/.grok/sessions/`.
- `comment-sicko` edits comments and runs backgrounded, as in Cursor.
- Upstream `tdd` and `teach` are not in this pack, so it ships 45 skills.

## Safety fixes over upstream

- `watch-pr` never reports `READY` while GitHub reports `BLOCKED` or `REVIEW_REQUIRED`, and it pages through every review thread instead of the first 100.
- `orch` never steals a store lock from a live or unknown owner, even with `--force`, only one process can take over a stale lock, and a writer that crashes while taking the lock never blocks later writers.
- `worktree-audit.sh` holds worktrees with untracked files (`hold-untracked`), ignored files including build directories (`hold-ignored`), or detached commits no ref contains (`hold-unreachable`), and the cleanup playbook removes worktrees without `--force`.
- The opening-a-PR reset uses `--keep` and runs only on a clean, fully pushed branch, and Shipping's rebase push leases against the verdict's head SHA.

A live Grok session has not yet been recorded following the Feature playbook end to end.

## Layout

| Path | What it is |
|---|---|
| `pack/skills/`, `pack/agents/`, `pack/workflows/` | The generated pack. |
| `port/port.py` | The generator. |
| `port/added-skills/` | Whole files the pack adds under `skills/`, such as its regression tests. |
| `port/tests/` | Tests for the generator, its publish step, and the worktree audit. |

## Regenerate

```sh
upstream=$(scripts/fetch-upstream.sh grok)
python3 grok/port/port.py "$upstream"
(cd grok/port && python3 -m unittest discover -s tests)
```

`port.py <upstream-pstack> [pack-dir]` writes to `grok/pack/` by default.
It re-applies every substitution as an exact match, so it stops on the first upstream text that drifted.
Fix that entry in the script, never the generated file.
It swaps `skills/`, `agents/`, and `workflows/` wholesale, so it refuses to run while git reports uncommitted or untracked files there.
It carries ignored files there, such as installed `node_modules` or a local `.env`, into the new pack, and refuses when the new pack ships a file at one of those paths.
A refresh that stops partway keeps the old tree as `<dir>.port-backup`, and the next run restores it if `<dir>` is missing.
