import { describe, expect, it } from "vitest";
import { biosAnswerStale, biosReadState } from "./biosReadState";

describe("biosReadState", () => {
  it("is pending while nothing is held and no read has failed", () => {
    expect(biosReadState(false, false)).toBe("pending");
  });

  it("is failed when a read failed with nothing held", () => {
    expect(biosReadState(false, true)).toBe("failed");
  });

  it("is answered whenever an answer is held, a failed re-read included", () => {
    expect(biosReadState(true, false)).toBe("answered");
    expect(biosReadState(true, true)).toBe("answered");
  });
});

describe("biosAnswerStale", () => {
  it("is set only for a held answer whose latest read failed", () => {
    expect(biosAnswerStale(true, true)).toBe(true);
    expect(biosAnswerStale(true, false)).toBe(false);
    expect(biosAnswerStale(false, true)).toBe(false);
  });
});
