import { describe, it, expect } from "vitest";
import { resolveSaveSyncLabel } from "./playSection";
import { saveSyncBadge } from "./saveSyncBadge";
import type { SaveFileStatus, SaveSetupInfo, SaveStatus, SaveSyncAvailability, SyncConflict } from "../types";

const INPUT: Parameters<typeof saveSyncBadge>[0] = {
  enabled: true,
  offline: false,
  installed: true,
  status: null,
  label: "",
  saveStatus: null,
  setupInfo: null,
  saveSyncAnswer: null,
};

function status(overrides: Partial<SaveStatus> = {}): SaveStatus {
  return { rom_id: 1, files: [], last_sync_check_at: null, ...overrides } as SaveStatus;
}

const LOCAL_FILE = {
  local_path: "/saves/game.srm",
  local_size: 8,
  local_mtime: "2026-11-01T00:00:00Z",
} as SaveFileStatus;

const WITH_LOCAL_FILE = status({ files: [LOCAL_FILE] });

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

function setup(overrides: Partial<SaveSetupInfo> = {}): SaveSetupInfo {
  return {
    has_local_saves: true,
    local_files: [],
    server_slots: [],
    default_slot: "default",
    slot_confirmed: false,
    active_slot: null,
    recommended_action: "show_wizard",
    ...overrides,
  };
}

function refused(overrides: Partial<SaveSyncAvailability> = {}): SaveSyncAvailability {
  return {
    available: false,
    installed: true,
    established: true,
    emulator_label: null,
    reason: "save_shape_unsupported",
    message: "Save sync is unavailable: this emulator keeps one save card that all games share.",
    short_message: "Shared save",
    ...overrides,
  };
}

describe("saveSyncBadge", () => {
  it("says sync is off whatever else is known", () => {
    expect(saveSyncBadge({ ...INPUT, enabled: false, offline: true, status: "conflict", label: "Conflict" })).toEqual({
      tone: "off",
      label: "Disabled",
    });
  });

  describe("a conflict", () => {
    it("is the store's verdict alone, before the save status has landed", () => {
      expect(saveSyncBadge({ ...INPUT, status: "conflict", label: "Conflict" })).toEqual({
        tone: "attention",
        label: "Save conflict",
      });
    });

    it("is read off the save status's conflicts", () => {
      const saveStatus = status({ conflicts: [{} as SyncConflict] });
      expect(saveSyncBadge({ ...INPUT, status: "synced", label: "3d ago", saveStatus }).label).toBe("Save conflict");
    });

    it("stands while RomM is offline, as the button's RESOLVE CONFLICT does", () => {
      expect(saveSyncBadge({ ...INPUT, offline: true, status: "conflict", label: "Conflict" }).label).toBe(
        "Save conflict",
      );
    });
  });

  describe("while RomM is offline", () => {
    const OFFLINE = { ...INPUT, offline: true, saveStatus: WITH_LOCAL_FILE };

    it("says RomM is unavailable for an installed game with no local save", () => {
      expect(saveSyncBadge({ ...INPUT, offline: true })).toEqual({
        tone: "unavailable",
        label: "RomM unavailable",
      });
    });

    it("says so in grey for a game not on this device, where nothing is wrong", () => {
      expect(saveSyncBadge({ ...INPUT, offline: true, installed: false })).toEqual({
        tone: "off",
        label: "RomM unavailable",
      });
    });

    it("says when a save last synced though the status read could not reach RomM", () => {
      const saveStatus = status({
        files: [{ ...LOCAL_FILE, last_sync_at: daysAgo(3) }],
        server_query_failed: true,
      });
      expect(
        saveSyncBadge({ ...INPUT, offline: true, status: "none", label: "Server unreachable", saveStatus }),
      ).toEqual({ tone: "attention", label: "Synced 3d ago" });
    });

    it("claims no sync where one ran and carried nothing, as an upload that failed", () => {
      const saveStatus = status({
        files: [{ ...LOCAL_FILE, last_sync_at: null }],
        server_query_failed: true,
        last_sync_check_at: new Date().toISOString(),
      });
      expect(
        saveSyncBadge({ ...INPUT, offline: true, status: "none", label: "Server unreachable", saveStatus }),
      ).toEqual({ tone: "attention", label: "Not synced" });
    });

    it("says the newest of its files' syncs", () => {
      const saveStatus = status({
        files: [daysAgo(2), daysAgo(1), daysAgo(3)].map((last_sync_at) => ({ ...LOCAL_FILE, last_sync_at })),
      });
      expect(saveSyncBadge({ ...INPUT, offline: true, status: "none", saveStatus }).label).toBe("Synced 1d ago");
    });

    it("says a game whose saves cannot sync is unavailable, the offline answer standing", () => {
      expect(saveSyncBadge({ ...INPUT, offline: true, saveSyncAnswer: refused() }).label).toBe("RomM unavailable");
    });

    it("says when a local save last synced, in the attention tone", () => {
      expect(saveSyncBadge({ ...OFFLINE, status: "synced", label: "3d ago" })).toEqual({
        tone: "attention",
        label: "Synced 3d ago",
      });
    });

    it("says what is waiting on a local save", () => {
      expect(saveSyncBadge({ ...OFFLINE, status: "pending", label: "Local changes" }).label).toBe("Local changes");
    });

    it.each(["none", null] as const)("claims no sync for a local save with status %s", (saveSyncStatus) => {
      expect(saveSyncBadge({ ...OFFLINE, status: saveSyncStatus, label: "No saves" }).label).toBe("Not synced");
    });

    it.each([
      ["its path", { local_path: "/saves/game.srm" }],
      ["its size", { local_size: 8 }],
      ["its time", { local_mtime: "2026-11-01T00:00:00Z" }],
    ])("counts a save its status row names by %s alone", (_name, named) => {
      const file = { local_path: null, local_size: null, local_mtime: null, ...named } as SaveFileStatus;
      const saveStatus = status({ files: [file] });
      expect(saveSyncBadge({ ...INPUT, offline: true, saveStatus }).tone).toBe("attention");
    });

    it("counts no save for a status row that names none", () => {
      const file = { local_path: null, local_size: null, local_mtime: null } as SaveFileStatus;
      const saveStatus = status({ files: [file] });
      expect(saveSyncBadge({ ...INPUT, offline: true, saveStatus }).label).toBe("RomM unavailable");
    });

    it("counts a save the setup answer found", () => {
      expect(saveSyncBadge({ ...INPUT, offline: true, setupInfo: setup() }).tone).toBe("attention");
    });

    it("counts no save for a game not on this device", () => {
      expect(saveSyncBadge({ ...OFFLINE, installed: false }).label).toBe("RomM unavailable");
    });
  });

  describe("an unconfirmed slot", () => {
    it("is said where a local save would have to choose one", () => {
      expect(saveSyncBadge({ ...INPUT, setupInfo: setup() })).toEqual({
        tone: "attention",
        label: "Slot not confirmed",
      });
    });

    it("is not said with no local save", () => {
      const setupInfo = setup({ has_local_saves: false });
      expect(saveSyncBadge({ ...INPUT, setupInfo }).label).toBe("Not checked");
    });

    it("is not said once the slot is confirmed", () => {
      const setupInfo = setup({ slot_confirmed: true });
      expect(saveSyncBadge({ ...INPUT, setupInfo }).label).toBe("Not checked");
    });

    it("is not said where the answer asks for no choice", () => {
      const setupInfo = setup({ recommended_action: "auto_confirm_default" });
      expect(saveSyncBadge({ ...INPUT, setupInfo }).label).toBe("Not checked");
    });
  });

  describe("a game whose saves cannot sync", () => {
    it("says why, in grey, in place of the store's No saves", () => {
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "No saves", saveSyncAnswer: refused() })).toEqual({
        tone: "off",
        label: "Shared save",
      });
    });

    it("says it cannot sync where the answer carries no few words", () => {
      const saveSyncAnswer = refused();
      delete saveSyncAnswer.short_message;
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "No saves", saveSyncAnswer })).toEqual({
        tone: "off",
        label: "Can't sync",
      });
    });

    it("stands over a slot still to be chosen", () => {
      expect(saveSyncBadge({ ...INPUT, setupInfo: setup(), saveSyncAnswer: refused() }).label).toBe("Shared save");
    });

    it("stands where the listing failed with a 404", () => {
      const saveStatus = status({ server_query_failed: true, server_query_reason: "not_found" });
      expect(
        saveSyncBadge({ ...INPUT, status: "none", label: "Server unreachable", saveStatus, saveSyncAnswer: refused() })
          .tone,
      ).toBe("off");
    });

    describe("where the availability read failed", () => {
      const BESIDE_THE_CONTENT = { status: "none", label: "Save sync off — saves in content dir" } as const;
      const saveStatus = status({ savefiles_in_content_dir: true });

      it("says so in grey from the save status, over the store's sentence", () => {
        expect(saveSyncBadge({ ...INPUT, ...BESIDE_THE_CONTENT, saveStatus })).toEqual({
          tone: "off",
          label: "Can't sync",
        });
      });

      it("stands over a slot still to be chosen", () => {
        expect(saveSyncBadge({ ...INPUT, ...BESIDE_THE_CONTENT, saveStatus, setupInfo: setup() }).label).toBe(
          "Can't sync",
        );
      });

      it("leaves the answer's own words standing where the read did not fail", () => {
        expect(saveSyncBadge({ ...INPUT, ...BESIDE_THE_CONTENT, saveStatus, saveSyncAnswer: refused() }).label).toBe(
          "Shared save",
        );
      });

      it("makes no claim about a game not on this device, nor a green one in the store's sentence", () => {
        expect(saveSyncBadge({ ...INPUT, ...BESIDE_THE_CONTENT, saveStatus, installed: false })).toEqual({
          tone: "off",
          label: "Not checked",
        });
      });

      it("still says what RomM holds for a game not on this device", () => {
        const holds = status({ savefiles_in_content_dir: true, server_has_saves: true });
        expect(saveSyncBadge({ ...INPUT, ...BESIDE_THE_CONTENT, saveStatus: holds, installed: false }).label).toBe(
          "Saved on RomM",
        );
      });
    });

    it("gives way where RomM could not be reached", () => {
      const saveStatus = status({ server_query_failed: true, server_query_reason: "server_unreachable" });
      expect(
        saveSyncBadge({ ...INPUT, status: "none", label: "Server unreachable", saveStatus, saveSyncAnswer: refused() }),
      ).toEqual({
        tone: "attention",
        label: "Server unreachable",
      });
    });

    it("makes no claim about a game not on this device, as the SAVES tab makes none", () => {
      expect(
        saveSyncBadge({ ...INPUT, status: "none", label: "No saves", saveSyncAnswer: refused({ installed: false }) })
          .label,
      ).toBe("No saves");
    });

    it("makes no claim on an answer that says the saves sync", () => {
      const saveSyncAnswer = { available: true, installed: true, established: true, emulator_label: null };
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "No saves", saveSyncAnswer }).label).toBe("No saves");
    });
  });

  describe("a save RomM holds and this device does not", () => {
    it("draws in the attention tone on an installed game, in place of No local saves", () => {
      const saveStatus = status({ server_has_saves: true });
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "No local saves", saveStatus })).toEqual({
        tone: "attention",
        label: "Saved on RomM",
      });
    });

    it("draws in the ok tone on a game not on this device, in place of No saves", () => {
      const saveStatus = status({ server_has_saves: true });
      expect(saveSyncBadge({ ...INPUT, installed: false, status: "none", label: "No saves", saveStatus })).toEqual({
        tone: "ok",
        label: "Saved on RomM",
      });
    });

    it("gives way to a local save at rest", () => {
      const saveStatus = status({ server_has_saves: true });
      expect(saveSyncBadge({ ...INPUT, status: "synced", label: "3d ago", saveStatus }).label).toBe("Synced 3d ago");
    });
  });

  describe("the store's verdict", () => {
    it("reads a recent sync after its verb", () => {
      expect(saveSyncBadge({ ...INPUT, status: "synced", label: "Just now" })).toEqual({
        tone: "ok",
        label: "Synced just now",
      });
    });

    it("draws a save at rest with no recorded sync in the attention tone, in resolveSaveSyncLabel's word", () => {
      const label = resolveSaveSyncLabel({ status: "synced", label: "Not synced", last_sync_check_at: null });
      expect(saveSyncBadge({ ...INPUT, status: "synced", label })).toEqual({ tone: "attention", label: "Not synced" });
    });

    it.each(["Local changes", "Server newer"])("never draws %s as synced, whatever the last check", (label) => {
      const saveStatus = status({ last_sync_check_at: "2026-11-01T00:00:00Z" });
      expect(saveSyncBadge({ ...INPUT, status: "pending", label, saveStatus })).toEqual({ tone: "attention", label });
    });

    it.each([
      ["server_unreachable", status({ server_query_failed: true, server_query_reason: "server_unreachable" })],
      ["none", status({ server_query_failed: true, server_query_reason: null })],
      ["absent", status({ server_query_failed: true })],
    ])("says the server is unreachable for a failed listing whose reason is %s", (_name, saveStatus) => {
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "Server unreachable", saveStatus })).toEqual({
        tone: "attention",
        label: "Server unreachable",
      });
    });

    it.each(["not_found", "device_not_registered", "auth_failed", "server_error", "unknown"] as const)(
      "does not call RomM unreachable for a listing that failed with %s",
      (server_query_reason) => {
        const saveStatus = status({ server_query_failed: true, server_query_reason });
        expect(saveSyncBadge({ ...INPUT, status: "none", label: "Server unreachable", saveStatus })).toEqual({
          tone: "attention",
          label: "Couldn't check RomM",
        });
      },
    );

    it("says there are no saves in the ok tone", () => {
      expect(saveSyncBadge({ ...INPUT, status: "none", label: "No saves" })).toEqual({ tone: "ok", label: "No saves" });
    });

    it("makes no green claim with no answer, before one lands or after a read failed", () => {
      expect(saveSyncBadge(INPUT)).toEqual({ tone: "off", label: "Not checked" });
    });
  });
});
