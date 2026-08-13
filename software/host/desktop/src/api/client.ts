import type {
  ApiDocument,
  ApiRuntimeConfig,
  CalibrationRecord,
  CalibrationResponse,
  CalibrationsResponse,
  ConfirmedPoint,
  DerivedComplexRatio,
  DeviceConnectRequest,
  DeviceStatusResponse,
  ExportRecord,
  ExportResponse,
  HealthResponse,
  LogsResponse,
  PortsResponse,
  PvnaEvent,
  RunPointsResponse,
  RunRecord,
  RunResponse,
  RunsResponse,
  RunSnapshot,
  RunSummary,
  RunSummaryResponse,
  ServicePoint,
  SweepRequest,
  TracePoint,
  TraceResponse,
} from "./types";

type FetchLike = typeof fetch;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function derivedRatio(real: number, imag: number): DerivedComplexRatio {
  return {
    real,
    imag,
    magnitude_db: 20 * Math.log10(Math.hypot(real, imag)),
    phase_deg: (Math.atan2(imag, real) * 180) / Math.PI,
  };
}

export function u64StringToSafeNumber(value: string, label: string): number {
  if (!/^\d+$/.test(value)) throw new ApiError(`${label} 必须是无符号十进制字符串`);
  const exact = BigInt(value);
  if (exact > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new ApiError(`${label} 超过 JavaScript 安全绘图范围`);
  }
  return Number(exact);
}

export function mapServicePoint(point: ServicePoint): ConfirmedPoint {
  const explicitReal = point.a_over_r_real ?? point.ratio_real;
  const explicitImag = point.a_over_r_imag ?? point.ratio_imag;
  const ratioAvailable = explicitReal !== null && explicitImag !== null;
  const ratioReal = explicitReal ?? 0;
  const ratioImag = explicitImag ?? 0;
  const aOverR = ratioAvailable ? derivedRatio(ratioReal, ratioImag) : undefined;
  const denominator = ratioReal ** 2 + ratioImag ** 2;
  const rOverA =
    !ratioAvailable || denominator === 0
      ? undefined
      : derivedRatio(ratioReal / denominator, -ratioImag / denominator);
  const mapped: ConfirmedPoint = {
    index: point.point_index,
    frequency_hz: u64StringToSafeNumber(point.requested_frequency_hz, "请求频率"),
    reference_i: point.r_i_acc,
    reference_q: point.r_q_acc,
    antenna_i: point.a_i_acc,
    antenna_q: point.a_q_acc,
    ...(aOverR ? { a_over_r: aOverR } : {}),
    ...(rOverA ? { r_over_a: rOverA } : {}),
  };
  assertRawPointIntegersAreStrings(mapped);
  return mapped;
}

export function mapTracePoint(point: TracePoint): ConfirmedPoint {
  const aOverR = derivedRatio(point.a_over_r_real, point.a_over_r_imag);
  const rOverA = point.r_over_a_real === null || point.r_over_a_imag === null
    ? undefined
    : derivedRatio(point.r_over_a_real, point.r_over_a_imag);
  const calibrated = point.s11_real === null || point.s11_imag === null
    ? undefined
    : derivedRatio(point.s11_real, point.s11_imag);
  const mapped: ConfirmedPoint = {
    index: point.point_index,
    frequency_hz: u64StringToSafeNumber(point.frequency_hz, "迹线频率"),
    reference_i: point.r_i_acc,
    reference_q: point.r_q_acc,
    antenna_i: point.a_i_acc,
    antenna_q: point.a_q_acc,
    a_over_r: aOverR,
    ...(rOverA ? { r_over_a: rOverA } : {}),
    ...(calibrated ? { calibrated_s11: calibrated } : {}),
  };
  assertRawPointIntegersAreStrings(mapped);
  return mapped;
}

export function mapRun(record: RunRecord): RunSnapshot {
  const confirmed = record.confirmed_points ?? 0;
  const total = record.expected_points ?? record.plan?.points ?? 0;
  const progress = record.progress ?? (total > 0 ? confirmed / total : 0);
  return {
    run_id: record.run_id,
    state: record.state.toLowerCase() as RunSnapshot["state"],
    progress: {
      confirmed,
      total,
      percent: Math.max(0, Math.min(100, progress * 100)),
    },
    ...(record.source ? { source: record.source } : {}),
    ...(record.created_at_utc ? { createdAtUtc: record.created_at_utc } : {}),
    ...(record.finished_at_utc ? { finishedAtUtc: record.finished_at_utc } : {}),
    ...(record.data_validation ? { dataValidation: record.data_validation } : {}),
    ...(record.points_sha256 ? { pointsSha256: record.points_sha256 } : {}),
    ...(record.safe_hold_confirmed !== undefined
      ? { safeHoldConfirmed: record.safe_hold_confirmed }
      : {}),
    ...(record.recovered_after_interruption !== undefined
      ? { recoveredAfterInterruption: record.recovered_after_interruption }
      : {}),
    ...(record.plan ? { plan: record.plan } : {}),
    ...(record.error ? { error: { message: record.error } } : {}),
  };
}

function assertLoopbackUrl(rawUrl: string, protocols: string[]): URL {
  const url = new URL(rawUrl);
  if (
    !protocols.includes(url.protocol) ||
    (url.hostname !== "127.0.0.1" && url.hostname !== "localhost")
  ) {
    throw new Error("API 配置必须使用本机回环地址");
  }
  return url;
}

function assertApiDocument(value: unknown): asserts value is ApiDocument {
  if (
    typeof value !== "object" ||
    value === null ||
    (value as { schema_version?: unknown }).schema_version !== "pvna.api.v1"
  ) {
    throw new ApiError("服务响应缺少受支持的 schema_version");
  }
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError("服务返回了无效 JSON", response.status, text);
  }
}

export class PvnaApiClient {
  private readonly baseUrl: string;

  constructor(
    private readonly config: ApiRuntimeConfig,
    private readonly fetchImpl: FetchLike = fetch,
  ) {
    const base = assertLoopbackUrl(config.baseUrl, ["http:", "https:"]);
    assertLoopbackUrl(config.eventUrl, ["ws:", "wss:"]);
    this.baseUrl = base.toString().replace(/\/$/, "");
  }

  private async request<T extends ApiDocument>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    if (!this.config.accessToken) {
      throw new ApiError("主进程尚未提供本机服务访问令牌");
    }

    const response = await this.fetchImpl.call(globalThis, `${this.baseUrl}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.config.accessToken}`,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const body = await parseBody(response);
    if (!response.ok) {
      const detail =
        typeof body === "object" && body !== null && "detail" in body
          ? String((body as { detail: unknown }).detail)
          : `本机服务请求失败 (${response.status})`;
      throw new ApiError(detail, response.status, body);
    }
    assertApiDocument(body);
    return body as T;
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  ports(): Promise<PortsResponse> {
    return this.request<PortsResponse>("/device/ports");
  }

  connect(request: DeviceConnectRequest): Promise<DeviceStatusResponse> {
    return this.request<DeviceStatusResponse>("/device/connect", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  disconnect(): Promise<DeviceStatusResponse> {
    return this.request<DeviceStatusResponse>("/device/disconnect", { method: "POST" });
  }

  deviceStatus(): Promise<DeviceStatusResponse> {
    return this.request<DeviceStatusResponse>("/device/status");
  }

  hold(): Promise<DeviceStatusResponse> {
    return this.request<DeviceStatusResponse>("/device/hold", { method: "POST" });
  }

  async createSweep(request: SweepRequest): Promise<RunSnapshot> {
    const response = await this.request<RunResponse>("/runs/sweeps", {
      method: "POST",
      body: JSON.stringify(request),
    });
    return mapRun(response.run);
  }

  async run(runId: string): Promise<RunSnapshot> {
    const response = await this.request<RunResponse>(`/runs/${encodeURIComponent(runId)}`);
    return mapRun(response.run);
  }

  async runs(): Promise<RunSnapshot[]> {
    const response = await this.request<RunsResponse>("/runs");
    return response.runs.map(mapRun);
  }

  async cancelRun(runId: string): Promise<RunSnapshot> {
    const response = await this.request<RunResponse>(`/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    });
    return mapRun(response.run);
  }

  async runPoints(runId: string): Promise<ConfirmedPoint[]> {
    const response = await this.request<RunPointsResponse>(`/runs/${encodeURIComponent(runId)}/points`);
    return response.points.map(mapServicePoint);
  }

  async runSummary(runId: string): Promise<RunSummary> {
    const response = await this.request<RunSummaryResponse>(
      `/runs/${encodeURIComponent(runId)}/summary`,
    );
    return response.summary;
  }

  async logs(): Promise<PvnaEvent[]> {
    const response = await this.request<LogsResponse>("/logs");
    return response.events;
  }

  async calibrations(): Promise<CalibrationRecord[]> {
    const response = await this.request<CalibrationsResponse>("/calibrations");
    return response.calibrations;
  }

  async createCalibration(request: {
    open_run_id: string;
    short_run_id: string;
    load_run_id: string;
  }): Promise<CalibrationRecord> {
    const response = await this.request<CalibrationResponse>("/calibrations", {
      method: "POST",
      body: JSON.stringify(request),
    });
    return response.calibration;
  }

  async trace(runId: string, calibrationId?: string): Promise<{ dataKind: "RAW" | "CALIBRATED"; points: ConfirmedPoint[] }> {
    const suffix = calibrationId ? `?calibration_id=${encodeURIComponent(calibrationId)}` : "";
    const response = await this.request<TraceResponse>(
      `/runs/${encodeURIComponent(runId)}/trace${suffix}`,
    );
    return { dataKind: response.trace.data_kind, points: response.trace.points.map(mapTracePoint) };
  }

  async exportS1p(runId: string, calibrationId?: string): Promise<ExportRecord> {
    const response = await this.request<ExportResponse>(
      `/runs/${encodeURIComponent(runId)}/exports/s1p`,
      { method: "POST", body: JSON.stringify({ calibration_id: calibrationId ?? null }) },
    );
    return response.export;
  }

  events(handlers: {
    onEvent: (event: PvnaEvent) => void;
    onConnectionChange?: (connected: boolean) => void;
    onResyncRequired?: () => void;
  }): () => void {
    if (!this.config.accessToken) {
      throw new ApiError("主进程尚未提供本机服务访问令牌");
    }

    const eventUrl = new URL(this.config.eventUrl);
    eventUrl.searchParams.set("access_token", this.config.accessToken);
    let stopped = false;
    let socket: WebSocket | undefined;
    let retry = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(eventUrl);
      socket.addEventListener("open", () => {
        const wasReconnect = retry > 0;
        retry = 0;
        handlers.onConnectionChange?.(true);
        if (wasReconnect) handlers.onResyncRequired?.();
      });
      socket.addEventListener("message", (message) => {
        try {
          const event = JSON.parse(String(message.data)) as PvnaEvent;
          if (event.schema_version === "pvna.events.v1") handlers.onEvent(event);
        } catch {
          // Malformed notifications are ignored; REST remains authoritative.
        }
      });
      socket.addEventListener("close", () => {
        handlers.onConnectionChange?.(false);
        if (!stopped) {
          retry += 1;
          reconnectTimer = setTimeout(connect, Math.min(500 * 2 ** retry, 8_000));
        }
      });
      socket.addEventListener("error", () => socket?.close());
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }
}

export function assertRawPointIntegersAreStrings(point: ConfirmedPoint): void {
  const rawValues = [point.reference_i, point.reference_q, point.antenna_i, point.antenna_q];
  if (rawValues.some((value) => typeof value !== "string" || !/^-?\d+$/.test(value))) {
    throw new ApiError("测量点的 64 位原始整数必须是十进制字符串");
  }
}
