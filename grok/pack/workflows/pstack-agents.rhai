let meta = #{
    name: "pstack-agents",
    description: "Run pstack subagents in parallel, each with its own model, read-only mode, and worktree choice",
    when_to_use: "A pstack skill spawns a subagent that sets a model or Cursor's readonly: true",
    phases: [ #{ title: "Agents" } ],
};

let entries = if args == () { () } else { args.agents };
if entries == () || type_of(entries) != "array" || entries.len() == 0 {
    pause("verification", "Pass args.agents: a list of { label, prompt, model, readonly, worktree }.");
}

phase("Agents");
let jobs = [];
for e in entries {
    let job = #{
        prompt: e.prompt,
        label: e.label,
        capability_mode: if e.readonly == true { "read-only" } else { "all" },
    };
    if e.model != () && e.model != "" && e.model != "auto" && e.model != "inherit-parent" {
        job.model = e.model;
    }
    if e.worktree == true {
        job.isolation_worktree = true;
    }
    jobs.push(job);
}
let results = parallel(jobs);

let report = [];
let i = 0;
for r in results {
    if r == () {
        report.push(#{ label: entries[i].label, success: false, output: "the agent did not start" });
    } else {
        report.push(#{ label: entries[i].label, agent_id: r.agent_id, success: r.success, output: r.output });
    }
    i += 1;
}
complete(#{ agents: report });
