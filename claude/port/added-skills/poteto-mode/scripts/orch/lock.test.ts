import { afterAll, afterEach, beforeAll, describe, expect, it } from "bun:test";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openStore } from "./store.ts";

const SCRIPT = join(import.meta.dir, "orch.ts");
const directories: string[] = [];
let crashDirectory = "";

beforeAll(async function writeKillBeforeNthWritePreload() {
  crashDirectory = await mkdtemp(join(tmpdir(), "orch-crash-"));
  await writeFile(
    join(crashDirectory, "crash-fs.ts"),
    `import * as fs from "node:fs/promises";
export * from "node:fs/promises";
const at = Number(process.env.ORCH_CRASH_AT);
const match = process.env.ORCH_CRASH_MATCH ?? "";
let writes = 0;
const killOnNthMatchingWrite = (path: unknown) => {
  if (String(path).includes(match) && ++writes === at) process.kill(process.pid, "SIGKILL");
};
const wrap = <F extends (...args: any[]) => any>(fn: F): F =>
  ((...args: any[]) => {
    killOnNthMatchingWrite(args[0]);
    return fn(...args);
  }) as F;
export const open = async (path: any, flags: any = "r", ...rest: any[]) => {
  if (/[wax+]/.test(String(flags))) killOnNthMatchingWrite(path);
  const handle = await fs.open(path, flags, ...rest);
  const fill = handle.writeFile.bind(handle);
  handle.writeFile = (...args: any[]) => (killOnNthMatchingWrite(path), fill(...args));
  return handle;
};
export const link = wrap(fs.link);
export const mkdir = wrap(fs.mkdir);
export const rename = wrap(fs.rename);
export const rm = wrap(fs.rm);
export const rmdir = wrap(fs.rmdir);
export const unlink = wrap(fs.unlink);
export const writeFile = wrap(fs.writeFile);
`
  );
  await writeFile(
    join(crashDirectory, "preload.ts"),
    `import { plugin } from "bun";
import { readFileSync } from "node:fs";
plugin({
  name: "orch-crash",
  setup(build) {
    build.onLoad({ filter: /\\/orch\\/store\\.ts$/ }, ({ path }) => ({
      contents: readFileSync(path, "utf8").replace(
        'from "node:fs/promises"',
        \`from \${JSON.stringify(new URL("./crash-fs.ts", import.meta.url).pathname)}\`
      ),
      loader: "ts",
    }));
  },
});
`
  );
});

afterAll(async () => {
  await rm(crashDirectory, { recursive: true, force: true });
});

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

async function deadPid(): Promise<number> {
  const exited = Bun.spawn(["true"]);
  await exited.exited;
  return exited.pid;
}

function runOrchKilledAt(
  directory: string,
  nth: number,
  match: string,
  args: readonly string[]
): "killed" | "completed" {
  const result = Bun.spawnSync(
    [
      process.execPath,
      "--preload",
      join(crashDirectory, "preload.ts"),
      SCRIPT,
      "--store",
      directory,
      ...args,
    ],
    {
      env: {
        ...process.env,
        ORCH_CRASH_AT: String(nth),
        ORCH_CRASH_MATCH: match,
      },
    }
  );
  if (result.signalCode === "SIGKILL") return "killed";
  expect(result.stderr.toString()).not.toContain("error");
  expect(result.exitCode).toBe(0);
  return "completed";
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
    await writeFile(join(directory, ".orch.lock"), `${await deadPid()}\n`);

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

  it("recovers on the next write when a writer dies while taking over a stale lock", async () => {
    for (let nth = 1; nth < 40; nth++) {
      const directory = await initializedStore();
      await writeFile(join(directory, ".orch.lock"), `${await deadPid()}\n`);
      const run = runOrchKilledAt(directory, nth, ".orch.lock", [
        "unit",
        "add",
        "u1",
        "--track",
        "build",
      ]);
      if (run === "completed") {
        expect(nth).toBeGreaterThan(1);
        return;
      }
      const store = openStore(directory);
      await store.units.add({ id: "u2", track: "build" });
      await store.close();
      expect(await readdir(directory)).not.toContain(".orch.lock");
    }
    throw new Error("the writer never completed");
  }, 60_000);

  it("keeps the inbox in place and readable when a drain dies partway", async () => {
    for (let nth = 1; nth < 40; nth++) {
      const directory = await initializedStore();
      const setup = openStore(directory);
      for (const unit of ["u1", "u2"])
        await setup.inbox.push({ agent: "worker", unit, status: "done" });
      await setup.close();
      const layout = (await readdir(directory)).sort();
      const run = runOrchKilledAt(directory, nth, "/inbox", ["inbox", "drain"]);
      if (run === "completed") {
        expect(nth).toBeGreaterThan(1);
        return;
      }
      expect(
        (await readdir(directory)).filter((name) => name !== ".orch.lock").sort()
      ).toEqual(layout);
      const store = openStore(directory);
      const left = (await store.inbox.peek()).map((pointer) => pointer.unit);
      await store.inbox.push({ agent: "worker", unit: "u3", status: "done" });
      expect((await store.inbox.drain()).map((pointer) => pointer.unit)).toEqual([
        ...left,
        "u3",
      ]);
      await store.close();
    }
    throw new Error("the drain never completed");
  }, 60_000);
});
