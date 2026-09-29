import { expect, it } from "bun:test";
import { mkdtemp, writeFile, chmod, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { main } from "./cli.ts";
import { fakeReader, passingCheck, type FakeReaderOptions } from "./fakes.test-helper.ts";
import { openStore } from "../orch/store.ts";

it("never emits READY for a review-blocked PR with passing checks", async () => {
  const output: string[] = [];
  const code = await main(["--owner", "owner", "--repo", "repo", "--pr", "1"], {
    reader: fakeReader({ facts: { mergeStateStatus: "BLOCKED", reviewDecision: "REVIEW_REQUIRED" }, fastPath: { kind: "checks", checks: [passingCheck()] }, commitRollups: [{ oid: "head", state: "SUCCESS" }] }),
    clock: { now: () => 0, observedAt: () => "", sleep: async () => { throw new Error("unexpected wait"); } },
    stdout: (text) => output.push(text), stderr: () => {},
  });
  expect(code).not.toBe(0);
  expect(JSON.parse(output.join(""))).toMatchObject({
    kind: "BLOCKER",
    blocker: { kind: "merge-gate", reason: "github-blocked" },
  });
});

async function verdict(facts: FakeReaderOptions["facts"]): Promise<{ code: number; verdict: unknown }> {
  const output: string[] = [];
  const code = await main(["--owner", "owner", "--repo", "repo", "--pr", "1"], {
    reader: fakeReader({ facts, fastPath: { kind: "checks", checks: [passingCheck()] }, commitRollups: [{ oid: "head", state: "SUCCESS" }] }),
    clock: { now: () => 0, observedAt: () => "", sleep: async () => { throw new Error("unexpected wait"); } },
    stdout: (text) => output.push(text), stderr: () => {},
  });
  return { code, verdict: JSON.parse(output.join("")) };
}

it("never emits READY while a required review is missing and GitHub's merge state is still UNKNOWN", async () => {
  const { code, verdict: result } = await verdict({ mergeStateStatus: "UNKNOWN", reviewDecision: "REVIEW_REQUIRED" });
  expect(result).toMatchObject({ kind: "BLOCKER", exitCode: 6, blocker: { kind: "merge-gate", reason: "review-required" } });
  expect(code).toBe(6);
});

it("still emits READY for a clean, approved PR with passing checks", async () => {
  const { code, verdict: result } = await verdict({ mergeStateStatus: "CLEAN", reviewDecision: "APPROVED" });
  expect(result).toMatchObject({ kind: "READY", exitCode: 0 });
  expect(code).toBe(0);
});

it("reads an unresolved thread beyond the first hundred through the CLI transport", async () => {
  const dir = await mkdtemp(join(tmpdir(), "pstack-pages-"));
  try {
    const mock = join(dir, "gh");
    await writeFile(mock, `#!${process.execPath}\nconst next = process.argv.includes('after=page-2');\nconst nodes = next ? [{id:'unresolved-101',isResolved:false,comments:{nodes:[]}}] : Array.from({length:100},(_,i)=>({id:'resolved-'+i,isResolved:true,comments:{nodes:[]}}));\nconsole.log(JSON.stringify({data:{repository:{pullRequest:{reviewThreads:{nodes,pageInfo:{hasNextPage:!next,endCursor:next?null:'page-2'}}}}}}));\n`);
    await chmod(mock, 0o755);
    const module = join(import.meta.dir, "github.ts");
    const child = Bun.spawn([process.execPath, "-e", `import {GhGitHubReader} from ${JSON.stringify(module)}; console.log(JSON.stringify(await new GhGitHubReader().reviewThreads({owner:'o',repo:'r',number:1})));`], { env: { ...process.env, PATH: `${dir}:${process.env.PATH}` }, stdout: "pipe", stderr: "pipe" });
    const text = await new Response(child.stdout).text();
    expect(await child.exited).toBe(0);
    expect(JSON.parse(text).map((row: { id: string }) => row.id)).toEqual(["unresolved-101"]);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

it("replays an unacknowledged drain after the consumer exits", async () => {
  const dir = await mkdtemp(join(tmpdir(), "pstack-delivery-"));
  try {
    const module = join(import.meta.dir, "../orch/store.ts");
    const child = Bun.spawn([process.execPath, "-e", `import {openStore} from ${JSON.stringify(module)}; const s=openStore(${JSON.stringify(dir)});await s.init();await s.inbox.push({agent:'reader',unit:'u1',status:'done'});await s.inbox.drain();process.exit(23);`], { stdout: "pipe", stderr: "pipe" });
    expect(await child.exited).toBe(23);
    const recovered = openStore(dir);
    try {
      const rows = await recovered.inbox.drain();
      expect(rows).toHaveLength(1);
      expect(rows[0]?.unit).toBe("u1");
      const receipt = rows[0]!.receipt;
      await expect(recovered.inbox.ack("../outside")).rejects.toThrow("invalid inbox receipt");
      await recovered.inbox.ack(receipt);
      await recovered.inbox.ack(receipt);
      expect(await recovered.inbox.drain()).toEqual([]);
    } finally { await recovered.close(); }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

it("allows at most one recovering process to hold the store", async () => {
  const dir = await mkdtemp(join(tmpdir(), "pstack-recovery-"));
  try {
    const dead = Bun.spawn([process.execPath, "-e", "process.exit(0)"]);
    await dead.exited;
    await writeFile(join(dir, ".orch.lock"), `${dead.pid}\n`);
    const module = join(import.meta.dir, "../orch/store.ts");
    const source = `import {openStore} from ${JSON.stringify(module)};import {existsSync,writeFileSync} from 'node:fs';const [dir,role]=process.argv.slice(-2);const s=openStore(dir,{onStaleLock:()=>{writeFileSync(dir+'/'+role+'-ready','');const deadline=Date.now()+700;while(!existsSync(dir+'/'+(role==='a'?'b-ready':'a-acquired'))&&Date.now()<deadline)Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,5);}});try{await s.init();writeFileSync(dir+'/'+role+'-acquired','');console.log('acquired');await Bun.sleep(900);}catch{console.log('blocked');}finally{await s.close();}`;
    const script = join(dir, "worker.ts"); await writeFile(script, source);
    const workers = ["a", "b"].map((role) => Bun.spawn([process.execPath, script, dir, role], { stdout: "pipe", stderr: "pipe" }));
    const outputs = await Promise.all(workers.map(async (w) => { const out = await new Response(w.stdout).text(); expect(await w.exited).toBe(0); return out.trim(); }));
    expect(outputs.filter((out) => out === "acquired")).toHaveLength(1);
    const store = openStore(dir); await store.init(); await store.close();
  } finally { await rm(dir, { recursive: true, force: true }); }
});
