# pstack-ports

Native ports of [pstack](https://github.com/cursor/plugins/tree/main/pstack) for Claude Code, Codex, and Grok CLI.

pstack is Lauren Tan's Cursor plugin that teaches coding agents to work like software engineers.
Its core is `poteto-mode`, a mode that matches each task to one of 23 playbooks (feature, bug fix, refactoring, shipping, and more), opens a todolist with the playbook's steps, and pulls in focused leaf skills such as `how`, `why`, `architect`, `interrogate`, and `reflect` as it goes.
All credit for the skills, playbooks, and helper scripts goes to the upstream project, which is MIT licensed.
This repository only translates them for other agent harnesses.

Every port is pinned to pstack 0.15.5 at upstream commit [`ecc249f`](https://github.com/cursor/plugins/tree/ecc249f1e306fc64ddf83c7bed16cacf7c2239db/pstack).

## The ports

Each port is self-contained, so you can install or develop one without the others.

| Folder | Harness | Skills | Install into | Start with |
|---|---|---|---|---|
| [`claude/`](claude/README.md) | Claude Code | 47 | a project's `.claude/` | `/poteto-mode` |
| [`codex/`](codex/README.md) | Codex | 47, prefixed `pstack-` | `~/.agents/skills/` or a project's `.agents/skills/` | `$poteto-mode` |
| [`grok/`](grok/README.md) | Grok CLI | 45 | a project's `.grok/` | `/poteto-mode` |

Every port folder has the same shape.

| Path | What it is |
|---|---|
| `pack/` | The generated skills (and agents, hooks, or workflows where the harness has them), ready to install. |
| `port/port.py` | The generator that rebuilds `pack/` from the pinned upstream archive. |
| `port/tests/` | Unit tests for the generator and the pack's scripts. |
| `README.md` | How to install and use the port, and how it maps Cursor mechanisms to the harness. |

Each port swaps every Cursor mechanism for its native form, such as Cursor's `Task` tool for Claude Code's `Agent` tool, Codex's `spawn_agent`, or Grok CLI's `spawn_subagent`.
The ports also carry the same safety fixes over upstream: `watch-pr` never reports a PR ready while GitHub still blocks it on a review or branch rule, `orch` never steals a live store lock, and the worktree cleanup never deletes untracked work or unreachable commits.

## Regenerate a pack

Never edit a file under `pack/` by hand.
Change the port's substitutions, overrides, or added files, then regenerate.

```sh
upstream=$(scripts/fetch-upstream.sh)   # downloads the pinned archive into .upstream/
python3 claude/port/port.py "$upstream"
python3 codex/port/port.py "$upstream"
python3 grok/port/port.py "$upstream"
```

Each generator applies its substitutions as exact matches, so it stops on the first upstream text that drifted instead of producing a half-ported skill.
The Grok generator refuses to run while git reports uncommitted or untracked files under `grok/pack/`, because it replaces that tree wholesale.

To confirm a committed pack matches a fresh regeneration byte for byte, run:

```sh
scripts/check-regen.sh claude
scripts/check-regen.sh codex
scripts/check-regen.sh grok
```

## Tests

```sh
(cd claude/port && python3 -m unittest discover -s tests)
(cd codex/port && python3 -m unittest discover -s tests)
(cd grok/port && python3 -m unittest discover -s tests)
```

The `poteto-mode` helper scripts in each pack are TypeScript tested with [Bun](https://bun.sh).
Run `bun install --frozen-lockfile && bun test orch watch-pr` from `<port>/pack/skills/poteto-mode/scripts/`.
CI runs all of these, plus the regeneration check, for every port.

## License

MIT, see [LICENSE](LICENSE).
The upstream pstack copyright notice is kept there and in [`codex/pack/PSTACK-LICENSE`](codex/pack/PSTACK-LICENSE).
