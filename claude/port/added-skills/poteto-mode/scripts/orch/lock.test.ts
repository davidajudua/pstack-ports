import { afterEach, describe, expect, it } from "bun:test";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openStore } from "./store.ts";

const SCRIPT = join(import.meta.dir, "orch.ts");
const directories: string[] = [];

afterEach(async () => {
  for (const directory of directories.splice(0))
    await rm(directory, { recursive: true, force: true });
});

async function initializedStore(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), "orch-lock-"));
  directories.push(directory);
  const store = openStore(directory);
  await store.init();
  await store.close();
  return directory;
}

describe("store lock", () => {
  it("never steals a live or unknown holder's lock, even with --force", async () => {
    const directory = await initializedStore();
    const lock = join(directory, ".orch.lock");
    const units = await readFile(join(directory, "units.tsv"), "utf8");

    for (const holder of [String(process.pid), "not-a-pid"]) {
      await writeFile(lock, `${holder}\n`);
      for (const flags of [[], ["--force"]]) {
        const result = Bun.spawnSync([
          process.execPath,
          SCRIPT,
          "--store",
          directory,
          ...flags,
          "unit",
          "add",
          "u1",
          "--track",
          "build",
        ]);
        expect(result.exitCode).toBe(1);
        expect(result.stderr.toString()).toContain(
          `store lock held by pid ${holder}`
        );
        expect(await readFile(lock, "utf8")).toBe(`${holder}\n`);
      }
    }
    expect(await readFile(join(directory, "units.tsv"), "utf8")).toBe(units);
  });

  it("lets only one of two racing processes take over a stale lock", async () => {
    const directory = await initializedStore();
    const exited = Bun.spawn(["true"]);
    await exited.exited;
    await writeFile(join(directory, ".orch.lock"), `${exited.pid}\n`);

    // Each worker parks inside the stale-lock takeover until the other one
    // reaches it too, which forces both into the takeover window at once.
    const worker = join(directory, "worker.ts");
    await writeFile(
      worker,
      `import { existsSync, writeFileSync } from "node:fs";
import { openStore } from ${JSON.stringify(join(import.meta.dir, "store.ts"))};
const [dir, role] = process.argv.slice(-2);
const other = role === "a" ? "b-ready" : "a-acquired";
const store = openStore(dir, {
  onStaleLock: () => {
    writeFileSync(dir + "/" + role + "-ready", "");
    const deadline = Date.now() + 700;
    while (!existsSync(dir + "/" + other) && Date.now() < deadline)
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
  },
});
try {
  await store.units.add({ id: "u-" + role, track: "build" });
  writeFileSync(dir + "/" + role + "-acquired", "");
  console.log("acquired");
  await Bun.sleep(900);
} catch {
  console.log("blocked");
} finally {
  await store.close();
}
`
    );
    const outputs = await Promise.all(
      ["a", "b"].map(async (role) => {
        const child = Bun.spawn([process.execPath, worker, directory, role], {
          stdout: "pipe",
        });
        const output = await new Response(child.stdout).text();
        expect(await child.exited).toBe(0);
        return output.trim();
      })
    );
    expect(outputs.filter((output) => output === "acquired")).toHaveLength(1);
    expect(await readdir(directory)).not.toContain(".orch.lock");
  });
});
