import { afterEach, describe, expect, it } from "bun:test";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  fakeReader,
  passingCheck,
  pendingCheck,
} from "./fakes.test-helper.ts";
import { classifyPr, readSnapshot } from "./policy.ts";
import { parsePrNumber } from "./types.ts";

const WATCHER = join(import.meta.dir, "watch-pr");
const directories: string[] = [];

afterEach(async () => {
  for (const directory of directories.splice(0))
    await rm(directory, { recursive: true, force: true });
});

interface Scenario {
  readonly mergeStateStatus: string;
  readonly reviewDecision: string | null;
  readonly threadPages: readonly (readonly {
    readonly id: string;
    readonly isResolved: boolean;
  }[])[];
  readonly checks?: readonly Record<string, string>[];
  readonly headRollupState?: string;
  readonly rules?: readonly unknown[] | "unreadable";
  readonly branch?: Record<string, unknown>;
  readonly headContexts?: readonly unknown[] | "unreadable";
}

// Stands in for the gh CLI so the watcher binary runs its real reader,
// parser, policy, and renderer end to end.
const FAKE_GH = `
const scenario = JSON.parse(process.env.FAKE_GH_SCENARIO);
const args = process.argv.slice(2);
const out = (value) => console.log(JSON.stringify(value));
const query = args.find((arg) => arg.startsWith("query=")) ?? "";
if (args[0] === "pr" && args[1] === "view")
  out({ mergeable: "MERGEABLE", mergeStateStatus: scenario.mergeStateStatus, reviewDecision: scenario.reviewDecision, headRefOid: "head", headRefName: "feature", baseRefName: "main", state: "OPEN", mergedAt: null, isDraft: false });
else if (args[0] === "pr" && args[1] === "checks")
  out(scenario.checks ?? [{ name: "ci", state: "SUCCESS", description: "", link: "", workflow: "ci", bucket: "pass" }]);
else if (args[0] === "api" && args.some((arg) => arg.includes("/rules/branches/"))) {
  if (scenario.rules === "unreadable") {
    console.error("HTTP 403: Resource not accessible by integration");
    process.exit(1);
  }
  out(scenario.rules ?? []);
} else if (args[0] === "api" && args.some((arg) => arg.includes("/branches/")))
  out(scenario.branch ?? { name: "main", protected: false });
else if (query.includes("query ReviewThreads")) {
  const after = args.find((arg) => arg.startsWith("after="));
  const page = after ? Number(after.slice("after=page-".length)) : 0;
  const nodes = (scenario.threadPages[page] ?? []).map((thread) => ({ ...thread, comments: { nodes: [] } }));
  const hasNextPage = page + 1 < scenario.threadPages.length;
  out({ data: { repository: { pullRequest: { reviewThreads: { nodes, pageInfo: { hasNextPage, endCursor: hasNextPage ? "page-" + (page + 1) : null } } } } } });
} else if (query.includes("query PrRequiredChecks")) {
  if (scenario.headContexts === "unreadable") {
    console.error("HTTP 502: Bad Gateway");
    process.exit(1);
  }
  out({ data: { repository: { pullRequest: { commits: { nodes: [{ commit: { statusCheckRollup: { contexts: { nodes: scenario.headContexts ?? [], pageInfo: { hasNextPage: false, endCursor: null } } } } }] } } } } });
} else if (query.includes("query PrCommitStatuses"))
  out({ data: { repository: { pullRequest: { commits: { nodes: [{ commit: { oid: "head", statusCheckRollup: { state: scenario.headRollupState ?? "SUCCESS" } } }] } } } } });
else {
  console.error("unexpected gh call: " + args.join(" "));
  process.exit(2);
}
`;

async function watchLines(
  scenario: Scenario,
  flags: readonly string[] = []
): Promise<{
  readonly code: number;
  readonly verdicts: readonly Record<string, unknown>[];
  readonly stderr: string;
}> {
  const bin = await mkdtemp(join(tmpdir(), "watch-pr-gh-"));
  directories.push(bin);
  const gh = join(bin, "gh");
  await writeFile(gh, `#!${process.execPath}\n${FAKE_GH}`);
  await chmod(gh, 0o755);
  const child = Bun.spawnSync(
    [
      process.execPath,
      WATCHER,
      "--owner",
      "o",
      "--repo",
      "r",
      "--pr",
      "1",
      ...flags,
    ],
    {
      env: {
        ...process.env,
        PATH: `${bin}:${process.env.PATH ?? ""}`,
        FAKE_GH_SCENARIO: JSON.stringify(scenario),
      },
    }
  );
  const stdout = child.stdout.toString().trim();
  return {
    code: child.exitCode,
    verdicts: stdout ? stdout.split("\n").map((line) => JSON.parse(line)) : [],
    stderr: child.stderr.toString(),
  };
}

async function watch(scenario: Scenario): Promise<{
  readonly code: number;
  readonly verdict: Record<string, unknown>;
}> {
  const { code, verdicts, stderr } = await watchLines(scenario);
  const [verdict] = verdicts;
  if (verdicts.length !== 1 || verdict === undefined)
    throw new Error(`watcher printed ${verdicts.length} lines: ${stderr}`);
  return { code, verdict };
}

const resolvedPage = Array.from({ length: 100 }, (_, index) => ({
  id: `resolved-${index}`,
  isResolved: true,
}));

describe("watch-pr never reports READY over an open review blocker", () => {
  it("blocks on GitHub's BLOCKED merge state even when every check passes", async () => {
    const { code, verdict } = await watch({
      mergeStateStatus: "BLOCKED",
      reviewDecision: "REVIEW_REQUIRED",
      threadPages: [[]],
    });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 6,
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
    expect(code).toBe(6);
  });

  it("blocks on a missing required review while GitHub's merge state is still UNKNOWN", async () => {
    const { code, verdict } = await watch({
      mergeStateStatus: "UNKNOWN",
      reviewDecision: "REVIEW_REQUIRED",
      threadPages: [[]],
    });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 6,
      blocker: { kind: "merge-gate", reason: "review-required" },
    });
    expect(code).toBe(6);
  });

  it("sees an unresolved review thread past the first hundred", async () => {
    const { code, verdict } = await watch({
      mergeStateStatus: "CLEAN",
      reviewDecision: "APPROVED",
      threadPages: [resolvedPage, [{ id: "unresolved-101", isResolved: false }]],
    });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 3,
      blocker: { kind: "review-threads", threads: [{ id: "unresolved-101" }] },
    });
    expect(code).toBe(3);
  });

  it("still reports READY for a clean, approved PR with every thread resolved", async () => {
    const { code, verdict } = await watch({
      mergeStateStatus: "CLEAN",
      reviewDecision: "APPROVED",
      threadPages: [resolvedPage, [{ id: "resolved-100", isResolved: true }]],
    });
    expect(verdict).toMatchObject({ kind: "READY", exitCode: 0 });
    expect(code).toBe(0);
  });
});

describe("a BLOCKED PR with a pending human Code Review Gate and a pending head rollup", () => {
  const gated = {
    mergeStateStatus: "BLOCKED",
    reviewDecision: "APPROVED",
    threadPages: [[]],
    headRollupState: "PENDING",
    checks: [
      {
        name: "ci",
        state: "SUCCESS",
        description: "",
        link: "",
        workflow: "ci",
        bucket: "pass",
      },
      {
        name: "Code Review Gate",
        state: "PENDING",
        description: "",
        link: "",
        workflow: "",
        bucket: "pending",
      },
    ],
    headContexts: [
      {
        __typename: "CheckRun",
        name: "ci",
        status: "COMPLETED",
        conclusion: "SUCCESS",
        isRequired: true,
      },
      {
        __typename: "StatusContext",
        context: "Code Review Gate",
        state: "PENDING",
        isRequired: true,
      },
    ],
  } as const;
  const requiring = (...contexts: string[]) => [
    {
      type: "required_status_checks",
      parameters: {
        required_status_checks: contexts.map((context) => ({
          context,
          integration_id: 15368,
        })),
      },
    },
  ];

  it("reports the merge gate when every other required check has passed", async () => {
    const { code, verdict } = await watch({
      ...gated,
      rules: requiring("ci", "Code Review Gate"),
    });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 6,
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
    expect(code).toBe(6);
  });

  it("keeps waiting on a required ruleset check that gh pr checks has not listed yet", async () => {
    const { code, verdicts } = await watchLines(
      { ...gated, rules: requiring("ci", "build", "Code Review Gate") },
      ["--timeout", "0.001"]
    );
    expect(verdicts.map((verdict) => verdict.kind)).toEqual([
      "WAITING",
      "TIMEOUT",
    ]);
    expect(code).toBe(5);
  });

  it("keeps waiting on a required check from classic branch protection", async () => {
    const { code, verdicts } = await watchLines(
      {
        ...gated,
        branch: {
          name: "main",
          protected: true,
          protection: {
            enabled: true,
            required_status_checks: {
              enforcement_level: "everyone",
              contexts: ["build"],
              checks: [{ context: "build", app_id: null }],
            },
          },
        },
      },
      ["--timeout", "0.001"]
    );
    expect(verdicts.map((verdict) => verdict.kind)).toEqual([
      "WAITING",
      "TIMEOUT",
    ]);
    expect(code).toBe(5);
  });

  it("keeps waiting when the passing check has the required name but not the required app", async () => {
    const { code, verdicts } = await watchLines(
      {
        ...gated,
        rules: requiring("ci", "Code Review Gate"),
        headContexts: gated.headContexts.map((context) =>
          context.__typename === "CheckRun"
            ? { ...context, isRequired: false }
            : context
        ),
      },
      ["--timeout", "0.001"]
    );
    expect(verdicts.map((verdict) => verdict.kind)).toEqual([
      "WAITING",
      "TIMEOUT",
    ]);
    expect(code).toBe(5);
  });

  it("reports the merge gate when the head's required contexts cannot be read", async () => {
    const { code, verdict } = await watch({
      ...gated,
      rules: requiring("ci", "Code Review Gate"),
      headContexts: "unreadable",
    });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 6,
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
    expect(code).toBe(6);
  });

  it("reports the merge gate when the required checks cannot be read", async () => {
    const { code, verdict } = await watch({ ...gated, rules: "unreadable" });
    expect(verdict).toMatchObject({
      kind: "BLOCKER",
      exitCode: 6,
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
    expect(code).toBe(6);
  });
});

describe("a BLOCKED merge state", () => {
  const snapshot = (reader: ReturnType<typeof fakeReader>) =>
    readSnapshot({
      reader,
      context: { owner: "owner", repo: "repo", number: parsePrNumber(1) },
      pendingHistory: "include",
      allowDraft: false,
    });

  it("still waits on pending required checks", async () => {
    const reader = fakeReader({
      facts: { mergeStateStatus: "BLOCKED", reviewDecision: "REVIEW_REQUIRED" },
      fastPath: { kind: "checks", checks: [pendingCheck()] },
    });
    expect(classifyPr(await snapshot(reader))).toMatchObject({
      kind: "waiting",
    });
  });

  it("keeps waiting while the head rollup is still pending with no visible pending check", async () => {
    const reader = fakeReader({
      facts: { mergeStateStatus: "BLOCKED", reviewDecision: "APPROVED" },
      fastPath: { kind: "checks", checks: [passingCheck()] },
      commitRollups: [{ oid: "head", state: "PENDING" }],
    });
    expect(classifyPr(await snapshot(reader))).toMatchObject({
      kind: "waiting",
    });
  });

  it("reports the merge gate when only the human Code Review Gate keeps the head rollup pending", async () => {
    const reader = fakeReader({
      facts: { mergeStateStatus: "BLOCKED", reviewDecision: "APPROVED" },
      fastPath: {
        kind: "checks",
        checks: [
          passingCheck(),
          {
            kind: "code-review-gate",
            name: "Code Review Gate",
            reportedState: "PENDING",
            description: "",
            link: "",
            workflow: "",
          },
        ],
      },
      commitRollups: [{ oid: "head", state: "PENDING" }],
    });
    expect(classifyPr(await snapshot(reader))).toMatchObject({
      kind: "blocker",
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
  });

  it("is a merge gate, not failing checks, once every check passes", async () => {
    const reader = fakeReader({
      facts: { mergeStateStatus: "BLOCKED", reviewDecision: "APPROVED" },
      fastPath: { kind: "checks", checks: [passingCheck()] },
      commitRollups: [{ oid: "head", state: "SUCCESS" }],
    });
    expect(classifyPr(await snapshot(reader))).toMatchObject({
      kind: "blocker",
      blocker: { kind: "merge-gate", reason: "github-blocked" },
    });
  });
});
