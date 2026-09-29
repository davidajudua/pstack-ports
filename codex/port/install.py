#!/usr/bin/env python3
"""Link every skill in the native PStack pack into Codex user skills (~/.agents/skills)."""
import os
import sys
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
PACK = Path(__file__).resolve().parents[1] / 'pack' / 'skills'


def is_link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def main():
    skills = Path.home() / '.agents' / 'skills'
    resolved = skills.resolve()
    expected = Path.home().resolve() / '.agents' / 'skills'
    if resolved.is_relative_to(CHECKOUT) or resolved != expected:
        print(f'{skills} resolves to a redirected or checkout directory ({resolved}), which other harnesses may also read.', file=sys.stderr)
        print('Linking the Codex-native skills there could add machine-specific links to a shared checkout and expose them to those harnesses.', file=sys.stderr)
        print(f'Replace {skills} with a real directory used only by Codex, then rerun.', file=sys.stderr)
        return 1
    sources = sorted(path for path in PACK.iterdir() if path.is_dir())
    targets = [(skills / source.name, source) for source in sources]
    for target, source in targets:
        if not (source / 'SKILL.md').is_file():
            print(f'Missing skill: {source}', file=sys.stderr)
            return 1
        if target.exists() and target.resolve() != source and not is_link(target):
            print(f'{target} is not a link. Move it aside or use its owning checkout, then rerun.', file=sys.stderr)
            return 1
    for target, source in targets:
        if target.exists() and target.resolve() == source:
            print(f'{target} already resolves to {source}')
            continue
        if is_link(target):
            os.rmdir(target) if os.name == 'nt' else target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(source), str(target))
        else:
            target.symlink_to(source, target_is_directory=True)
        print(f'Linked {target} -> {source}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
