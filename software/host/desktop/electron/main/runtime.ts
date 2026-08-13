import { spawn, type ChildProcessByStdio } from "node:child_process";
import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import type { Readable } from "node:stream";

export const LOOPBACK_HOST = "127.0.0.1";
export const DEFAULT_API_PORT = 8765;

export type ServiceState = "STARTING" | "AVAILABLE" | "UNAVAILABLE" | "STOPPED";
export type HoldOutcome = "CONFIRMED" | "UNKNOWN" | "NOT_STARTED";

export interface ServiceStatus {
  state: ServiceState;
  available: boolean;
  reason: string;
}

export interface HoldResult {
  outcome: HoldOutcome;
  detail: string;
}

export interface SupervisorOptions {
  pythonPath: string;
  backendDir: string;
  runtimeRoot: string;
  runRoot: string;
  auditPath: string;
  port: number;
  accessToken: string;
  startupTimeoutMs?: number;
  holdTimeoutMs?: number;
  stopTimeoutMs?: number;
}

export interface PythonDiscoveryOptions {
  override?: string;
  backendDir: string;
  resourcesPath?: string;
  packaged: boolean;
}

export interface PythonDiscovery {
  pythonPath?: string;
  reason?: string;
}

export function parseApiPort(raw: string | undefined): number {
  const candidate = Number.parseInt(raw ?? "", 10);
  return Number.isInteger(candidate) && candidate > 0 && candidate <= 65_535
    ? candidate
    : DEFAULT_API_PORT;
}

export function buildPythonArguments(port: number): string[] {
  return [
    "-m",
    "pvna_host.main",
    "--host",
    LOOPBACK_HOST,
    "--port",
    String(port),
    "--log-level",
    "warning",
  ];
}

export function discoverPython(options: PythonDiscoveryOptions): PythonDiscovery {
  if (options.override) {
    const override = path.resolve(options.override);
    return existsSync(override)
      ? { pythonPath: override }
      : { reason: `PVNA_PYTHON 指向的解释器不存在：${override}` };
  }

  const candidates = options.packaged
    ? options.resourcesPath
      ? [
          path.join(options.resourcesPath, "python", "python.exe"),
          path.join(options.resourcesPath, "backend", ".venv", "Scripts", "python.exe"),
        ]
      : []
    : [path.join(options.backendDir, ".venv", "Scripts", "python.exe")];
  const pythonPath = candidates.find((candidate) => existsSync(candidate));
  return pythonPath
    ? { pythonPath }
    : {
        reason:
          "未找到 Python 服务解释器；请设置 PVNA_PYTHON，或在 backend/.venv/Scripts/python.exe 创建开发环境",
      };
}

export function redactRuntimeText(value: string, accessToken: string): string {
  return value
    .replaceAll(accessToken, "[REDACTED]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/-]+/gi, "Bearer [REDACTED]");
}

export function holdIsConfirmed(body: unknown): boolean {
  if (typeof body !== "object" || body === null || !("device" in body)) return false;
  const device = (body as { device?: unknown }).device;
  if (typeof device !== "object" || device === null) return false;
  const record = device as { state?: unknown; rf_output_enabled?: unknown };
  return record.state === "HOLD" && record.rf_output_enabled === false;
}

export function healthBelongsToInstance(body: unknown, instanceId: string | undefined): boolean {
  if (!instanceId || typeof body !== "object" || body === null) return false;
  const record = body as { status?: unknown; instance_id?: unknown };
  return record.status === "ok" && record.instance_id === instanceId;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function boundedFetch(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export class PythonServiceSupervisor {
  private child?: ChildProcessByStdio<null, Readable, Readable>;
  private serviceInstanceId?: string;
  private startupFailureReason?: string;
  private statusValue: ServiceStatus = {
    state: "STOPPED",
    available: false,
    reason: "本机服务尚未启动",
  };

  constructor(private readonly options: SupervisorOptions) {}

  get status(): ServiceStatus {
    return { ...this.statusValue };
  }

  get baseUrl(): string {
    return `http://${LOOPBACK_HOST}:${this.options.port}/api/v1`;
  }

  get eventUrl(): string {
    return `ws://${LOOPBACK_HOST}:${this.options.port}/api/v1/events`;
  }

  start(): Promise<ServiceStatus> {
    if (this.child) return Promise.resolve(this.status);
    mkdirSync(this.options.runtimeRoot, { recursive: true });
    mkdirSync(this.options.runRoot, { recursive: true });
    mkdirSync(path.dirname(this.options.auditPath), { recursive: true });
    this.statusValue = { state: "STARTING", available: false, reason: "正在启动本机服务" };
    this.startupFailureReason = undefined;
    this.serviceInstanceId = randomUUID();
    this.audit("service.starting", {
      python_path: this.options.pythonPath,
      bind_host: LOOPBACK_HOST,
      port: this.options.port,
      run_root: this.options.runRoot,
      token_transport: "child-environment-only",
    });

    try {
      const child = spawn(
        this.options.pythonPath,
        buildPythonArguments(this.options.port),
        {
          cwd: this.options.runtimeRoot,
          env: {
            ...process.env,
            PVNA_ACCESS_TOKEN: this.options.accessToken,
            PVNA_INSTANCE_ID: this.serviceInstanceId,
            PVNA_RUN_ROOT: this.options.runRoot,
            PYTHONUNBUFFERED: "1",
          },
          stdio: ["ignore", "pipe", "pipe"],
          windowsHide: true,
        },
      );
      this.child = child;
      child.stdout.on("data", (chunk: Buffer) => this.serviceOutput("stdout", chunk));
      child.stderr.on("data", (chunk: Buffer) => this.serviceOutput("stderr", chunk));
      child.once("error", (error) => this.markUnavailable(`无法启动 Python 服务：${error.message}`));
      child.once("exit", (code, signal) => {
        this.child = undefined;
        if (this.statusValue.state !== "STOPPED") {
          this.markUnavailable(
            this.startupFailureReason
              ?? `Python 服务已退出（code=${String(code)}, signal=${String(signal)}）`,
          );
        }
      });
    } catch (error) {
      this.markUnavailable(`无法启动 Python 服务：${error instanceof Error ? error.message : String(error)}`);
      return Promise.resolve(this.status);
    }
    return this.waitUntilHealthy();
  }

  async requestHold(): Promise<HoldResult> {
    if (!this.child || !this.statusValue.available) {
      const result = { outcome: "NOT_STARTED", detail: "本机服务未处于可用状态" } as const;
      this.audit("shutdown.hold", result);
      return result;
    }
    try {
      const response = await boundedFetch(
        `${this.baseUrl}/device/hold`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${this.options.accessToken}` },
        },
        this.options.holdTimeoutMs ?? 1_500,
      );
      const body = (await response.json()) as unknown;
      if (response.ok && holdIsConfirmed(body)) {
        const result = { outcome: "CONFIRMED", detail: "服务确认设备处于 HOLD 且 RF 关闭" } as const;
        this.audit("shutdown.hold", result);
        return result;
      }
      const result = {
        outcome: "UNKNOWN",
        detail: `HOLD 未确认（HTTP ${response.status}）`,
      } as const;
      this.audit("shutdown.hold", result);
      return result;
    } catch (error) {
      const result = {
        outcome: "UNKNOWN",
        detail: `HOLD 请求失败或超时：${error instanceof Error ? error.message : String(error)}`,
      } as const;
      this.audit("shutdown.hold", result);
      return result;
    }
  }

  async stop(): Promise<HoldResult> {
    const holdResult = await this.requestHold();
    const child = this.child;
    if (!child) {
      this.statusValue = { state: "STOPPED", available: false, reason: holdResult.detail };
      return holdResult;
    }

    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    this.statusValue = { state: "STOPPED", available: false, reason: holdResult.detail };
    child.kill();
    await Promise.race([exited, delay(this.options.stopTimeoutMs ?? 1_500)]);
    if (this.child === child) {
      child.kill("SIGKILL");
      await Promise.race([exited, delay(500)]);
    }
    this.child = undefined;
    this.audit("service.stopped", {
      hold_outcome: holdResult.outcome,
      hold_detail: holdResult.detail,
    });
    return holdResult;
  }

  private async waitUntilHealthy(): Promise<ServiceStatus> {
    const deadline = Date.now() + (this.options.startupTimeoutMs ?? 8_000);
    while (this.child && Date.now() < deadline) {
      try {
        const response = await boundedFetch(
          `${this.baseUrl}/health`,
          { headers: { Authorization: `Bearer ${this.options.accessToken}` } },
          500,
        );
        const body = response.ok ? await response.json() as unknown : undefined;
        if (response.ok && healthBelongsToInstance(body, this.serviceInstanceId)) {
          this.statusValue = { state: "AVAILABLE", available: true, reason: "本机服务已就绪" };
          this.audit("service.available", { bind_host: LOOPBACK_HOST, port: this.options.port });
          return this.status;
        }
      } catch {
        // The child is still within its bounded startup window.
      }
      await delay(100);
    }
    if (!this.child) return this.status;
    if (this.child) {
      this.child.kill();
      this.child = undefined;
    }
    this.markUnavailable("Python 服务未在限定时间内通过健康检查");
    return this.status;
  }

  private markUnavailable(reason: string): void {
    this.statusValue = { state: "UNAVAILABLE", available: false, reason };
    this.audit("service.unavailable", { reason });
  }

  private serviceOutput(stream: "stdout" | "stderr", chunk: Buffer): void {
    const text = redactRuntimeText(chunk.toString("utf8"), this.options.accessToken).trim();
    if (/Errno 10048|winerror 10048/i.test(text)) {
      this.startupFailureReason = `本机端口 ${this.options.port} 已被占用，Python 服务未启动`;
    }
    if (text) this.audit("service.output", { stream, text });
  }

  private audit(event: string, data: Record<string, unknown>): void {
    const payload = {
      timestamp_utc: new Date().toISOString(),
      event,
      ...data,
    };
    appendFileSync(this.options.auditPath, `${JSON.stringify(payload)}\n`, { encoding: "utf8" });
  }
}
