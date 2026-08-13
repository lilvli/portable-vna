import { describe, expect, it, vi } from "vitest";
import { ApiError, PvnaApiClient, assertRawPointIntegersAreStrings, mapServicePoint, mapTracePoint, u64StringToSafeNumber } from "../src/api/client";
import type { ApiRuntimeConfig, ConfirmedPoint } from "../src/api/types";

const config: ApiRuntimeConfig = {
  baseUrl: "http://127.0.0.1:8765/api/v1",
  eventUrl: "ws://127.0.0.1:8765/api/v1/events",
  accessToken: "test-token",
  tokenPresent: true,
};

describe("PvnaApiClient", () => {
  it("sends bearer authorization only to a loopback API", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ schema_version: "pvna.api.v1", status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new PvnaApiClient(config, fetchMock);

    await client.health();

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8765/api/v1/health");
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer test-token",
    });
  });

  it("does not invoke browser fetch with the API client as its this value", async () => {
    const fetchMock = vi.fn(function (this: unknown) {
      expect(this).not.toBeInstanceOf(PvnaApiClient);
      return Promise.resolve(new Response(JSON.stringify({ schema_version: "pvna.api.v1", status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    }) as unknown as typeof fetch;
    const client = new PvnaApiClient(config, fetchMock);
    await client.health();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("binds the default global fetch to globalThis", async () => {
    const defaultFetch = vi.fn(function (this: unknown) {
      expect(this).toBe(globalThis);
      return Promise.resolve(new Response(JSON.stringify({ schema_version: "pvna.api.v1", status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    });
    vi.stubGlobal("fetch", defaultFetch);
    try {
      const client = new PvnaApiClient(config);
      await client.health();
      expect(defaultFetch).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("rejects remote API configuration", () => {
    expect(
      () =>
        new PvnaApiClient({
          ...config,
          baseUrl: "https://example.com/api/v1",
        }),
    ).toThrow("本机回环地址");
  });

  it("fails closed when the access token is missing", async () => {
    const client = new PvnaApiClient({ ...config, accessToken: "", tokenPresent: false });
    await expect(client.health()).rejects.toBeInstanceOf(ApiError);
  });

  it("maps the frozen run list and preserves safety evidence", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({
        schema_version: "pvna.api.v1",
        runs: [{
          run_id: "run_example",
          source: "SIMULATED",
          state: "COMPLETED",
          confirmed_points: 3,
          expected_points: 3,
          progress: 1,
          safe_hold_confirmed: true,
          data_validation: "VALID",
        }],
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const client = new PvnaApiClient(config, fetchMock);

    await expect(client.runs()).resolves.toEqual([
      expect.objectContaining({
        run_id: "run_example",
        state: "completed",
        safeHoldConfirmed: true,
        dataValidation: "VALID",
        progress: { confirmed: 3, total: 3, percent: 100 },
      }),
    ]);
  });
});

describe("64-bit raw measurement values", () => {
  const validPoint: ConfirmedPoint = {
    index: 0,
    frequency_hz: 5_000_000,
    reference_i: "9223372036854775807",
    reference_q: "-9223372036854775808",
    antenna_i: "42",
    antenna_q: "-42",
  };

  it("preserves decimal strings beyond JavaScript safe integers", () => {
    expect(() => assertRawPointIntegersAreStrings(validPoint)).not.toThrow();
    expect(validPoint.reference_i).toBe("9223372036854775807");
  });

  it("rejects a non-decimal raw value at runtime", () => {
    expect(() =>
      assertRawPointIntegersAreStrings({
        ...validPoint,
        antenna_i: 42 as unknown as string,
      }),
    ).toThrow("十进制字符串");
  });

  it("maps backend point names, keeping A/R authoritative and deriving R/A", () => {
    const point = mapServicePoint({
      point_index: 7,
      requested_frequency_hz: "10000000",
      r_i_acc: "9223372036854775807",
      r_q_acc: "0",
      a_i_acc: "123",
      a_q_acc: "456",
      ratio_real: 0,
      ratio_imag: 2,
    });

    expect(point.index).toBe(7);
    expect(point.a_over_r).toMatchObject({ real: 0, imag: 2 });
    expect(point.r_over_a).toMatchObject({ real: 0, imag: -0.5 });
    expect(point.reference_i).toBe("9223372036854775807");
  });

  it("does not invent R/A when the backend A/R ratio is zero", () => {
    const point = mapServicePoint({
      point_index: 0,
      requested_frequency_hz: "5000000",
      r_i_acc: "1",
      r_q_acc: "0",
      a_i_acc: "0",
      a_q_acc: "0",
      ratio_real: 0,
      ratio_imag: 0,
    });

    expect(point.a_over_r?.magnitude_db).toBe(Number.NEGATIVE_INFINITY);
    expect(point.r_over_a).toBeUndefined();
  });

  it("prefers the explicit A/R aliases over the legacy ratio fields", () => {
    const point = mapServicePoint({
      point_index: 0,
      requested_frequency_hz: "5000000",
      r_i_acc: "10",
      r_q_acc: "0",
      a_i_acc: "5",
      a_q_acc: "0",
      ratio_real: 99,
      ratio_imag: 99,
      a_over_r_real: 0.5,
      a_over_r_imag: 0,
    });
    expect(point.a_over_r?.real).toBe(0.5);
    expect(point.r_over_a?.real).toBe(2);
  });

  it("marks both ratio directions unavailable when the backend ratio is null", () => {
    const point = mapServicePoint({
      point_index: 0,
      requested_frequency_hz: "5000000",
      r_i_acc: "0",
      r_q_acc: "0",
      a_i_acc: "12",
      a_q_acc: "34",
      ratio_real: null,
      ratio_imag: null,
    });

    expect(point.a_over_r).toBeUndefined();
    expect(point.r_over_a).toBeUndefined();
  });

  it("converts response u64 frequencies only after an exact safe-range check", () => {
    expect(u64StringToSafeNumber("100000000", "请求频率")).toBe(100_000_000);
    expect(() => u64StringToSafeNumber("9007199254740992", "请求频率"))
      .toThrow("超过 JavaScript 安全绘图范围");
  });

  it("keeps raw accumulators while mapping a calibrated S11 trace", () => {
    const point = mapTracePoint({
      point_index: 2,
      frequency_hz: "10000000",
      r_i_acc: "100",
      r_q_acc: "0",
      a_i_acc: "25",
      a_q_acc: "0",
      a_over_r_real: 0.25,
      a_over_r_imag: 0,
      r_over_a_real: 4,
      r_over_a_imag: 0,
      s11_real: 0.1,
      s11_imag: -0.2,
      magnitude_db: -13.0103,
      phase_deg: -63.4349,
    });
    expect(point.reference_i).toBe("100");
    expect(point.a_over_r?.real).toBe(0.25);
    expect(point.calibrated_s11).toMatchObject({ real: 0.1, imag: -0.2 });
  });
});
