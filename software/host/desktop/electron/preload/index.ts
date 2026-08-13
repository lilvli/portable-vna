import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("pvnaDesktop", {
  getBootstrapConfig: () => ipcRenderer.invoke("pvna:get-bootstrap-config"),
  saveTextFile: (filename: string, content: string) =>
    ipcRenderer.invoke("pvna:save-text-file", { filename, content }),
});
