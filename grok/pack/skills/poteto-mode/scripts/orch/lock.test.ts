import { afterEach, describe, expect, it } from "bun:test";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
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

  it("lets later writers in after a writer crashed while taking the lock", async () => {
    const directory = await initializedStore();
    const crashed = Bun.spawn(["true"]);
    await crashed.exited;
    // What a writer killed partway through acquisition leaves on disk.
    await mkdir(join(directory, ".orch.lock-acquisition"));
    await writeFile(join(directory, ".orch.lock"), `${crashed.pid}\n`);

    const result = Bun.spawnSync([
      process.execPath,
      SCRIPT,
      "--store",
      directory,
      "unit",
      "add",
      "u1",
      "--track",
      "build",
    ]);
    expect(result.stderr.toString()).not.toContain("error:");
    expect(result.exitCode).toBe(0);
    expect(await readFile(join(directory, "units.tsv"), "utf8")).toContain(
      "u1\tbuild"
    );
    expect(await readdir(directory)).not.toContain(".orch.lock");
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

  it("never lets two writers hold the lock after a stale takeover", async () => {
    // Eight writers find the same dead lock and resume at once, over several
    // rounds, so a takeover that can displace a fresh lock is caught.
    for (let round = 0; round < 5; round += 1) {
      const directory = await initializedStore();
      const exited = Bun.spawn(["true"]);
      await exited.exited;
      await writeFile(join(directory, ".orch.lock"), `${exited.pid}\n`);
      const worker = join(directory, "worker.ts");
      await writeFile(
        worker,
        `import { existsSync, readdirSync, unlinkSync, writeFileSync } from "node:fs";
import { openStore } from ${JSON.stringify(join(import.meta.dir, "store.ts"))};
const [dir, role] = process.argv.slice(-2);
const store = openStore(dir, {
  onStaleLock: () => {
    writeFileSync(dir + "/ready-" + role, "");
    while (!existsSync(dir + "/go"))
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1);
  },
});
try {
  await store.units.add({ id: "u-" + role, track: "build" });
  writeFileSync(dir + "/holding-" + role, "");
  await Bun.sleep(100);
  const holders = readdirSync(dir).filter((name) => name.startsWith("holding-"));
  unlinkSync(dir + "/holding-" + role);
  console.log(holders.length === 1 ? "acquired" : "overlapped");
} catch {
  console.log("blocked");
} finally {
  await store.close();
}
`
      );
      const roles = ["a", "b", "c", "d", "e", "f", "g", "h"];
      const children = roles.map((role) =>
        Bun.spawn([process.execPath, worker, directory, role], {
          stdout: "pipe",
        })
      );
      const ready = async (): Promise<number> =>
        (await readdir(directory)).filter((name) => name.startsWith("ready-"))
          .length;
      while ((await ready()) < roles.length) await Bun.sleep(2);
      await writeFile(join(directory, "go"), "");
      const outputs = await Promise.all(
        children.map(async (child) => {
          const output = await new Response(child.stdout).text();
          expect(await child.exited).toBe(0);
          return output.trim();
        })
      );
      expect(outputs).not.toContain("overlapped");
      expect(outputs).toContain("acquired");
      expect(
        (await readdir(directory)).filter((name) => name.startsWith(".orch.lock"))
      ).toEqual([]);
    }
  }, 30000);
});
