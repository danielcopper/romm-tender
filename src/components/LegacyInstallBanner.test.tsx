import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import { useEffect, FC } from "react";
import {
  LegacyInstallBanner,
  LegacyInstallNotice,
  LEGACY_DATA_TITLE,
  LEGACY_INSTALL_TITLE,
  LEGACY_REMOVABLE_TITLE,
  legacyInstallStatement,
} from "./LegacyInstallBanner";
import { getLegacyInstallNotice, getSyncStats } from "../api/backend";
import { setLegacyInstallState, fetchLegacyInstallState } from "../utils/legacyInstallStore";
import { getLauncherState, resetLauncherStoreForTests, setLauncherRelocated } from "../utils/launcherStore";
import { refreshSyncStats, resetSyncStatsStoreForTests } from "../utils/syncStatsStore";
import type { SyncStats } from "../types";

const STRANDED_SENTENCE = "still in that older install";

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

const defaultStats = (roms: number): SyncStats => ({
  roms,
  platforms: 0,
  collections: 0,
  total_shortcuts: 0,
  last_sync: null,
});

/**
 * The real notice behind the plugin-load fetch and the panel's own stats read,
 * so the callable → store → render pipeline is exercised end-to-end without
 * mounting all of MainPage. `romCount` drives the ROM count the notice joins
 * `legacyDataPresent` with; `undefined` leaves the stats store empty, which is
 * the first-paint state.
 */
const LegacyNoticeHost: FC<{ romCount?: number }> = ({ romCount }) => {
  useEffect(() => {
    fetchLegacyInstallState().catch(() => {});
    if (romCount !== undefined) {
      vi.mocked(getSyncStats).mockResolvedValue(defaultStats(romCount));
      refreshSyncStats().catch(() => {});
    }
  }, [romCount]);
  return <LegacyInstallNotice />;
};

describe("legacyInstallStatement", () => {
  it("warns against removing the older plugin while the shortcuts still point into it", () => {
    const statement = legacyInstallStatement(false, false);
    expect(statement.title).toBe(LEGACY_INSTALL_TITLE);
    expect(statement.body).toContain("removing it from Decky stops your games from starting");
    expect(statement.body).toContain("Leave it in place");
    expect(statement.dismissible).toBe(false);
  });

  it("omits the stranded-data sentence when the data is not stranded", () => {
    expect(legacyInstallStatement(false, false).body).not.toContain(STRANDED_SENTENCE);
  });

  it("adds the stranded-data sentence to the launcher warning when the data is stranded", () => {
    const statement = legacyInstallStatement(false, true);
    expect(statement.title).toBe(LEGACY_INSTALL_TITLE);
    expect(statement.body).toContain(STRANDED_SENTENCE);
    expect(statement.body).toContain("this version starts empty until the move happens");
    expect(statement.dismissible).toBe(false);
  });

  it("says the older install can be removed once nothing points into it", () => {
    const statement = legacyInstallStatement(true, false);
    expect(statement.title).toBe(LEGACY_REMOVABLE_TITLE);
    expect(statement.body).toContain("no longer launch through it");
    expect(statement.dismissible).toBe(true);
  });

  it("tells the reader where to remove it", () => {
    expect(legacyInstallStatement(true, false).body).toContain("Decky's settings, under Plugins");
  });

  it("never offers removal while the library is still in the older install", () => {
    // The launcher argument has gone; the data argument has not, and it is the
    // one where removing costs something no copy exists of.
    const statement = legacyInstallStatement(true, true);
    expect(statement.title).toBe(LEGACY_DATA_TITLE);
    expect(statement.body).toContain(STRANDED_SENTENCE);
    expect(statement.body).toContain("Leave it in place");
    expect(statement.body).not.toContain("you can remove it");
    expect(statement.dismissible).toBe(false);
  });
});

describe("LegacyInstallBanner component", () => {
  it("renders the headline and the warning body", () => {
    const { container } = render(<LegacyInstallBanner dataStranded={false} relocated={false} />);
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).toContain(legacyInstallStatement(false, false).body);
  });

  it("renders the stranded-data sentence only when the data is stranded", () => {
    const { container: plain } = render(<LegacyInstallBanner dataStranded={false} relocated={false} />);
    expect(plain.textContent).not.toContain(STRANDED_SENTENCE);

    const { container: stranded } = render(<LegacyInstallBanner dataStranded={true} relocated={false} />);
    expect(stranded.textContent).toContain(STRANDED_SENTENCE);
  });

  it("offers no action while the card describes something the user would lose by acting", () => {
    const { container: launcher } = render(<LegacyInstallBanner dataStranded={true} relocated={false} />);
    expect(launcher.querySelector("button")).toBeNull();

    const { container: stranded } = render(
      <LegacyInstallBanner dataStranded={true} relocated={true} onDismiss={vi.fn()} />,
    );
    expect(stranded.querySelector("button")).toBeNull();
  });

  it("offers Dismiss on the removable statement, and calls it", () => {
    const onDismiss = vi.fn();
    const { container } = render(<LegacyInstallBanner dataStranded={false} relocated={true} onDismiss={onDismiss} />);

    const button = container.querySelector("button");
    expect(button).not.toBeNull();
    expect(container.textContent).toContain("Dismiss");

    fireEvent.click(button!);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("LegacyInstallNotice store-driven visibility", () => {
  beforeEach(() => {
    vi.mocked(getLegacyInstallNotice).mockReset();
    vi.mocked(getSyncStats).mockReset();
    setLegacyInstallState({ pending: false, legacyDataPresent: false });
    resetLauncherStoreForTests();
    resetSyncStatsStoreForTests();
  });

  // Both stores outlive the component and this hook runs before RTL's cleanup,
  // so the notify reaches a still-mounted subscriber — act, or React reports the
  // update as unwrapped.
  afterEach(() => {
    act(() => {
      setLegacyInstallState({ pending: false, legacyDataPresent: false });
      resetLauncherStoreForTests();
    });
    resetSyncStatsStoreForTests();
  });

  it("shows the banner when the callable reports pending:true", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("adds the stranded-data sentence when the older install has data and this one shows nothing", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true });
    const { container } = render(<LegacyNoticeHost romCount={0} />);
    await flushAsync();
    expect(container.textContent).toContain(STRANDED_SENTENCE);
  });

  it("drops the stranded-data sentence once this install has a library of its own", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true });
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();
    // The launcher warning is the half that must survive: it is what stops the
    // irreversible removal, and no library reading may take it down.
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("withholds the stranded-data sentence while no ROM count has landed yet", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    // A null stats read is not knowledge that this install is empty, so the
    // sentence waits. The launcher warning does not.
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("renders nothing when the callable reports pending:false", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: false, legacy_data_present: false });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    expect(container.textContent).toBe("");
  });

  it("turns into the removal statement once the shortcuts have been relocated", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false });
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);

    act(() => {
      setLauncherRelocated(true);
    });

    expect(container.textContent).toContain(LEGACY_REMOVABLE_TITLE);
    expect(container.textContent).not.toContain(LEGACY_INSTALL_TITLE);
  });

  it("takes the removal statement down for good once it is dismissed", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false });
    setLauncherRelocated(true);
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();

    act(() => {
      fireEvent.click(container.querySelector("button")!);
    });

    expect(container.textContent).toBe("");
    expect(getLauncherState().removalDismissed).toBe(true);
  });

  it("a dismissal does not silence a statement the user is not free to ignore", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false });
    setLauncherRelocated(true);
    const { container } = render(<LegacyNoticeHost romCount={0} />);
    await flushAsync();
    act(() => {
      fireEvent.click(container.querySelector("button")!);
    });
    expect(container.textContent).toBe("");

    // The older install turns out to hold the library after all. Dismissing an
    // offer to remove it was never an answer to that.
    act(() => {
      setLegacyInstallState({ pending: true, legacyDataPresent: true });
    });

    expect(container.textContent).toContain(LEGACY_DATA_TITLE);
    expect(container.querySelector("button")).toBeNull();
  });
});
