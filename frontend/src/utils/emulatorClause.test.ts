import { describe, expect, it } from "vitest";
import { emulatorStart, libretroEmu, standaloneEmu } from "../test-utils/coreFixtures";
import { emulatorClause, type EmulatorClauseSource } from "./emulatorClause";

const known = (overrides: Partial<EmulatorClauseSource>): EmulatorClauseSource => ({
  emulator_data_available: true,
  emulators: [libretroEmu("mgba_libretro", "mGBA", true)],
  active_core_label: "mGBA",
  start: emulatorStart({ entry: "mGBA" }),
  ...overrides,
});

describe("emulatorClause", () => {
  it("names the emulator where one is resolved, and the games start", () => {
    expect(emulatorClause(known({}))).toEqual({ text: "mGBA", gamesCannotStart: false });
  });

  it("names a default Tender cannot bake, which the games cannot start with", () => {
    const emulators = [standaloneEmu("mGBA (Standalone)", true, { bakeable: false, reason: "inject" })];
    const start = emulatorStart({ state: "cannot_start", reason: "not_bakeable", entry: "mGBA (Standalone)" });
    expect(emulatorClause(known({ emulators, active_core_label: "mGBA (Standalone)", start }))).toEqual({
      text: "mGBA (Standalone)",
      gamesCannotStart: true,
    });
  });

  it("names a default that cannot start the games, in the state they cannot start in", () => {
    const emulators = [standaloneEmu("Ryubing (Standalone)", true, { bakeable: false, reason: "not_installed" })];
    const start = emulatorStart({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" });
    expect(emulatorClause(known({ emulators, active_core_label: "Ryubing (Standalone)", start }))).toEqual({
      text: "Ryubing (Standalone)",
      gamesCannotStart: true,
    });
  });

  it("says no default where the default's installation could not be established", () => {
    const emulators = [standaloneEmu("BlastEm", false, { reason: "no_find_rule" })];
    const start = emulatorStart({ state: "choose", reason: "no_find_rule", entry: "BlastEm" });
    expect(emulatorClause(known({ emulators, active_core_label: null, start }))).toEqual({
      text: "no default",
      gamesCannotStart: true,
    });
  });

  it("says no emulator where the list holds none at all", () => {
    expect(emulatorClause(known({ emulators: [], active_core_label: null }))).toEqual({
      text: "no emulator",
      gamesCannotStart: true,
    });
  });

  it("names an emulator by the one label it was picked under, two labels that run one command being two rows", () => {
    const emulators = [libretroEmu("pcsx2_libretro", "LRPS2", true), libretroEmu("pcsx2_libretro", "PCSX2")];
    expect(emulatorClause(known({ emulators, active_core_label: "PCSX2" }))?.text).toBe("PCSX2");
  });

  it("says nothing where there is no answer yet", () => {
    expect(emulatorClause(null)).toBeNull();
  });

  it("says nothing where the emulator list could not be read, empty as it then is", () => {
    expect(
      emulatorClause(known({ emulator_data_available: false, emulators: [], active_core_label: null })),
    ).toBeNull();
  });
});
