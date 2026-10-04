---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget. Detects the models this Claude Code session can run subagents on and writes an always-applied personal rule that overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.claude/rules/pstack-models.md`, a personal rule Claude Code loads in every session, that sets pstack's model per role.

## Steps

### 1. Detect available models

Enumerate the model values you can pass to the `Agent` tool in this session. Its `model` parameter lists the aliases (`sonnet`, `opus`, `haiku`, `fable`). That is the dependable source. Confirm each alias with a probe: spawn one `pstack-readonly` subagent per alias, all in one message, with the prompt "reply ok", and drop any alias whose spawn fails or whose reply says the model is unavailable. Pass `model` on every probe. `pstack-readonly` pins `model: sonnet`, so a probe that omits `model` does not test the alias. Omitting `model` is wrong, because without that pin the call inherits the parent model. If you cannot detect any, ask the user to paste the aliases or full model IDs they have access to. Never write a value you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected values.

### 2. Load current state

The default role-to-model mapping is the rule shape shown in step 5 below. If `~/.claude/rules/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer `AskUserQuestion` over free text. Offer these four options with these exact labels, and name the current budget when the rule records one.

- `unlimited - keep max`
- `large - xhigh reasoning`
- `medium - high reasoning`
- `small - medium reasoning`

**(b) Apply it.** Build the working table from the skill defaults, and on a re-run keep any role you changed by alias, list, or `inherit-parent` and `auto`. In Claude Code the reasoning budget is the effort level, which every pack subagent inherits because none pins `effort`, so the budget does not change the model values. `unlimited` is effort `max`, `large` is `xhigh`, `medium` is `high`, and `small` is `medium`. Record the label and its effort on the `# budget` line. `inherit-parent` and `auto` do not change.

**(c) Show the roles and confirm.** Show every role with its model, marking any value not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent chat model, which keeps a session on whatever model the user picked) as the options. Prefer `AskUserQuestion` over free text. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it that differs from the parent's model when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real value written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen value is not available, stop and ask again.

### 5. Write the rule

Write `~/.claude/rules/pstack-models.md` with a `# budget` line carrying the chosen label and its target effort, and one line per role, using the same labels poteto-mode uses. No frontmatter, so the rule loads in every session. Overwrite the whole file so re-runs stay idempotent. Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent chat model (omit `model` on the Agent call). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited (max)
feature, refactoring: opus
bug-fix: opus
perf-issue: opus
hillclimb: opus
judgment and prose: opus
hardest tasks: fable
how explorer: sonnet
how explainer: opus
why investigators: sonnet
why synthesizer: opus
reflect tooling: sonnet
reflect judgment, divergent, synthesizer: opus
arena runners: fable, opus, sonnet
arena cross-judge pool: fable, opus, sonnet
swarm workers: sonnet
architect runners: fable, opus, sonnet
interrogate reviewers: fable, opus, sonnet
```

### 6. Apply the budget and confirm

Write the budget into the project's `.claude/settings.local.json`, creating the file when it is missing and keeping every other key. For `xhigh`, `high`, and `medium`, merge `"effortLevel": "<level>"` at the top level and remove any `CLAUDE_CODE_EFFORT_LEVEL` entry from its `env` object. For `max`, merge `"CLAUDE_CODE_EFFORT_LEVEL": "max"` into the `env` object and remove any top-level `effortLevel`, because Claude Code keeps `max` only for the current session unless the environment variable sets it. A project-level setting applies to every model, Opus 5.5 included, and to every subagent, and it loads in each new session for this project; `/effort` still changes the current session, and an organization cap still applies. Tell the user the rule and the setting were written and take effect in new sessions. Re-running this skill updates both.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, `/run` and `/verify` already taught the project, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, invoke `/create-verification-skill` (resolves wherever pstack is installed: project or personal skills). On no, move on without pushing.
