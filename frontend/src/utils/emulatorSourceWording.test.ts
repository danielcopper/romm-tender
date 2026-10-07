import { describe, expect, it } from "vitest";
import type { EmulatorSource } from "../types/emulatorSources";
import {
  NO_SOURCE_BANNER,
  SOURCES_READING,
  SOURCES_UNREAD,
  cannotStartSentence,
  emulatorDataReasonSentence,
  findingIsBanner,
  findingSentence,
  mainSourceBanners,
  sourceName,
  sourceRowLines,
} from "./emulatorSourceWording";

const REPAIR = " Repair it with RetroDECK's 'Repair RetroDECK Paths'.";

describe("sourceName", () => {
  it.each([
    ["retrodeck", "RetroDECK"],
    ["emudeck", "EmuDeck"],
    ["bare_retroarch_flatpak", "RetroArch (Flatpak)"],
    ["bare_retroarch_native", "RetroArch (native)"],
  ])("names %s as %s", (kind, name) => {
    expect(sourceName(kind)).toBe(name);
  });

  it("shows a kind it does not know as the kind itself", () => {
    expect(sourceName("standalone_pcsx2")).toBe("standalone_pcsx2");
  });

  it("does not take a name off the object prototype", () => {
    expect(sourceName("constructor")).toBe("constructor");
  });
});

describe("findingSentence", () => {
  it.each([
    ["marker-missing", "EmuDeck: its settings file /h/settings.sh is missing."],
    ["marker-unreadable", "EmuDeck: its settings file /h/settings.sh cannot be read."],
    [
      "marker-invalid",
      "EmuDeck: its settings file /h/settings.sh is damaged, so Tender cannot tell where its folders are.",
    ],
    [
      "root-missing",
      "EmuDeck: its folder /h/settings.sh does not exist. If it is on an SD card or another drive, insert it.",
    ],
    [
      "saves-root-missing",
      "EmuDeck: its saves folder /h/settings.sh does not exist, so saves of its emulators cannot be synced.",
    ],
    [
      "companion-config-missing",
      "EmuDeck: RetroArch's settings file /h/settings.sh cannot be read; EmuDeck's RetroArch may be missing or broken.",
    ],
    ["config-unreadable", "EmuDeck: its settings file /h/settings.sh cannot be read."],
  ])("words %s for a source other than RetroDECK", (code, sentence) => {
    expect(findingSentence("emudeck", { code, data: { path: "/h/settings.sh", status: "denied" } })).toBe(sentence);
  });

  it("says a RetroDECK that is not set up is installed and how to set it up", () => {
    const finding = { code: "not-set-up", data: { path: "/h/retrodeck.json", app_id: "net.retrodeck.retrodeck" } };
    expect(findingSentence("retrodeck", finding)).toBe(
      "RetroDECK is installed but has not been set up yet. Start RetroDECK once and finish its first-run setup.",
    );
  });

  it("adds RetroDECK's repair to a damaged settings file of RetroDECK's", () => {
    expect(findingSentence("retrodeck", { code: "marker-invalid", data: { path: "/r.json", key: "paths" } })).toBe(
      `RetroDECK: its settings file /r.json is damaged, so Tender cannot tell where its folders are.${REPAIR}`,
    );
  });

  it("adds RetroDECK's repair to a missing saves folder of RetroDECK's", () => {
    expect(findingSentence("retrodeck", { code: "saves-root-missing", data: { path: "/sd/saves" } })).toBe(
      `RetroDECK: its saves folder /sd/saves does not exist, so saves of its emulators cannot be synced.${REPAIR}`,
    );
  });

  it("adds no repair to a finding RetroDECK's repair does not fix", () => {
    expect(findingSentence("retrodeck", { code: "root-missing", data: { path: "/sd/retrodeck" } })).not.toContain(
      "Repair",
    );
  });

  it.each([
    ["parse-error", "does not parse as XML"],
    ["missing-systemlist", "has no <systemList>"],
  ])("words a broken systems file (%s) with the source, the file and the reason", (problem, reason) => {
    const finding = { code: "catalogue-invalid", data: { path: "/es/custom_systems/es_systems.xml", problem } };
    expect(findingSentence("retrodeck", finding)).toBe(
      `RetroDECK: ES-DE's systems file /es/custom_systems/es_systems.xml ${reason}. ` +
        "ES-DE shows no systems until it is fixed, and Tender cannot tell which emulators RetroDECK offers.",
    );
  });

  it.each([
    ["missing", "is missing"],
    ["not-a-link", "is not a link"],
    ["diverted", "points elsewhere"],
  ])("words a content tree that does not reach its emulator (%s)", (problem, state) => {
    const finding = {
      code: "content-tree-unwired",
      data: { family: "texture_packs", hub: "/rd/texture_packs", path: "/rd/emu/textures", problem },
    };
    expect(findingSentence("retrodeck", finding)).toBe(
      `RetroDECK: texture packs or mods in /rd/texture_packs do not reach the emulator, because /rd/emu/textures ${state}. ` +
        "Resetting that emulator in RetroDECK fixes it.",
    );
  });

  it("names a kind it does not know by the kind", () => {
    expect(findingSentence("standalone_x", { code: "root-missing", data: { path: "/x" } })).toBe(
      "standalone_x: its folder /x does not exist. If it is on an SD card or another drive, insert it.",
    );
  });

  it("words a code it has no sentence for as a problem with the source, never the resolver's message", () => {
    expect(
      findingSentence("emudeck", { code: "brand-new-code", data: { path: "/p", message: "resolver prose" } }),
    ).toBe("Problem with EmuDeck: brand-new-code");
  });

  it.each([
    ["no path", { code: "root-missing", data: {} }],
    ["a path that is not text", { code: "root-missing", data: { path: 3 } }],
    ["an unknown systems-file problem", { code: "catalogue-invalid", data: { path: "/es.xml", problem: "other" } }],
    ["no systems-file problem", { code: "catalogue-invalid", data: { path: "/es.xml" } }],
    ["no hub", { code: "content-tree-unwired", data: { path: "/p", problem: "missing" } }],
    ["an unknown link problem", { code: "content-tree-unwired", data: { path: "/p", hub: "/h", problem: "toString" } }],
  ])("falls back to the code where a fact the sentence needs is missing: %s", (_label, finding) => {
    expect(findingSentence("retrodeck", finding)).toBe(`Problem with RetroDECK: ${finding.code}`);
  });
});

describe("findingIsBanner", () => {
  it.each([
    "marker-missing",
    "marker-unreadable",
    "marker-invalid",
    "root-missing",
    "saves-root-missing",
    "companion-config-missing",
    "config-unreadable",
    "catalogue-invalid",
    "brand-new-code",
  ])("shows %s as a banner", (code) => {
    expect(findingIsBanner({ code, data: {} })).toBe(true);
  });

  it("keeps a content tree that does not reach its emulator out of the banners", () => {
    expect(findingIsBanner({ code: "content-tree-unwired", data: {} })).toBe(false);
  });
});

const RETRODECK = { kind: "retrodeck", starts_games: true };
const EMUDECK = { kind: "emudeck", starts_games: false };

describe("emulatorDataReasonSentence", () => {
  it.each([
    ["no_source", null, "No emulator source was found, so Tender cannot tell which emulators this platform offers."],
    ["switched_off", null, "Every emulator source is switched off in Settings → Emulator sources."],
    [
      "catalogue_invalid",
      RETRODECK,
      "RetroDECK: ES-DE's systems file is broken, so its emulators are not established.",
    ],
    ["not_set_up", RETRODECK, "RetroDECK has not been set up yet, so its emulators are not established."],
    ["unavailable", RETRODECK, "RetroDECK's emulator list is not established."],
    ["sealed", EMUDECK, "EmuDeck's emulator list cannot be read yet."],
  ] as const)("words %s", (reason, source, sentence) => {
    expect(emulatorDataReasonSentence(reason, source)).toBe(sentence);
  });

  it("never says there is no emulator", () => {
    for (const reason of [
      "no_source",
      "switched_off",
      "catalogue_invalid",
      "not_set_up",
      "unavailable",
      "sealed",
    ] as const) {
      expect(emulatorDataReasonSentence(reason, RETRODECK)).not.toMatch(/no emulator\b(?! source)/i);
    }
  });

  it("names a source of a kind it does not know by its kind", () => {
    expect(emulatorDataReasonSentence("unavailable", { kind: "standalone_x", starts_games: false })).toBe(
      "standalone_x's emulator list is not established.",
    );
  });

  it("reads an answer no source gave as no source found, whatever its reason", () => {
    expect(emulatorDataReasonSentence("unavailable", null)).toBe(
      "No emulator source was found, so Tender cannot tell which emulators this platform offers.",
    );
  });
});

function source(overrides: Partial<EmulatorSource>): EmulatorSource {
  return {
    kind: "retrodeck",
    enabled: true,
    starts_games: true,
    root: "/rd",
    findings: [],
    catalogue: "read",
    ...overrides,
  };
}

describe("sourceRowLines", () => {
  it("says a healthy source RetroDECK starts games through has no problems", () => {
    expect(sourceRowLines(source({}))).toEqual([{ tone: "ok", text: "No problems found." }]);
  });

  it("says EmuDeck's list cannot be read and Tender cannot start games through it", () => {
    expect(sourceRowLines(source({ kind: "emudeck", starts_games: false, catalogue: "sealed" }))).toEqual([
      { tone: "warning", text: "EmuDeck's emulator list cannot be read yet." },
      { tone: "info", text: "Tender cannot start games through EmuDeck yet." },
    ]);
  });

  it("says a RetroArch (Flatpak) without a frontend cannot start games, and nothing else", () => {
    expect(
      sourceRowLines(source({ kind: "bare_retroarch_flatpak", starts_games: false, catalogue: "unavailable" })),
    ).toEqual([{ tone: "info", text: "Tender cannot start games through RetroArch (Flatpak) yet." }]);
  });

  it("says a list it could not read is not established instead of no problems", () => {
    expect(sourceRowLines(source({ catalogue: "unavailable" }))).toEqual([
      { tone: "warning", text: "RetroDECK's emulator list is not established." },
    ]);
  });

  it("words every finding, the one kept out of the banners too", () => {
    const unwired = {
      code: "content-tree-unwired",
      data: { hub: "/rd/mods", path: "/emu/mods", problem: "missing" },
    };
    expect(sourceRowLines(source({ findings: [{ code: "root-missing", data: { path: "/sd" } }, unwired] }))).toEqual([
      {
        tone: "warning",
        text: "RetroDECK: its folder /sd does not exist. If it is on an SD card or another drive, insert it.",
      },
      {
        tone: "warning",
        text:
          "RetroDECK: texture packs or mods in /rd/mods do not reach the emulator, because /emu/mods is missing. " +
          "Resetting that emulator in RetroDECK fixes it.",
      },
    ]);
  });

  it("says Tender cannot start games through a switched-off source, on its card (#2265 D6)", () => {
    expect(
      sourceRowLines(source({ kind: "emudeck", enabled: false, starts_games: false, catalogue: "sealed" })),
    ).toContainEqual({ tone: "info", text: "Tender cannot start games through EmuDeck yet." });
  });

  it("says a RetroArch without a frontend cannot start games", () => {
    expect(
      sourceRowLines(source({ kind: "bare_retroarch_native", starts_games: false, catalogue: "unavailable" })),
    ).toEqual([{ tone: "info", text: "Tender cannot start games through RetroArch (native) yet." }]);
  });

  it("states the section's own lines (#2188 D33)", () => {
    expect(SOURCES_READING).toBe("Reading the emulator sources…");
    expect(SOURCES_UNREAD).toBe("Could not read the emulator sources. Reopen the page to try again.");
  });
});

const texts = (listing: Parameters<typeof mainSourceBanners>[0]) =>
  mainSourceBanners(listing).map((banner) => banner.text);

describe("mainSourceBanners", () => {
  it("keys two banners with the same sentence apart", () => {
    const twice = { code: "root-missing", data: { path: "/sd" } };
    const banners = mainSourceBanners({ answering: "retrodeck", sources: [source({ findings: [twice, twice] })] });
    expect(banners.map((banner) => banner.text)).toEqual([banners[0]!.text, banners[0]!.text]);
    expect(new Set(banners.map((banner) => banner.key)).size).toBe(2);
  });

  it("says no source was found where none is detected", () => {
    expect(texts({ sources: [], answering: null })).toEqual([NO_SOURCE_BANNER]);
    expect(NO_SOURCE_BANNER).toBe("No emulator source was found.");
  });

  it("puts every banner finding of every source on Main, and not the unwired content tree", () => {
    const listing = {
      answering: "retrodeck",
      sources: [
        source({
          findings: [
            { code: "marker-invalid", data: { path: "/rd.json" } },
            { code: "content-tree-unwired", data: { hub: "/h", path: "/p", problem: "missing" } },
          ],
        }),
        source({
          kind: "emudeck",
          starts_games: false,
          findings: [{ code: "companion-config-missing", data: { path: "/ra.cfg" } }],
        }),
      ],
    };
    expect(texts(listing)).toEqual([
      "RetroDECK: its settings file /rd.json is damaged, so Tender cannot tell where its folders are. " +
        "Repair it with RetroDECK's 'Repair RetroDECK Paths'.",
      "EmuDeck: RetroArch's settings file /ra.cfg cannot be read; EmuDeck's RetroArch may be missing or broken.",
      "Tender cannot start games through EmuDeck yet.",
    ]);
  });

  it("says Tender cannot start games through the answering source where that is the case", () => {
    const listing = { answering: "emudeck", sources: [source({ kind: "emudeck", starts_games: false })] };
    expect(texts(listing)).toEqual([cannotStartSentence("emudeck")]);
    expect(cannotStartSentence("emudeck")).toBe("Tender cannot start games through EmuDeck yet.");
  });

  it("says Tender cannot start games through EmuDeck while RetroDECK answers beside it", () => {
    const listing = {
      answering: "retrodeck",
      sources: [source({}), source({ kind: "emudeck", starts_games: false })],
    };
    expect(texts(listing)).toEqual(["Tender cannot start games through EmuDeck yet."]);
  });

  it("keeps a switched-off source's findings off Main", () => {
    const listing = {
      answering: "retrodeck",
      sources: [
        source({}),
        source({
          kind: "emudeck",
          enabled: false,
          starts_games: false,
          findings: [{ code: "companion-config-missing", data: { path: "/ra.cfg" } }],
        }),
      ],
    };
    expect(texts(listing)).toEqual([]);
  });

  it("says nothing about starting games where every source is switched off", () => {
    const listing = { answering: null, sources: [source({ kind: "emudeck", enabled: false, starts_games: false })] };
    expect(texts(listing)).toEqual([]);
  });
});
