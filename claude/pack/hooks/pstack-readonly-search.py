#!/usr/bin/env python3
"""Read-only search for the pstack-readonly agent, served through its Read tool.

Claude Code builds that embed rg, bfs, and ugrep drop the Glob and Grep tools
unless the session is launched with --tools or --allowedTools naming them, and
pstack-readonly has no shell. So the agent searches by reading a virtual path:

    /.pstack-search/rg -n 'def main' src

This PreToolUse hook runs that one command without a shell, writes its output
to a private temporary file, and points the Read at that file. Only rg, grep, find, and
ls run, and never with an option that writes a file or runs another program.
Every other Read passes through untouched. Claude Code skips project hooks in
an untrusted workspace, and then the virtual path simply does not exist, so the
agent cannot search there but still has no way to run anything.

Reads the hook payload as JSON on stdin. Exit 0 lets the Read go ahead
(redirected when it was a search), exit 2 refuses the search with a reason.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PREFIX = "/.pstack-search/"
PROGRAMS = {"find", "grep", "ls", "rg"}
FIND_WRITERS = {"-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprint0", "-fprintf", "-ok", "-okdir"}
RG_RUNNERS = {"hostname-bin", "pre", "pre-glob", "search-zip"}
GREP_RUNNERS = {"config", "filter", "filter-magic-label", "format-open", "pager", "query", "save-config", "view"}
# How Claude Code's own Bash wrappers run the tools its binary bundles.
EMBEDDED = {
    "rg": ("rg", []),
    "find": ("bfs", ["-S", "dfs", "-regextype", "findutils-default"]),
    "grep": (
        "ugrep",
        ["-G", "--ignore-files", "--hidden", "-I", *(f"--exclude-dir={d}" for d in (".git", ".svn", ".hg", ".bzr", ".jj", ".sl"))],
    ),
}
# rg would otherwise load RIPGREP_CONFIG_PATH, which can name a --pre program.
ALWAYS = {"rg": ["--no-config"]}
TIMEOUT_SECONDS = 60
OUTPUT_LIMIT = 1_000_000
RESULTS = "pstack-readonly-search-"
STALE_SECONDS = 3600

USAGE = (
    f"Search by reading {PREFIX} followed by one rg, grep, find, or ls command, quoted as in a shell, "
    f"for example {PREFIX}rg -n 'def main' src. It runs without a shell, so there are no pipes, redirection, "
    "or glob expansion: use the tool's own options (rg -g, rg --files, rg -m, find -name). "
    "Options that write a file or run another program are refused."
)


class Refused(Exception):
    pass


def long_name(arg: str) -> str | None:
    if arg.startswith("--") and len(arg) > 2:
        return arg[2:].split("=", 1)[0]
    return None


def names_any(arg: str, names: set[str]) -> bool:
    """True when a long option is, or abbreviates, one of `names` (getopt accepts unique prefixes)."""
    name = long_name(arg)
    return name is not None and any(n.startswith(name) for n in names)


def short_flags(arg: str) -> str:
    if arg.startswith("-") and not arg.startswith("--"):
        return arg[1:]
    return ""


def check(argv: list[str]) -> None:
    if not argv:
        raise Refused("an empty search")
    program, args = argv[0], argv[1:]
    if program not in PROGRAMS:
        raise Refused(f"`{program}`")
    for arg in args:
        if program == "find" and arg in FIND_WRITERS:
            raise Refused(f"find {arg}")
        if program == "rg" and (names_any(arg, RG_RUNNERS) or "z" in short_flags(arg)):
            raise Refused(f"rg {arg}")
        if program == "grep" and (arg.startswith("---") or names_any(arg, GREP_RUNNERS) or "Q" in short_flags(arg)):
            raise Refused(f"grep {arg}")


def resolve(program: str) -> tuple[str, list[str], dict[str, str]]:
    """The executable, argv prefix, and extra environment that run `program`."""
    claude = os.environ.get("CLAUDE_CODE_EXECPATH", "")
    if program in EMBEDDED and claude and os.access(claude, os.X_OK):
        name, wrapper = EMBEDDED[program]
        return claude, [name, *wrapper, *ALWAYS.get(program, [])], {"ARGV0": name}
    path = shutil.which(program)
    if path is None:
        raise Refused(f"`{program}`, which is not installed here")
    return path, [program, *ALWAYS.get(program, [])], {}


def search(command: str, cwd: str) -> Path:
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise Refused(f"a command that does not parse ({error})") from error
    check(argv)
    executable, prefix, extra_env = resolve(argv[0])
    body, footer = run([*prefix, *argv[1:]], executable, cwd, {**os.environ, **extra_env})
    if body and not body.endswith("\n"):
        body += "\n"
    sweep()
    fd, name = tempfile.mkstemp(prefix=RESULTS, suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(f"$ {command}\n{body}{footer}\n")
    return Path(name)


def run(argv: list[str], executable: str, cwd: str, env: dict[str, str]) -> tuple[str, str]:
    """Run one search, keeping at most OUTPUT_LIMIT bytes of its output and stopping it there or at the timeout."""
    process = subprocess.Popen(
        argv,
        executable=executable,
        cwd=cwd or None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    timed_out = threading.Event()

    def stop() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(TIMEOUT_SECONDS, stop)
    timer.start()
    try:
        output = process.stdout.read(OUTPUT_LIMIT + 1)
        if len(output) > OUTPUT_LIMIT:
            process.kill()
        returncode = process.wait()
    finally:
        timer.cancel()
        process.stdout.close()
    if timed_out.is_set():
        footer = f"[stopped after {TIMEOUT_SECONDS} seconds; narrow the search]"
    elif len(output) > OUTPUT_LIMIT:
        footer = f"[output cut at {OUTPUT_LIMIT} bytes; narrow the search]"
    else:
        footer = f"[exit status {returncode}]"
    return output[:OUTPUT_LIMIT].decode("utf-8", "replace"), footer


def sweep() -> None:
    """Delete result files from searches more than an hour old. Other users' files fail to delete and stay."""
    cutoff = time.time() - STALE_SECONDS
    for old in Path(tempfile.gettempdir()).glob(f"{RESULTS}*.txt"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_input = payload["tool_input"]
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path.startswith(PREFIX):
            return 0
        results = search(file_path[len(PREFIX) :], payload.get("cwd", ""))
    except Refused as refusal:
        print(f"Refused {refusal}. {USAGE}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - any failure refuses the search instead of guessing
        print(f"Refused: the search hook failed ({error!r}). {USAGE}", file=sys.stderr)
        return 2
    decision = {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {**tool_input, "file_path": str(results)}}
    print(json.dumps({"hookSpecificOutput": decision}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
