import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { warnInConsole, warnWithError } from "./warnWithError";

const logWarn = vi.hoisted(() => vi.fn());
vi.mock("../api/backend", () => ({ logWarn }));

let warn: MockInstance<typeof console.warn>;

beforeEach(() => {
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  logWarn.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("warnInConsole", () => {
  it("writes the line to the console with the prefix and the error itself", () => {
    const error = new Error("gone");

    warnInConsole("the store moved", error);

    expect(warn.mock.calls).toEqual([["[Tender] the store moved", error]]);
    expect(logWarn).not.toHaveBeenCalled();
  });
});

describe("warnWithError", () => {
  it("writes the console line with the error itself and the plugin log line with the error as text", () => {
    const error = new TypeError("gone");

    warnWithError("the store moved", error);

    expect(warn.mock.calls).toEqual([["[Tender] the store moved", error]]);
    expect(logWarn.mock.calls).toEqual([["the store moved: TypeError: gone"]]);
  });

  it("writes the line alone where no error is handed", () => {
    warnWithError("the store moved");

    expect(warn.mock.calls).toEqual([["[Tender] the store moved"]]);
    expect(logWarn.mock.calls).toEqual([["the store moved"]]);
  });

  it("does not throw where the plugin log throws, and the console line has gone out", () => {
    logWarn.mockImplementation(() => {
      throw new Error("loader gone");
    });
    const error = new Error("gone");

    expect(() => {
      warnWithError("the store moved", error);
    }).not.toThrow();
    expect(warn.mock.calls).toEqual([["[Tender] the store moved", error]]);
  });
});
