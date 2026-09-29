---
name: pstack-reflect
description: Spawn three parallel review subagents over the active transcript, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect.
---

Read [the Codex runtime contract](../poteto-mode/references/codex-runtime.md) before applying this skill.
# Reflect

Mine the current conversation for durable learnings, then route them into skill edits.

## When to invoke

Invoke when the user says "reflect" or "$pstack-reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.

## Process

### 1. Locate the active transcript

The parent finds its own transcript file before fanning out. The system prompt names the active workspace's `agent-transcripts/` directory. Use that path. Do not glob across `~/.codex/sessions/`. That crosses workspace boundaries and reads private chats from unrelated projects.

```bash
ls -t <agent-transcripts>/*.jsonl <agent-transcripts>/*/*.jsonl <agent-transcripts>/*/subagents/*.jsonl 2>/dev/null | head -10
```

Three transcript layouts: legacy flat (`<id>.jsonl`), current nested (`<id>/<id>.jsonl`), and subagent (`<parent>/subagents/<child>.jsonl`).

For each candidate, read the first JSONL line and check that `message.content[0].text` contains the conversation's opening user prompt. Take the matching path. If no path resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

One message, three `spawn_agent` calls, a general worker brief in `message`, with `model` set as below, agent mode (a read-write brief). Reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript). Tool access follows the session permissions; a read-only brief is not a sandbox.

Use the Codex runtime contract to select each worker model explicitly.
Reading, exploration, and non-engineering work use `gpt-6-luna`; engineering and harder judgment use `gpt-6-sol`.
The read-only information-gathering path is pinned to Luna.
Missing configuration uses that explicit model, never the parent.
If the required model or an explicit model-selection mechanism is unavailable, do not spawn the worker; report the limitation.
Ignore `auto`, `inherit-parent`, and stale model overrides that conflict with this routing.

| Lens | Role line | Default `model` | Prompt template |
|---|---|---|---|
| Judgment | `reflect judgment, divergent, synthesizer` | `gpt-6-sol` | `references/judgment-reviewer.md` |
| Tooling | `reflect tooling` | `gpt-6-luna` | `references/tooling-reviewer.md` |
| Divergent | `reflect judgment, divergent, synthesizer` | `gpt-6-sol` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the transcript path or digest where marked. Reviewers return findings in the `spawn_agent` response body.

### 3. Synthesize

One `spawn_agent` call, a general worker brief in `message`, with `model` from the `reflect judgment, divergent, synthesizer` line (default `gpt-6-sol`), agent mode (a read-write brief). The synthesizer's quality check includes spot-verifying citations, which can require MCP access. Tool access follows the session permissions; a read-only brief is not a sandbox. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Structural enforcement check

Sanity-check the synthesizer's Accepted list. For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See the **encode-lessons-in-structure** principle skill.

### 5. Apply

Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes affect every future agent in the org. Do not auto-apply.

Backlog items file to whatever devex / backlog tracker your team uses automatically. Only the Accepted list waits for approval.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Codex's `$skill-creator` skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and run its description-optimization loop.
- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.

If your environment ships a SKILL.md validator, run it on every touched skill before declaring done. Skip this step if it doesn't.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
