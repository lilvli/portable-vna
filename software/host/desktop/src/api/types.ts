export type ApiSchemaVersion = "pvna.api.v1";
export type EventSchemaVersion = "pvna.events.v1";
export type Int64String = string;

export type OperatorDeviceState = "SIMULATED" | "HARDWARE" | "FAULT" | "UNKNOWN";
export type DeviceSource = "simulated" | "serial";
export type ServiceDeviceSource = "SIMULATED" | "HARDWARE" | null;
export type MeasurementRole = "dut" | "open" | "short" | "load";

export interface ApiDocument {
  schema_version: ApiSchemaVersion;
}

export interface ApiRuntimeConfig {
  baseUrl: string;
  eventUrl: string;
  accessToken: string;
  tokenPresent: boolean;
  serviceAvailable?: boolean;
  unavailableReason?: string;
}

export interface DesktopBootstrapConfig {
  api: ApiRuntimeConfig;
  runtime: {
    platform: string;
    version: string;
    serviceState: "STARTING" | "AVAILABLE" | "UNAVAILABLE" | "STOPPED";
    serviceReason: string;
  };
}

export interface HealthResponse extends ApiDocument {
  status: string;
  service_version?: string;
  process_id?: number;
  instance_id?: string;
}

export interface SerialPortInfo {
  device: string;
  description: string;
  hwid: string;
}

export interface PortsResponse extends ApiDocument {
  ports: SerialPortInfo[];
}

export interface DeviceRecord {
  connected: boolean;
  source: ServiceDeviceSource;
  state: string;
  port?: string;
  evidence?: string;
  detail?: string;
  error?: string | null;
  last_error?: string | null;
  rf_output_enabled?: boolean;
}

export interface DeviceStatusResponse extends ApiDocument {
  device: DeviceRecord;
}

export interface DeviceConnectRequest {
  source: DeviceSource;
  port?: string;
}

export type SweepSpacing = "linear";

export interface SweepRequest {
  source: DeviceSource;
  start_hz: number;
  stop_hz: number;
  points: number;
  spacing: SweepSpacing;
  stimulus_amplitude_q15: number;
  settle_us: number;
  integration_count: number;
  point_timeout_ms: number;
  measurement_role: MeasurementRole;
  simulation_profile?: MeasurementRole;
  port_path?: "PORT1_REFLECTION";
  reference_impedance_ohm?: number;
}

export interface FrozenSweepPlan {
  start_hz: Int64String;
  stop_hz: Int64String;
  points: number;
  spacing: "linear" | "log";
  stimulus_amplitude_q15: number;
  settle_us: number;
  integration_count: number;
  point_timeout_ms: number;
  frequency_axis_hz?: Int64String[];
  measurement_role: MeasurementRole;
  simulation_profile?: MeasurementRole;
  port_path?: "PORT1_REFLECTION";
  reference_impedance_ohm?: number;
}

export type ServiceRunState =
  | "CREATED"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "CANCELLING"
  | "UNKNOWN";

export interface RunRecord {
  run_id: string;
  measurement_id?: number;
  source?: "SIMULATED" | "HARDWARE";
  state: ServiceRunState;
  confirmed_points?: number;
  expected_points?: number;
  progress?: number;
  error?: string | null;
  created_at_utc?: string;
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  data_validation?: string;
  points_sha256?: string | null;
  safe_hold_confirmed?: boolean;
  recovered_after_interruption?: boolean;
  device_id?: string;
  fpga_build_id?: string;
  plan?: FrozenSweepPlan;
}

export interface RunResponse extends ApiDocument {
  run: RunRecord;
}

export interface RunsResponse extends ApiDocument {
  runs: RunRecord[];
}

export interface RunProgress {
  confirmed: number;
  total: number;
  percent: number;
}

export interface RunSnapshot {
  run_id: string;
  state: Lowercase<ServiceRunState>;
  progress: RunProgress;
  source?: "SIMULATED" | "HARDWARE";
  createdAtUtc?: string;
  finishedAtUtc?: string;
  dataValidation?: string;
  pointsSha256?: string;
  safeHoldConfirmed?: boolean;
  recoveredAfterInterruption?: boolean;
  plan?: RunRecord["plan"];
  error?: {
    code?: string;
    message: string;
  };
}

export interface DerivedComplexRatio {
  real: number;
  imag: number;
  magnitude_db: number;
  phase_deg: number;
}

export interface ConfirmedPoint {
  index: number;
  frequency_hz: number;
  reference_i: Int64String;
  reference_q: Int64String;
  antenna_i: Int64String;
  antenna_q: Int64String;
  r_over_a?: DerivedComplexRatio;
  a_over_r?: DerivedComplexRatio;
  calibrated_s11?: DerivedComplexRatio;
}

export interface ServicePoint {
  point_index: number;
  requested_frequency_hz: Int64String;
  actual_frequency_hz?: Int64String;
  r_i_acc: Int64String;
  r_q_acc: Int64String;
  a_i_acc: Int64String;
  a_q_acc: Int64String;
  ratio_real: number | null;
  ratio_imag: number | null;
  a_over_r_real?: number | null;
  a_over_r_imag?: number | null;
}

export interface RunPointsResponse extends ApiDocument {
  points: ServicePoint[];
}

export interface RunSummary {
  schema_version: "pvna.summary.v1";
  run_id: string;
  source: "SIMULATED" | "HARDWARE";
  state: ServiceRunState;
  measurement_id: number;
  confirmed_points: number;
  expected_points: number;
  created_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  safe_hold_confirmed: boolean;
  data_validation: string;
  points_sha256: string | null;
  error: string | null;
  calibration_id: null;
  evidence_boundary: string;
  plan: FrozenSweepPlan;
}

export interface RunSummaryResponse extends ApiDocument {
  summary: RunSummary;
}

export interface LogsResponse extends ApiDocument {
  events: PvnaEvent[];
}

export interface CalibrationRecord {
  calibration_id: string;
  source: "SIMULATED" | "HARDWARE";
  port: number;
  path: string;
  device_id: string;
  fpga_build_id: string;
  frequency_axis_hz: Int64String[];
  points: number;
  valid_from_utc: string;
  valid_until_utc: string | null;
  standard_runs: {
    open?: string;
    short?: string;
    load?: string;
  };
}

export interface CalibrationResponse extends ApiDocument {
  calibration: CalibrationRecord;
}

export interface CalibrationsResponse extends ApiDocument {
  calibrations: CalibrationRecord[];
}

export interface TracePoint {
  point_index: number;
  frequency_hz: Int64String;
  r_i_acc: Int64String;
  r_q_acc: Int64String;
  a_i_acc: Int64String;
  a_q_acc: Int64String;
  a_over_r_real: number;
  a_over_r_imag: number;
  r_over_a_real: number | null;
  r_over_a_imag: number | null;
  s11_real: number | null;
  s11_imag: number | null;
  magnitude_db: number | null;
  phase_deg: number;
}

export interface TraceRecord {
  run_id: string;
  source: "SIMULATED" | "HARDWARE";
  calibration_id: string | null;
  data_kind: "RAW" | "CALIBRATED";
  points: TracePoint[];
}

export interface TraceResponse extends ApiDocument {
  trace: TraceRecord;
}

export interface ExportRecord {
  filename: string;
  content: string;
  data_kind: "raw" | "calibrated";
}

export interface ExportResponse extends ApiDocument {
  export: ExportRecord;
}

export interface PvnaEvent<T = unknown> {
  schema_version: EventSchemaVersion;
  event_id: number;
  event:
    | "device.status_changed"
    | "run.started"
    | "point.accepted"
    | "point.confirmed"
    | "run.progress"
    | "run.completed"
    | "run.failed"
    | "run.cancelled"
    | "run.unknown"
    | "service.log";
  timestamp_utc: string;
  run_id?: string;
  data: T;
}
