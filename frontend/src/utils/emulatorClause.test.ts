import { describe, expect, it } from "vitest";
import { libretroEmu, sharedBy, standaloneEmu } from "../test-utils/coreFixtures";
import { emulatorClause, type EmulatorClauseSource } from "./emulatorClause";

const known = (overrides: Partial<EmulatorClauseSource>): EmulatorClauseSource => ({
  emulator_data_available: true,
  emulators: [libretroEmu("mgba_libretro", "mGBA", true)],
  active_core_label: "mGBA",
  ...overrides,
});

describe("emulatorClause", () => {
  it("names the emulator where one is resolved, and the games start", () => {
    expect(emulatorClause(known({}))).toEqual({ text: "mGBA", gamesCannotStart: false });
  });

  it("says RetroDECK decides where nothing is bakeable but its fallback can run", () => {
    const emulators = [standaloneEmu("mGBA (Standalone)", false, { bakeable: false, reason: "inject" })];
    expect(emulatorClause(known({ emulators, active_core_label: null }))).toEqual({
      text: "RetroDECK decides",
      gamesCannotStart: false,
    });
  });

  it("says no emulator installed where the fallback RetroDECK would use is not installed", () => {
    const emulators = [
      standaloneEmu("mGBA (Standalone)", false, { bakeable: false, reason: "not_installed" }),
      standaloneEmu("VBA-M (Standalone)", false, { bakeable: false, reason: "inject" }),
    ];
    expect(emulatorClause(known({ emulators, active_core_label: null }))).toEqual({
      text: "no emulator installed",
      gamesCannotStart: true,
    });
  });

  it("reads only the fallback: a later option that is not installed leaves RetroDECK deciding", () => {
    const emulators = [
      standaloneEmu("mGBA (Standalone)", false, { bakeable: false, reason: "inject" }),
      standaloneEmu("VBA-M (Standalone)", false, { bakeable: false, reason: "not_installed" }),
    ];
    expect(emulatorClause(known({ emulators, active_core_label: null }))?.text).toBe("RetroDECK decides");
  });

  it("says no emulator where the list holds none at all", () => {
    expect(emulatorClause(known({ emulators: [], active_core_label: null }))).toEqual({
      text: "no emulator",
      gamesCannotStart: true,
    });
  });

  it("names an emulator stored under one of several labels by every label its entry carries", () => {
    const emulators = [sharedBy(libretroEmu("pcsx2_libretro", "LRPS2", true), ["LRPS2", "PCSX2"])];
    expect(emulatorClause(known({ emulators, active_core_label: "PCSX2" }))?.text).toBe("LRPS2 / PCSX2");
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
