import { describe, expect, it, vi } from "vitest";
import type { GameLaunch } from "../types";
import { askEntryLaunches } from "./emulatorLaunches";

vi.mock("../api/backend", () => ({
  getEmulatorLaunches: vi.fn(),
  logError: vi.fn(),
}));

function launch(entry: string, state: GameLaunch["state"] = "starts"): GameLaunch {
  return {
    state,
    entry,
    system: "ps3",
    file: "Game.ps3",
    extension: ".ps3",
    starts_from: [],
    other_entries: [],
    install_in: state === "needs_installing" ? [] : null,
    reason: null,
    contents_unread: false,
    format_unestablished: false,
  };
}

/** A read held open until the test answers it. */
function held(): {
  read: (romId: number) => Promise<GameLaunch[]>;
  answer: (list: GameLaunch[]) => void;
  fail: () => void;
  asked: number[];
} {
  const asked: number[] = [];
  let answer!: (list: GameLaunch[]) => void;
  let fail!: () => void;
  const promise = new Promise<GameLaunch[]>((resolve, reject) => {
    answer = resolve;
    fail = () => reject(new Error("socket closed"));
  });
  return {
    read: (romId) => {
      asked.push(romId);
      return promise;
    },
    answer: (list) => answer(list),
    fail: () => fail(),
    asked,
  };
}

async function settle(): Promise<void> {
  for (let index = 0; index < 4; index++) await Promise.resolve();
}

describe("askEntryLaunches (#2195 D22, D31)", () => {
  it("asks for the ROM it was opened for, and answers nothing until the answer lands", async () => {
    const read = held();
    const launches = askEntryLaunches(42, () => true, read.read);

    expect(read.asked).toEqual([42]);
    expect(launches.forLabel("RPCS3 Directory (Standalone)")).toBeUndefined();

    read.answer([launch("RPCS3 Directory (Standalone)"), launch("RPCS3 Shortcut (Standalone)", "not_this_emulator")]);
    await settle();

    expect(launches.forLabel("RPCS3 Directory (Standalone)")?.state).toBe("starts");
    expect(launches.forLabel("RPCS3 Shortcut (Standalone)")?.state).toBe("not_this_emulator");
    expect(launches.forLabel("RPCS3 ISO (Standalone)")).toBeNull();
  });

  it("tells every listener when the answer lands, and none that stopped listening", async () => {
    const read = held();
    const launches = askEntryLaunches(42, () => true, read.read);
    const listening = vi.fn();
    const stopped = vi.fn();
    launches.subscribe(listening);
    launches.subscribe(stopped)();

    read.answer([launch("RPCS3 Directory (Standalone)")]);
    await settle();

    expect(listening).toHaveBeenCalledTimes(1);
    expect(stopped).not.toHaveBeenCalled();
  });

  it("drops an answer that lands after the page moved to another version", async () => {
    const read = held();
    let shown = 42;
    const launches = askEntryLaunches(42, () => shown === 42, read.read);
    const listening = vi.fn();
    launches.subscribe(listening);

    shown = 43;
    read.answer([launch("RPCS3 Directory (Standalone)")]);
    await settle();

    expect(launches.forLabel("RPCS3 Directory (Standalone)")).toBeUndefined();
    expect(listening).not.toHaveBeenCalled();
  });

  it("ends every wait without a verdict where the read fails", async () => {
    const read = held();
    const launches = askEntryLaunches(42, () => true, read.read);

    read.fail();
    await settle();

    expect(launches.forLabel("RPCS3 Directory (Standalone)")).toBeNull();
  });
});
