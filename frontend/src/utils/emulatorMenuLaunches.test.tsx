import { describe, it, expect, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";

// Rendered rather than inspected as an element tree: each entry's line is a
// component that waits for its answer.
vi.mock("@decky/ui", () => ({
  Menu: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  MenuItem: ({ children, onClick, disabled }: { children?: ReactNode; onClick?: () => void; disabled?: boolean }) => (
    <button type="button" disabled={disabled} onClick={onClick}>
      {children}
    </button>
  ),
  MenuSeparator: () => <hr />,
  Spinner: () => <span data-testid="spinner" />,
}));

vi.mock("../api/backend", () => ({
  getEmulatorLaunches: vi.fn(),
  logError: vi.fn(),
}));

import { buildEmulatorMenu, type EmulatorMenuConfig } from "./emulatorMenu";
import { askEntryLaunches } from "./emulatorLaunches";
import { standaloneEmu } from "../test-utils/coreFixtures";
import type { GameLaunch } from "../types";

const SHORTCUT = "RPCS3 Shortcut (Standalone)";
const DIRECTORY = "RPCS3 Directory (Standalone)";

function launch(entry: string, overrides: Partial<GameLaunch> = {}): GameLaunch {
  return {
    state: "starts",
    entry,
    system: "ps3",
    file: "Game.ps3",
    extension: ".ps3",
    starts_from: [],
    other_entries: [],
    install_in: null,
    reason: null,
    contents_unread: false,
    format_unestablished: false,
    ...overrides,
  };
}

function config(overrides: Partial<EmulatorMenuConfig> = {}): EmulatorMenuConfig {
  return {
    emulators: [standaloneEmu(SHORTCUT, true), standaloneEmu(DIRECTORY)],
    emulatorDataAvailable: true,
    emulatorDataReason: null,
    emulatorSource: { kind: "retrodeck", starts_games: true },
    emulatorPlatformSystem: null,
    activeLabel: null,
    activeColor: "#1a9fff",
    platformCoreLabel: null,
    onPick: vi.fn(),
    ...overrides,
  };
}

function heldRead(): { read: () => Promise<GameLaunch[]>; answer: (list: GameLaunch[]) => Promise<void> } {
  let resolve!: (list: GameLaunch[]) => void;
  const promise = new Promise<GameLaunch[]>((r) => (resolve = r));
  return {
    read: () => promise,
    answer: async (list) => {
      await act(async () => {
        resolve(list);
        await promise;
      });
    },
  };
}

function row(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) => b.textContent.startsWith(label));
  if (found === undefined) throw new Error(`no row ${label}`);
  return found;
}

describe("the emulator menu's answer per entry (#2195 D22, D31)", () => {
  it("opens at once, every entry choosable and waiting on a spinner", () => {
    const read = heldRead();
    const onPick = vi.fn();
    const { container } = render(
      <>{buildEmulatorMenu(config({ onPick, launches: askEntryLaunches(42, () => true, read.read) }))}</>,
    );

    expect(row(container, SHORTCUT).querySelector('[data-testid="spinner"]')).not.toBeNull();
    expect(row(container, DIRECTORY).querySelector('[data-testid="spinner"]')).not.toBeNull();
    row(container, DIRECTORY).click();
    expect(onPick).toHaveBeenCalledWith(DIRECTORY);
  });

  it("shows each entry's answer as it lands, why it cannot start where it cannot", async () => {
    const read = heldRead();
    const { container } = render(
      <>{buildEmulatorMenu(config({ launches: askEntryLaunches(42, () => true, read.read) }))}</>,
    );

    await read.answer([
      launch(SHORTCUT, { state: "not_this_emulator", other_entries: [DIRECTORY] }),
      launch(DIRECTORY, { format_unestablished: true }),
    ]);

    expect(container.querySelector('[data-testid="spinner"]')).toBeNull();
    expect(row(container, SHORTCUT).textContent).toContain("cannot start it: does not start .ps3");
    expect(row(container, DIRECTORY).textContent).toContain("whether it reads .ps3 is not established");
  });

  it("keeps waiting where the answer is about a version the page no longer shows", async () => {
    const read = heldRead();
    let shown = 42;
    const { container } = render(
      <>{buildEmulatorMenu(config({ launches: askEntryLaunches(42, () => shown === 42, read.read) }))}</>,
    );

    shown = 43;
    await read.answer([launch(SHORTCUT, { state: "not_this_emulator" })]);

    expect(row(container, SHORTCUT).textContent).not.toContain("cannot start it");
  });

  it("shows no answer line at all on a menu that asks none", () => {
    const { container } = render(<>{buildEmulatorMenu(config())}</>);

    expect(container.querySelector('[data-testid="spinner"]')).toBeNull();
    expect(row(container, SHORTCUT).textContent).toBe(`${SHORTCUT} (default) ✓`);
  });
});
