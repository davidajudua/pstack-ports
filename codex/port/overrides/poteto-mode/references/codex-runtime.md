# Codex runtime contract

This pack ports PStack 0.15.5 at `ecc249f1e306fc64ddf83c7bed16cacf7c2239db`.
It preserves its skills, principles, playbook steps, agent roles, and helper scripts.
This contract resolves platform differences wherever an upstream instruction assumes a Cursor capability.
The user's scope, repository rules, and actual tool schemas take precedence over playbook defaults.
A skill never grants permission to message others, publish, deploy, delete data, or bypass an approval gate.

## Invocation and paths

Type `$poteto-mode` in the Codex composer and press Enter to start the session mode.
Use `/skills` to browse the installed catalog.
A bare `/poteto-mode` is not a Codex slash command, and `!` runs shell commands rather than skills.
The mode remains a conversation instruction, not a process-wide setting.
Start it again in each new session.
Every native leaf is named `pstack-<upstream-name>` to distinguish it from shared Cursor skills.
Within the pack, resolve shorthand names by opening `../../pstack-<name>/SKILL.md` relative to this file's directory, or the corresponding sibling directory of `poteto-mode`.
Direct invocation is `$pstack-how`, `$pstack-architect`, `$pstack-tdd`, and so on.
The repo's existing `tdd` and `teach` remain untouched.

## Models and workers

Select the worker model from the work it will do, before spawning.
The parent can remain GPT-6 Astra; its model is not a worker default.

| Work | Explicit worker model |
| --- | --- |
| Reading files, code exploration, evidence collection, history mining, summaries, and other non-engineering work | `gpt-6-luna` |
| Ordinary engineering, implementation, architecture, code review, and harder judgment | `gpt-6-sol` |

How explorers and explainers, Why investigators and explanatory synthesizers, Recall and Automate-me history miners, and Reflect tooling readers use Luna.
Reflect judgment, divergent review and synthesis, engineering Arena and Architect seats, Interrogate reviewers, and code-writing delegates use Sol.
Swarm classifies every workstream separately; an engineering parent does not turn a reading worker into an engineering worker.
Design and correctness judgment use Sol even when their review brief forbids writes.
A read-only information-gathering worker stays on Luna.
Both models default to `high` reasoning.
Panels retain their seat counts and independent prompts.
Same-model agreement is not cross-model evidence; disclose that limitation.

Use `spawn_agent` with a unique `task_name`, scoped `message`, the explicit model from the table, `reasoning_effort: "high"`, and `fork_turns: "none"` when those fields are exposed.
For a reader, always send `model: "gpt-6-luna"`; never omit `model`, use `auto` or `inherit-parent`, or use a full-history fork.
A missing reader configuration resolves to explicit Luna, not the parent.
If Luna is unavailable or rejected, do not spawn that reader on Astra, Sol, or an unspecified model.
Report the unavailable reading path and retain the task as incomplete until an authorized model choice resolves it.
Likewise, an unavailable Sol engineering worker must not silently inherit Astra.
Some Codex versions expose `agent_type` instead of per-call model fields.
Use such a type only if its actual configuration explicitly pins the required model; otherwise report unsupported model selection and do not spawn it.
A read-only brief or `readonly` flag alone does not pin a model.
Do not start nested CLI sessions as a fallback.

`$pstack-setup-pstack` configures supported reasoning effort and panel sizes within this split.
Ignore stale role lines or inheritance aliases that would route a reader to Astra or otherwise violate the table.
Never manufacture provider slugs or append effort suffixes to a Codex model ID.

Workers run in the background automatically.
Use `send_message` to steer a running worker, `followup_task` to resume an idle one, `wait_agent` to receive results, and `interrupt_agent` to stop work when those tools exist.
Other versions name these operations `send_input`, `wait`, and `close_agent`; inspect the schema rather than assuming aliases.
A poteto worker's message must direct it to read `references/agents/poteto-agent.md` and this mode before work.
A comment reviewer must read `references/agents/comment-sicko.md`.
These are role briefs, not registered `subagent_type` values.

Respect the current concurrency and nesting limits.
Queue excess workers and collect all requested seats as slots become free.
Do not assume ten concurrent children or depth three.
If delegation is forbidden or unavailable, own the implementation and perform a separate review pass, explicitly recording the lost independent review.
Do not start nested CLI agents to evade a delegation restriction.

Codex local workers share the checkout.
`environment`, `cloud_base_branch`, and `readonly` are not spawn parameters.
For disjoint writers, scope their paths; for competing implementations, use separately authorized worktrees or task-local output directories.
Never let two writers mutate the same branch or file concurrently.
A read-only brief is behavioral guidance, not an enforced sandbox.
Use an available read-only agent or sandbox when enforcement is needed.
Cloud execution is optional and requires the environment's supported remote mechanism; never claim local workers run on separate VMs.

## Questions, plans, verification, and loops

Use the session's structured question tool when available, respecting its schema and mode restrictions.
Otherwise ask a concise question in chat.
Codex has no Cursor `allow_multiple` field; ask for a textual list when multiple choices matter.
Use the plan tool or a visible checklist, with upstream steps and reasons for skips.

`control-cli`, `control-ui`, and `deslop` belong to a separate Cursor plugin and are not bundled here.
Use the terminal with a PTY for CLI/TUI checks and the available browser tool for web checks, or a project verification skill.
Read bundled `pstack-unslop` and review the diff for unnecessary code before committing.
Use the bundled comment reviewer for the separate no-comments pass.
Unavailable external tools are limitations to report, not checks to mark passed.

Codex has no portable Cursor `/loop` command.
Run a bounded watcher through the terminal or wait tools and re-evaluate its predicate after each event.
Use the exposed goal tool only when the user explicitly requests a goal; otherwise track the predicate in the plan.
A workflow that needs persistent wakeups across stopped sessions requires a separately configured scheduler.
Report that dependency instead of claiming a timer is armed.
No skill can promise to survive process termination.

## Transcripts and external services

Codex session history may be in its app-server store or legacy `$CODEX_HOME/sessions` rollouts.
Use only the current session's known transcript, an explicitly supplied export, or session metadata filtered to this exact working directory before reading message bodies.
Never sweep all home sessions for task context.
If no scoped export is available, request the relevant transcript or report the gap.
References to an `agent-transcripts/` folder in upstream-derived text mean this scoped Codex export, not an existing Cursor directory.
The same rule applies to `recall`, `reflect`, `automate-me`, evals, and decision-trail audits.
Do not inspect another supervisor's endpoint namespace.

The make-bot-ui skill describes a Cursor Automations service integration.
Codex lacks its routine-management tools, so the service owner must create that routine in Cursor and provide the actual webhook URL.
Then Codex can implement and test the client within the authorized scope.
Never invent `update_state`, `SendToUser`, endpoints, credentials, or a successful external action.

The bundled watcher and orchestration helpers require Bun; the worktree audit also requires `rg`, `jq`, and `gh`.
Their forge reads retain their upstream GitHub behavior.
Run them only within the current task's authorization, and honor any repository requirement to use a forge wrapper for interactive operations.

## Durable orchestration delivery

`orch inbox drain` claims messages and returns a receipt for each batch.
A claimed batch is replayed on later drains until `orch inbox ack <receipt>` explicitly acknowledges it.
Acknowledge only after processing every message in the batch; consumers must tolerate replay.
`inbox count` and `--peek` describe new, unclaimed messages; `drain` also returns unacknowledged batches.
The store serializes lock acquisition and stale-owner recovery with `.orch.lock-acquisition`.
A live or unknown lock owner cannot be stolen, including with `--force`.
If a process crashes while acquiring a lock, acquisition fails closed until the operator verifies no acquisition is active and removes that guard directory.
This trades automatic guard recovery for protection against concurrent writers.

Set `PSTACK_MODE_DIR` to the absolute native `poteto-mode` directory after following the loaded skill symlink.
Use that resolved path for checker commands even when the working directory is outside the checkout.
Its `scripts/check-plan.mjs` works from any working directory when invoked by absolute path.
