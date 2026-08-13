import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  atomicWriteValidatedS1p,
  parseTouchstoneS1p,
  safeExportFilename,
  validateTextExport,
} from "../electron/main/save";

const fixture = [
  "! independent desktop boundary fixture",
  "# MHz S RI R 50",
  "5 0.25 -0.5",
  "20 -0.125 0.75",
  "",
].join("\n");

describe("safe Touchstone export bridge", () => {
  it("reduces an API filename to a local S1P basename", () => {
    expect(safeExportFilename("../run 01/raw?.s1p")).toBe("raw_.s1p");
  });

  it("accepts only bounded, semantically valid S1P text payloads", () => {
    expect(validateTextExport({ filename: "run.s1p", content: fixture }))
      .toEqual({ filename: "run.s1p", content: fixture });
    expect(() => validateTextExport({ filename: "run.s1p", content: 42 }))
      .toThrow("must be text");
    expect(() => validateTextExport({ filename: "run.s1p", content: "# Hz S MA R 50\n1 1 0\n" }))
      .toThrow("RI");
  });

  it("independently parses MHz RI data into Hz values", () => {
    expect(parseTouchstoneS1p(fixture)).toEqual({
      referenceOhms: 50,
      points: [
        { frequencyHz: 5_000_000, real: 0.25, imaginary: -0.5 },
        { frequencyHz: 20_000_000, real: -0.125, imaginary: 0.75 },
      ],
    });
  });

  it("writes, fsyncs, re-parses and atomically replaces in a real isolated directory", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "pvna-desktop-s1p-"));
    const target = path.join(root, "roundtrip.s1p");
    try {
      await writeFile(target, "old target remains until publish", "utf8");
      await atomicWriteValidatedS1p(target, fixture);
      const persisted = await readFile(target, "utf8");
      expect(persisted).toBe(fixture);
      expect(parseTouchstoneS1p(persisted).points).toHaveLength(2);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it("preserves an existing target when atomic replacement fails", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "pvna-desktop-s1p-failure-"));
    const target = path.join(root, "preserved.s1p");
    const oldContent = "authoritative previous file";
    try {
      await writeFile(target, oldContent, "utf8");
      await expect(atomicWriteValidatedS1p(target, fixture, {
        replace: async (temporaryPath, requestedTarget) => {
          expect(path.dirname(temporaryPath)).toBe(root);
          expect(requestedTarget).toBe(target);
          expect(parseTouchstoneS1p(await readFile(temporaryPath, "utf8")).points).toHaveLength(2);
          throw new Error("injected replace failure");
        },
      })).rejects.toThrow("injected replace failure");
      expect(await readFile(target, "utf8")).toBe(oldContent);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
