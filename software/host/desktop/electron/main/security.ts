import { pathToFileURL } from "node:url";
import type { BrowserWindowConstructorOptions } from "electron";

export const DEVELOPMENT_RENDERER_ORIGIN = "http://127.0.0.1:5173";

export const WINDOW_SECURITY: Readonly<BrowserWindowConstructorOptions["webPreferences"]> = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  webviewTag: false,
  allowRunningInsecureContent: false,
};

export interface RendererUrlPolicy {
  productionEntryUrl: string;
  developmentOrigin?: string;
}

export interface IpcSenderIdentity {
  senderFrameUrl: string;
  senderFrameIsMainFrame: boolean;
  senderWebContentsId: number;
  expectedWebContentsId: number;
  senderWindowId: number | null;
  expectedWindowId: number;
}

export function createRendererUrlPolicy(
  productionEntryPath: string,
  requestedDevelopmentUrl?: string,
): RendererUrlPolicy {
  let developmentOrigin: string | undefined;
  if (requestedDevelopmentUrl) {
    try {
      const requested = new URL(requestedDevelopmentUrl);
      if (requested.origin === DEVELOPMENT_RENDERER_ORIGIN) developmentOrigin = DEVELOPMENT_RENDERER_ORIGIN;
    } catch {
      // Invalid or non-fixed development URLs never enable development navigation.
    }
  }
  return {
    productionEntryUrl: pathToFileURL(productionEntryPath).href,
    developmentOrigin,
  };
}

export function isAllowedRendererUrl(candidate: string, policy: RendererUrlPolicy): boolean {
  try {
    const url = new URL(candidate);
    if (policy.developmentOrigin) return url.origin === policy.developmentOrigin;
    return url.protocol === "file:" && url.href === policy.productionEntryUrl;
  } catch {
    return false;
  }
}

export function isTrustedIpcSender(identity: IpcSenderIdentity, policy: RendererUrlPolicy): boolean {
  return identity.senderFrameIsMainFrame &&
    identity.senderWebContentsId === identity.expectedWebContentsId &&
    identity.senderWindowId === identity.expectedWindowId &&
    isAllowedRendererUrl(identity.senderFrameUrl, policy);
}
