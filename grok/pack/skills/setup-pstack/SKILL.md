---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget. Detects the models this Grok CLI session can run subagents on and writes an always-applied personal rule that overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.grok/rules/pstack-models.md`, a personal rule Grok CLI loads in every session, that sets pstack's model per role.
That path is Grok CLI's own. It lives outside every repository, so no commit picks it up. Never write Cursor's `~/.cursor/rules/pstack-models.mdc` or Claude Code's `~/.claude/rules/pstack-models.md` from here, and ignore both when reading current state.

## Steps

### 1. Detect available models

Run `grok models` and take the model slugs it lists (such as `grok-4.7`, `grok-4.7-build-fast`, `grok-4.6`). That is the dependable source. If it prints nothing usable, ask the user to paste the slugs they have access to. Never write a slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.

### 2. Load current state

The default role-to-model mapping is the rule shape shown in step 5 below. If `~/.grok/rules/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer `ask_user_question` over free text. Offer these four options with these exact labels, and name the current budget when the rule records one.

- `unlimited - keep max`
- `large - xhigh reasoning`
- `medium - high reasoning`
- `small - medium reasoning`

**(b) Apply it.** Build the working table from the skill defaults, and on a re-run keep any role you changed by slug, list, or `inherit-parent` and `auto`. In Grok CLI the reasoning budget is the session effort level, which subagents inherit, so the budget does not change the model values. `unlimited` and `large` are effort `xhigh`, `medium` is `high`, and `small` is `medium`. Record the label and its effort on the `# budget` line. `inherit-parent` and `auto` do not change.

**(c) Show the roles and confirm.** Show every role with its model, marking any slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent chat model, which keeps a session on whatever model the user picked) as the options. Prefer `ask_user_question` over free text. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it that differs from the parent's model when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen slug is not available, stop and ask again.

### 5. Write the rule

Write `~/.grok/rules/pstack-models.md` with a `# budget` line carrying the chosen label and its target effort, and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent chat model (omit `model` on the pstack-agents entry). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited (xhigh)
feature, refactoring: grok-4.7-build-fast
bug-fix: grok-4.7-build-fast
perf-issue: grok-4.7-build-fast
hillclimb: grok-4.7-build-fast
judgment and prose: grok-4.7
hardest tasks: grok-4.7
how explorer: grok-4.7-build-fast
how explainer: grok-4.7
why investigators: grok-4.7-build-fast
why synthesizer: grok-4.7
reflect tooling: grok-4.6
reflect judgment, divergent, synthesizer: grok-4.7
arena runners: grok-4.7, grok-4.6, grok-4.7-build-fast
arena cross-judge pool: grok-4.7, grok-4.6, grok-4.7-build-fast
swarm workers: grok-4.7-build-fast
architect runners: grok-4.7, grok-4.6, grok-4.7-build-fast
interrogate reviewers: grok-4.7, grok-4.6, grok-4.7-build-fast
```

### 6. Apply the budget and confirm

The budget's effort is a session setting. Tell the user to set it with `/effort <level>`, which you cannot type for them. Tell the user the rule was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Nesting depth

Cursor nests subagents to depth 3. Grok CLI nests to `[subagents] max_depth` in `~/.grok/config.toml`, which `/setup-pstack` sets to 3. When a spawn still fails with a depth-limit error, do that step yourself in your own context. Read `[subagents] max_depth` in `~/.grok/config.toml`. When the key is missing or below 3, set `max_depth = 3` under `[subagents]`: merge that one key into the file, creating the table when it is missing and keeping every other key and table. Tell the user it applies to new sessions.

### 8. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, read `.grok/skills/create-verification-skill/SKILL.md` and follow it. On no, move on without pushing.
