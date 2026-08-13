import { randomBytes } from "node:crypto";
import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, session, type IpcMainInvokeEvent } from "electron";
import {
  PythonServiceSupervisor,
  discoverPython,
  parseApiPort,
  type ServiceStatus,
} from "./runtime.js";
import {
  createRendererUrlPolicy,
  isAllowedRendererUrl,
  isTrustedIpcSender,
  WINDOW_SECURITY,
} from "./security.js";
import { atomicWriteValidatedS1p, validateTextExport } from "./save.js";
import { runSimulatedEvidence } from "./e2e.js";

if (process.env.PVNA_E2E_ARTIFACTS) {
  // Keep automated capture deterministic on Windows hosts with unavailable GPU runtimes.
  app.disableHardwareAcceleration();
}

const accessToken = randomBytes(32).toString("base64url");
const port = parseApiPort(process.env.PVNA_API_PORT);
if (process.env.PVNA_E2E_USER_DATA) {
  app.setPath("userData", path.resolve(process.env.PVNA_E2E_USER_DATA, `run-${process.pid}`));
}
let supervisor: PythonServiceSupervisor | undefined;
let mainWindow: BrowserWindow | undefined;
let serviceStatus: ServiceStatus = {
  state: "UNAVAILABLE",
  available: false,
  reason: "本机服务尚未初始化",
};
let quitting = false;

function backendDirectory(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, "backend")
    : path.resolve(__dirname, "../../../backend");
}

function bootstrapConfig() {
  return {
    api: {
      baseUrl: `http://127.0.0.1:${port}/api/v1`,
      eventUrl: `ws://127.0.0.1:${port}/api/v1/events`,
      accessToken,
      tokenPresent: true,
      serviceAvailable: serviceStatus.available,
      unavailableReason: serviceStatus.available ? undefined : serviceStatus.reason,
    },
    runtime: {
      platform: process.platform,
      version: app.getVersion(),
      serviceState: serviceStatus.state,
      serviceReason: serviceStatus.reason,
    },
  };
}

const productionEntryPath = path.resolve(__dirname, "../../dist/index.html");
const rendererPolicy = createRendererUrlPolicy(productionEntryPath, process.env.VITE_DEV_SERVER_URL);

function assertTrustedIpcEvent(event: IpcMainInvokeEvent): BrowserWindow {
  const expected = mainWindow;
  const senderFrame = event.senderFrame;
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!expected || expected.isDestroyed() || !senderFrame || !owner || !isTrustedIpcSender({
    senderFrameUrl: senderFrame.url,
    senderFrameIsMainFrame: senderFrame === event.sender.mainFrame,
    senderWebContentsId: event.sender.id,
    expectedWebContentsId: expected.webContents.id,
    senderWindowId: owner.id,
    expectedWindowId: expected.id,
  }, rendererPolicy)) {
    throw new Error("Rejected IPC call from an untrusted renderer");
  }
  return expected;
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 720,
    backgroundColor: "#07101c",
    show: false,
    title: "Portable VNA Console",
    webPreferences: {
      ...WINDOW_SECURITY,
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  window.removeMenu();
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.webContents.on("render-process-gone", (_event, details) => {
    console.error(`renderer process gone: ${details.reason} (${details.exitCode})`);
  });
  window.webContents.on("will-navigate", (event, destination) => {
    if (!isAllowedRendererUrl(destination, rendererPolicy)) event.preventDefault();
  });
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = undefined;
  });
  mainWindow = window;
  window.once("ready-to-show", () => window.show());
  const evidenceRoot = process.env.PVNA_E2E_ARTIFACTS;
  if (evidenceRoot) {
    window.webContents.once("did-finish-load", () => {
      void runSimulatedEvidence(window, evidenceRoot)
        .then(async () => {
          if (supervisor) await supervisor.stop();
          quitting = true;
          app.exit(0);
        })
        .catch(async (error) => {
          console.error(error instanceof Error ? error.message : String(error));
          if (supervisor) await supervisor.stop();
          quitting = true;
          app.exit(1);
        });
    });
  }

  if (rendererPolicy.developmentOrigin) {
    void window.loadURL(rendererPolicy.developmentOrigin).catch((error) => console.error(`loadURL failed: ${error.message}`));
  } else {
    void window.loadFile(productionEntryPath)
      .catch((error) => console.error(`loadFile failed: ${error.message}`));
  }
  return window;
}

async function initializeService(): Promise<void> {
  const backendDir = backendDirectory();
  const discovery = discoverPython({
    override: process.env.PVNA_PYTHON,
    backendDir,
    resourcesPath: process.resourcesPath,
    packaged: app.isPackaged,
  });
  if (!discovery.pythonPath) {
    serviceStatus = { state: "UNAVAILABLE", available: false, reason: discovery.reason! };
    return;
  }

  const userData = app.getPath("userData");
  supervisor = new PythonServiceSupervisor({
    pythonPath: discovery.pythonPath,
    backendDir,
    runtimeRoot: path.join(userData, "runtime"),
    runRoot: path.join(userData, "runs"),
    auditPath: path.join(userData, "audits", "lifecycle.jsonl"),
    port,
    accessToken,
  });
  serviceStatus = await supervisor.start();
}

app.whenReady().then(async () => {
  await initializeService();
  ipcMain.handle("pvna:get-bootstrap-config", (event) => {
    assertTrustedIpcEvent(event);
    return bootstrapConfig();
  });
  ipcMain.handle("pvna:save-text-file", async (event, payload: unknown) => {
    const owner = assertTrustedIpcEvent(event);
    const exportFile = validateTextExport(payload);
    const options = {
      title: "保存 Touchstone S1P",
      defaultPath: path.join(app.getPath("documents"), exportFile.filename),
      filters: [{ name: "Touchstone S1P", extensions: ["s1p"] }],
      properties: ["showOverwriteConfirmation" as const],
    };
    const selection = await dialog.showSaveDialog(owner, options);
    if (selection.canceled || !selection.filePath) return { saved: false, canceled: true };
    await atomicWriteValidatedS1p(selection.filePath, exportFile.content);
    return { saved: true, canceled: false };
  });
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  session.defaultSession.setPermissionCheckHandler(() => false);
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  void (async () => {
    if (supervisor) await supervisor.stop();
    app.quit();
  })();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
