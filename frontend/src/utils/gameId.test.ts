import { describe, it, expect } from "vitest";
import { appIdFromGameId } from "./gameId";

/** A constructed shortcut: `(APP_ID << 32) | 0x02000000` is `GAME_ID`. */
const APP_ID = 3000000001;
const GAME_ID = "12884901892328521728";

describe("appIdFromGameId", () => {
  it.each([
    ["a shortcut's game ID, as Steam reports its start", GAME_ID, APP_ID],
    ["a small decimal, which is the appId itself", "1234", 1234],
    ["the largest 32-bit value", String(0xffffffff), 0xffffffff],
    ["the largest 64-bit value with the shortcut mark", ((0xffffffffn << 32n) | 0x02000000n).toString(), 0xffffffff],
  ])("names an appId for %s", (_label, gameId, appId) => {
    expect(appIdFromGameId(gameId)).toBe(appId);
  });

  it.each([
    ["the first value past 32 bits, whose low bits are no shortcut mark", String(0x100000000)],
    ["a large value with another game-ID type in its low bits", ((BigInt(APP_ID) << 32n) | 0x01000000n).toString()],
    ["a large value whose low bits are the shortcut mark and more", ((BigInt(APP_ID) << 32n) | 0x02000001n).toString()],
    [
      "a value past 64 bits whose low bits are exactly the shortcut mark",
      ((1n << 64n) | (BigInt(APP_ID) << 32n) | 0x02000000n).toString(),
    ],
    ["an empty string", ""],
    ["a negative number", "-1"],
    ["trailing garbage", "12abc"],
    ["a leading space", " 1"],
    ["a 21-digit value, beyond 64 bits", "100000000000000000000"],
  ])("answers null for %s", (_label, gameId) => {
    expect(appIdFromGameId(gameId)).toBeNull();
  });

  it("the constructed pair is the shortcut layout", () => {
    expect(GAME_ID).toBe(((BigInt(APP_ID) << 32n) | 0x02000000n).toString());
  });
});
