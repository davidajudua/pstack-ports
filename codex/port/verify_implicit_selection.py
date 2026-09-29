#!/usr/bin/env python3
"""Verify that Codex selects representative PStack skills from a plain prompt.

Runs a live `codex exec` in a throwaway project whose .agents/skills holds a copy of the pack.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from port import ROOT


PROMPT = """Perform a read-only autonomous audit of how this repository records decision trails for long-running work.
Select and load every applicable mode and leaf skill on your own.
Do not use a dollar-prefixed skill invocation.
Do not modify files, run git setup, spawn agents, commit, or publish.
Report which native Codex skill files you loaded and why.
"""

EXPECTED = (
    '.agents/skills/poteto-mode/SKILL.md',
    '.agents/skills/pstack-show-me-your-work/SKILL.md',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='pstack-implicit-') as temp:
        project = Path(temp)
        subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
        shutil.copytree(ROOT / 'pack' / 'skills', project / '.agents' / 'skills')
        command = [
            'codex', 'exec', '--json', '--ignore-user-config', '-m', args.model,
            '-s', 'read-only', '-C', str(project), PROMPT,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
        except subprocess.TimeoutExpired as error:
            raise SystemExit(f'Codex implicit-selection check timed out after {args.timeout} seconds.') from error
    if result.returncode:
        raise SystemExit(result.stderr or result.stdout)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    evidence = '\n'.join(json.dumps(event) for event in events)
    missing = [path for path in EXPECTED if path not in evidence]
    if missing:
        raise SystemExit(f'Codex did not load expected skills: {", ".join(missing)}')
    thread = next((event['thread_id'] for event in events if event.get('type') == 'thread.started'), 'unknown')
    print(f'PASS: Codex implicitly loaded {", ".join(EXPECTED)} in thread {thread}')


if __name__ == '__main__':
    main()
