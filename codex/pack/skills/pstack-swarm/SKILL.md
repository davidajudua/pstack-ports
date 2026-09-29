---
name: pstack-swarm
description: "Fan out N parallel workers, drain them, and return one report. Use for $pstack-swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
---

Read [the Codex runtime contract](../poteto-mode/references/codex-runtime.md) before applying this skill.
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
3. Set N from the user or derive it from the shape. N is total workers, not the cloud concurrency limit.
4. Classify each workstream before spawning.
   Use the Codex runtime contract to select each worker model explicitly.
   Reading, exploration, and non-engineering work use `gpt-6-luna`; engineering and harder judgment use `gpt-6-sol`.
   The read-only information-gathering path is pinned to Luna.
   Missing configuration uses that explicit model, never the parent.
   If the required model or an explicit model-selection mechanism is unavailable, do not spawn the worker; report the limitation.
   Ignore `auto`, `inherit-parent`, and stale model overrides that conflict with this routing.
   For an explicitly requested model comparison, state each arm and any conflict with the reading pin before running it.
5. Give each worker its own writable output when it writes. When workers verify or measure commits, each brief names the exact SHAs. A measurement brief also names the method (sample count, what one sample is, order). The worker records both in its result.

## Phase B: Fan out

Spawn all N workers in one message with a general worker brief in `message`, isolated output paths, background execution (automatic), and the explicit step 4 model with `fork_turns: "none"`. Use the local worker context only when the worker needs access to something on the user's computer.

When a worker must start from a non-default pushed branch, name the starting branch in the brief and verify the checkout before writing.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and rerun that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

Keep a compact result table, one-line evidenced issues, and explicit gaps or dropouts.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.
