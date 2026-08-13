# Phase 1 desktop audit remediation evidence

Date: 2026-08-12
Scope: `software/host/desktop/**` only
Execution boundary: Electron 43.4.0 + controlled Python service + `SIMULATED`; serial and real-device accesses were both zero.

## Remediated findings

- **P2-03 renderer and IPC trust:** production accepts only the normalized `dist/index.html` URL. Development accepts only the fixed `http://127.0.0.1:5173` origin; file URLs are not accepted in development. Both exposed IPC handlers require the main frame URL, exact `webContents`, and owning `BrowserWindow` to match the active application window. Unit tests reject another file, query/hash variants, another origin, a child frame, another `webContents`, and another window.
- **P2-04 measurement fail-closed:** a run requires service readiness, explicit connection, known source, protocol state `HOLD`, `IDLE`, or `RESULT_READY`, and no active run/operation. Connected `HARDWARE` snapshots in `UNKNOWN`, `FAULT`, `BOOT`, or `BUSY` are rejected with the protocol state in the visible disabled reason.
- **P2-05 S1P publication:** the desktop boundary independently parses Touchstone one-port RI data, writes an exclusive temporary file in the target directory, calls file `fsync`, reads and independently re-parses the persisted bytes, compares semantics, and then atomically replaces the target. A real isolated-directory Windows test replaces an existing file and round-trips MHz RI data; an injected replacement failure proves the previous target remains unchanged. The Electron E2E also obtained a calibrated S1P, saved it through this same primitive in an isolated directory, parsed 11 points at 50 ohm, recorded its SHA-256, and removed the temporary evidence directory.
- **P3-01 keyboard evidence:** the Electron E2E uses `webContents.sendInputEvent`, not DOM `.click()`, for real `Tab`, `Shift+Tab`, `Enter`, `Space`, and `Escape` paths. It covers explicit connection, HOLD, single point, cancel, validation-error focus restoration, disabled-control skipping, visible focus, and repeated save-control traversal at 1920x1080, 1366x768, and 1080x720.
- **P3-02 authority and graph semantics:** the top status is derived from the selected authoritative run snapshot and shows terminal state, confirmed/expected count, and HOLD evidence. The plot heading now says `幅度曲线 · dB`; phase remains explicitly identified as table data.
- **Measurement role API update:** every run request carries `measurement_role`. `simulation_profile` is sent only to the simulator. OPEN/SHORT/LOAD candidates use `measurement_role` and must match the currently selected source, including HARDWARE candidates without claiming hardware validation.

## Verification

Commands run from `software/host/desktop`:

```text
npm.cmd test                 PASS: 6 files, 49 tests
npm.cmd run lint             PASS
npm.cmd run typecheck        PASS
npm.cmd run build            PASS (Electron TypeScript + Vite production build)
npm.cmd run e2e:simulated    PASS: 24/24 evidence steps
```

The successful E2E report records `electron_version=43.4.0`, `run_mode=SIMULATED`, `real_serial_accesses=0`, `real_device_accesses=0`, and `browser_preview_evidence=false`. The isolated userData was removed after the successful lifecycle audit was copied out. Old failure/renderer diagnostic artifacts were removed.

## Evidence index and SHA-256

| Evidence | SHA-256 |
|---|---|
| `test-artifacts/electron-e2e/e2e-report.json` | `AF75ECCC228829DDA0653FEDAE05639F47846857F710D30E5070A33DC9342D27` |
| `test-artifacts/electron-e2e/lifecycle-e2e.jsonl` | `CE6FB7DE57A84931DF4D85804228F567EC891A0CB13213F07CA631CE4733B2D5` |
| `test-artifacts/electron-e2e/electron-1920x1080-initial-disconnected.png` | `2C51CB1BE3AF4BB1F5D1C5E4D574CD661D58C1A6A1FE76BADAA3095BB1F32A1A` |
| `test-artifacts/electron-e2e/electron-1920x1080-simulated-sol.png` | `8ECA2AE3E9920A5A9D4B892F1E580A3EC6804FF279AC359331720AAE14446D32` |
| `test-artifacts/electron-e2e/electron-1366x768-simulated-sol.png` | `2564155B4C1415D43C5FC795E678DDF76440A94719E1CDE5EC1C3B4EC2918B3A` |
| `test-artifacts/electron-e2e/electron-1080x720-simulated-sol.png` | `B84D8C9AB860A404CCF82905C2B25D5F8C448C574BD414BAF7E796823490ED60` |

## Unverified boundary

No serial port, FPGA, JESD link, RF path, DUT, or physical SOL standard was accessed. The evidence proves the Windows desktop, local-service, simulated measurement, UI, and file-publication behavior only; it is not real-hardware validation.
