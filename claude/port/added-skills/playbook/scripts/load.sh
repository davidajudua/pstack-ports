#!/bin/sh
# Print the pstack playbook that /playbook names, or a usage line listing the playbooks when the name matches none.
LC_ALL=C
export LC_ALL
dir=$(dirname "$0")/../../poteto-mode/playbooks
name=$(IFS=-; printf '%s' "$*" | tr 'A-Z' 'a-z' | tr ' ' '-')
name=${name%.md}
case $name in
  '' | *[!a-z0-9-]*) ;;
  *)
    if [ -f "$dir/$name.md" ]; then
      printf 'Playbook %s, from .claude/skills/poteto-mode/playbooks/%s.md. Relative paths below resolve under .claude/skills/poteto-mode/.\n\n' "$name" "$name"
      exec cat "$dir/$name.md"
    fi
    ;;
esac
list=
for file in "$dir"/*.md; do
  [ -f "$file" ] || continue
  base=${file##*/}
  list="${list:+$list, }${base%.md}"
done
printf 'Usage: /playbook <name>. Available playbooks: %s\n' "$list"
