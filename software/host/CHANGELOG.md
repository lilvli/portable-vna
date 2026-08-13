# Changelog

All notable changes to the Portable VNA host application are documented here.

## [0.1.0] - 2026-08-13

### Added

- Electron and React operator console with explicit DISCONNECTED, SIMULATED, HARDWARE, FAULT, and UNKNOWN states.
- Python business-state service for single-point and swept measurements, cancellation, HOLD handling, persistence, and replay.
- Offline PVNA-Link V0.1 framing, CRC, resynchronization, correlation, retry, recovery, and fake/virtual transports.
- One-port SOL calibration, raw R/A preservation, calibrated S11 display, and Touchstone S1P export.
- Local-only API security, controlled Electron preload/IPC boundaries, and atomic save behavior.
- Reproducible Python and frontend dependency locks, automated tests, build checks, and independent phase-one audit evidence.

### Validation

- Independent phase-one software audit: PASS with P0/P1/P2/P3 open counts all zero.
- Release verification: 82 Python tests and 50 frontend tests passed; Ruff, format, lint, TypeScript, compile, lock, production build, and whitespace checks passed.
- Real serial, FPGA, JESD, ADC/DAC, RF, and physical SOL hardware access: 0.

### Boundary

This release validates the host software and SIMULATED/offline paths only. It is not real-hardware acceptance and does not include an installer package.
