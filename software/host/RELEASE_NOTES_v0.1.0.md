# Portable VNA Host v0.1.0

Release date: 2026-08-13

## Scope

This is the first versioned release of the Windows host application. The release commit contains only `software/host/**`; FPGA, JESD, RF, hardware-design, generated runtime, cache, log, dependency, and installer content is excluded.

## Highlights

- Electron + React desktop operator workflow backed by Python as the sole business state machine.
- Explicit SIMULATED and future HARDWARE adapters with fail-closed UNKNOWN behavior.
- Single-point and swept acquisition, cancellation, safe HOLD completion, persisted runs, and read-only replay.
- Offline serial protocol session handling with fake/virtual transports and fault-path coverage.
- One-port SOL calibration and auditable Touchstone S1P export without overwriting raw R/A data.
- Local API token, loopback-only service binding, Electron navigation/IPC restrictions, and atomic file publication.

## Release gate

- Independent review result: PASS; open findings are P0 0 / P1 0 / P2 0 / P3 0.
- Python: 82 passed; Ruff check and format check passed; compile check passed; lock check passed.
- Frontend: 6 test files / 50 tests passed; lint, TypeScript typecheck, and production build passed.
- Whitespace check for `software/host/**`: passed.
- One third-party Starlette/httpx deprecation warning remains; it did not fail the test suite.

## Hardware validation boundary

No real COM port, FPGA/JTAG, JESD/LMK/DAC/ADC, RF path, DUT, or physical SOL standard was enumerated, connected, or accessed for this release. All such acceptance work remains pending separately authorized bench testing.
