# pstack for Codex

A native Codex port of all 47 skills and 23 playbooks from [pstack 0.15.5](https://github.com/cursor/plugins/tree/ecc249f1e306fc64ddf83c7bed16cacf7c2239db/pstack), Lauren Tan's Cursor plugin.

## Install

Codex reads skills from `~/.agents/skills/` for every project, or from `.agents/skills/` inside one project.

For every project, run this once from a long-lived clone of this repository:

```sh
python3 codex/port/install.py
```

It links each skill in `codex/pack/skills/` into `~/.agents/skills/`.
It reports success without changes when a link already points at this clone, replaces a link to any other location, and refuses to touch a real directory.
It checks every destination for conflicts before changing any link.
If `~/.agents/skills` itself is a symlink or resolves into this clone, it refuses and changes nothing, so make it a real directory first.
Pull the clone to update the pack.

For a single project, copy the skills instead:

```sh
mkdir -p <project>/.agents/skills
cp -R codex/pack/skills/. <project>/.agents/skills/
```

## Use

1. Run `codex -m gpt-6-astra`.
2. Type `$poteto-mode`, press Enter to select the skill, then Enter again to send it.
3. Send your engineering task in the next message.

`/skills` also opens the skill picker.
Every skill ships `agents/openai.yaml` with `allow_implicit_invocation: true`, so Codex can also pick `poteto-mode` and the leaves from their descriptions without a typed invocation.
Individual skills use a `pstack-` prefix, such as `$pstack-how`, `$pstack-architect`, and `$pstack-tdd`.
Within the mode, shorthand names resolve to this pack's files.

Reading, exploration, and other non-engineering workers use GPT-6 Luna, and engineering and harder judgment workers use GPT-6 Sol, both at high reasoning by default.
`$pstack-setup-pstack` stores effort and panel settings in `~/.codex/pstack-models.md`.
The pack does not change the parent model.

## Port boundaries

The [runtime contract](pack/skills/poteto-mode/references/codex-runtime.md) maps Cursor tool calls to Codex tools and records unsupported capabilities.
The two upstream agents are role briefs under `pack/skills/poteto-mode/references/agents/`, which Codex workers receive explicitly.
Cursor cloud spawn flags, durable loop scheduling, and Automation routine creation have no direct local Codex equivalent, so the port uses scoped local workers and bounded watchers.
The port does not add MCP servers.

The upstream Bun helpers stay bundled with their tests and lockfile.
`watch-pr` reports a green PR that GitHub still blocks, or that still needs a required review, as a `merge-gate` blocker instead of `READY`.
The worktree audit holds worktrees with untracked files (`hold-untracked`) or a detached HEAD whose commits no ref contains (`hold-unreachable`), and the cleanup playbook never runs `git worktree remove --force`.
The upstream license is [PSTACK-LICENSE](pack/PSTACK-LICENSE).

## Layout

| Path | What it is |
|---|---|
| `pack/skills/` | The 47 generated skills. |
| `pack/PSTACK-LICENSE` | The upstream license, copied by the generator. |
| `port/port.py` | The generator: deterministic text mappings over the pinned archive. |
| `port/overrides/` | Whole files that replace their generated counterparts. |
| `port/session-entry.md` | The Codex session instructions injected into `poteto-mode`. |
| `port/source-manifest.json` | SHA-256 of every upstream skill and agent file at the pin. |
| `port/verify.py` | Structural checks on the pack, plus a byte-for-byte regeneration check with `--source`. |
| `port/install.py` | Links the pack into `~/.agents/skills/`. |
| `port/verify_implicit_selection.py` | A live check that Codex picks representative skills from a plain prompt. It needs Codex authentication. |

## Regenerate

```sh
upstream=$(scripts/fetch-upstream.sh codex)
python3 codex/port/port.py "$upstream"
python3 codex/port/verify.py --source "$upstream"
(cd codex/port && python3 -m unittest discover -s tests)
```

`port.py` writes to `codex/pack/` by default, or to `--output <dir>`.
It checks every upstream skill and agent file against the manifest before writing, so a new upstream revision needs a review of the source diff, manifest, mappings, and overrides together.
Fix a drifted mapping in `port.py` or an override, never the generated file.
