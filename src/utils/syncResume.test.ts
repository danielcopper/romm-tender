import { describe, it, expect } from "vitest";
import { syncResumeState } from "./syncResume";
import type { SyncStats } from "../types";

/** A resume situation: shortcuts on disk, and games the next run can pass over.
 *  Recorded launch commands are the ordinary carrier — every stopped run has
 *  committed chunks — so they, not a completion stamp, are the default here.
 *  Without either kind the offer is off whatever the run history says (#1789). */
function resumeStats(overrides: Partial<SyncStats> = {}): SyncStats {
  return {
    last_sync: null,
    platforms: 1,
    collections: 0,
    roms: 42,
    total_shortcuts: 42,
    last_attempt: { finished_at: "2026-06-01T17:48:00", status: "interrupted" },
    resumable_games: 30,
    has_completion_stamp: false,
    ...overrides,
  };
}

describe("syncResumeState", () => {
  // Every non-completed, non-errored terminal status resumes: interrupted,
  // cancelled, and the #1383 session-budget pause.
  it.each(["interrupted", "cancelled", "paused"] as const)(
    "offers a resume when the newest attempt was %s and progress survives",
    (status) => {
      const state = syncResumeState(resumeStats({ last_attempt: { finished_at: "2026-06-01T17:48:00", status } }));
      expect(state.canResume).toBe(true);
      expect(state.label).toBe("Resume Sync");
    },
  );

  it("falls back to a fresh sync once a force-clear left NEITHER kind of progress", () => {
    // The #1789 regression. Force Full Sync deletes the completion stamps and
    // the recorded launch commands but not the shortcuts, and it deliberately
    // preserves the run history (#1318) — so the incomplete attempt and the
    // bound shortcuts both survive while everything the next run could skip is
    // gone. Whichever button is pressed the next run is a full one.
    const state = syncResumeState(resumeStats({ resumable_games: 0, has_completion_stamp: false }));
    expect(state.canResume).toBe(false);
    expect(state.label).toBe("Sync Library");
    expect(state.scopeText).toBeNull();
  });

  it("resumes on recorded games alone — a cancel inside the first platform", () => {
    // No unit reached its final chunk, so no stamp exists; the committed chunks
    // still wrote shortcuts and recorded their launch commands, and the next run
    // passes over every one of them. Keying the offer on stamps alone called
    // this a fresh start, which it is not.
    const state = syncResumeState(resumeStats({ resumable_games: 12, has_completion_stamp: false }));
    expect(state.canResume).toBe(true);
    expect(state.scopeText).toBe("12 games already synced — a resume continues from there.");
  });

  it("resumes on a completion stamp alone, and says nothing under it", () => {
    // The mirror case: rows predating migration 015 carry no recorded command
    // while their platform's stamp survives, so an upgraded install can hold
    // stamps and zero recorded games — those platforms still skip wholesale.
    // With no number to state, the line is omitted rather than reading "0 games".
    const state = syncResumeState(resumeStats({ resumable_games: 0, has_completion_stamp: true }));
    expect(state.canResume).toBe(true);
    expect(state.scopeText).toBeNull();
  });

  it("counts the resume line in games, singular at one", () => {
    expect(syncResumeState(resumeStats({ resumable_games: 1 })).scopeText).toBe(
      "1 game already synced — a resume continues from there.",
    );
  });

  it("refuses when an interrupted attempt left ZERO bound shortcuts (all removed — nothing to resume)", () => {
    // After an interrupted run the user removed every shortcut (DangerZone
    // "remove all"), so roms is 0 and the next run is a full fresh import. A
    // surviving stamp alongside zero shortcuts is a REAL state, not a contrived
    // fixture: removal invalidates only the platform slugs its removed rows
    // name, and prune deletes rows without touching platform stamps, so a
    // platform RomM dropped keeps a stamp no remaining row can point the removal
    // at. `roms > 0` is the only thing that catches it.
    const state = syncResumeState(resumeStats({ roms: 0, resumable_games: 30, has_completion_stamp: true }));
    expect(state.canResume).toBe(false);
    expect(state.label).toBe("Sync Library");
  });

  it("refuses when the newest attempt errored (resume isn't the model)", () => {
    // An errored run often failed before applying anything (config error, etc.),
    // so "resume" would mislead — the fresh label stays. Shortcuts and both
    // kinds of progress are present, so only the status can withhold the offer.
    const state = syncResumeState(
      resumeStats({ last_attempt: { finished_at: "2026-06-01T17:48:00", status: "errored" } }),
    );
    expect(state.canResume).toBe(false);
    expect(state.label).toBe("Sync Library");
  });

  it("refuses when there is no last attempt (clean state)", () => {
    // A fully-synced library carries recorded launch commands for every game, so
    // the absent last_attempt is what has to withhold the offer.
    const state = syncResumeState(resumeStats({ last_attempt: null }));
    expect(state.canResume).toBe(false);
    expect(state.label).toBe("Sync Library");
  });

  it("refuses before the stats have been read at all", () => {
    const state = syncResumeState(null);
    expect(state.canResume).toBe(false);
    expect(state.label).toBe("Sync Library");
    expect(state.scopeText).toBeNull();
  });
});
