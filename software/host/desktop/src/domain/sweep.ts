import type {
  DeviceSource,
  DeviceStatusResponse,
  MeasurementRole,
  OperatorDeviceState,
  RunSnapshot,
  SweepRequest,
} from "../api/types";

export type MeasurementMode = "single" | "sweep";

export interface SweepFormValue {
  mode: MeasurementMode;
  source: DeviceSource;
  startMhz: string;
  stopMhz: string;
  points: string;
  stimulusAmplitudeQ15: string;
  settleUs: string;
  integrationCount: string;
  pointTimeoutMs: string;
}

function finiteNumber(label: string, raw: string, minimum: number, maximum: number): number {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${label} 必须在 ${minimum} 到 ${maximum} 之间`);
  }
  return value;
}

function integer(label: string, raw: string, minimum: number, maximum: number): number {
  const value = finiteNumber(label, raw, minimum, maximum);
  if (!Number.isInteger(value)) throw new Error(`${label} 必须是整数`);
  return value;
}

export function createSweepRequest(form: SweepFormValue): SweepRequest {
  const startHz = finiteNumber("起始频率", form.startMhz, 0.001, 100_000) * 1_000_000;
  const stopHz =
    form.mode === "single"
      ? startHz
      : finiteNumber("终止频率", form.stopMhz, 0.001, 100_000) * 1_000_000;
  if (stopHz < startHz) throw new Error("终止频率不能低于起始频率");

  return {
    source: form.source,
    start_hz: Math.round(startHz),
    stop_hz: Math.round(stopHz),
    points: form.mode === "single" ? 1 : integer("点数", form.points, 2, 100_001),
    spacing: "linear",
    stimulus_amplitude_q15: integer("激励幅度", form.stimulusAmplitudeQ15, 0, 32_767),
    settle_us: integer("稳定时间", form.settleUs, 0, 60_000_000),
    integration_count: integer("积分次数", form.integrationCount, 1, 4_294_967_295),
    point_timeout_ms: integer("单点超时", form.pointTimeoutMs, 1, 600_000),
    measurement_role: "dut",
  };
}

const MEASUREMENT_PROTOCOL_STATES = new Set(["HOLD", "IDLE", "RESULT_READY"]);

export interface MeasurementAvailability {
  serviceReady: boolean;
  serviceReason?: string;
  connected: boolean;
  deviceState: OperatorDeviceState;
  protocolState: string;
  activeRunId?: string;
  busy: boolean;
}

export function measurementBlockReason(state: MeasurementAvailability): string | undefined {
  if (!state.serviceReady) return state.serviceReason ?? "本机服务不可用";
  if (!state.connected) return "请先显式连接 SIMULATED 或 HARDWARE";
  if (!(["SIMULATED", "HARDWARE"] as OperatorDeviceState[]).includes(state.deviceState)) {
    return `设备来源 ${state.deviceState} 不允许测量`;
  }
  const protocolState = (state.protocolState || "UNKNOWN").toUpperCase();
  if (!MEASUREMENT_PROTOCOL_STATES.has(protocolState)) {
    return `设备协议状态 ${protocolState} 不允许测量；需要 HOLD、IDLE 或 RESULT_READY`;
  }
  if (state.activeRunId) return `运行 ${state.activeRunId} 尚未结束`;
  if (state.busy) return "操作进行中";
  return undefined;
}

export function authoritativeRunStatus(snapshot: RunSnapshot): string {
  const progress = `${snapshot.progress.confirmed}/${snapshot.progress.total}`;
  const state = snapshot.state.toUpperCase();
  const hold = snapshot.safeHoldConfirmed === true
    ? "HOLD CONFIRMED"
    : snapshot.safeHoldConfirmed === false
      ? "HOLD UNKNOWN"
      : "HOLD PENDING";
  return `运行 ${snapshot.run_id} · ${state} · 已确认 ${progress} · ${hold}`;
}

export function isStandardRunCandidate(
  snapshot: RunSnapshot,
  role: Exclude<MeasurementRole, "dut">,
  selectedSource: DeviceSource,
): boolean {
  const expectedSource = selectedSource === "simulated" ? "SIMULATED" : "HARDWARE";
  return snapshot.state === "completed" &&
    snapshot.dataValidation === "VALID" &&
    snapshot.source === expectedSource &&
    snapshot.plan?.measurement_role === role;
}

export function operatorStateFromSnapshot(snapshot: DeviceStatusResponse): OperatorDeviceState {
  const normalizedState = snapshot.device.state?.toLowerCase() ?? "";
  if (normalizedState.includes("fault") || normalizedState.includes("error")) return "FAULT";
  if (!snapshot.device.connected) return "UNKNOWN";
  if (snapshot.device.source === "SIMULATED") return "SIMULATED";
  if (snapshot.device.source === "HARDWARE") return "HARDWARE";
  return "UNKNOWN";
}

export interface SweepEstimate {
  estimatedPoints: number | undefined;
  stepMhz: number | undefined;
}

export function estimateSweep(form: SweepFormValue): SweepEstimate {
  if (form.mode === "single") return { estimatedPoints: 1, stepMhz: undefined };
  const start = Number(form.startMhz);
  const stop = Number(form.stopMhz);
  const points = Number(form.points);
  if (
    !Number.isFinite(start) ||
    !Number.isFinite(stop) ||
    !Number.isInteger(points) ||
    points < 2 ||
    stop < start
  ) {
    return { estimatedPoints: undefined, stepMhz: undefined };
  }
  return { estimatedPoints: points, stepMhz: (stop - start) / (points - 1) };
}
