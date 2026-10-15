import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  formatBytes,
  formatLastPlayed,
  formatPlaytime,
  formatReleaseDate,
  formatTimestamp,
  formatTimestampWithYear,
  formatTimeAgo,
  formatUninstallStatus,
  resolveLastPlayed,
} from "./formatters";

describe("formatBytes", () => {
  it("returns empty string for null", () => {
    expect(formatBytes(null)).toBe("");
  });

  it("formats values under 1 KB as 'N B'", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("formats values under 1 MB as 'N.N KB'", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(1024 * 1024 - 1)).toBe("1024.0 KB");
  });

  it("formats values under 1 GB as 'N.N MB'", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("formats values 1 GB and up as 'N.NN GB'", () => {
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.00 GB");
    expect(formatBytes(2.5 * 1024 * 1024 * 1024)).toBe("2.50 GB");
  });
});

describe("formatTimestamp", () => {
  it("returns 'unknown' for null", () => {
    expect(formatTimestamp(null)).toBe("unknown");
  });

  it("formats a valid ISO timestamp as a locale string", () => {
    const out = formatTimestamp("2025-06-15T12:34:56Z");
    expect(out).toContain("Jun");
    expect(out).not.toBe("unknown");
  });

  it("returns the original string when Date construction succeeds but produces Invalid Date", () => {
    // toLocaleString on an Invalid Date returns "Invalid Date" — no throw, no
    // fallback. This documents that branch; if behavior changes we want to know.
    const out = formatTimestamp("not-a-date");
    expect(out).toBe("Invalid Date");
  });
});

describe("formatTimestampWithYear", () => {
  it("returns 'unknown' for null", () => {
    expect(formatTimestampWithYear(null)).toBe("unknown");
  });

  it("carries the year its sibling drops, and drops the seconds", () => {
    // The one caller shows two folders that can be a year apart and differ in
    // nothing else a reader can see; seconds tell them apart in neither case.
    const out = formatTimestampWithYear("2025-06-15T12:34:56Z");
    expect(out).toContain("2025");
    expect(out).toContain("Jun");
    expect(formatTimestamp("2025-06-15T12:34:56Z")).not.toContain("2025");
  });

  it("returns 'Invalid Date' for an unparseable string, like its sibling", () => {
    expect(formatTimestampWithYear("not-a-date")).toBe("Invalid Date");
  });
});

describe("formatTimeAgo", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null for an unparseable string", () => {
    expect(formatTimeAgo("nope")).toBeNull();
  });

  it("returns 'Just now' for timestamps less than a minute old", () => {
    expect(formatTimeAgo("2025-06-15T11:59:30Z")).toBe("Just now");
  });

  it("returns Xm ago for minutes", () => {
    expect(formatTimeAgo("2025-06-15T11:45:00Z")).toBe("15m ago");
  });

  it("returns Xh ago for hours", () => {
    expect(formatTimeAgo("2025-06-15T08:00:00Z")).toBe("4h ago");
  });

  it("returns Xd ago for days", () => {
    expect(formatTimeAgo("2025-06-12T12:00:00Z")).toBe("3d ago");
  });
});

const realToLocaleDateString = Date.prototype.toLocaleDateString;

/** Format dates as a Steam client in *locale* would: the code under test asks
 *  for the context's own language (`undefined`), which a test cannot set, and
 *  a call that names a language fails the test. */
function formatDatesIn(locale: string): void {
  vi.spyOn(Date.prototype, "toLocaleDateString").mockImplementation(function (
    this: Date,
    locales?: Intl.LocalesArgument,
    options?: Intl.DateTimeFormatOptions,
  ) {
    if (locales !== undefined) throw new Error(`asked for ${String(locales)}, not the client's own language`);
    return realToLocaleDateString.call(this, locale, options);
  });
}

const seconds = (date: Date): number => Math.floor(date.getTime() / 1000);

describe("formatLastPlayed", () => {
  // Local-time fixtures, so every label below holds in any time zone.
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2025, 5, 15, 12));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("returns 'Never' for 0 or negative", () => {
    expect(formatLastPlayed(0)).toBe("Never");
    expect(formatLastPlayed(-1)).toBe("Never");
  });

  it("returns 'Today' for today's timestamp", () => {
    expect(formatLastPlayed(seconds(new Date(2025, 5, 15, 10)))).toBe("Today");
  });

  it("returns 'Today' for a timestamp later than now (a clock behind the recording one)", () => {
    expect(formatLastPlayed(seconds(new Date(2025, 5, 15, 13)))).toBe("Today");
    expect(formatLastPlayed(seconds(new Date(2025, 5, 18, 12)))).toBe("Today");
  });

  it("returns 'Yesterday' for one day ago", () => {
    expect(formatLastPlayed(seconds(new Date(2025, 5, 14, 10)))).toBe("Yesterday");
  });

  it("returns 'N days ago' for under a week", () => {
    expect(formatLastPlayed(seconds(new Date(2025, 5, 12, 10)))).toBe("3 days ago");
  });

  it("names this year's older dates by day and short month in the client's language", () => {
    const tenthOfApril = seconds(new Date(2025, 3, 10, 12));
    formatDatesIn("en-GB");
    expect(formatLastPlayed(tenthOfApril)).toBe("10 Apr");
    formatDatesIn("en-US");
    expect(formatLastPlayed(tenthOfApril)).toBe("Apr 10");
    formatDatesIn("de-DE");
    expect(formatLastPlayed(tenthOfApril)).toBe("10. Apr.");
  });

  it("writes a single-digit day without a leading zero", () => {
    formatDatesIn("en-GB");
    expect(formatLastPlayed(seconds(new Date(2025, 3, 5, 12)))).toBe("5 Apr");
    expect(formatLastPlayed(seconds(new Date(2024, 7, 5, 12)))).toBe("5 Aug 2024");
  });

  it("adds the year for another year's dates", () => {
    const twentiethOfAugust = seconds(new Date(2024, 7, 20, 12));
    formatDatesIn("en-GB");
    expect(formatLastPlayed(twentiethOfAugust)).toBe("20 Aug 2024");
    formatDatesIn("en-US");
    expect(formatLastPlayed(twentiethOfAugust)).toBe("Aug 20, 2024");
  });
});

describe("formatPlaytime", () => {
  it("returns 'None' for 0 or negative", () => {
    expect(formatPlaytime(0)).toBe("None");
    expect(formatPlaytime(-5)).toBe("None");
  });

  it("returns 'N Min' under an hour", () => {
    expect(formatPlaytime(42)).toBe("42 Min");
  });

  it("returns '1 Hour' singular at exactly 60 minutes", () => {
    expect(formatPlaytime(60)).toBe("1 Hour");
  });

  it("returns 'N Hours' plural for whole multiples", () => {
    expect(formatPlaytime(120)).toBe("2 Hours");
  });

  it("returns 'Nh Mm' for non-whole hours", () => {
    expect(formatPlaytime(125)).toBe("2h 5m");
  });
});

describe("formatUninstallStatus", () => {
  it("omits the suffix when there are no errors", () => {
    expect(formatUninstallStatus(5, 0)).toBe("Removed 5 ROMs");
  });

  it("appends '(N errors)' when errors are present", () => {
    expect(formatUninstallStatus(5, 2)).toBe("Removed 5 ROMs (2 errors)");
  });

  it("handles the all-failed case (zero removed, errors present)", () => {
    expect(formatUninstallStatus(0, 3)).toBe("Removed 0 ROMs (3 errors)");
  });

  it("handles the empty edge case (zero removed, zero errors)", () => {
    expect(formatUninstallStatus(0, 0)).toBe("Removed 0 ROMs");
  });

  it("renders a single error without pluralizing (suffix stays '(1 errors)')", () => {
    // The label intentionally keeps the simple plural form; documenting that
    // here so a future change is a conscious decision, not a drift.
    expect(formatUninstallStatus(1, 1)).toBe("Removed 1 ROMs (1 errors)");
  });
});

describe("resolveLastPlayed", () => {
  it("resolves from restoredIso when present and valid", () => {
    const iso = new Date("2024-05-10T15:30:00Z").toISOString();
    const result = resolveLastPlayed(iso, 0);
    expect(result).not.toBe("Never");
  });

  it("falls back to steamUnixSeconds when restoredIso is null", () => {
    expect(resolveLastPlayed(null, 0)).toBe("Never");
  });

  it("falls back to steamUnixSeconds when restoredIso is invalid", () => {
    expect(resolveLastPlayed("invalid-date-string", 0)).toBe("Never");
  });

  it("reads a naive ISO string as UTC, not local time", () => {
    // In New York, 02:00 UTC on the 14th is 22:00 local on the 13th, and 03:00
    // UTC on the 15th is 23:00 on the 14th: read as UTC the string is 25 hours
    // old ("Yesterday"), read as local time 21 ("Today").
    vi.stubEnv("TZ", "America/New_York");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T03:00:00Z"));
    try {
      expect(resolveLastPlayed("2025-06-14T02:00:00", 0)).toBe("Yesterday");
      expect(resolveLastPlayed("2025-06-14T02:00:00Z", 0)).toBe("Yesterday");
      expect(resolveLastPlayed("2025-06-14T02:00:00+00:00", 0)).toBe("Yesterday");
    } finally {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    }
  });
});

describe("formatReleaseDate", () => {
  it("returns null for null, 0, or negative timestamp", () => {
    expect(formatReleaseDate(null)).toBeNull();
    expect(formatReleaseDate(0)).toBeNull();
    expect(formatReleaseDate(-100)).toBeNull();
  });

  it("formats day, short month and year in the client's language", () => {
    const releaseDay = seconds(new Date(2004, 3, 22, 12));
    formatDatesIn("en-GB");
    expect(formatReleaseDate(releaseDay)).toBe("22 Apr 2004");
    formatDatesIn("en-US");
    expect(formatReleaseDate(releaseDay)).toBe("Apr 22, 2004");
    vi.restoreAllMocks();
  });

  it("writes a single-digit day without a leading zero", () => {
    formatDatesIn("en-GB");
    expect(formatReleaseDate(seconds(new Date(2004, 3, 5, 12)))).toBe("5 Apr 2004");
    vi.restoreAllMocks();
  });
});
