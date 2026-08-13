import { describe, expect, it } from "vitest";
import { RunDetailGate } from "../src/domain/runDetailGate";

describe("RunDetailGate", () => {
  it("prevents a slow historical request from overwriting a newly selected active run", () => {
    const gate = new RunDetailGate();
    const oldRequest = gate.begin("run_old_401_points", true);
    const activeRequest = gate.begin("run_active_cancelled", true);

    expect(gate.isCurrent(activeRequest)).toBe(true);
    expect(gate.isCurrent(oldRequest)).toBe(false);
    expect(gate.selectedRunId).toBe("run_active_cancelled");
  });

  it("allows list refreshes for other runs without selecting them", () => {
    const gate = new RunDetailGate();
    const selected = gate.begin("run_selected", true);
    const listOnly = gate.begin("run_history", false);

    expect(gate.isCurrent(selected)).toBe(true);
    expect(listOnly.appliesToDetail).toBe(false);
    expect(gate.selectedRunId).toBe("run_selected");
  });

  it("uses request generations so only the latest same-run response wins", () => {
    const gate = new RunDetailGate();
    const slow = gate.begin("run_active", true);
    const fresh = gate.begin("run_active", false);

    expect(gate.isCurrent(slow)).toBe(false);
    expect(gate.isCurrent(fresh)).toBe(true);
  });
});
