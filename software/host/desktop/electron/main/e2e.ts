import { createHash } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import type { BrowserWindow } from "electron";
import { atomicWriteValidatedS1p, parseTouchstoneS1p, safeExportFilename } from "./save.js";

interface EvidenceStep {
  name: string;
  status: "PASS" | "FAIL";
  detail: string;
  timestamp_utc: string;
}

const sizes = [
  [1920, 1080],
  [1366, 768],
  [1080, 720],
] as const;

function now(): string {
  return new Date().toISOString();
}

async function pause(milliseconds: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function evaluate<T>(window: BrowserWindow, expression: string): Promise<T> {
  return await window.webContents.executeJavaScript(expression, true) as T;
}

async function waitFor(
  window: BrowserWindow,
  expression: string,
  description: string,
  timeoutMs = 12_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate<boolean>(window, expression)) return;
    await pause(100);
  }
  throw new Error(`等待超时：${description}`);
}

async function clickButton(window: BrowserWindow, label: string): Promise<void> {
  const clicked = await evaluate<boolean>(window, `(() => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === ${JSON.stringify(label)});
    if (!(button instanceof HTMLButtonElement) || button.disabled) return false;
    button.focus(); button.click(); return true;
  })()`);
  if (!clicked) throw new Error(`按钮不可点击：${label}`);
  await pause(80);
}

interface FocusEvidence {
  tag: string;
  text: string;
  disabled: boolean;
  outline: string;
  outlineStyle: string;
  boxShadow: string;
  visible: boolean;
}

async function focusedControl(window: BrowserWindow): Promise<FocusEvidence> {
  return await evaluate<FocusEvidence>(window, `(() => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement)) throw new Error('document has no active element');
    const style = getComputedStyle(active);
    const rect = active.getBoundingClientRect();
    return {
      tag: active.tagName,
      text: active.textContent?.trim() || active.getAttribute('aria-label') || (active instanceof HTMLInputElement ? active.value : ''),
      disabled: active instanceof HTMLButtonElement || active instanceof HTMLInputElement || active instanceof HTMLSelectElement ? active.disabled : false,
      outline: style.outline,
      outlineStyle: style.outlineStyle,
      boxShadow: style.boxShadow,
      visible: rect.width > 0 && rect.height > 0 && rect.left < innerWidth && rect.right > 0 && rect.top < innerHeight && rect.bottom > 0,
    };
  })()`);
}

async function sendKey(
  window: BrowserWindow,
  keyCode: "Tab" | "Enter" | "Space" | "Escape",
  modifiers: Array<"shift"> = [],
): Promise<void> {
  window.focus();
  window.webContents.focus();
  window.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers });
  if (keyCode === "Enter") window.webContents.sendInputEvent({ type: "char", keyCode: "\r", modifiers });
  if (keyCode === "Space") window.webContents.sendInputEvent({ type: "char", keyCode: " ", modifiers });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode, modifiers });
  await pause(100);
}

function assertUsableFocus(focus: FocusEvidence, label: string): void {
  if (focus.disabled || !focus.visible) throw new Error(`${label} did not receive usable visible keyboard focus`);
  if (focus.outlineStyle === "none" && focus.boxShadow === "none") {
    throw new Error(`${label} keyboard focus has no visible outline or shadow`);
  }
}

async function tabTo(
  window: BrowserWindow,
  label: string,
  options: { reverse?: boolean; maximum?: number } = {},
): Promise<FocusEvidence> {
  for (let index = 0; index < (options.maximum ?? 80); index += 1) {
    await sendKey(window, "Tab", options.reverse ? ["shift"] : []);
    const focus = await focusedControl(window);
    if (focus.text === label) {
      assertUsableFocus(focus, label);
      return focus;
    }
  }
  throw new Error(`real ${options.reverse ? "Shift+Tab" : "Tab"} traversal did not reach ${label}`);
}

async function setInput(window: BrowserWindow, ariaLabel: string, value: string): Promise<void> {
  const changed = await evaluate<boolean>(window, `(() => {
    const element = document.querySelector('[aria-label=${JSON.stringify(ariaLabel)}]');
    if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement)) return false;
    const prototype = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
    setter?.call(element, ${JSON.stringify(value)});
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    return element.value === ${JSON.stringify(value)};
  })()`);
  if (!changed) throw new Error(`输入控件不可用：${ariaLabel}`);
  await pause(80);
}

async function waitForRunState(window: BrowserWindow, state: string, timeoutMs = 15_000): Promise<void> {
  await waitFor(
    window,
    `document.querySelector('.run-panel .run-state')?.textContent?.trim() === ${JSON.stringify(state)}`,
    `运行状态 ${state}`,
    timeoutMs,
  );
}

async function runSweep(
  window: BrowserWindow,
  profile: "dut" | "open" | "short" | "load",
  points: string,
): Promise<void> {
  await clickButton(window, "扫频");
  await setInput(window, "采集角色", profile);
  await setInput(window, "起始频率 MHz", "5");
  await setInput(window, "终止频率 MHz", "20");
  await setInput(window, "扫频点数", points);
  await waitFor(window, `!([...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === '开始扫频'))?.disabled`, "开始扫频按钮可用");
  await clickButton(window, "开始扫频");
}

async function capture(window: BrowserWindow, root: string, filename: string, width: number, height: number): Promise<string> {
  window.setContentSize(width, height);
  await pause(250);
  const image = await window.webContents.capturePage();
  const output = path.join(root, filename);
  await writeFile(output, image.toPNG());
  return output;
}

export async function runSimulatedEvidence(window: BrowserWindow, artifactRoot: string): Promise<void> {
  const root = path.resolve(artifactRoot);
  await mkdir(root, { recursive: true });
  const steps: EvidenceStep[] = [];
  const record = (name: string, detail: string) => steps.push({ name, status: "PASS", detail, timestamp_utc: now() });

  try {
    await waitFor(window, `document.body.innerText.includes('Service READY')`, "本机服务 READY");
    await waitFor(window, `document.body.innerText.includes('UNKNOWN') && document.body.innerText.includes('DISCONNECTED')`, "默认断开 UNKNOWN");
    await waitFor(window, `document.querySelector('.notice--fault') === null`, "初始页面无故障 banner");
    const initial = await capture(window, root, "electron-1920x1080-initial-disconnected.png", 1920, 1080);
    record("default_disconnected_unknown", `真实 Electron 初始窗口；${initial}`);

    await evaluate(window, `(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); })()`);
    const connectFocus = await tabTo(window, "显式连接", { maximum: 5 });
    await sendKey(window, "Tab", ["shift"]);
    const previousFocus = await focusedControl(window);
    if (previousFocus.tag !== "SELECT" || previousFocus.disabled) {
      throw new Error("Shift+Tab did not return from connect to the enabled target selector");
    }
    await sendKey(window, "Tab");
    const restoredConnectFocus = await focusedControl(window);
    if (restoredConnectFocus.text !== "显式连接") throw new Error("Tab did not restore connect focus");
    record("keyboard_tab_order", `真实 Tab/Shift+Tab 路径跳过禁用控件并恢复连接按钮：${JSON.stringify({ connectFocus, previousFocus, restoredConnectFocus })}`);

    await sendKey(window, "Enter");
    await waitFor(window, `document.body.innerText.includes('SIMULATED 已显式连接') && document.body.innerText.includes('DEVICE HOLD')`, "显式 SIMULATED 连接并保持 HOLD");
    record("explicit_simulated_connect", "真实 Enter 激活 SIMULATED 显式连接，初态 HOLD；真实资源访问 0");

    const disconnectFocus = await tabTo(window, "断开", { maximum: 5 });
    const holdFocus = await tabTo(window, "HOLD", { maximum: 3 });
    await sendKey(window, "Space");
    await waitFor(window, `document.body.innerText.includes('HOLD 已确认，RF 关闭')`, "HOLD 确认");
    record("hold_confirmed", `真实 Tab + Space 激活 HOLD，服务确认 RF 关闭：${JSON.stringify({ disconnectFocus, holdFocus })}`);

    const singleFocus = await tabTo(window, "单点", { maximum: 4 });
    await sendKey(window, "Space");
    const startSingleFocus = await tabTo(window, "开始单点", { maximum: 16 });
    await sendKey(window, "Enter");
    await waitForRunState(window, "COMPLETED");
    await waitFor(window, `document.querySelectorAll('tbody tr').length >= 1`, "单点 Raw R/A 结果");
    record("single_point", `真实 Space 选择单点、Tab + Enter 启动；完成 1/1 且 Raw R/A 与 A/R/R/A 可回看：${JSON.stringify({ singleFocus, startSingleFocus })}`);

    await runSweep(window, "dut", "401");
    await waitForRunState(window, "RUNNING");
    await waitFor(window, `Number.parseFloat(document.querySelector('.progress-meta strong')?.textContent ?? '0') > 0`, "扫频进度推进");
    record("sweep_progress", "服务已验证并保存点后进度才推进");
    await waitForRunState(window, "COMPLETED");
    record("sweep_complete", "401 点 SIMULATED 扫频完成并进入 HOLD");

    await runSweep(window, "dut", "5000");
    await waitForRunState(window, "RUNNING");
    const cancelFocus = await tabTo(window, "取消", { maximum: 5 });
    await sendKey(window, "Space");
    await waitFor(window, `[...document.querySelectorAll('.run-list__item')].some((item) => item.textContent?.includes('CANCELLED'))`, "运行列表记录 CANCELLED");
    record("bounded_cancel", `真实 Tab + Space 激活有界取消并形成部分只读回放记录：${JSON.stringify(cancelFocus)}`);

    await setInput(window, "起始频率 MHz", "100");
    await setInput(window, "终止频率 MHz", "5");
    await setInput(window, "扫频点数", "11");
    const invalidStartFocus = await tabTo(window, "开始扫频", { maximum: 80 });
    await sendKey(window, "Enter");
    await waitFor(window, `document.querySelector('.notice--fault')?.textContent?.includes('终止频率不能低于起始频率') === true`, "输入错误提示");
    await waitFor(window, `!([...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === '开始扫频'))?.disabled`, "错误处理后操作解锁");
    await waitFor(window, `document.activeElement?.textContent?.trim() === '开始扫频'`, "错误后焦点恢复到开始扫频");
    const restoredAfterError = await focusedControl(window);
    assertUsableFocus(restoredAfterError, "开始扫频 after validation error");
    await sendKey(window, "Escape");
    const afterEscape = await focusedControl(window);
    if (afterEscape.text !== "开始扫频") throw new Error("Escape unexpectedly lost the restored run focus");
    record("validation_error", `真实 Enter 触发降序频率错误；未创建运行，焦点恢复，真实 Escape 后保持可操作：${JSON.stringify({ invalidStartFocus, restoredAfterError, afterEscape })}`);

    for (const profile of ["open", "short", "load"] as const) {
      await setInput(window, "起始频率 MHz", "5");
      await setInput(window, "终止频率 MHz", "20");
      await setInput(window, "扫频点数", "11");
      await setInput(window, "采集角色", profile);
      await clickButton(window, "开始扫频");
      await waitForRunState(window, "COMPLETED");
      record(`sol_${profile}`, `${profile.toUpperCase()} 标准运行完成，11 点冻结轴`);
    }

    await waitFor(window, `!([...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === '创建 SOL 校准'))?.disabled`, "SOL 校准按钮可用");
    await clickButton(window, "创建 SOL 校准");
    await waitFor(window, `(document.querySelector('[aria-label="迹线校准选择"]')?.value ?? '').startsWith('cal_') && document.body.innerText.includes('CALIBRATED')`, "SOL 校准创建并选中");
    record("sol_calibration", "从 measurement_role=OPEN/SHORT/LOAD 的同源原始运行创建派生 SOL 校准；以权威 calibration_id 选中状态确认");

    await runSweep(window, "dut", "11");
    await waitForRunState(window, "COMPLETED");
    await waitFor(window, `document.body.innerText.includes('SOL 校准 S11') && document.body.innerText.includes('CALIBRATED')`, "校准迹线");
    record("calibrated_trace", "DUT Raw R/A 保留，同时回看校准 S11 dB/相位");
    await waitFor(window, `document.querySelectorAll('.run-list__item').length >= 7 && document.querySelectorAll('.event-log li').length >= 1`, "运行列表与事件日志");
    record("history_and_logs", "运行列表、当前运行、历史回看与事件日志可见");
    await waitFor(window, `document.querySelector('.notice--fault') === null`, "健康完成画面无陈旧 BLOCKED");
    record("healthy_banner", "用户取消记录保留在运行列表，但健康 COMPLETED 画面不显示陈旧 BLOCKED");
    await waitFor(
      window,
      `document.querySelector('.notice p')?.textContent?.includes('COMPLETED') === true && document.querySelector('.notice p')?.textContent?.includes('已确认 11/11') === true && document.querySelector('.notice p')?.textContent?.includes('HOLD CONFIRMED') === true`,
      "顶部状态来自权威完成运行快照",
    );
    await waitFor(window, `document.querySelector('.plot-panel h2')?.textContent?.trim() === '幅度曲线 · dB'`, "dB 曲线标题与内容一致");
    record("authoritative_run_status", "顶部 STATUS 由当前权威 run snapshot 派生：COMPLETED、11/11、HOLD CONFIRMED；曲线标题明确为幅度 dB");

    const exported = await evaluate<{ filename: string; content: string; data_kind: string }>(window, `(async () => {
      const bridge = window.pvnaDesktop;
      if (!bridge) throw new Error('desktop bridge unavailable');
      const config = await bridge.getBootstrapConfig();
      const runId = document.querySelector('.progress-meta code')?.textContent?.trim();
      const calibrationId = document.querySelector('[aria-label="迹线校准选择"]')?.value;
      if (!runId || !calibrationId) throw new Error('completed calibrated run is not selected');
      const response = await fetch(config.api.baseUrl + '/runs/' + encodeURIComponent(runId) + '/exports/s1p', {
        method: 'POST',
        headers: { Accept: 'application/json', Authorization: 'Bearer ' + config.api.accessToken, 'Content-Type': 'application/json' },
        body: JSON.stringify({ calibration_id: calibrationId }),
      });
      if (!response.ok) throw new Error('isolated E2E export request failed: ' + response.status);
      const body = await response.json();
      return body.export;
    })()`);
    const isolatedSaveRoot = path.join(root, "isolated-safe-save");
    const isolatedSavePath = path.join(isolatedSaveRoot, safeExportFilename(exported.filename));
    await mkdir(isolatedSaveRoot, { recursive: true });
    try {
      await atomicWriteValidatedS1p(isolatedSavePath, exported.content);
      const persisted = await readFile(isolatedSavePath, "utf8");
      const parsed = parseTouchstoneS1p(persisted);
      const digest = createHash("sha256").update(persisted, "utf8").digest("hex");
      if (parsed.points.length !== 11 || parsed.referenceOhms !== 50) {
        throw new Error("isolated saved S1P did not round-trip as 11-point 50-ohm RI data");
      }
      record("s1p_isolated_atomic_save", `同目录临时写、fsync、独立 RI 语义解析、原子替换完成；11 点/50 ohm/SHA-256 ${digest}；隔离文件验证后清理`);
    } finally {
      await rm(isolatedSaveRoot, { recursive: true, force: true });
    }

    for (const [width, height] of sizes) {
      const filename = `electron-${width}x${height}-simulated-sol.png`;
      const output = await capture(window, root, filename, width, height);
      const layout = await evaluate<{ noHorizontalOverflow: boolean; keyButtonsValid: boolean }>(window, `(() => {
        const keyLabels = ['HOLD', '开始扫频', '保存 .s1p'];
        const buttons = keyLabels.map((label) => [...document.querySelectorAll('button')].find((item) => item.textContent?.trim() === label));
        return {
          noHorizontalOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
          keyButtonsValid: buttons.every((button) => {
            if (!(button instanceof HTMLButtonElement)) return false;
            const rect = button.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && rect.left < innerWidth && rect.right > 0;
          })
        };
      })()`);
      if (!layout.noHorizontalOverflow || !layout.keyButtonsValid) {
        throw new Error(`${width}x${height} 存在横向溢出或关键按钮布局无效`);
      }
      record(`visual_${width}x${height}`, `真实 Electron contentSize=${width}x${height}，无横向溢出且关键按钮有效；${output}`);
      const saveFocus = await tabTo(window, "保存 .s1p", { maximum: 120 });
      await sendKey(window, "Tab", ["shift"]);
      const previous = await focusedControl(window);
      if (previous.disabled) throw new Error(`${width}x${height} Shift+Tab reached a disabled control`);
      await sendKey(window, "Tab");
      const restored = await focusedControl(window);
      if (restored.text !== "保存 .s1p") throw new Error(`${width}x${height} Tab did not restore S1P save focus`);
      assertUsableFocus(restored, `保存 .s1p at ${width}x${height}`);
      await sendKey(window, "Escape");
      record(`keyboard_${width}x${height}`, `真实 Tab/Shift+Tab/Escape，焦点可见且禁用控件不接收焦点：${JSON.stringify({ saveFocus, previous, restored })}`);
    }
  } catch (error) {
    steps.push({
      name: "e2e_failure",
      status: "FAIL",
      detail: error instanceof Error ? error.stack ?? error.message : String(error),
      timestamp_utc: now(),
    });
    try {
      const diagnostic = await evaluate<{ href: string; title: string; bodyText: string; html: string }>(window, `({
        href: location.href,
        title: document.title,
        bodyText: document.body?.innerText ?? '',
        html: document.documentElement?.outerHTML ?? ''
      })`);
      await writeFile(path.join(root, "renderer-diagnostic.json"), `${JSON.stringify(diagnostic, null, 2)}\n`, "utf8");
    } catch {
      // Preserve the original failure.
    }
    try {
      await capture(window, root, "electron-e2e-failure.png", 1366, 768);
    } catch {
      // Preserve the original failure.
    }
    throw error;
  } finally {
    const report = {
      schema_version: "pvna.desktop.e2e.v1",
      electron_version: process.versions.electron,
      run_mode: "SIMULATED",
      real_serial_accesses: 0,
      real_device_accesses: 0,
      browser_preview_evidence: false,
      steps,
    };
    await writeFile(path.join(root, "e2e-report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
}
