## Start the Codex session

Read [codex-runtime.md](references/codex-runtime.md) in full now.
This invocation enables PStack for subsequent engineering turns in this conversation until the user opts out.
Keep the mode and runtime contract in compaction summaries.
If this turn only enables the mode, acknowledge it briefly and wait for the task.
For each engineering task, open the matching playbook below before editing, then create a plan whose first entries are its steps verbatim.
Use `update_plan` when exposed; otherwise keep the checklist in a task-local Markdown file or the conversation.
Retain skipped steps with a concrete reason.
A skill read is its invocation: resolve every PStack name within this native pack, never to the similarly named Cursor or third-party copy.
`how` means `../pstack-how/SKILL.md`, `tdd` means `../pstack-tdd/SKILL.md`, and the same `pstack-` prefix applies to every other skill except `poteto-mode`.
Read only the leaves the task needs.
Relative playbook and script paths are relative to this `poteto-mode` directory, not the shell working directory.
Load `~/.codex/pstack-models.md` if it exists for compatible effort and panel-count settings.
Reading, exploration, and non-engineering workers use explicit `gpt-6-luna`; engineering and harder judgment workers use explicit `gpt-6-sol`, both at `high` reasoning by default.
The read-only information-gathering path never inherits the parent model, even when its configuration is missing.
Skills cannot change the parent model: start with `codex -m gpt-6-astra` or choose GPT-6 Astra using `/model` before invoking this mode.
