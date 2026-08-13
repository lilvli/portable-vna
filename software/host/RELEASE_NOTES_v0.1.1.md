# Portable VNA Host v0.1.1

Release date: 2026-08-13

## Summary

This patch release makes the host-only publication self-contained. It includes an in-scope Markdown snapshot of the PVNA-Link V0.1 protocol under `software/host/docs/` and updates the README to use that path. Four Markdown hard breaks are encoded as explicit `<br>` elements so the file passes the repository whitespace gate without changing the rendered metadata layout.

## Release gate

- Python: 82 tests passed; Ruff check and format check passed; compile check passed; lock check passed.
- Frontend: 6 test files / 50 tests passed; lint, TypeScript typecheck, and production build passed.
- Independent phase-one review remains PASS with P0 0 / P1 0 / P2 0 / P3 0.
- Upload scope remains exclusively `software/host/**`.

## Hardware validation boundary

No real COM port, FPGA/JTAG, JESD/LMK/DAC/ADC, RF path, DUT, or physical SOL standard was enumerated, connected, or accessed. This remains an offline/SIMULATED host release, not real-hardware acceptance.
