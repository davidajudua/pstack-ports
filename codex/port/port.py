#!/usr/bin/env python3
"""Port the pinned Cursor PStack archive to native Codex skills."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

PIN = 'ecc249f1e306fc64ddf83c7bed16cacf7c2239db'
ROOT = Path(__file__).resolve().parents[1]


def name(skill):
    return skill if skill == 'poteto-mode' else 'pstack-' + skill


def transform(text, names, skill=""):
    model = "gpt-6-luna" if skill in {"how", "why", "recall", "automate-me"} else "gpt-6-sol"
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    for skill_name in sorted(names, key=len, reverse=True):
        text = re.sub(r'(?<![\w./])/' + re.escape(skill_name) + r'\b', '$' + name(skill_name), text)
        text = re.sub(r'(?<=/)' + re.escape(skill_name) + r'(?=/)', name(skill_name), text)
    text = re.sub(r'\b(?:grok-4\.[\w.-]+|claude-(?:opus|fable|sonnet)-[\w.-]+|gpt-5\.[\w.-]+)\b', model, text)
    text = text.replace('~/.cursor/rules/pstack-models.mdc', '~/.codex/pstack-models.md')
    text = text.replace('pstack-models.mdc', 'pstack-models.md')
    text = text.replace('~/.cursor/skills/', '~/.agents/skills/')
    text = text.replace('.cursor/skills/', '.agents/skills/')
    text = text.replace('~/.cursor/plugins/', '~/.codex/plugins/')
    text = text.replace('~/.cursor/pstack/', '~/.codex/pstack/')
    text = text.replace('~/.cursor/projects/*/', '~/.codex/sessions/')
    text = text.replace("Cursor's built-in `create-skill`", "Codex's `$skill-creator`")
    text = text.replace('**create-skill** skill (Cursor\'s built-in for authoring SKILL.md files)', '**skill-creator** skill (for authoring SKILL.md files)')
    text = text.replace('**create-skill**', '**skill-creator**').replace('/create-skill', '$skill-creator')
    text = text.replace('the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)', 'the bundled **unslop** skill plus a diff review for unnecessary code')
    text = text.replace('`/deslop` from `cursor-team-kit`', 'the bundled `$pstack-unslop` skill and a diff review for unnecessary code')
    text = text.replace('`/deslop`', 'the bundled `$pstack-unslop` skill and a diff review for unnecessary code')
    text = text.replace('node pstack/skills/poteto-mode/scripts/check-plan.mjs', 'node "$PSTACK_MODE_DIR/scripts/check-plan.mjs"')
    text = text.replace('from trunk with `git show origin/main:pstack/skills/', 'from the installed pack with `git show origin/main:pstack/skills/')
    text = text.replace('Read these from trunk at program start.', 'Read these at program start, pack files from the installed pack and repo files from trunk.')
    text = text.replace('Re-read the execution playbook from trunk and', 'Re-read the execution playbook from the installed pack and')
    text = text.replace('On GitHub, stop at `READY` for one PR (single or stack mode).', 'On GitHub, stop at `READY` for one PR (single or stack mode). A `BLOCKER` with `merge-gate` reason `github-blocked` or `review-required` means checks are green and nothing is left to fix, but GitHub still blocks the merge on a required review, approval, or branch rule. That is the human\'s line from step 9. Report the PR as waiting on review or branch rules and stop the watcher. Do not rearm it and do not try to fix the gate.')
    text = text.replace('After GitHub reports `READY`, a queued `WAITING`/`merge-queue` stop, or `COMPLETE`, or after', 'After GitHub reports `READY`, a `github-blocked` or `review-required` merge gate, a queued `WAITING`/`merge-queue` stop, or `COMPLETE`, or after')
    text = text.replace('or `COMPLETE` in queued mode. On Origin, that is', 'or `COMPLETE` in queued mode, or a `github-blocked` or `review-required` merge gate in any mode. On Origin, that is')
    text = text.replace('`scratch:N` is untracked throwaway, safe to drop, but name the files. Per Autonomy, clean and merged and not-in-use proceeds. `wip` and in-use pause.', '`untracked:N` (`hold-untracked`) is N untracked files, which removal deletes too, so name the files and get the same decision. `wip:N,untracked:M` has both, so show the diff and name the files. `hold-unreachable` is a detached HEAD whose commits no branch or tag contains, so removal strands them; show `git log` and get a decision. Per Autonomy, clean and merged and not-in-use proceeds. `wip`, `untracked`, `hold-unreachable`, and in-use pause.')
    text = text.replace('Per path, `git worktree remove --force <path>`. If the dir survives on ignored build artifacts, `rm -rf` it, then `git worktree prune`. Branch refs survive, so no commits are lost.', "Per path, `git worktree remove <path>`, never `--force`. If git refuses, the worktree has uncommitted or untracked work, so take it back to step 4. `git worktree remove` already deletes ignored build output. Then `git worktree prune`. Branch refs survive, so a removed worktree's branch commits are not lost.")
    text = re.sub(r'git show origin/main:pstack/skills/poteto-mode/([^`]*)', r'cat "$PSTACK_MODE_DIR/\1"', text)
    text = re.sub(r'git show origin/main:pstack/skills/([^`]*)', r'cat "$PSTACK_MODE_DIR/../\1"', text)
    text = text.replace('pstack/skills/poteto-mode/', '$PSTACK_MODE_DIR/')
    text = text.replace('pstack/skills/', '$PSTACK_MODE_DIR/../')
    text = text.replace('Begin each batch with `orch inbox drain`.', 'Begin each batch with `orch inbox drain --json`. Process each batch and then run `orch inbox ack <receipt>` for its receipt. Unacknowledged batches replay after a crash; make processing idempotent.')
    text = text.replace('AskQuestion', 'request_user_input_async')
    text = text.replace('`allow_multiple: true`', 'free-text selection of multiple options (Codex has no allow_multiple parameter)')
    text = text.replace('`subagent_type: "poteto-agent"`', 'a `message` that directs the worker to read the poteto-agent role in this pack')
    text = text.replace('`subagent_type: "Comment Sicko"`', 'a `message` that directs the worker to read the comment-sicko role in this pack')
    text = text.replace('`subagent_type: generalPurpose`', 'a general worker brief in `message`')
    text = text.replace('- `subagent_type`: `generalPurpose`', '- `message`: the scoped worker brief')
    text = text.replace('`subagent_type`', 'worker role')
    text = text.replace('`run_in_background: true`', 'background execution (automatic)')
    text = text.replace('`readonly`: `true`', 'Read-only brief: do not change files')
    text = text.replace('`readonly: true`', 'a read-only brief')
    text = text.replace('`readonly: false`', 'a read-write brief')
    text = text.replace('Readonly strips MCPs.', 'Tool access follows the session permissions; a read-only brief is not a sandbox.')
    text = text.replace('readonly strips MCP', 'tool access follows session permissions')
    text = text.replace('Task tool', 'spawn_agent tool').replace('`Task`', '`spawn_agent`').replace('Task subagent', 'Codex subagent').replace('Task calls', 'spawn_agent calls').replace('Task schema', 'spawn_agent schema').replace('Task `model`', 'spawn_agent `model`')
    text = text.replace('Cursor dashboard', 'Codex agent list')
    text = text.replace('Cursor restart', 'Codex restart')
    text = text.replace("Cursor's `/loop` command (a built-in, not a pstack skill)", 'a bounded watcher using the terminal or wait tools')
    text = text.replace("Cursor's `/loop` command", 'a bounded terminal watcher')
    text = text.replace('`/loop`', 'the bounded watcher').replace('`/goal`', 'the explicit goal tool when available')
    text = text.replace('Cursor cloud agent', 'Codex worker with isolated output')
    text = text.replace('`environment: "cloud"`', 'isolated output paths')
    text = text.replace('`environment: "local"`', 'the local worker context')
    text = text.replace('pass `cloud_base_branch`', 'name the starting branch in the brief and verify the checkout before writing')
    text = text.replace('from the Cursor environment. Use the available-tools map when present. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers.', 'from the current Codex tool catalog. Use tool discovery when exposed; otherwise use `codex mcp list`. Never assume a Cursor `mcps/` directory exists.')
    text = re.sub(r'Transcripts live at `~/.cursor/projects/.*?Every line is one chat message\.', 'Use a scoped Codex transcript export as described in the runtime contract. Its format depends on the export mechanism; inspect it before parsing.', text)
    text = text.replace('Cursor restart', 'Codex restart')
    text = text.replace('a fast, cheap model', '`gpt-6-luna` with explicit `model` and `fork_turns: "none"`')
    text = text.replace('Run multiple parallel subagents across slices of history', 'Run multiple parallel `gpt-6-luna` subagents with explicit `model` and `fork_turns: "none"` across slices of history')
    text = text.replace('| `reflect tooling` | `gpt-6-sol` |', '| `reflect tooling` | `gpt-6-luna` |')
    routing = ('Use the Codex runtime contract to select each worker model explicitly. '
               'Reading, exploration, and non-engineering work use `gpt-6-luna`; '
               'engineering and harder judgment use `gpt-6-sol`. '
               'The read-only information-gathering path is pinned to Luna. '
               'Missing configuration uses that explicit model, never the parent. '
               'If the required model or an explicit model-selection mechanism is unavailable, '
               'do not spawn the worker; report the limitation. '
               'Ignore `auto`, `inherit-parent`, and stale model overrides that conflict with this routing.')
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(('Each spawn below names', 'Each reviewer and the synthesizer name', 'If the spawn_agent tool rejects a configured entry')):
            lines[i] = routing
        elif line.startswith('**Defaults for every `spawn_agent` call.'):
            lines[i] = ('**Defaults for every `spawn_agent` call.** Background execution is automatic. '
                        'Pass scoped file pointers rather than inline dumps. ' + routing +
                        ' Use `fork_turns: "none"` with the explicit model and a self-contained brief. '
                        'The hardest changes still use Sol. Configure effort through `$pstack-setup-pstack`.')
        elif line.startswith('3. Pick the runners.'):
            lines[i] = ('3. Pick the runners. Use three independent Sol workers for engineering candidates and Luna for non-engineering candidates. '
                        'Keep configured panel seat counts and assign each its own brief. ' + routing +
                        ' Spawn more when the arena covers multiple design directions.')
        elif line.startswith('4. Pick the worker model from'):
            lines[i] = '4. Classify each workstream before spawning. ' + routing + ' For an explicitly requested model comparison, state each arm and any conflict with the reading pin before running it.'
        elif line.startswith('- `model`: the configured `interrogate reviewers`'):
            lines[i] = '- `model`: `gpt-6-sol` for each engineering judgment reviewer, explicitly supplied with `fork_turns: "none"`.'
        if lines[i] != line:
            marker = re.match(r'^\d+\. ', lines[i])
            prefix = marker.group(0) if marker else ''
            lines[i] = prefix + lines[i][len(prefix):].replace('. ', '.\n' + ' ' * len(prefix))
    text = '\n'.join(lines) + '\n'
    text = text.replace('and the step 4 model, left unset for `auto` or `inherit-parent`', 'and the explicit step 4 model with `fork_turns: "none"`')
    text = text.replace('Alias and rejected entries follow the runner rules', 'Explicit models and unavailable-model handling follow the runner rules')
    return text


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and 'node_modules' not in p.parts
            and '__pycache__' not in p.parts}


def port(args):
    source = args.source.resolve()
    expected = json.loads((Path(__file__).parent / 'source-manifest.json').read_text())
    actual = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for directory in ('skills', 'agents') for p in sorted((source / directory).rglob('*')) if p.is_file()}
    if actual != expected['sha256']:
        raise SystemExit('Source differs from pinned PStack archive; review the upstream diff before updating the manifest.')
    names = sorted(p.name for p in (source / 'skills').iterdir() if p.is_dir())
    for skill in names:
        dest = args.output / 'skills' / name(skill)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source / 'skills' / skill, dest)
        for path in dest.rglob('*'):
            if path.is_file() and path.suffix in {'.md', '.ts', '.sh'}:
                text = transform(path.read_text(), names, skill)
                if path.name == 'SKILL.md':
                    _, front, body = text.split('---', 2)
                    description = re.search(r'^description: (.*(?:\n[ \t]+.*)*)', front, re.M).group(1)
                    text = f'---\nname: {name(skill)}\ndescription: {description}\n---\n\n'
                    text += 'Read [the Codex runtime contract](../poteto-mode/references/codex-runtime.md) before applying this skill.\n' if skill != 'poteto-mode' else ''
                    text += body.lstrip()
                path.write_text(text.rstrip() + "\n")
        policy = dest / 'agents' / 'openai.yaml'
        policy.parent.mkdir(exist_ok=True)
        policy.write_text('policy:\n  allow_implicit_invocation: true\n')
    mode = args.output / 'skills' / 'poteto-mode'
    roles = mode / 'references' / 'agents'
    roles.mkdir(exist_ok=True)
    for path in (source / 'agents').glob('*.md'):
        (roles / path.name).write_text(transform(path.read_text(), names))
    for path in (Path(__file__).parent / 'overrides').rglob('*'):
        if path.is_file():
            dest = args.output / 'skills' / path.relative_to(Path(__file__).parent / 'overrides')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    entry = mode / 'SKILL.md'
    text = entry.read_text().replace('# Poteto mode\n', '# Poteto mode\n\n' + (Path(__file__).parent / 'session-entry.md').read_text() + '\n', 1)
    entry.write_text(text)
    shutil.copyfile(source / 'LICENSE', args.output / 'PSTACK-LICENSE')
    print(f'Ported {len(names)} skills and 2 agent roles from {PIN} to {args.output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='pstack directory extracted from the pinned archive')
    parser.add_argument('--output', type=Path, default=ROOT / 'pack')
    port(parser.parse_args())


if __name__ == '__main__':
    main()
