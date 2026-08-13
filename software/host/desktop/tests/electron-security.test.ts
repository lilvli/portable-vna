import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  createRendererUrlPolicy,
  DEVELOPMENT_RENDERER_ORIGIN,
  isAllowedRendererUrl,
  isTrustedIpcSender,
  WINDOW_SECURITY,
} from "../electron/main/security";

describe("Electron window security", () => {
  it("keeps renderer privileges closed", () => {
    expect(WINDOW_SECURITY).toMatchObject({
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
    });
  });

  it("allows only the exact packaged entry file", () => {
    const entry = path.resolve("dist/index.html");
    const policy = createRendererUrlPolicy(entry);
    expect(isAllowedRendererUrl(policy.productionEntryUrl, policy)).toBe(true);
    expect(isAllowedRendererUrl(policy.productionEntryUrl.replace("index.html", "other.html"), policy)).toBe(false);
    expect(isAllowedRendererUrl("file:///C:/Windows/System32/drivers/etc/hosts", policy)).toBe(false);
    expect(isAllowedRendererUrl(`${policy.productionEntryUrl}?unexpected=1`, policy)).toBe(false);
    expect(isAllowedRendererUrl(`${policy.productionEntryUrl}#unexpected`, policy)).toBe(false);
  });

  it("enables only the fixed development origin", () => {
    const entry = path.resolve("dist/index.html");
    const policy = createRendererUrlPolicy(entry, `${DEVELOPMENT_RENDERER_ORIGIN}/anything`);
    expect(policy.developmentOrigin).toBe(DEVELOPMENT_RENDERER_ORIGIN);
    expect(isAllowedRendererUrl(`${DEVELOPMENT_RENDERER_ORIGIN}/src/main.tsx`, policy)).toBe(true);
    expect(isAllowedRendererUrl(policy.productionEntryUrl, policy)).toBe(false);
    expect(isAllowedRendererUrl("file:///C:/tmp/hostile.html", policy)).toBe(false);
    expect(isAllowedRendererUrl("http://127.0.0.1:5174/", policy)).toBe(false);
    expect(isAllowedRendererUrl("http://localhost:5173/", policy)).toBe(false);
    expect(createRendererUrlPolicy(entry, "http://127.0.0.1:9999").developmentOrigin).toBeUndefined();
  });

  it("requires URL, main frame, webContents and BrowserWindow identities for IPC", () => {
    const policy = createRendererUrlPolicy(path.resolve("dist/index.html"));
    const trusted = {
      senderFrameUrl: policy.productionEntryUrl,
      senderFrameIsMainFrame: true,
      senderWebContentsId: 7,
      expectedWebContentsId: 7,
      senderWindowId: 3,
      expectedWindowId: 3,
    };
    expect(isTrustedIpcSender(trusted, policy)).toBe(true);
    expect(isTrustedIpcSender({ ...trusted, senderFrameUrl: "file:///C:/tmp/hostile.html" }, policy)).toBe(false);
    expect(isTrustedIpcSender({ ...trusted, senderFrameIsMainFrame: false }, policy)).toBe(false);
    expect(isTrustedIpcSender({ ...trusted, senderWebContentsId: 8 }, policy)).toBe(false);
    expect(isTrustedIpcSender({ ...trusted, senderWindowId: 4 }, policy)).toBe(false);
  });
});
