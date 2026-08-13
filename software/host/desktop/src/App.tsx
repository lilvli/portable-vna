import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, PvnaApiClient, assertRawPointIntegersAreStrings } from "./api/client";
import type {
  ApiRuntimeConfig,
  CalibrationRecord,
  ConfirmedPoint,
  DeviceSource,
  OperatorDeviceState,
  PvnaEvent,
  RunSnapshot,
  RunSummary,
  SerialPortInfo,
} from "./api/types";
import {
  authoritativeRunStatus,
  createSweepRequest,
  estimateSweep,
  isStandardRunCandidate,
  measurementBlockReason,
  operatorStateFromSnapshot,
  type MeasurementMode,
  type SweepFormValue,
} from "./domain/sweep";
import { RunDetailGate } from "./domain/runDetailGate";

const defaultApiConfig: ApiRuntimeConfig = {
  baseUrl: "http://127.0.0.1:8765/api/v1",
  eventUrl: "ws://127.0.0.1:8765/api/v1/events",
  accessToken: "",
  tokenPresent: false,
  serviceAvailable: false,
  unavailableReason: "浏览器预览未启动受控 Python 服务",
};

const stateOrder: OperatorDeviceState[] = ["SIMULATED", "HARDWARE", "FAULT", "UNKNOWN"];
const terminalRunStates = new Set(["completed", "failed", "cancelled", "unknown"]);
const initialForm: SweepFormValue = {
  mode: "sweep",
  source: "simulated",
  startMhz: "5",
  stopMhz: "100",
  points: "201",
  stimulusAmplitudeQ15: "8192",
  settleUs: "1000",
  integrationCount: "65536",
  pointTimeoutMs: "2000",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "发生未知错误";
}

function frequencyLabel(frequencyHz: number): string {
  if (frequencyHz >= 1_000_000) return `${(frequencyHz / 1_000_000).toFixed(6)} MHz`;
  if (frequencyHz >= 1_000) return `${(frequencyHz / 1_000).toFixed(3)} kHz`;
  return `${frequencyHz} Hz`;
}

function ratioLabel(value: number | undefined, suffix: string): string {
  return value === undefined || !Number.isFinite(value) ? "—" : `${value.toFixed(3)} ${suffix}`;
}

function progressOf(snapshot: RunSnapshot | undefined): number {
  return Math.max(0, Math.min(100, snapshot?.progress.percent ?? 0));
}

function shortRunId(runId: string): string {
  return runId.length > 20 ? `${runId.slice(0, 12)}…${runId.slice(-6)}` : runId;
}

function timestampLabel(raw: string | undefined): string {
  if (!raw) return "—";
  const date = new Date(raw);
  return Number.isNaN(date.valueOf()) ? raw : date.toLocaleString("zh-CN", { hour12: false });
}

function eventText(event: PvnaEvent): string {
  const names: Record<string, string> = {
    "device.status_changed": "设备状态变化",
    "run.started": "运行开始",
    "point.accepted": "测量点已接受",
    "point.confirmed": "测量点已保存确认",
    "run.progress": "运行进度更新",
    "run.completed": "运行完成",
    "run.failed": "运行失败",
    "run.cancelled": "运行已取消",
    "run.unknown": "运行结果不可证明，状态 UNKNOWN",
    "service.log": "服务日志",
  };
  const data = event.data as Record<string, unknown>;
  const suffix =
    typeof data?.message === "string"
      ? data.message
      : event.run_id
        ? shortRunId(event.run_id)
        : "";
  return `${names[event.event] ?? event.event}${suffix ? ` · ${suffix}` : ""}`;
}

function TracePlot({ points, dataKind }: { points: ConfirmedPoint[]; dataKind: "RAW" | "CALIBRATED" }) {
  const series = useMemo(() => {
    const samples = points
      .filter((point) => point.r_over_a && (point.calibrated_s11 || point.a_over_r))
      .map((point) => ({
        frequency: point.frequency_hz,
        ar: (point.calibrated_s11 ?? point.a_over_r)!.magnitude_db,
        ra: point.r_over_a!.magnitude_db,
      }))
      .filter((point) => Number.isFinite(point.ar) && Number.isFinite(point.ra));
    if (samples.length === 0) return undefined;
    const width = 760;
    const height = 230;
    const minX = Math.min(...samples.map((sample) => sample.frequency));
    const maxX = Math.max(...samples.map((sample) => sample.frequency));
    let minY = Math.min(...samples.flatMap((sample) => [sample.ra, sample.ar]));
    let maxY = Math.max(...samples.flatMap((sample) => [sample.ra, sample.ar]));
    if (minY === maxY) [minY, maxY] = [minY - 1, maxY + 1];
    const x = (value: number) => 48 + ((value - minX) / Math.max(1, maxX - minX)) * 694;
    const y = (value: number) => 20 + ((maxY - value) / (maxY - minY)) * 176;
    const path = (key: "ra" | "ar") =>
      samples.map((sample, index) => `${index ? "L" : "M"}${x(sample.frequency)},${y(sample[key])}`).join(" ");
    return { width, height, minY, maxY, minX, maxX, raPath: path("ra"), arPath: path("ar") };
  }, [points]);

  if (!series) {
    return (
      <div className="plot-empty">
        <div className="plot-empty__line" />
        <p>等待已确认测量点</p>
        <span>A/R 为服务结果；R/A 是可用时的派生倒数</span>
      </div>
    );
  }

  return (
    <div className="plot-wrap">
      <svg className="trace-plot" viewBox={`0 0 ${series.width} ${series.height}`} role="img" aria-label={`${dataKind === "CALIBRATED" ? "校准 S11" : "A/R"} 与 R/A 幅度曲线`}>
        {[0, 1, 2, 3, 4].map((tick) => {
          const y = 20 + (tick / 4) * 176;
          const value = series.maxY - (tick / 4) * (series.maxY - series.minY);
          return <g key={tick}><line x1="48" x2="742" y1={y} y2={y} className="plot-grid" /><text x="42" y={y + 4} textAnchor="end" className="plot-label">{value.toFixed(1)}</text></g>;
        })}
        <path d={series.arPath} className="trace trace--cyan" />
        <path d={series.raPath} className="trace trace--amber" />
        <text x="48" y="220" className="plot-label">{frequencyLabel(series.minX)}</text>
        <text x="742" y="220" textAnchor="end" className="plot-label">{frequencyLabel(series.maxX)}</text>
        <text x="8" y="16" className="plot-label">dB</text>
      </svg>
      <div className="legend" aria-label="曲线图例">
        <span><i className="legend__line legend__line--cyan" />{dataKind === "CALIBRATED" ? "S11 校准" : "A/R"}</span>
        <span><i className="legend__line legend__line--amber" />R/A</span>
      </div>
    </div>
  );
}

export default function App() {
  const [apiConfig, setApiConfig] = useState<ApiRuntimeConfig>(defaultApiConfig);
  const [runtimeLabel, setRuntimeLabel] = useState("浏览器预览");
  const client = useMemo(() => new PvnaApiClient(apiConfig), [apiConfig]);
  const [deviceState, setDeviceState] = useState<OperatorDeviceState>("UNKNOWN");
  const [protocolState, setProtocolState] = useState("DISCONNECTED");
  const [connected, setConnected] = useState(false);
  const [source, setSource] = useState<DeviceSource>("simulated");
  const [ports, setPorts] = useState<SerialPortInfo[]>([]);
  const [selectedPort, setSelectedPort] = useState("");
  const [form, setForm] = useState<SweepFormValue>(initialForm);
  const [runs, setRuns] = useState<RunSnapshot[]>([]);
  const [run, setRun] = useState<RunSnapshot>();
  const [summary, setSummary] = useState<RunSummary>();
  const [points, setPoints] = useState<ConfirmedPoint[]>([]);
  const [logs, setLogs] = useState<PvnaEvent[]>([]);
  const [simulationProfile, setSimulationProfile] = useState<"dut" | "open" | "short" | "load">("dut");
  const [calibrations, setCalibrations] = useState<CalibrationRecord[]>([]);
  const [selectedCalibrationId, setSelectedCalibrationId] = useState("");
  const [calibrationRuns, setCalibrationRuns] = useState({ open: "", short: "", load: "" });
  const [traceDataKind, setTraceDataKind] = useState<"RAW" | "CALIBRATED">("RAW");
  const [busy, setBusy] = useState<string>();
  const [message, setMessage] = useState("默认断开；未访问任何设备");
  const [serviceFault, setServiceFault] = useState<string>();
  const [deviceFault, setDeviceFault] = useState<string>();
  const [operationFault, setOperationFault] = useState<string>();
  const [wsConnected, setWsConnected] = useState(false);
  const stopEventsRef = useRef<(() => void) | undefined>(undefined);
  const activeRunIdRef = useRef<string | undefined>(undefined);
  const detailGateRef = useRef(new RunDetailGate());
  const runButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadRuntime() {
      if (!window.pvnaDesktop) return;
      const bootstrap = await window.pvnaDesktop.getBootstrapConfig();
      if (cancelled) return;
      setApiConfig(bootstrap.api);
      setRuntimeLabel(`${bootstrap.runtime.platform} · v${bootstrap.runtime.version} · ${bootstrap.runtime.serviceState}`);
      if (!bootstrap.api.serviceAvailable) setServiceFault(bootstrap.api.unavailableReason ?? bootstrap.runtime.serviceReason);
    }
    void loadRuntime().catch((error) => setServiceFault(errorMessage(error)));
    return () => { cancelled = true; };
  }, []);

  const applyDevice = useCallback((snapshot: Awaited<ReturnType<PvnaApiClient["deviceStatus"]>>) => {
    setConnected(snapshot.device.connected);
    setDeviceState(operatorStateFromSnapshot(snapshot));
    setProtocolState(snapshot.device.state ?? "UNKNOWN");
    const lastError = snapshot.device.last_error ?? snapshot.device.error;
    const unsafeState = ["FAULT", "UNKNOWN"].includes(snapshot.device.state ?? "UNKNOWN");
    setDeviceFault(unsafeState && lastError ? String(lastError) : undefined);
  }, []);

  const refreshDevice = useCallback(async () => {
    if (!client) return;
    const snapshot = await client.deviceStatus();
    applyDevice(snapshot);
  }, [applyDevice, client]);

  const refreshRuns = useCallback(async () => {
    if (!client) return [];
    const snapshots = await client.runs();
    setRuns(snapshots);
    activeRunIdRef.current = snapshots.find((item) => !terminalRunStates.has(item.state))?.run_id;
    return snapshots;
  }, [client]);

  const loadRun = useCallback(async (runId: string, select = false) => {
    if (!client) return;
    const ticket = detailGateRef.current.begin(runId, select);
    const [snapshot, confirmedPoints] = await Promise.all([client.run(runId), client.runPoints(runId)]);
    confirmedPoints.forEach(assertRawPointIntegersAreStrings);
    setRuns((current) => [snapshot, ...current.filter((item) => item.run_id !== snapshot.run_id)]);
    if (!detailGateRef.current.isCurrent(ticket)) return;
    setRun(snapshot);
    setPoints(confirmedPoints);
    setSummary(undefined);
    if (terminalRunStates.has(snapshot.state)) {
      try {
        setSummary(await client.runSummary(runId));
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 409)) throw error;
      }
    }
    setOperationFault(
      (snapshot.state === "failed" || snapshot.state === "unknown") && snapshot.error?.message
        ? snapshot.error.message
        : undefined,
    );
  }, [client]);

  useEffect(() => {
    if (!apiConfig.serviceAvailable || !apiConfig.accessToken) return;
    let stopped = false;
    void (async () => {
      try {
        await client.health();
        const [device, availableRuns, recentLogs, availableCalibrations] = await Promise.all([
          client.deviceStatus(), client.runs(), client.logs(), client.calibrations(),
        ]);
        if (stopped) return;
        applyDevice(device);
        setRuns(availableRuns);
        setLogs(recentLogs);
        setCalibrations(availableCalibrations);
        const selected = availableRuns.find((item) => !terminalRunStates.has(item.state)) ?? availableRuns[0];
        activeRunIdRef.current = availableRuns.find((item) => !terminalRunStates.has(item.state))?.run_id;
        if (selected) await loadRun(selected.run_id, true);
        setServiceFault(undefined);
        setMessage("本机服务已就绪；设备保持默认断开");
      } catch (error) {
        if (!stopped) setServiceFault(errorMessage(error));
      }
    })();

    stopEventsRef.current?.();
    stopEventsRef.current = client.events({
      onConnectionChange: setWsConnected,
      onResyncRequired: () => {
        void refreshDevice().catch((error) => setServiceFault(errorMessage(error)));
        void refreshRuns().catch((error) => setServiceFault(errorMessage(error)));
        const runId = activeRunIdRef.current;
        if (runId) void loadRun(runId).catch((error) => setOperationFault(errorMessage(error)));
      },
      onEvent: (event) => {
        setLogs((current) => [...current.filter((item) => item.event_id !== event.event_id), event].slice(-200));
        if (event.event === "device.status_changed" || event.event.startsWith("run.")) {
          void refreshDevice().catch((error) => setDeviceFault(errorMessage(error)));
        }
        if (event.run_id) {
          if (event.event === "run.started") {
            activeRunIdRef.current = event.run_id;
            void loadRun(event.run_id, true).catch((error) => setOperationFault(errorMessage(error)));
          } else if (["run.completed", "run.failed", "run.cancelled", "run.unknown"].includes(event.event)) {
            void loadRun(event.run_id, detailGateRef.current.selectedRunId === event.run_id)
              .catch((error) => setOperationFault(errorMessage(error)));
          }
        }
      },
    });
    return () => {
      stopped = true;
      stopEventsRef.current?.();
      stopEventsRef.current = undefined;
    };
  }, [apiConfig.serviceAvailable, applyDevice, client, loadRun, refreshDevice, refreshRuns]);

  useEffect(() => {
    const active = runs.find((item) => !terminalRunStates.has(item.state));
    if (!client || !active) return;
    const timer = window.setInterval(() => {
      void loadRun(active.run_id).catch((error) => setOperationFault(errorMessage(error)));
    }, 700);
    return () => window.clearInterval(timer);
  }, [client, loadRun, runs]);

  useEffect(() => {
    if (!client || !run || !terminalRunStates.has(run.state)) {
      setTraceDataKind("RAW");
      return;
    }
    let cancelled = false;
    void client.trace(run.run_id, selectedCalibrationId || undefined)
      .then((trace) => {
        if (cancelled) return;
        setPoints(trace.points);
        setTraceDataKind(trace.dataKind);
      })
      .catch((error) => {
        if (!cancelled) setOperationFault(errorMessage(error));
      });
    return () => { cancelled = true; };
  }, [client, run?.run_id, run?.state, selectedCalibrationId]);

  useEffect(() => {
    const next = { ...calibrationRuns };
    for (const profile of ["open", "short", "load"] as const) {
      const candidates = runs.filter((item) => isStandardRunCandidate(item, profile, source));
      if (!candidates.some((item) => item.run_id === next[profile])) next[profile] = candidates[0]?.run_id ?? "";
    }
    if (next.open !== calibrationRuns.open || next.short !== calibrationRuns.short || next.load !== calibrationRuns.load) {
      setCalibrationRuns(next);
    }
  }, [calibrationRuns, runs, source]);

  async function connectDevice() {
    if (!client) return;
    if (source === "serial" && !selectedPort) {
      setOperationFault("请选择串口后再显式连接 HARDWARE；当前没有访问任何串口");
      return;
    }
    setBusy("connect"); setOperationFault(undefined);
    try {
      await client.health();
      const snapshot = await client.connect({ source, ...(source === "serial" ? { port: selectedPort } : {}) });
      applyDevice(snapshot);
      setMessage(`${snapshot.device.source} 已显式连接，当前 ${snapshot.device.state}`);
    } catch (error) {
      setConnected(false); setDeviceState("UNKNOWN"); setProtocolState("UNKNOWN"); setOperationFault(errorMessage(error));
    } finally { setBusy(undefined); }
  }

  async function disconnectDevice() {
    if (!client) return;
    setBusy("disconnect"); setOperationFault(undefined);
    try {
      const snapshot = await client.disconnect();
      applyDevice(snapshot);
      setMessage("已安全断开；设备安全状态不再由本机服务确认");
    } catch (error) { setOperationFault(errorMessage(error)); }
    finally { setBusy(undefined); }
  }

  async function loadPorts() {
    if (!client) return;
    setBusy("ports"); setOperationFault(undefined);
    try {
      const response = await client.ports();
      setPorts(response.ports);
      if (!selectedPort && response.ports[0]) setSelectedPort(response.ports[0].device);
      setMessage(`只读枚举发现 ${response.ports.length} 个串口；尚未打开`);
    } catch (error) { setOperationFault(errorMessage(error)); }
    finally { setBusy(undefined); }
  }

  async function requestHold() {
    if (!client) return;
    setBusy("hold"); setOperationFault(undefined);
    try {
      const snapshot = await client.hold();
      applyDevice(snapshot);
      setMessage(snapshot.device.state === "HOLD" && snapshot.device.rf_output_enabled === false
        ? "HOLD 已确认，RF 关闭" : "HOLD 未得到完整安全确认");
    } catch (error) { setProtocolState("UNKNOWN"); setDeviceFault(`设备安全状态 UNKNOWN：${errorMessage(error)}`); }
    finally { setBusy(undefined); }
  }

  async function startRun(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    setBusy("run"); setOperationFault(undefined);
    try {
      const request = createSweepRequest({ ...form, source });
      const response = await client.createSweep({
        ...request,
        measurement_role: simulationProfile,
        ...(source === "simulated" ? { simulation_profile: simulationProfile } : {}),
        port_path: "PORT1_REFLECTION",
        reference_impedance_ohm: 50,
      });
      activeRunIdRef.current = response.run_id;
      detailGateRef.current.select(response.run_id);
      setRun(response); setSummary(undefined); setPoints([]);
      setRuns((current) => [response, ...current.filter((item) => item.run_id !== response.run_id)]);
      setMessage(`运行 ${shortRunId(response.run_id)} 已创建；服务已冻结参数快照`);
      await loadRun(response.run_id, true);
    } catch (error) {
      setOperationFault(errorMessage(error));
      window.requestAnimationFrame(() => runButtonRef.current?.focus());
    }
    finally { setBusy(undefined); }
  }

  async function cancelRun() {
    const active = runs.find((item) => !terminalRunStates.has(item.state)) ?? run;
    if (!client || !active) return;
    setBusy("cancel"); setOperationFault(undefined);
    try {
      const snapshot = await client.cancelRun(active.run_id);
      setRun(snapshot); setMessage(`已请求有界取消 ${shortRunId(active.run_id)}`);
      await loadRun(active.run_id, true);
    } catch (error) { setOperationFault(errorMessage(error)); }
    finally { setBusy(undefined); }
  }

  async function createCalibration() {
    if (!client) return;
    setBusy("calibration"); setOperationFault(undefined);
    try {
      const calibration = await client.createCalibration({
        open_run_id: calibrationRuns.open,
        short_run_id: calibrationRuns.short,
        load_run_id: calibrationRuns.load,
      });
      setCalibrations((current) => [calibration, ...current.filter((item) => item.calibration_id !== calibration.calibration_id)]);
      setSelectedCalibrationId(calibration.calibration_id);
      setMessage(`SOL 校准 ${shortRunId(calibration.calibration_id)} 已创建；原始标准运行保持不变`);
    } catch (error) { setOperationFault(errorMessage(error)); }
    finally { setBusy(undefined); }
  }

  async function exportCurrentRun() {
    if (!client || !run) return;
    if (!window.pvnaDesktop) { setOperationFault("Touchstone 保存只在 Electron 安全文件桥中可用"); return; }
    setBusy("export"); setOperationFault(undefined);
    try {
      const exported = await client.exportS1p(run.run_id, selectedCalibrationId || undefined);
      const result = await window.pvnaDesktop.saveTextFile(exported.filename, exported.content);
      setMessage(result.saved ? `${exported.data_kind.toUpperCase()} Touchstone 已保存` : "已取消保存；没有写入文件");
    } catch (error) { setOperationFault(errorMessage(error)); }
    finally { setBusy(undefined); }
  }

  function updateForm<K extends keyof SweepFormValue>(key: K, value: SweepFormValue[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const activeRun = runs.find((item) => !terminalRunStates.has(item.state));
  const serviceReady = Boolean(apiConfig.serviceAvailable && client && apiConfig.tokenPresent);
  const connectBlocked = !serviceReady ? apiConfig.unavailableReason ?? "本机服务不可用" : connected ? "已有设备连接" : busy ? "操作进行中" : undefined;
  const runBlocked = measurementBlockReason({
    serviceReady,
    serviceReason: apiConfig.unavailableReason,
    connected,
    deviceState,
    protocolState,
    activeRunId: activeRun?.run_id,
    busy: Boolean(busy),
  });
  const estimate = estimateSweep(form);
  const calibrationBlocked = !calibrationRuns.open || !calibrationRuns.short || !calibrationRuns.load
    ? "需要同一冻结频率轴的 OPEN / SHORT / LOAD 完整有效运行"
    : busy
      ? "操作进行中"
      : undefined;
  const standardRuns = (profile: "open" | "short" | "load") =>
    runs.filter((item) => isStandardRunCandidate(item, profile, source));
  const fault = serviceFault ?? deviceFault ?? operationFault;
  const statusMessage = run ? authoritativeRunStatus(run) : message;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div><p className="eyebrow">PORTABLE VNA · PHASE 1</p><h1>测量操作台</h1></div>
        <div className="topbar__right">
          <div className="state-rail" aria-label={`证据来源 ${deviceState}`}>
            {stateOrder.map((state) => <span key={state} className={`state-pill state-pill--${state.toLowerCase()} ${deviceState === state ? "is-active" : ""}`}><i />{state}</span>)}
          </div>
          <p className="runtime">{runtimeLabel} · DEVICE {protocolState}</p>
        </div>
      </header>

      <section className="notice-row" aria-live="polite">
        <div className={`notice ${fault ? "notice--fault" : ""}`}><span>{fault ? "BLOCKED" : "STATUS"}</span><p>{fault ?? statusMessage}</p></div>
        <div className="service-indicator"><i className={wsConnected ? "is-online" : ""} />WS {wsConnected ? "在线" : "离线"} · Service {serviceReady ? "READY" : "UNAVAILABLE"}</div>
      </section>

      <div className="workspace-grid">
        <aside className="control-stack">
          <section className="panel connection-panel">
            <div className="panel__heading"><div><span className="section-number">01</span><h2>连接与安全</h2></div><span className="endpoint">127.0.0.1 only</span></div>
            <label className="field"><span>目标</span><select value={source} disabled={connected} onChange={(event) => setSource(event.target.value as DeviceSource)}><option value="simulated">SIMULATED · 离线仿真</option><option value="serial">HARDWARE · 串口软件层（实板未验收）</option></select></label>
            {source === "serial" && <div className="port-row"><label className="field field--grow"><span>串口（只在显式连接后打开）</span><select value={selectedPort} disabled={connected} onChange={(event) => setSelectedPort(event.target.value)}><option value="">请选择</option>{ports.map((port) => <option key={port.device} value={port.device}>{port.description ? `${port.device} · ${port.description}` : port.device}</option>)}</select></label><button className="button button--quiet" type="button" disabled={connected || Boolean(busy) || !serviceReady} onClick={() => void loadPorts()}>只读刷新</button></div>}
            <div className="button-row">
              <button className="button button--primary" type="button" disabled={Boolean(connectBlocked)} title={connectBlocked} onClick={() => void connectDevice()}>显式连接</button>
              <button className="button button--quiet" type="button" disabled={!connected || Boolean(activeRun) || Boolean(busy)} title={!connected ? "当前未连接" : activeRun ? "运行结束后才能断开" : undefined} onClick={() => void disconnectDevice()}>断开</button>
              <button className="button button--hold" type="button" disabled={!connected || Boolean(busy)} title={!connected ? "当前未连接，无法确认 HOLD" : undefined} onClick={() => void requestHold()}>HOLD</button>
            </div>
            {connectBlocked && !connected && <p className="disabled-reason">连接禁用：{connectBlocked}</p>}
          </section>

          <form className="panel" onSubmit={startRun}>
            <div className="panel__heading"><div><span className="section-number">02</span><h2>测量参数</h2></div><span className="snapshot-note">启动后服务冻结</span></div>
            <div className="segmented" aria-label="测量模式">{(["single", "sweep"] as MeasurementMode[]).map((mode) => <button key={mode} className={form.mode === mode ? "is-selected" : ""} type="button" disabled={Boolean(activeRun)} onClick={() => updateForm("mode", mode)}>{mode === "single" ? "单点" : "扫频"}</button>)}</div>
            <label className="field profile-field"><span>采集角色<b>{source === "simulated" ? "决定模拟器生成类型" : "标准件由操作员接入"}</b></span><select aria-label="采集角色" value={simulationProfile} disabled={Boolean(activeRun)} onChange={(event) => setSimulationProfile(event.target.value as typeof simulationProfile)}><option value="dut">DUT · 被测件</option><option value="open">OPEN · 开路标准</option><option value="short">SHORT · 短路标准</option><option value="load">LOAD · 负载标准</option></select></label>
            <div className="form-grid">
              <label className="field"><span>{form.mode === "single" ? "频率" : "起始频率"}<b>MHz</b></span><input aria-label={form.mode === "single" ? "频率 MHz" : "起始频率 MHz"} type="number" min="0.001" step="0.001" value={form.startMhz} onChange={(event) => updateForm("startMhz", event.target.value)} /></label>
              {form.mode === "sweep" && <label className="field"><span>终止频率<b>MHz</b></span><input aria-label="终止频率 MHz" type="number" min="0.001" step="0.001" value={form.stopMhz} onChange={(event) => updateForm("stopMhz", event.target.value)} /></label>}
              {form.mode === "sweep" && <label className="field"><span>点数</span><input aria-label="扫频点数" type="number" min="2" step="1" value={form.points} onChange={(event) => updateForm("points", event.target.value)} /></label>}
              <label className="field"><span>激励幅度<b>Q15</b></span><input type="number" min="0" max="32767" step="1" value={form.stimulusAmplitudeQ15} onChange={(event) => updateForm("stimulusAmplitudeQ15", event.target.value)} /></label>
              <label className="field"><span>稳定时间<b>μs</b></span><input type="number" min="0" step="1" value={form.settleUs} onChange={(event) => updateForm("settleUs", event.target.value)} /></label>
              <label className="field"><span>积分次数</span><input type="number" min="1" step="1" value={form.integrationCount} onChange={(event) => updateForm("integrationCount", event.target.value)} /></label>
              <label className="field"><span>单点超时<b>ms</b></span><input type="number" min="1" step="1" value={form.pointTimeoutMs} onChange={(event) => updateForm("pointTimeoutMs", event.target.value)} /></label>
            </div>
            <div className="plan-estimate"><span>线性步进<strong>{estimate.stepMhz === undefined ? "—" : `${estimate.stepMhz.toFixed(6)} MHz`}</strong></span><span>估算点数<strong>{estimate.estimatedPoints ?? "输入无效"}</strong></span></div>
            <div className="run-actions"><button ref={runButtonRef} className="button button--run" type="submit" disabled={Boolean(runBlocked)} title={runBlocked}>{form.mode === "single" ? "开始单点" : "开始扫频"}</button><button className="button button--danger" type="button" disabled={!activeRun || Boolean(busy)} title={!activeRun ? "没有活动运行" : undefined} onClick={() => void cancelRun()}>取消</button></div>
            {runBlocked && <p className="disabled-reason">测量禁用：{runBlocked}</p>}
          </form>

          <section className="panel calibration-panel">
            <div className="panel__heading"><div><span className="section-number">03</span><h2>SOL 校准</h2></div><span className="snapshot-note">只派生，不覆盖 Raw R/A</span></div>
            <div className="calibration-grid">
              {(["open", "short", "load"] as const).map((profile) => <label className="field" key={profile}><span>{profile.toUpperCase()} 标准运行</span><select aria-label={`${profile.toUpperCase()} 标准运行`} value={calibrationRuns[profile]} onChange={(event) => setCalibrationRuns((current) => ({ ...current, [profile]: event.target.value }))}><option value="">请选择已完成运行</option>{standardRuns(profile).map((item) => <option key={item.run_id} value={item.run_id}>{shortRunId(item.run_id)} · {item.progress.total} 点</option>)}</select></label>)}
            </div>
            <button className="button button--calibrate" type="button" disabled={Boolean(calibrationBlocked)} title={calibrationBlocked} onClick={() => void createCalibration()}>创建 SOL 校准</button>
            {calibrationBlocked && <p className="disabled-reason">校准禁用：{calibrationBlocked}</p>}
          </section>

          <section className="panel run-list-panel">
            <div className="panel__heading"><div><span className="section-number">04</span><h2>运行列表</h2></div><button className="text-button" type="button" disabled={!serviceReady || Boolean(busy)} onClick={() => void refreshRuns()}>刷新</button></div>
            <div className="run-list">{runs.length === 0 ? <p className="empty-copy">暂无可回看的运行</p> : runs.map((item) => <button key={item.run_id} type="button" className={`run-list__item ${run?.run_id === item.run_id ? "is-selected" : ""}`} onClick={() => void loadRun(item.run_id, true)}><span><b>{shortRunId(item.run_id)}</b><small>{timestampLabel(item.createdAtUtc)}</small></span><em className={`run-state run-state--${item.state}`}>{item.state.toUpperCase()}</em><span className="run-list__progress">{item.progress.confirmed}/{item.progress.total}</span></button>)}</div>
          </section>
        </aside>

        <section className="results-stack">
          <section className="panel run-panel">
            <div className="panel__heading"><div><span className="section-number">05</span><h2>当前运行 / 回看</h2></div><span className={`run-state run-state--${run?.state ?? "idle"}`}>{run?.state?.toUpperCase() ?? "IDLE"}</span></div>
            <div className="progress-meta"><strong>{progressOf(run).toFixed(1)}%</strong><span>{run ? `${run.progress.confirmed} / ${run.progress.total} 个已验证并保存点` : "尚未选择运行"}</span><code>{run?.run_id ?? "—"}</code></div>
            <progress aria-label="运行进度" max="100" value={progressOf(run)} />
            <div className="trace-controls"><label className="field"><span>迹线数据</span><select aria-label="迹线校准选择" value={selectedCalibrationId} disabled={!run || run.state !== "completed"} onChange={(event) => setSelectedCalibrationId(event.target.value)}><option value="">RAW · 未校准 A/R</option>{calibrations.map((item) => <option key={item.calibration_id} value={item.calibration_id}>CALIBRATED · {shortRunId(item.calibration_id)}</option>)}</select></label><button className="button button--quiet" type="button" disabled={!run || run.state !== "completed" || Boolean(busy)} title={!run || run.state !== "completed" ? "只能导出完整有效运行" : undefined} onClick={() => void exportCurrentRun()}>保存 .s1p</button></div>
            {run && <div className="run-facts"><span>来源 <b>{run.source ?? "UNKNOWN"}</b></span><span>数据 <b>{run.dataValidation ?? "PENDING"}</b></span><span>安全 HOLD <b>{run.safeHoldConfirmed === true ? "CONFIRMED" : run.safeHoldConfirmed === false ? "UNKNOWN" : "PENDING"}</b></span></div>}
            {summary && <p className="evidence-boundary">{summary.evidence_boundary}</p>}
          </section>

          <section className="panel plot-panel"><div className="panel__heading"><div><span className="section-number">06</span><h2>幅度曲线 · dB</h2></div><span className="snapshot-note">{traceDataKind === "CALIBRATED" ? "SOL 校准 S11" : "RAW A/R"} · 相位见下表</span></div><TracePlot points={points} dataKind={traceDataKind} /></section>

          <section className="panel table-panel">
            <div className="panel__heading"><div><span className="section-number">07</span><h2>Raw R / A 与复数比值</h2></div><span className="snapshot-note">原始 64 位整数保持十进制字符串</span></div>
            <div className="table-scroll"><table><thead><tr><th>#</th><th>频率</th><th>Raw R (I / Q)</th><th>Raw A (I / Q)</th><th>A/R · dB / 相位</th><th>R/A · dB / 相位</th><th>校准 S11 · dB / 相位</th></tr></thead><tbody>{points.length === 0 ? <tr><td colSpan={7} className="empty-cell">没有已确认测量点</td></tr> : points.map((point) => <tr key={`${point.index}-${point.frequency_hz}`}><td>{point.index}</td><td>{frequencyLabel(point.frequency_hz)}</td><td className="raw-value" title={`${point.reference_i} / ${point.reference_q}`}>{point.reference_i} / {point.reference_q}</td><td className="raw-value" title={`${point.antenna_i} / ${point.antenna_q}`}>{point.antenna_i} / {point.antenna_q}</td><td>{ratioLabel(point.a_over_r?.magnitude_db, "dB")} / {ratioLabel(point.a_over_r?.phase_deg, "°")}</td><td>{point.r_over_a ? `${ratioLabel(point.r_over_a.magnitude_db, "dB")} / ${ratioLabel(point.r_over_a.phase_deg, "°")}` : "不可用"}</td><td>{point.calibrated_s11 ? `${ratioLabel(point.calibrated_s11.magnitude_db, "dB")} / ${ratioLabel(point.calibrated_s11.phase_deg, "°")}` : "—"}</td></tr>)}</tbody></table></div>
          </section>

          <section className="panel log-panel">
            <div className="panel__heading"><div><span className="section-number">08</span><h2>服务事件日志</h2></div><span className="snapshot-note">最近 {logs.length} / 200</span></div>
            <ol className="event-log" aria-live="polite">{logs.length === 0 ? <li className="empty-copy">暂无服务事件</li> : [...logs].reverse().slice(0, 40).map((event) => <li key={event.event_id}><time>{timestampLabel(event.timestamp_utc)}</time><span>{eventText(event)}</span><code>#{event.event_id}</code></li>)}</ol>
          </section>
        </section>
      </div>
    </main>
  );
}
