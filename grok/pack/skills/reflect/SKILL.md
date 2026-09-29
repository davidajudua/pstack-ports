---
name: reflect
description: Spawn three parallel review subagents over the active transcript, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect.
---

# Reflect

Mine the current conversation for durable learnings, then route them into skill edits.

## When to invoke

Invoke when the user says "reflect" or "/reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.

## Process

### 1. Locate the active transcript

The parent finds its own transcript file before fanning out. Grok CLI sessions for this working directory live under `~/.grok/sessions/<cwd-key>/<session-id>/`, where `<cwd-key>` is the working directory path URL-encoded with every `/` as `%2F` (`python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.getcwd(), safe=""))'`). A key longer than 255 bytes is a slug plus a hash instead, with the original path in that directory's `.cwd` file. `$GROK_SESSION_ID` names the current session when the shell sets it. Use only that directory. Do not glob across `~/.grok/sessions/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.

```bash
key=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.getcwd(), safe=""))')
echo "${GROK_SESSION_ID:-}"
ls -td ~/.grok/sessions/"$key"/*/ 2>/dev/null | head -10
```

Each session is a directory whose `chat_history.jsonl` is its transcript. A subagent's session is a sibling directory, and its parent's `subagents/<child-id>/meta.json` names it.

For each candidate, read the first `chat_history.jsonl` line whose `type` is `user` and whose text holds `<user_query>`, and check that it contains the conversation's opening user prompt. Take the matching path. If no path resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

One run of the `pstack-agents` workflow. Only the top-level session has the `workflow` tool, and the parent owns workflow launches. Before spawning, check whether you are a subagent: if you are, do not call the `workflow` tool. Spawn each entry with `spawn_subagent` instead, passing `model` only when its live schema lists it and `isolation: "worktree"` for `worktree: true`. For `readonly: true`, open the prompt with an explicit read-only instruction: read and search only, no file edits, no state-changing commands. Then collect each child's output with `get_command_or_subagent_output`. The top-level session calls the `workflow` tool with `source: {type: "name", name: "pstack-agents"}` and `args: {agents: [...]}`, one `{label, prompt, model, readonly, worktree}` entry per subagent. The run is backgrounded and reports each entry's `label`, `success`, and `output` when it completes, three entries, with `model` set as below, agent mode (`readonly: false`). Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), and read-only mode is not guaranteed to keep it.

Each reviewer and the synthesizer name a role line in the `~/.grok/rules/pstack-models.md` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the run or spawn fails on a model value, redo that entry with `model` removed so it runs on the session model, and say so. `~/.grok/rules/pstack-models.md` is the only pstack model rule in Grok CLI. Ignore any `pstack-models` rule loaded from `~/.cursor/rules/` or `~/.claude/rules/`.

| Lens | Role line | Default `model` | Prompt template |
|---|---|---|---|
| Judgment | `reflect judgment, divergent, synthesizer` | `grok-4.7` | `references/judgment-reviewer.md` |
| Tooling | `reflect tooling` | `grok-4.6` | `references/tooling-reviewer.md` |
| Divergent | `reflect judgment, divergent, synthesizer` | `grok-4.7` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the transcript path or digest where marked. Reviewers return findings in their entry's `output` in the run's completion report. A subagent caller collects them with `get_command_or_subagent_output` instead.

### 3. Synthesize

One run of the `pstack-agents` workflow (a subagent uses `spawn_subagent` instead; see the **poteto-mode** skill's Model and read-only spawns) with one entry, `model` from the `reflect judgment, divergent, synthesizer` line (default `grok-4.7`), agent mode (`readonly: false`). The synthesizer's quality check includes spot-verifying citations, which can require MCP access. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Structural enforcement check

Sanity-check the synthesizer's Accepted list. For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See the **encode-lessons-in-structure** principle skill.

### 5. Apply

Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes affect every future agent in the org. Do not auto-apply.

Backlog items file to whatever devex / backlog tracker your team uses automatically. Only the Accepted list waits for approval.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Grok CLI's bundled `create-skill` skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and tune the description until the skill triggers.
- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.

Run `grok inspect` before declaring done and check that every touched skill still loads.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
