import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, act } from "@testing-library/react";
import { useEffect, FC } from "react";
import {
  LegacyInstallBanner,
  LegacyInstallNotice,
  LEGACY_INSTALL_TITLE,
  legacyInstallMessage,
} from "./LegacyInstallBanner";
import { getLegacyInstallNotice, getSyncStats } from "../api/backend";
import { setLegacyInstallState, fetchLegacyInstallState } from "../utils/legacyInstallStore";
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

describe("legacyInstallMessage", () => {
  it("warns against removing the older plugin", () => {
    expect(legacyInstallMessage(false)).toContain("removing it from Decky stops your games from starting");
    expect(legacyInstallMessage(false)).toContain("Leave it in place");
  });

  it("omits the stranded-data sentence when the data is not stranded", () => {
    expect(legacyInstallMessage(false)).not.toContain(STRANDED_SENTENCE);
  });

  it("adds the stranded-data sentence when the data is stranded", () => {
    expect(legacyInstallMessage(true)).toContain(STRANDED_SENTENCE);
    expect(legacyInstallMessage(true)).toContain("this version starts empty until the move happens");
  });
});

describe("LegacyInstallBanner component", () => {
  it("renders the headline and the warning body", () => {
    const { container } = render(<LegacyInstallBanner dataStranded={false} />);
    expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
    expect(container.textContent).toContain(legacyInstallMessage(false));
  });

  it("renders the stranded-data sentence only when the data is stranded", () => {
    const { container: plain } = render(<LegacyInstallBanner dataStranded={false} />);
    expect(plain.textContent).not.toContain(STRANDED_SENTENCE);

    const { container: stranded } = render(<LegacyInstallBanner dataStranded={true} />);
    expect(stranded.textContent).toContain(STRANDED_SENTENCE);
  });

  it("offers no action — the condition ends with the migration, not with a click", () => {
    const { container } = render(<LegacyInstallBanner dataStranded={true} />);
    expect(container.querySelector("button")).toBeNull();
  });
});

describe("LegacyInstallNotice store-driven visibility", () => {
  beforeEach(() => {
    vi.mocked(getLegacyInstallNotice).mockReset();
    vi.mocked(getSyncStats).mockReset();
    setLegacyInstallState({ pending: false, legacyDataPresent: false });
    resetSyncStatsStoreForTests();
  });

  // Both stores outlive the component and this hook runs before RTL's cleanup,
  // so the notify reaches a still-mounted subscriber — act, or React reports the
  // update as unwrapped.
  afterEach(() => {
    act(() => {
      setLegacyInstallState({ pending: false, legacyDataPresent: false });
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
});
