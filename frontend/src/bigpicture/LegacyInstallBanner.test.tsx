import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import { useEffect, FC } from "react";
import {
  LegacyInstallBanner,
  LegacyInstallNotice,
  LEGACY_INSTALL_TITLE,
  LEGACY_REMOVABLE_TITLE,
  legacyInstallStatement,
} from "./LegacyInstallBanner";
import { dismissLegacyInstallNotice, getLegacyInstallNotice, getSyncStats } from "../api/backend";
import { setLegacyInstallState, fetchLegacyInstallState } from "../utils/legacyInstallStore";
import { resetLauncherStoreForTests, setLauncherRelocated } from "../utils/launcherStore";
import { refreshSyncStats, resetSyncStatsStoreForTests } from "../utils/syncStatsStore";
import type { SyncStats } from "../types";

const STRANDED_SENTENCE = "Your library and settings are still in that older install too";

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

  it("points at Decky's plugin list without naming a menu path", () => {
    // The path is unverified against Decky's own UI, so the sentence has to be
    // true whatever that menu turns out to be called.
    expect(legacyInstallStatement(true, false).body).toContain("wherever Decky lists your installed plugins");
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

  it("offers no action while the shortcuts still launch through the older install", () => {
    const { container } = render(<LegacyInstallBanner dataStranded={true} relocated={false} onDismiss={vi.fn()} />);
    expect(container.querySelector("button")).toBeNull();
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
    vi.mocked(dismissLegacyInstallNotice).mockReset().mockResolvedValue({ success: true });
    setLegacyInstallState({ pending: false, legacyDataPresent: false, dismissed: false });
    resetLauncherStoreForTests();
    resetSyncStatsStoreForTests();
  });

  // Both stores outlive the component and this hook runs before RTL's cleanup,
  // so the notify reaches a still-mounted subscriber — act, or React reports the
  // update as unwrapped.
  afterEach(() => {
    act(() => {
      setLegacyInstallState({ pending: false, legacyDataPresent: false, dismissed: false });
      resetLauncherStoreForTests();
    });
    resetSyncStatsStoreForTests();
  });

  it("shows the banner when the callable reports pending:true", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({
      pending: true,
      legacy_data_present: false,
      dismissed: false,
    });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("adds the stranded-data sentence when the older install has data and this one shows nothing", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true, dismissed: false });
    const { container } = render(<LegacyNoticeHost romCount={0} />);
    await flushAsync();
    expect(container.textContent).toContain(STRANDED_SENTENCE);
  });

  it("drops the stranded-data sentence once this install has a library of its own", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true, dismissed: false });
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();
    // The launcher warning is the half that must survive: it is what stops the
    // irreversible removal, and no library reading may take it down.
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("withholds the stranded-data sentence while no ROM count has landed yet", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: true, dismissed: false });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    // A null stats read is not knowledge that this install is empty, so the
    // sentence waits. The launcher warning does not.
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).not.toContain(STRANDED_SENTENCE);
  });

  it("renders nothing when the callable reports pending:false", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({
      pending: false,
      legacy_data_present: false,
      dismissed: false,
    });
    const { container } = render(<LegacyNoticeHost />);
    await flushAsync();
    expect(container.textContent).toBe("");
  });

  it("turns into the removal statement once the shortcuts have been relocated", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({
      pending: true,
      legacy_data_present: false,
      dismissed: false,
    });
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);

    act(() => {
      setLauncherRelocated(true);
    });

    expect(container.textContent).toContain(LEGACY_REMOVABLE_TITLE);
    expect(container.textContent).not.toContain(LEGACY_INSTALL_TITLE);
  });

  it("persists the dismissal before taking the card down", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({
      pending: true,
      legacy_data_present: false,
      dismissed: false,
    });
    setLauncherRelocated(true);
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();

    fireEvent.click(container.querySelector("button")!);
    await flushAsync();

    expect(vi.mocked(dismissLegacyInstallNotice)).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("");
  });

  it("stays down on the next start, because the backend answered dismissed", async () => {
    // The whole point of persisting it: a card that came back at every Steam
    // start is the standing warning the Dismiss exists to prevent.
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false, dismissed: true });
    setLauncherRelocated(true);
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();

    expect(container.textContent).toBe("");
  });

  it("a dismissal does not silence the statement the user is not free to ignore", async () => {
    // Answered "I am keeping it" on a previous start, and this start's
    // shortcuts point into that install again — a restore, a downgraded build.
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({ pending: true, legacy_data_present: false, dismissed: true });
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();

    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.querySelector("button")).toBeNull();
  });

  it("keeps the card up when the dismissal could not be persisted", async () => {
    vi.mocked(getLegacyInstallNotice).mockResolvedValue({
      pending: true,
      legacy_data_present: false,
      dismissed: false,
    });
    vi.mocked(dismissLegacyInstallNotice).mockRejectedValue(new Error("settings unwritable"));
    setLauncherRelocated(true);
    const { container } = render(<LegacyNoticeHost romCount={42} />);
    await flushAsync();

    fireEvent.click(container.querySelector("button")!);
    await flushAsync();

    // Hiding it here would bring it back at the next start, which reads as the
    // plugin forgetting what it was told.
    expect(container.textContent).toContain(LEGACY_REMOVABLE_TITLE);
  });
});
