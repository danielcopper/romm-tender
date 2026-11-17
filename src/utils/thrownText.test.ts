import { describe, expect, it } from "vitest";

import { thrownText } from "./thrownText";

const unreadable = (): never => {
  throw new Error("unreadable");
};

describe("thrownText", () => {
  it.each<[string, () => unknown, string]>([
    ["an error", () => new RangeError("no fiber"), "RangeError: no fiber"],
    [
      "an error whose message getter throws",
      () => Object.defineProperty(new Error("x"), "message", { get: unreadable }),
      "unreadable",
    ],
    ["a proxy whose every read throws", () => new Proxy({}, { get: unreadable }), "unreadable"],
    ["an object whose name and message are not strings", () => ({ name: 7, message: null }), "number: object"],
    ["a function", () => function moved() {}, "moved: undefined"],
    ["null", () => null, "object null"],
    ["undefined", () => undefined, "undefined undefined"],
    ["a string", () => "store moved", "string store moved"],
  ])("tells %s", (_value, make, text) => {
    expect(thrownText(make())).toBe(text);
  });
});
