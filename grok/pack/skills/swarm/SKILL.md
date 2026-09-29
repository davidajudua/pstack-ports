---
name: swarm
description: "Fan out N parallel workers, drain them, and return one report. Use for /swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
---

# Swarm

Fan out N parallel cloud workers. They may cover separate slices, race the same brief, or mix both. The parent waits, aggregates, and returns one report.

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Aggregate
4. Report

## Phase A: Frame

1. State the done predicate and the artifact or report the swarm must return.
2. Choose the shape. Partition into slices, race N workers on identical briefs, or mix both. For a race or mixed shape, declare `first pass`, `rank all`, or `best-of` before spawning.
3. Set N from the user or derive it from the shape. N is total workers, not the concurrent subagent limit.
4. Pick the worker model from the `swarm workers` line in `~/.grok/rules/pstack-models.md`. If the rule or that line is missing, use `grok-4.7-build-fast`. For `auto` or `inherit-parent`, omit `model` so the workers run on the parent model. If the run or spawn fails on a model value, redo that entry with `model` removed so it runs on the session model, and say so. `~/.grok/rules/pstack-models.md` is the only pstack model rule in Grok CLI. Ignore any `pstack-models` rule loaded from `~/.cursor/rules/` or `~/.claude/rules/`. For a model race, name each arm's model up front.
5. Give each worker its own writable output when it writes. When workers verify or measure commits, each brief names the exact SHAs. A measurement brief also names the method (sample count, what one sample is, order). The worker records both in its result.

## Phase B: Fan out

Spawn all N workers in one run of the `pstack-agents` workflow. Only the top-level session has the `workflow` tool, and the parent owns workflow launches. Before spawning, check whether you are a subagent: if you are, do not call the `workflow` tool. Spawn each entry with `spawn_subagent` instead, passing `model` only when its live schema lists it and `isolation: "worktree"` for `worktree: true`. For `readonly: true`, open the prompt with an explicit read-only instruction: read and search only, no file edits, no state-changing commands. Then collect each child's output with `get_command_or_subagent_output`. The top-level session calls the `workflow` tool with `source: {type: "name", name: "pstack-agents"}` and `args: {agents: [...]}`, one `{label, prompt, model, readonly, worktree}` entry per subagent. The run is backgrounded and reports each entry's `label`, `success`, and `output` when it completes, each entry with `readonly: false`, `worktree: true` (each worker gets its own checkout), and the step 4 model, left unset for `auto` or `inherit-parent`. Drop `worktree` only when the worker needs this session's checkout or something else on the user's computer.

When a worker must start from a non-default pushed branch, name the branch in its brief. The worker runs `git fetch origin <branch> && git checkout <branch>` inside its worktree before anything else.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and rerun that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

Keep a compact result table, one-line evidenced issues, and explicit gaps or dropouts.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.
