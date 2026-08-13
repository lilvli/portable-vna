import { describe, expect, it } from "vitest";
import {
  authoritativeRunStatus,
  createSweepRequest,
  estimateSweep,
  isStandardRunCandidate,
  measurementBlockReason,
  operatorStateFromSnapshot,
} from "../src/domain/sweep";

describe("createSweepRequest", () => {
  it("creates the phase-one sweep payload", () => {
    expect(
      createSweepRequest({
        mode: "sweep",
        source: "simulated",
        startMhz: "5",
        stopMhz: "100",
        points: "201",
        stimulusAmplitudeQ15: "8192",
        settleUs: "1000",
        integrationCount: "65536",
        pointTimeoutMs: "2000",
      }),
    ).toEqual({
      source: "simulated",
      start_hz: 5_000_000,
      stop_hz: 100_000_000,
      points: 201,
      spacing: "linear",
      stimulus_amplitude_q15: 8192,
      settle_us: 1000,
      integration_count: 65536,
      point_timeout_ms: 2000,
      measurement_role: "dut",
    });
  });

  it("forces single-point runs to one frequency and one point", () => {
    const request = createSweepRequest({
      mode: "single",
      source: "serial",
      startMhz: "12.345678",
      stopMhz: "100",
      points: "201",
      stimulusAmplitudeQ15: "8192",
      settleUs: "1000",
      integrationCount: "65536",
      pointTimeoutMs: "2000",
    });

    expect(request.start_hz).toBe(12_345_678);
    expect(request.stop_hz).toBe(12_345_678);
    expect(request.points).toBe(1);
  });

  it("rejects a descending sweep", () => {
    expect(() =>
      createSweepRequest({
        mode: "sweep",
        source: "simulated",
        startMhz: "100",
        stopMhz: "5",
        points: "201",
        stimulusAmplitudeQ15: "8192",
        settleUs: "1000",
        integrationCount: "65536",
        pointTimeoutMs: "2000",
      }),
    ).toThrow("终止频率不能低于起始频率");
  });
});

describe("operatorStateFromSnapshot", () => {
  const schema_version = "pvna.api.v1" as const;

  it("does not infer a ready state from a disconnected response", () => {
    expect(operatorStateFromSnapshot({ schema_version, device: { connected: false, source: null, state: "DISCONNECTED" } })).toBe("UNKNOWN");
  });

  it("maps connected sources and faults explicitly", () => {
    expect(operatorStateFromSnapshot({ schema_version, device: { connected: true, source: "SIMULATED", state: "HOLD" } })).toBe("SIMULATED");
    expect(operatorStateFromSnapshot({ schema_version, device: { connected: true, source: "HARDWARE", state: "HOLD" } })).toBe("HARDWARE");
    expect(operatorStateFromSnapshot({ schema_version, device: { connected: true, source: "HARDWARE", state: "FAULT" } })).toBe("FAULT");
  });
});

describe("estimateSweep", () => {
  it("shows the linear step and estimated point count without starting a run", () => {
    expect(
      estimateSweep({
        mode: "sweep",
        source: "simulated",
        startMhz: "5",
        stopMhz: "100",
        points: "201",
        stimulusAmplitudeQ15: "8192",
        settleUs: "1000",
        integrationCount: "65536",
        pointTimeoutMs: "2000",
      }),
    ).toEqual({ estimatedPoints: 201, stepMhz: 0.475 });
  });

  it("does not invent an estimate for an invalid plan", () => {
    expect(
      estimateSweep({
        mode: "sweep",
        source: "simulated",
        startMhz: "100",
        stopMhz: "5",
        points: "201",
        stimulusAmplitudeQ15: "8192",
        settleUs: "1000",
        integrationCount: "65536",
        pointTimeoutMs: "2000",
      }),
    ).toEqual({ estimatedPoints: undefined, stepMhz: undefined });
  });
});

describe("measurementBlockReason", () => {
  const readyHardware = {
    serviceReady: true,
    connected: true,
    deviceState: "HARDWARE" as const,
    protocolState: "HOLD",
    busy: false,
  };

  it.each(["UNKNOWN", "FAULT", "BOOT", "BUSY"])(
    "fails closed for connected HARDWARE in protocol state %s",
    (protocolState) => {
      expect(measurementBlockReason({ ...readyHardware, protocolState }))
        .toContain(`设备协议状态 ${protocolState} 不允许测量`);
    },
  );

  it.each(["HOLD", "IDLE", "RESULT_READY"])("allows authoritative protocol state %s", (protocolState) => {
    expect(measurementBlockReason({ ...readyHardware, protocolState })).toBeUndefined();
  });

  it("still blocks an active run", () => {
    expect(measurementBlockReason({ ...readyHardware, activeRunId: "run-123" })).toContain("run-123");
  });
});

describe("authoritativeRunStatus", () => {
  it("derives the top status from a completed run snapshot", () => {
    expect(authoritativeRunStatus({
      run_id: "run-authoritative",
      state: "completed",
      progress: { confirmed: 11, total: 11, percent: 100 },
      safeHoldConfirmed: true,
    })).toBe("运行 run-authoritative · COMPLETED · 已确认 11/11 · HOLD CONFIRMED");
  });
});

describe("isStandardRunCandidate", () => {
  const hardwareOpen = {
    run_id: "hardware-open",
    state: "completed" as const,
    source: "HARDWARE" as const,
    dataValidation: "VALID",
    progress: { confirmed: 11, total: 11, percent: 100 },
    plan: {
      start_hz: "5000000",
      stop_hz: "20000000",
      points: 11,
      spacing: "linear" as const,
      stimulus_amplitude_q15: 8192,
      settle_us: 1000,
      integration_count: 65536,
      point_timeout_ms: 2000,
      measurement_role: "open" as const,
    },
  };

  it("uses source-independent measurement_role and requires the selected source", () => {
    expect(isStandardRunCandidate(hardwareOpen, "open", "serial")).toBe(true);
    expect(isStandardRunCandidate(hardwareOpen, "open", "simulated")).toBe(false);
    expect(isStandardRunCandidate(hardwareOpen, "short", "serial")).toBe(false);
  });
});
