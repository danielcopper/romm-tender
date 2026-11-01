import { describe, it, expect } from "vitest";
import type { VersionInfo } from "../api/backend";
import { NO_FILE_ON_ROMM_LABEL } from "./noFileOnRommWording";
import { VANISHED_HINT, versionRowHint } from "./versionPickerWording";

const version = (over: Partial<VersionInfo> = {}): VersionInfo => ({
  rom_id: 1,
  name: "Game (USA)",
  label: "USA",
  regions: ["USA"],
  languages: [],
  revision: "",
  tags: [],
  synced: true,
  installed: false,
  active: false,
  is_default: false,
  switchable: true,
  vanished: false,
  ...over,
});

describe("versionRowHint", () => {
  it("gives a row that can be switched to no hint", () => {
    expect(versionRowHint(version())).toBeNull();
  });

  it("says a version RomM no longer serves is gone, whatever else holds", () => {
    expect(versionRowHint(version({ vanished: true, bound_elsewhere: true, no_file: true, switchable: false }))).toBe(
      VANISHED_HINT,
    );
  });

  it("says another shortcut is on the version ahead of its missing file", () => {
    expect(versionRowHint(version({ bound_elsewhere: true, no_file: true, installed: true }))).toBe(
      "On another shortcut of this game",
    );
  });

  it("says RomM holds no file ahead of a conflicting match", () => {
    expect(versionRowHint(version({ no_file: true, switchable: false }))).toBe(NO_FILE_ON_ROMM_LABEL);
  });

  it("says the match conflicts where nothing else holds", () => {
    expect(versionRowHint(version({ switchable: false }))).toBe("conflicting metadata match in RomM");
  });
});
