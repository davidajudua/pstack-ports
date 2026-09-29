---
name: pstack-setup-pstack
description: Configure PStack model roles and reasoning effort for Codex.
---

# Setup PStack for Codex

Read [the runtime contract](../poteto-mode/references/codex-runtime.md).

1. Detect available model IDs with `codex debug models` and the current spawn tool schema.
   Confirm account access with an authorized minimal model probe before persisting a model not already proven in this session.
   Pin reading, exploration, and non-engineering roles to `gpt-6-luna`; pin engineering and harder judgment to `gpt-6-sol`.
   Default the separate reasoning effort to `high`.
2. Read `~/.codex/pstack-models.md` if present.
   Keep only the roles listed below and identify retired role lines.
   Replace missing or stale reader models with explicit Luna.
   Never turn a missing setting into parent-model inheritance.
3. Ask for the budget using the current structured question tool or chat.
   Offer unlimited (`max`), large (`xhigh`), medium (`high`), and small (`medium`).
   Show every role's proposed model and effort, then confirm any requested changes.
   Reject `auto`, `inherit-parent`, and model choices that conflict with the runtime split.
   A reader must explicitly select Luna; if Luna is unavailable, stop that path and report it.
   Panel entries retain their count even when models repeat.
4. Validate every model ID and effort against the installed catalog and actual tool schema.
   Never encode effort as a suffix on the model ID.
   Report unavailable choices instead of silently substituting another model.
5. Write the confirmed choices to `~/.codex/pstack-models.md` as role lines and a `# budget` line.
   Preserve unrelated user files and honor any task restriction on home writes.
   The mode reads this file on invocation; Codex does not automatically load it as a rules file.
6. Report the saved path and choices.
   Restart the mode to load them, and use `/model` or `codex -m <id>` to change the parent model.
7. If the project lacks a way to drive its real app, offer `$pstack-create-verification-skill` once.

Roles:

Roles without a Luna annotation use Sol for engineering or harder judgment.
For generic roles, non-engineering work always uses Luna.

- feature, refactoring
- bug-fix
- perf-issue
- hillclimb
- judgment and prose
- hardest tasks
- how explorer (Luna)
- how explainer (Luna)
- why investigators (Luna)
- why synthesizer (Luna for explanatory synthesis)
- reflect tooling (Luna)
- reflect judgment, divergent, synthesizer
- arena runners (three independent Sol entries by default)
- arena cross-judge pool (Sol for engineering judgment)
- swarm workers (Luna for reading and non-engineering slices; Sol for engineering)
- architect runners (three independent Sol entries by default)
- interrogate reviewers (three independent Sol entries by default)

Example saved line: `how explorer: gpt-6-luna; reasoning_effort=high`.
For a panel, repeat complete entries separated by ` | `.
