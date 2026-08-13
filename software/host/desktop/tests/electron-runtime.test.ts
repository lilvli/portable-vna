import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildPythonArguments,
  discoverPython,
  healthBelongsToInstance,
  holdIsConfirmed,
  parseApiPort,
  redactRuntimeText,
} from "../electron/main/runtime";

describe("Electron Python lifecycle contract", () => {
  it("pins the service to loopback and never puts the token in command arguments", () => {
    const token = "secret-token-that-must-not-leak";
    const args = buildPythonArguments(8765);
    expect(args).toContain("127.0.0.1");
    expect(args).toContain("8765");
    expect(args.join(" ")).not.toContain(token);
  });

  it("discovers the development backend virtual environment", () => {
    const backendDir = path.resolve(process.cwd(), "../backend");
    expect(discoverPython({ backendDir, packaged: false }).pythonPath)
      .toBe(path.join(backendDir, ".venv", "Scripts", "python.exe"));
  });

  it("reports an explicit PVNA_PYTHON failure instead of falling back silently", () => {
    const missing = path.resolve(process.cwd(), "definitely-missing-python.exe");
    expect(discoverPython({ override: missing, backendDir: "ignored", packaged: false }))
      .toEqual({ reason: `PVNA_PYTHON 指向的解释器不存在：${missing}` });
  });

  it("classifies HOLD only when RF-off is explicitly confirmed", () => {
    expect(holdIsConfirmed({ device: { state: "HOLD", rf_output_enabled: false } })).toBe(true);
    expect(holdIsConfirmed({ device: { state: "HOLD" } })).toBe(false);
    expect(holdIsConfirmed({ device: { state: "UNKNOWN", rf_output_enabled: false } })).toBe(false);
  });

  it("accepts health only from the Python service instance that Electron spawned", () => {
    expect(healthBelongsToInstance({ status: "ok", instance_id: "current" }, "current")).toBe(true);
    expect(healthBelongsToInstance({ status: "ok", instance_id: "stale" }, "current")).toBe(false);
    expect(healthBelongsToInstance({ status: "ok" }, "current")).toBe(false);
  });

  it("redacts access tokens and bearer values from captured service output", () => {
    expect(redactRuntimeText("token=abc123 Bearer abc123", "abc123"))
      .toBe("token=[REDACTED] Bearer [REDACTED]");
  });

  it("uses a fixed safe default for invalid ports", () => {
    expect(parseApiPort("9001")).toBe(9001);
    expect(parseApiPort("0")).toBe(8765);
    expect(parseApiPort("remote-host")).toBe(8765);
  });
});
