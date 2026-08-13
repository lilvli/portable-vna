export {};

import type { DesktopBootstrapConfig } from "./api/types";

declare global {
  interface Window {
    pvnaDesktop?: {
      getBootstrapConfig(): Promise<DesktopBootstrapConfig>;
      saveTextFile(filename: string, content: string): Promise<{
        saved: boolean;
        canceled: boolean;
      }>;
    };
  }
}
