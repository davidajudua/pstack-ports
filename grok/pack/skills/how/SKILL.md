---
name: how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
---

# How

Explore the codebase to answer "how does X work?" questions. Produce architectural explanations at the level of a senior engineer onboarding onto a subsystem, enough to build a working mental model, not so much that it reads like annotated source code.

Every spawn below is read-only, so it runs through the `pstack-agents` workflow. Only the top-level session has the `workflow` tool, and the parent owns workflow launches. Before spawning, check whether you are a subagent: if you are, do not call the `workflow` tool. Spawn each entry with `spawn_subagent` instead, passing `model` only when its live schema lists it and `isolation: "worktree"` for `worktree: true`. For `readonly: true`, open the prompt with an explicit read-only instruction: read and search only, no file edits, no state-changing commands. Then collect each child's output with `get_command_or_subagent_output`. The top-level session calls the `workflow` tool with `source: {type: "name", name: "pstack-agents"}` and `args: {agents: [...]}`, one `{label, prompt, model, readonly, worktree}` entry per subagent. The run is backgrounded and reports each entry's `label`, `success`, and `output` when it completes. Each spawn names a role line in the `~/.grok/rules/pstack-models.md` rule and a default. Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the run or spawn fails on a model value, redo that entry with `model` removed so it runs on the session model, and say so. `~/.grok/rules/pstack-models.md` is the only pstack model rule in Grok CLI. Ignore any `pstack-models` rule loaded from `~/.cursor/rules/` or `~/.claude/rules/`.

## Step 1. Assess Complexity

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

- **Simple** (a single module, a small utility, a narrow question such as "how does function X work"): no explorers. One explainer explores and explains in a single pass. Go to Step 2b.
- **Complex** (a subsystem spanning multiple files or services, a cross-cutting feature, a full architectural overview): spawn parallel explorers first, then hand off to the explainer. Go to Step 2a.

When in doubt, take the simple path.

## Step 2a. Explore (complex questions only)

Decompose the question into 2 to 4 exploration angles, each a distinct slice of the subsystem. Spawn all explorers in a single message:

- `model`: the `how explorer` line, default `grok-4.7-build-fast`
- `readonly`: `true`

Each explorer gets the prompt in `references/explorer-prompt.md` with its angle filled in. Then go to Step 3.

## Step 2b. Direct Explain (simple questions)

Spawn one `pstack-agents` subagent that explores and explains in one pass:

- `model`: the `how explainer` line, default `grok-4.7`
- `readonly`: `true`

Build its prompt from `references/explainer-prompt.md` without the explorer-findings section. Go to Step 4.

## Step 3. Synthesize (complex questions only)

Once all explorers have returned, spawn one `pstack-agents` subagent to synthesize their findings into one explanation:

- `model`: the `how explainer` line, default `grok-4.7`
- `readonly`: `true`

Build its prompt from `references/explainer-prompt.md` with every explorer's findings filled in.

## Step 4. Present

Present the explainer's output to the user. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.

## Output Format

The explanation uses the sections defined in `references/explainer-prompt.md`, dropping any that do not apply: Overview, Key Concepts, How It Works, Where Things Live, Gotchas.
