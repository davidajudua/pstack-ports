#!/usr/bin/env python3
"""Validate the shipped pack's inventory, references, policies, and optional regeneration."""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from port import ROOT, files, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    manifest = json.loads((Path(__file__).parent / 'source-manifest.json').read_text())
    upstream = manifest['sha256']
    native = ROOT / 'pack' / 'skills'
    skills = sorted({Path(p).parts[1] for p in upstream if p.startswith('skills/')})
    assert len(skills) == 47
    assert {p.parent.name for p in native.glob('*/SKILL.md')} == {name(s) for s in skills}
    for relative in upstream:
        parts = Path(relative).parts
        target = (native / name(parts[1]) / Path(*parts[2:]) if parts[0] == 'skills'
                  else native / 'poteto-mode/references/agents' / parts[-1])
        assert target.is_file(), target
    assert len(list((native / 'poteto-mode/playbooks').glob('*.md'))) == 23
    for skill in skills:
        directory = native / name(skill)
        text = (directory / 'SKILL.md').read_text()
        front = text.split('---', 2)[1]
        assert f'name: {name(skill)}\n' in front
        description = re.search(r'^description: (.+)', front, re.M)
        assert description, directory
        if description[1] in ('>-', '|', '>'):
            assert re.search(r'^  \S', front, re.M), directory
        assert (directory / 'agents/openai.yaml').read_text() == 'policy:\n  allow_implicit_invocation: true\n'
        for path in directory.rglob('*.md'):
            if 'node_modules' in path.parts:
                continue
            body = path.read_text()
            assert '\u2014' not in body, path
            assert 'worktree remove --force' not in body, path
            for link in re.findall(r'\]\(([^)]+)\)', body):
                if link == 'url' or '://' in link or link.startswith(('#', '/')) or '<' in link:
                    continue
                link = link.split('#')[0]
                if link:
                    assert (path.parent / link).exists(), (path, link)
        assert 'disable-model-invocation:' not in front, directory
    if args.source:
        with tempfile.TemporaryDirectory(prefix='pstack-regen-') as temp:
            subprocess.run([sys.executable, str(Path(__file__).with_name('port.py')), str(args.source), '--output', temp], check=True)
            assert files(Path(temp) / 'skills') == files(native), 'Committed pack differs from regeneration'
            assert (Path(temp) / 'PSTACK-LICENSE').read_bytes() == (ROOT / 'pack/PSTACK-LICENSE').read_bytes()
    print('PASS: 47 skills, 23 playbooks, 2 roles, resources, local links, model-invocable policies' + (', deterministic regeneration' if args.source else ''))


if __name__ == '__main__':
    main()
