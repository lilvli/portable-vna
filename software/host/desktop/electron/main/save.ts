import { randomBytes } from "node:crypto";
import { open, readFile, rename, unlink } from "node:fs/promises";
import path from "node:path";

export const MAX_TEXT_EXPORT_BYTES = 50 * 1024 * 1024;

export interface TouchstonePoint {
  frequencyHz: number;
  real: number;
  imaginary: number;
}

export interface ParsedTouchstoneS1p {
  referenceOhms: number;
  points: TouchstonePoint[];
}

export interface AtomicSaveOptions {
  replace?: (temporaryPath: string, targetPath: string) => Promise<void>;
}

export function safeExportFilename(candidate: string): string {
  const basename = path.basename(candidate).replace(/[^A-Za-z0-9._-]/g, "_");
  const stem = basename.replace(/\.s1p$/i, "") || "portable-vna-export";
  return `${stem}.s1p`;
}

function parseFinite(token: string, lineNumber: number): number {
  const value = Number(token);
  if (!Number.isFinite(value)) throw new Error(`S1P line ${lineNumber} contains a non-finite number`);
  return value;
}

/** An independent, strict Touchstone 1.0 one-port RI parser used at the file boundary. */
export function parseTouchstoneS1p(text: string): ParsedTouchstoneS1p {
  const units: Readonly<Record<string, number>> = { HZ: 1, KHZ: 1e3, MHZ: 1e6, GHZ: 1e9 };
  let unitScale: number | undefined;
  let referenceOhms: number | undefined;
  const points: TouchstonePoint[] = [];

  for (const [index, original] of text.split(/\r?\n/).entries()) {
    const lineNumber = index + 1;
    const withoutComment = original.split("!", 1)[0].trim();
    if (!withoutComment) continue;
    const tokens = withoutComment.split(/\s+/);
    if (tokens[0] === "#") {
      if (unitScale !== undefined) throw new Error("S1P must contain exactly one option line");
      if (tokens.length !== 6) throw new Error("S1P option line must be '# <unit> S RI R <ohms>'");
      unitScale = units[tokens[1].toUpperCase()];
      if (unitScale === undefined) throw new Error(`S1P line ${lineNumber} uses an unsupported frequency unit`);
      if (tokens[2].toUpperCase() !== "S" || tokens[3].toUpperCase() !== "RI" || tokens[4].toUpperCase() !== "R") {
        throw new Error("Only one-port S-parameter RI data is accepted");
      }
      referenceOhms = parseFinite(tokens[5], lineNumber);
      if (referenceOhms <= 0) throw new Error("S1P reference impedance must be positive");
      continue;
    }
    if (unitScale === undefined) throw new Error("S1P option line must precede network data");
    if (tokens.length !== 3) throw new Error(`S1P RI data line ${lineNumber} must contain three values`);
    const point = {
      frequencyHz: parseFinite(tokens[0], lineNumber) * unitScale,
      real: parseFinite(tokens[1], lineNumber),
      imaginary: parseFinite(tokens[2], lineNumber),
    };
    if (point.frequencyHz <= 0) throw new Error(`S1P line ${lineNumber} frequency must be positive`);
    if (points.length > 0 && point.frequencyHz <= points[points.length - 1].frequencyHz) {
      throw new Error("S1P frequencies must be strictly increasing");
    }
    points.push(point);
  }
  if (unitScale === undefined || referenceOhms === undefined) throw new Error("S1P option line is missing");
  if (points.length === 0) throw new Error("S1P contains no network data");
  return { referenceOhms, points };
}

function sameSemantics(left: ParsedTouchstoneS1p, right: ParsedTouchstoneS1p): boolean {
  if (left.referenceOhms !== right.referenceOhms || left.points.length !== right.points.length) return false;
  return left.points.every((point, index) => {
    const other = right.points[index];
    return point.frequencyHz === other.frequencyHz && point.real === other.real && point.imaginary === other.imaginary;
  });
}

export function validateTextExport(payload: unknown): { filename: string; content: string } {
  if (typeof payload !== "object" || payload === null) throw new Error("Invalid export request");
  const record = payload as { filename?: unknown; content?: unknown };
  if (typeof record.filename !== "string" || typeof record.content !== "string") {
    throw new Error("Export filename and content must be text");
  }
  if (Buffer.byteLength(record.content, "utf8") > MAX_TEXT_EXPORT_BYTES) {
    throw new Error("Export text exceeds the 50 MiB safety limit");
  }
  parseTouchstoneS1p(record.content);
  return { filename: safeExportFilename(record.filename), content: record.content };
}

/** Write, fsync, independently re-parse, then atomically replace within one directory. */
export async function atomicWriteValidatedS1p(
  targetPath: string,
  content: string,
  options: AtomicSaveOptions = {},
): Promise<void> {
  if (!path.isAbsolute(targetPath) || path.extname(targetPath).toLowerCase() !== ".s1p") {
    throw new Error("Touchstone output must be an absolute .s1p path");
  }
  const expected = parseTouchstoneS1p(content);
  const temporaryPath = path.join(
    path.dirname(targetPath),
    `.${path.basename(targetPath)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`,
  );
  let handle: Awaited<ReturnType<typeof open>> | undefined;
  try {
    handle = await open(temporaryPath, "wx", 0o600);
    await handle.writeFile(content, { encoding: "utf8" });
    await handle.sync();
    await handle.close();
    handle = undefined;

    const persisted = await readFile(temporaryPath, "utf8");
    const reparsed = parseTouchstoneS1p(persisted);
    if (!sameSemantics(expected, reparsed)) throw new Error("S1P semantic verification failed before publish");
    await (options.replace ?? rename)(temporaryPath, targetPath);
  } catch (error) {
    if (handle) await handle.close().catch(() => undefined);
    await unlink(temporaryPath).catch(() => undefined);
    throw error;
  }
}
