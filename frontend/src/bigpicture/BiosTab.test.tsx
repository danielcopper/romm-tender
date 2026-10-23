// The pane renders here with the state the panel holds for it
// (`test-utils/biosTab.tsx`); whether it is mounted at all is the game page's
// tabs' call, pinned beside the panel (RomMGameInfoPanel.test.tsx).

import { describe, it, expect, vi } from "vitest";
import { useState, type FC, type ReactElement } from "react";
import { act, fireEvent, render } from "@testing-library/react";
import * as deckyUi from "@decky/ui";
import { showModal } from "@decky/ui";
import * as backend from "../api/backend";
import { EmulatorStepScope } from "./emulatorStepping";
import { BiosTab, useBiosTab } from "./BiosTab";
import { GamePageTabs } from "./GamePageTabs";
import { BiosTabWithState } from "../test-utils/biosTab";
import { libretroEmu } from "../test-utils/coreFixtures";
import { biosColorForLevel } from "../utils/biosColor";
import type { BiosFileStatus, BiosStatus, CoreInfo, MissingConfiguredImage } from "../types";

// The game page's tabs reach Steam's tabbed page through this module, a webpack
// probe with no answer under happy-dom.
vi.mock("../utils/deckyUiInternals", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/deckyUiInternals")>()),
  Tabs: (await import("../test-utils/steamTabs")).StubTabs,
}));

const coreInfo: CoreInfo = {
  active_core: "snes9x_libretro.so",
  active_core_label: "Snes9x",
  platform_core_label: null,
  has_game_override: false,
  emulator_data_available: true,
  emulator_data_reason: null,
  emulator_source: { kind: "retrodeck", starts_games: true },
  platform_system: null,
  emulators: [
    {
      label: "Snes9x",
      labels: ["Snes9x"],
      name: "Snes9x",
      kind: "libretro",
      core_so: "snes9x_libretro",
      emulator: "snes9x_libretro.so",
      is_default: true,
      bakeable: true,
      reason: null,
    },
  ],
};

// A state the backend can actually emit: the "missing" level comes from a
// required file that is absent, so the required counts have to be there. Paired
// with no `required_count` it would be unreachable — a zero required count
// always computes "ok".
const biosStatus: BiosStatus = {
  needs_bios: true,
  server_count: 1,
  local_count: 0,
  ratio_server_count: 1,
  ratio_local_count: 0,
  all_downloaded: false,
  required_count: 1,
  required_downloaded: 0,
  locating: "by-name",
};

describe("BiosTab", () => {
  it("renders the requirement and the emulator it is scoped to when it is the active tab", () => {
    const { container } = render(<BiosTabWithState biosStatus={biosStatus} biosLevel="missing" coreInfo={coreInfo} />);
    expect(container.textContent).toContain(
      "The one file the launching emulator opens by name is not in place (0/1 RomM library files)",
    );
  });

  it("shows its heading and Checking… behind Steam's spinner while no answer is held", () => {
    const { container, getByTestId } = render(
      <BiosTabWithState biosStatus={null} biosLevel={null} coreInfo={coreInfo} />,
    );
    expect(container.querySelector(".romm-panel-section-title")?.textContent).toBe("BIOS");
    expect(getByTestId("waiting-line").textContent).toBe("Checking…");
    expect(getByTestId("waiting-line").querySelector('[data-testid="spinner"]')).not.toBeNull();
  });

  it("says the read failed while no answer is held, and never that it is still checking", () => {
    const { container, queryByTestId } = render(
      <BiosTabWithState biosStatus={null} biosLevel={null} readFailed coreInfo={coreInfo} />,
    );
    expect(container.textContent).toBe("BIOSCould not read the BIOS state. Open the game page again to retry.");
    expect(queryByTestId("waiting-line")).toBeNull();
  });

  it("keeps the answer it holds under a notice when its re-read failed", () => {
    const { container, getByTestId } = render(
      <BiosTabWithState biosStatus={biosStatus} biosLevel="missing" readFailed coreInfo={coreInfo} />,
    );
    expect(getByTestId("bios-stale").textContent).toBe(
      "Could not re-read the BIOS state, so what is below may be out of date. Open the game page again to retry.",
    );
    expect(container.textContent).toContain("The one file the launching emulator opens by name is not in place");
  });

  it("draws the answer that no BIOS is needed like any other: a green sentence, the selector and the list", () => {
    const noneNeeded: BiosStatus = {
      needs_bios: false,
      server_count: 0,
      local_count: 0,
      all_downloaded: true,
      required_count: 0,
      required_downloaded: 0,
      emulator_read: true,
      active_core_label: "Snes9x",
      files: [],
      emulator_tabs: [
        {
          emulator: "snes9x_libretro.so",
          label: "Snes9x",
          launching: true,
          requirements_met: true,
          rows: [],
        },
        {
          emulator: "bsnes_libretro.so",
          label: "bsnes",
          launching: false,
          requirements_met: true,
          rows: [
            {
              file_name: "optional.bin",
              wanted: "optional",
              required_by_active: false,
              downloaded: false,
            } as BiosFileStatus,
          ],
        },
      ],
    };
    const { container } = render(<BiosTabWithState biosStatus={noneNeeded} biosLevel="ok" coreInfo={coreInfo} />);

    expect(container.textContent).toContain("Snes9x marks none of its BIOS files as required");
    expect(container.querySelector<HTMLElement>(".romm-status-dot")?.style.backgroundColor).toBe(
      biosColorForLevel("ok"),
    );
    expect(container.textContent).toContain("1 / 2");
    // The launching emulator's list holds no row, required or not.
    expect(container.querySelector('[data-testid="bios-file-row"]')).toBeNull();
  });

  it("renders the unknown reading off a status with no counts at all", () => {
    // What a platform whose emulators cannot be asked hands the tab: the wire
    // payload for "nothing could establish it", with no file rows and no
    // aggregates to read. The pane still has to say so rather than fall through
    // to the no-requirement sentence, whose counts would both be zero.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).toContain("Nothing could be established about what the launching emulator needs");
    expect(container.textContent).not.toContain("marks none of its BIOS files as required");
    expect(container.innerHTML).toContain("#8f98a0");
  });

  it("gives no reason line where the answering source gives an emulator list", () => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={coreInfo}
      />,
    );
    expect(container.querySelector('[data-testid="bios-reason"]')).toBeNull();
  });

  it("says on a game page that the source has no system for the game's platform", () => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={{
          ...coreInfo,
          emulators: [],
          emulator_data_available: false,
          emulator_data_reason: "no_platform_system",
          platform_system: { state: "no_system", source: "retrodeck", system: null, platform: "Commodore VIC-20" },
        }}
      />,
    );
    expect(container.querySelector('[data-testid="bios-reason"]')?.textContent).toBe(
      "RetroDECK has no system for Commodore VIC-20, so Tender cannot download its games.",
    );
  });

  it("puts the reason line between the BIOS heading and the state", () => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={{
          ...coreInfo,
          emulator_data_available: false,
          emulator_data_reason: "switched_off",
          emulator_source: null,
          platform_system: null,
        }}
      />,
    );
    const reason = container.querySelector('[data-testid="bios-reason"]');
    expect(reason?.textContent).toBe("Every emulator source is switched off in Settings › Emulator sources.");
    expect(reason?.previousElementSibling?.textContent).toBe("BIOS");
    expect(reason?.nextElementSibling?.textContent).toContain(
      "Nothing could be established about what the launching emulator needs",
    );
  });

  it.each([
    [2, "All 2 files the launching emulator opens by name are in place (5/20 RomM library files)"],
    [1, "The one file the launching emulator opens by name is in place (5/20 RomM library files)"],
  ])("states a ready requirement of %i beside a library ratio that counts a different set", (required, sentence) => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 5,
          ratio_server_count: 20,
          ratio_local_count: 5,
          all_downloaded: false,
          required_count: required,
          required_downloaded: required,
          locating: "by-name",
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).toContain(sentence);
  });

  it("drops the ratio when the library holds none of the platform's files", () => {
    // A "(0/0 RomM library files)" beside the sentence counts a set that does not exist.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
          ratio_server_count: 0,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 0,
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).toContain("The launching emulator marks none of its BIOS files as required");
    expect(container.textContent).not.toContain("RomM library files");
  });

  it.each([
    ["PlayStation", "PlayStation"],
    // No name held: the sentence still stands, naming the system generically.
    ["", "This system"],
  ])(
    "says the console does not start without an image where the counts would say nothing is required (%j)",
    (platformName, named) => {
      // The PlayStation state: SwanStation marks every image it declares optional
      // — a libretro `.info` cannot say the console needs one of them — so
      // `required_count` is 0 while no game on the platform would start. The
      // resolver weighs the console's need into its verdict (red), and the
      // sentence says so over the images the emulator knows.
      const images = ["scph5500.bin", "scph5501.bin", "scph5502.bin", "scph1001.bin", "scph7001.bin"].map(
        (file_name) => ({
          file_name,
          downloaded: false,
          local_path: "",
          description: file_name,
          wanted: "optional" as const,
          required_by_active: false,
          used_by_active: true,
          satisfied: false,
        }),
      );
      const { container } = render(
        <BiosTabWithState
          biosStatus={{
            needs_bios: true,
            server_count: 20,
            local_count: 0,
            ratio_server_count: 20,
            ratio_local_count: 0,
            all_downloaded: false,
            required_count: 0,
            required_downloaded: 0,
            console_needs_image: true,
            files: images,
          }}
          biosLevel="missing"
          coreInfo={coreInfo}
          platformName={platformName}
        />,
      );
      // The console is RomM's name for the platform.
      expect(container.textContent).toContain(
        `${named} does not start without a BIOS image, and none of the five the launching emulator knows is in ` +
          "place (0/20 RomM library files)",
      );
      expect(container.textContent).not.toContain("marks none of its BIOS files as required");
      expect(container.innerHTML).toContain("#d94126");
    },
  );

  it("says the console needs an image only under a red verdict", () => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          console_needs_image: true,
        }}
        biosLevel="unknown"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).not.toContain("does not start without a BIOS image");
  });

  it("says no BIOS is chosen in the emulator's settings, in the resolver's words", () => {
    // PCSX2 (Standalone) with `[Filenames] BIOS` empty.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          active_core_label: "PCSX2",
          emulator_notes: [{ kind: "bios-unchosen", setting: "Filenames/BIOS", tied: null, regions: [] }],
        }}
        biosLevel="unknown"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).toContain("No BIOS is chosen in PCSX2's settings, and no game boots until one is");
  });

  it("says nothing of the console where the resolver records no need for an image", () => {
    // A console nothing is recorded about must change nothing — its own sentence
    // would be a claim about an unasked question.
    for (const consoleNeedsImage of [{ console_needs_image: false }, {}]) {
      const { container } = render(
        <BiosTabWithState
          biosStatus={{
            needs_bios: true,
            server_count: 20,
            local_count: 1,
            ratio_server_count: 20,
            ratio_local_count: 1,
            all_downloaded: false,
            required_count: 0,
            required_downloaded: 0,
            ...consoleNeedsImage,
          }}
          biosLevel="ok"
          coreInfo={coreInfo}
        />,
      );
      expect(container.textContent).toContain(
        "The launching emulator marks none of its BIOS files as required (1/20 RomM library files)",
      );
      expect(container.textContent).not.toContain("cannot start this system");
    }
  });

  it("says whose requirement the empty count is, and never that the console needs nothing", () => {
    // `required_count: 0` is one emulator's declaration and nothing else: the
    // launching emulator marks none of the files it names required. It says
    // nothing about the CONSOLE, which nobody may have asked about — so a
    // headline that dropped the subject was read as an all-clear the reading
    // never gave.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 3,
          local_count: 1,
          ratio_server_count: 3,
          ratio_local_count: 1,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );
    expect(container.textContent).toContain(
      "The launching emulator marks none of its BIOS files as required (1/3 RomM library files)",
    );
    // The subject, spelled out: the sentence may not stand without it. "Nothing
    // required" is the state's short form, which this surface never shows —
    // printing it here would be the subjectless headline the sentence replaced.
    expect(container.textContent).not.toContain("Nothing required");
  });

  it("names the emulator the answer was scoped to, where the answer names one", () => {
    // The pane names the same emulator two inches away, under its Emulator
    // heading, so a subjectless sentence says less than everything around it.
    // The name is the label half of the one pick the backend filtered these
    // counts by, carried on the answer itself — reading it off the core payload
    // beside it would be a second resolution of the same question.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 1,
          ratio_server_count: 20,
          ratio_local_count: 1,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          active_core_label: "mGBA",
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );

    expect(container.textContent).toContain("mGBA marks none of its BIOS files as required (1/20 RomM library files)");
    expect(container.textContent).not.toContain("The launching emulator");
  });

  it("falls back to the nameless sentence where the answer names no emulator", () => {
    // No pick could be made, or it carries no label of its own. The sentence is
    // then exactly what it always was; inventing a name from the core list on
    // the page is the split this field exists to close.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 1,
          ratio_server_count: 20,
          ratio_local_count: 1,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          active_core_label: null,
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );

    expect(container.textContent).toContain(
      "The launching emulator marks none of its BIOS files as required (1/20 RomM library files)",
    );
  });

  it("shows no description under a row a packaged card describes", () => {
    // The same field carries two kinds of writing. A `.info`'s is a label for
    // the file; a card's is atlas explaining the requirement in sentences, and
    // on this pane it filled the row. The register is read off the declaration
    // and never guessed from the row.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 1,
          local_count: 0,
          ratio_server_count: 1,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 1,
          required_downloaded: 0,
          required_withheld: 0,
          files: [
            {
              file_name: "scph1001.bin",
              downloaded: false,
              local_path: "",
              declared_path: "scph1001.bin",
              description:
                "a PlayStation BIOS image — the console runs it before any disc, and DuckStation starts " +
                "nothing without one — found by the search, not named by any setting",
              declaration: "packaged",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              satisfied: false,
            },
          ],
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
      />,
    );

    expect(container.textContent).toContain("scph1001.bin");
    expect(container.textContent).not.toContain("the console runs it before any disc");
  });

  it("heads a row with the file it declares, and adds only what the description still says", () => {
    // The description is the packager's prose out of a core's `.info` — outside
    // the resolver's contract, and routinely spelling the row's own name into
    // its words. Heading the row with it put that prose where the file's
    // identity belongs; printing it whole would print the name twice. The two
    // rows below are the common shapes, and the second is the one with nothing
    // to add. Both are `read` rows, because that is what a `.info` is and only
    // a `.info`'s prose is shown at all.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 2,
          local_count: 0,
          ratio_server_count: 2,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 2,
          required_downloaded: 0,
          required_withheld: 0,
          files: [
            {
              file_name: "dc_boot.bin",
              downloaded: false,
              local_path: "",
              declared_path: "dc/dc_boot.bin",
              description: "dc/dc_boot.bin (Dreamcast BIOS)",
              declaration: "read",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              satisfied: false,
            },
            {
              file_name: "macventure.dat",
              downloaded: false,
              local_path: "",
              declared_path: "macventure.dat",
              description: "macventure.dat",
              declaration: "read",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              satisfied: false,
            },
          ],
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
      />,
    );

    const rows = [...container.querySelectorAll<HTMLElement>('[data-testid="bios-file-row"]')];
    // The declared path heads the row — the folder is the one thing a reader
    // placing the file by hand needs, and `file_name` is only its basename —
    // and what the description still says stands on the line under it, in the
    // packager's own punctuation.
    expect(rows.map((row) => row.querySelector('[data-testid="bios-file-name"]')?.textContent)).toEqual([
      "dc/dc_boot.bin",
      "macventure.dat",
    ]);
    expect(rows[0]?.textContent).toContain("(Dreamcast BIOS)");
    // A description that is nothing but the name adds nothing, so that row says
    // the name once.
    expect(rows[1]?.textContent.split("macventure.dat")).toHaveLength(2);
  });

  it("states each row's need under its name, so a met required file and a met optional one read apart", () => {
    // Both rows carry a ✓; the word under the name is what says which one this
    // launch cannot do without.
    const row = (file_name: string, required: boolean) => ({
      file_name,
      downloaded: true,
      local_path: `/bios/${file_name}`,
      declared_path: file_name,
      description: file_name,
      declaration: "read" as const,
      wanted: required ? ("needed" as const) : ("optional" as const),
      required_by_active: required,
      on_server: true,
      satisfied: true,
    });
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          locating: "by-name",
          required_count: 1,
          required_downloaded: 1,
          files: [row("dc_boot.bin", true), row("dc_flash.bin", false)],
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );

    const rowOf = (name: string) =>
      [...container.querySelectorAll<HTMLElement>('[data-testid="bios-file-row"]')].find(
        (el) => el.querySelector('[data-testid="bios-file-name"]')?.textContent === name,
      ) as HTMLElement;
    const needUnder = (name: string) => rowOf(name).querySelector('[data-testid="bios-need"]');
    expect(needUnder("dc_boot.bin")?.textContent).toBe("required");
    expect(needUnder("dc_flash.bin")?.textContent).toBe("optional");
    expect(needUnder("dc_flash.bin")?.previousElementSibling).toBe(
      rowOf("dc_flash.bin").querySelector('[data-testid="bios-file-name"]'),
    );
  });

  it("puts a satisfied folder's images on their own lines under its row, the row named by its folder alone", () => {
    // Folding the image descriptions into the row's name ran it to ~150
    // characters — so the images go in the indented block below.
    const images = [
      "USA     v02.00(14/06/2004)  Console 20040614-100909",
      "Europe  v02.00(14/06/2004)  Console 20040614-100914",
    ];
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
          ratio_server_count: 0,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 1,
          required_downloaded: 1,
          required_withheld: 0,
          files: [
            {
              file_name: "bios",
              downloaded: true,
              local_path: "",
              declared_path: "pcsx2/bios",
              description: "'pcsx2/bios' folder",
              wanted: "needed",
              required_by_active: true,
              on_server: false,
              declared_kind: "directory",
              satisfied: true,
              caveats: ["firmware-image-identified"],
              images,
            },
          ],
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );

    const name = container.querySelector('[data-testid="bios-file-name"]');
    const rendered = [...container.querySelectorAll("div")].map((div) => div.textContent);
    for (const image of images) expect(rendered).toContain(image);
    // The name carries the row and nothing else — no joined run of images, and
    // no "holds" heading over a list that is its own sentence. It is the row's
    // own declared path, never the packager's prose: `'pcsx2/bios' folder`
    // reduces to the bare word "folder" once the declaration comes out of it,
    // which is a restatement of `declared_kind`, so the row shows none.
    expect(name?.textContent).toBe("pcsx2/bios");
    expect(container.textContent).not.toContain(images.join(", "));
    // That bare word is the whole of what a description line would say on this
    // row, so its absence is the only assertion here that can see the guard.
    expect(container.textContent).not.toContain("folder");
  });

  it("names an unreadable destination on a file row, which otherwise says nothing at all", () => {
    // This pane leaves plain absence to its dot and appends no word of its own,
    // so without the note an unreadable destination is indistinguishable from a
    // file that is simply not there.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 1,
          local_count: 0,
          ratio_server_count: 1,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 1,
          required_downloaded: 0,
          required_withheld: 0,
          files: [
            {
              file_name: "dc_boot.bin",
              downloaded: false,
              local_path: "/retrodeck/bios/dc_boot.bin",
              description: "Dreamcast boot ROM",
              declaration: "read",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              declared_kind: "file",
              satisfied: null,
              caveats: ["firmware-path-inaccessible"],
            },
          ],
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
      />,
    );

    // Under the row: what the file IS, then how it STANDS — beside a mark
    // that settles nothing.
    const row = container.querySelector<HTMLElement>('[data-testid="bios-file-row"]');
    expect(row?.textContent).toContain("Dreamcast boot ROM");
    expect(row?.textContent).toContain(
      "Tender could not look into bios/, so whether dc_boot.bin is there is not known",
    );
    expect(row?.querySelector('[data-testid="disk-mark"]')?.textContent).toBe("?");
  });

  it.each([
    ["mismatch", "wrong file — not the image Beetle PSX expects"],
    ["refused", "refused — Beetle PSX does not accept a file of this size"],
  ] as const)("names the emulator on a %s required file, in the platform page's words", (checked, words) => {
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 1,
          local_count: 1,
          ratio_server_count: 1,
          ratio_local_count: 1,
          all_downloaded: true,
          required_count: 1,
          required_downloaded: 0,
          required_withheld: 0,
          active_core_label: "Beetle PSX",
          files: [
            {
              file_name: "scph5501.bin",
              downloaded: true,
              local_path: "",
              declared_path: "scph5501.bin",
              description: "",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              declared_kind: "file",
              satisfied: false,
              checked,
            },
          ],
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
      />,
    );
    // In the Contents cell, once: a note the cell already says is not repeated under the row.
    const row = container.querySelector<HTMLElement>('[data-testid="bios-file-row"]');
    expect(row?.textContent.split(words)).toHaveLength(2);
  });

  it("draws a missing optional file's mark grey, never amber, where another emulator requires it", () => {
    // Amber means only a one-of group whose images cover some regions. What
    // another emulator demands is shown when that emulator is chosen,
    // so a file this launch does not need is grey here whoever else needs it.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 1,
          local_count: 0,
          ratio_server_count: 1,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          required_withheld: 0,
          files: [
            {
              file_name: "scph5500.bin",
              downloaded: false,
              local_path: "",
              declared_path: "scph5500.bin",
              description: "",
              wanted: "optional",
              required_by_active: false,
              on_server: true,
              declared_kind: "file",
              satisfied: false,
            },
          ],
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
      />,
    );
    const mark = container.querySelector<HTMLElement>('[data-testid="disk-mark"]');
    expect(mark?.textContent).toBe("✗");
    expect(mark?.style.color).toBe("#8f98a0");
  });

  it("draws a folder row whose contents could not be read grey, never green", () => {
    // The colours are the pane's own, so they are pinned here rather than
    // through the panel: `downloaded` is true for a folder — something is at the
    // destination — and green over a verdict nothing established is the false
    // all-clear this row exists to avoid. Grey is the colour of "not
    // established"; amber is no mark of a row's.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
          ratio_server_count: 0,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 1,
          required_downloaded: 0,
          required_withheld: 1,
          files: [
            {
              file_name: "bios",
              downloaded: true,
              local_path: "",
              declared_path: "pcsx2/bios",
              description: "'pcsx2/bios' folder",
              wanted: "needed",
              required_by_active: true,
              on_server: false,
              declared_kind: "directory",
              satisfied: null,
              caveats: ["firmware-scan-incomplete"],
            },
          ],
        }}
        biosLevel="unknown"
        coreInfo={coreInfo}
      />,
    );
    const mark = container.querySelector<HTMLElement>('[data-testid="disk-mark"]');
    expect(mark?.textContent).toBe("?");
    expect(mark?.style.color).toBe("#8f98a0");
    expect(container.innerHTML).not.toContain("#d4a72c");
    expect(container.innerHTML).not.toContain("#5ba32b");
  });

  it.each([
    ["green over a folder holding an image", true, "✓", "#5ba32b", ["firmware-image-identified"], ["Europe"]],
    ["red over a folder holding none", false, "✗", "#d94126", ["firmware-directory-holds-no-image"], []],
  ] as const)("draws %s", (_name, satisfied, glyph, colour, caveats, images) => {
    // The folder's verdict is what it HOLDS, so it takes the same two colours a
    // declared file does — the amber above is for a verdict, not for a folder.
    //
    // Asserted on the file row's own mark, because the header dot beside it
    // emits the same hex for these two levels.
    const { container } = render(
      <BiosTabWithState
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
          ratio_server_count: 0,
          ratio_local_count: 0,
          all_downloaded: false,
          required_count: 1,
          required_downloaded: satisfied ? 1 : 0,
          required_withheld: 0,
          files: [
            {
              file_name: "bios",
              downloaded: true,
              local_path: "",
              declared_path: "pcsx2/bios",
              description: "'pcsx2/bios' folder",
              wanted: "needed",
              required_by_active: true,
              on_server: false,
              declared_kind: "directory",
              satisfied,
              caveats: [...caveats],
              images: [...images],
            },
          ],
        }}
        biosLevel={satisfied ? "ok" : "missing"}
        coreInfo={coreInfo}
      />,
    );
    const row = container.querySelector('[data-testid="bios-file-row"]');
    const mark = row?.querySelector<HTMLElement>('[data-testid="disk-mark"]');
    expect(mark?.textContent).toBe(glyph);
    expect(mark?.style.color).toBe(colour);
    expect(row?.innerHTML).not.toContain("#d4a72c");
  });

  describe("which rows the pane shows", () => {
    const row = (file_name: string, over: Partial<BiosFileStatus> = {}): BiosFileStatus => ({
      file_name,
      downloaded: false,
      local_path: "",
      declared_path: file_name,
      description: "",
      wanted: "optional",
      required_by_active: false,
      on_server: false,
      declared_kind: "file",
      satisfied: false,
      ...over,
    });

    const statusWith = (files: BiosFileStatus[]): BiosStatus => ({
      needs_bios: true,
      server_count: 20,
      local_count: 1,
      ratio_server_count: 20,
      ratio_local_count: 1,
      all_downloaded: false,
      required_count: 0,
      required_downloaded: 0,
      required_withheld: 0,
      files,
    });

    const namesIn = (root: Element | null): string[] =>
      [...(root?.querySelectorAll('[data-testid="bios-file-name"]') ?? [])].map((el) => el.textContent);

    it("lists every row of the shown emulator, as the platform page does", () => {
      // Flycast declares the arcade BIOSes it also emulates Naomi and AtomisWave
      // with: not required for the launch, not present, not in the library —
      // each is a row of its own all the same.
      const arcade = ["airlbios.zip", "naomi.zip"].map((name) => row(name, { declared_path: `dc/${name}` }));
      const { container } = render(
        <BiosTabWithState
          biosStatus={statusWith([row("dc_boot.bin", { required_by_active: true }), ...arcade])}
          biosLevel="ok"
          coreInfo={coreInfo}
        />,
      );
      expect(namesIn(container)).toEqual(["dc_boot.bin", "dc/airlbios.zip", "dc/naomi.zip"]);
      expect(container.textContent).not.toContain("All files are on the platform page");
    });

    it("says on a row what the latest press on the page did to it, until the page is left", async () => {
      const place = "/home/deck/retrodeck/bios/dc_boot.bin";
      const backup = "/d/backups/bios/2026-10-22_10-00-00/t/bios/dc/dc_boot.bin";
      vi.mocked(backend.downloadPlatformFirmwareFile).mockResolvedValue({
        success: true,
        message: "Downloaded dc_boot.bin",
        downloaded: 1,
        backups: [backup],
        link_failures: [{ place, file_name: "dc_boot.bin", reason: "Permission denied" }],
      });
      const files = [row("dc_boot.bin", { required_by_active: true, local_path: place, on_server: true })];
      const tab = () => (
        <BiosTabWithState biosStatus={statusWith(files)} biosLevel="missing" coreInfo={coreInfo} platformSlug="dc" />
      );
      const first = render(tab());
      const download = [...first.container.querySelectorAll("button")].find((b) => b.textContent === "Download");
      await act(async () => {
        fireEvent.click(download as HTMLElement);
      });

      const rowEl = first.container.querySelector('[data-testid="bios-file-row"]');
      expect(rowEl?.textContent).toContain(`Could not place a link at ${place}: Permission denied`);
      // What the download moved out of the store is said under the buttons, as on the platform page.
      expect(first.getByTestId("status-bios").textContent).toBe(
        `A different dc_boot.bin was already in Tender's firmware store. It was moved to ${backup}.`,
      );

      first.unmount();
      const again = render(tab());
      expect(again.container.textContent).not.toContain("Could not place a link");
    });

    describe("while another tab of the game page is shown", () => {
      // The panel holds the tab's state and Steam's tabbed page unmounts the pane
      // whenever another tab is shown, as the stand-in for it does here.
      const place = "/home/deck/retrodeck/bios/dc_boot.bin";
      const files = [row("dc_boot.bin", { required_by_active: true, local_path: place, on_server: true })];
      const GamePage: FC = () => {
        const [active, setActive] = useState("bios");
        const status = statusWith(files);
        const tabState = useBiosTab({ biosStatus: status, platformSlug: "dc" });
        return (
          <GamePageTabs
            tabs={[
              { id: "info", title: "GAME INFO", content: <div>game info</div> },
              {
                id: "bios",
                title: "BIOS",
                content: <BiosTab biosStatus={status} biosLevel="missing" coreInfo={coreInfo} tabState={tabState} />,
              },
            ]}
            activeTab={active}
            onShowTab={setActive}
          />
        );
      };
      const show = (container: HTMLElement, tab: "info" | "bios") =>
        fireEvent.click(container.querySelector(`[data-testid="tab-${tab}"]`) as HTMLElement);
      const download = (container: HTMLElement) =>
        [...container.querySelectorAll("button")].find((b) => b.textContent === "Download") as HTMLElement;

      it("keeps a press under way, and shows it running when the tab is shown again", async () => {
        let finish!: (answer: { success: true; message: string; downloaded: number }) => void;
        vi.mocked(backend.downloadPlatformFirmwareFile).mockReturnValue(
          new Promise((resolve) => {
            finish = resolve;
          }),
        );
        const { container } = render(<GamePage />);
        await act(async () => {
          fireEvent.click(download(container));
        });
        expect(container.querySelector('[data-testid="bios-file-row"] [data-testid="spinner"]')).not.toBeNull();

        show(container, "info");
        expect(container.querySelector('[data-testid="bios-file-row"]')).toBeNull();
        show(container, "bios");
        expect(container.querySelector('[data-testid="bios-file-row"] [data-testid="spinner"]')).not.toBeNull();
        await act(async () => {
          finish({ success: true, message: "", downloaded: 1 });
        });
      });

      it("keeps the line a press left, and shows it when the tab is shown again", async () => {
        const backup = "/d/backups/bios/2026-10-22_10-00-00/t/bios/dc/dc_boot.bin";
        vi.mocked(backend.downloadPlatformFirmwareFile).mockResolvedValue({
          success: true,
          message: "Downloaded dc_boot.bin",
          downloaded: 1,
          backups: [backup],
        });
        const { container, getByTestId } = render(<GamePage />);
        await act(async () => {
          fireEvent.click(download(container));
        });
        const line = `A different dc_boot.bin was already in Tender's firmware store. It was moved to ${backup}.`;
        expect(getByTestId("status-bios").textContent).toBe(line);

        show(container, "info");
        show(container, "bios");
        expect(getByTestId("status-bios").textContent).toBe(line);
      });
    });

    it("lists server files nothing asks for in the library's own section, in the column beside the emulator's rows", () => {
      const nobodys = [0, 1].map((i) => row(`extra${i}.bin`, { wanted: "not_needed", on_server: true }));
      const { container, getByTestId } = render(
        <BiosTabWithState
          biosStatus={statusWith([row("dc_boot.bin", { required_by_active: true }), ...nobodys])}
          biosLevel="ok"
          coreInfo={coreInfo}
        />,
      );
      expect(namesIn(getByTestId("bios-main-column"))).toEqual(["dc_boot.bin"]);
      const library = getByTestId("bios-library-column");
      expect(namesIn(library)).toEqual(["extra0.bin", "extra1.bin"]);
      expect(library.textContent).toContain("IN YOUR ROMM LIBRARY, ASKED FOR BY NO EMULATOR HERE");
      expect(container.textContent).toContain("not required");
      expect(container.textContent).not.toContain("on server no installed emulator asks for");
    });
  });

  describe("a one-of group on the game page", () => {
    // Beetle PSX's three region images: one requirement, judged for the game's
    // own regions wherever RomM names one that maps.
    const beetle: CoreInfo = {
      active_core: "mednafen_psx_libretro.so",
      active_core_label: "Beetle PSX",
      platform_core_label: null,
      has_game_override: false,
      emulator_data_available: true,
      emulator_data_reason: null,
      emulator_source: { kind: "retrodeck", starts_games: true },
      platform_system: null,
      emulators: [libretroEmu("mednafen_psx_libretro", "Beetle PSX", true)],
    };
    const option = (file_name: string, region: string, here: boolean): BiosFileStatus => ({
      file_name,
      downloaded: here,
      local_path: "",
      declared_path: file_name,
      description: "",
      wanted: "optional",
      required_by_active: false,
      one_of: { regions: [region], every_region: false },
      on_server: true,
      declared_kind: "file",
      satisfied: here,
    });
    const files = [
      option("scph5500.bin", "ntsc-j", false),
      option("scph5501.bin", "ntsc-u", true),
      option("scph5502.bin", "pal", false),
    ];
    const forTheGame = (state: "met" | "unmet", covered: string[], missing: string[], game: string[]): BiosStatus => ({
      needs_bios: true,
      server_count: 3,
      local_count: 1,
      ratio_server_count: 3,
      ratio_local_count: 1,
      all_downloaded: false,
      required_count: 1,
      required_downloaded: state === "met" ? 1 : 0,
      required_withheld: 0,
      one_of_groups: [
        {
          state,
          covered,
          missing,
          unchecked: [],
          game_regions: game,
          regions: ["ntsc-j", "ntsc-u", "pal"],
          options: [
            { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: false },
            { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: true },
            { file_name: "scph5502.bin", regions: ["pal"], satisfied: false },
          ],
        },
      ],
      active_core_label: "Beetle PSX",
      files,
    });
    const renderFor = (status: BiosStatus, level: "ok" | "missing") =>
      render(<BiosTabWithState biosStatus={status} biosLevel={level} coreInfo={beetle} />).container;
    const sectionTitles = (container: HTMLElement): string[] =>
      [...container.querySelectorAll<HTMLElement>(".romm-panel-section-title")].map((title) => title.textContent);

    it("names the regions the group states nothing for on the page of a game with no clear region", () => {
      // A World release: no game region narrows the group, so the platform's
      // verdict stands — and with it the clause naming Europe.
      const status = forTheGame("met", ["ntsc-u"], ["ntsc-j"], []);
      const [group] = status.one_of_groups ?? [];
      const open = { ...group!, state: "partial" as const, unchecked: ["pal"], unstated: ["pal"] };
      const container = render(
        <BiosTabWithState
          biosStatus={{ ...status, required_downloaded: 0, one_of_groups: [open] }}
          biosLevel="unknown"
          coreInfo={beetle}
        />,
      ).container;

      expect(container.textContent).toContain(
        "— for Europe discs Beetle PSX states no BIOS image, so whether they start could not be established",
      );
    });

    it("says the game's own region is covered", () => {
      const container = renderFor(forTheGame("met", ["ntsc-u"], [], ["ntsc-u"]), "ok");

      expect(container.textContent).toContain(
        "The BIOS image Beetle PSX needs for this game's region (USA) is in place",
      );
    });

    it("names the file the game's own region needs, and that it is missing", () => {
      const container = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");

      expect(container.textContent).toContain(
        "Beetle PSX needs scph5500.bin to start this game (Japan) — it is missing",
      );
    });

    it("lists the group above the file list, one line per region, the game's marked", () => {
      const container = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");

      const block = container.querySelector<HTMLElement>('[data-testid="bios-group"]');
      const lines = [...(block?.querySelectorAll<HTMLElement>(".romm-panel-group-line") ?? [])];
      expect(lines.map((line) => line.textContent)).toEqual([
        "Japan · scph5500.bin · missing ← this game's region",
        "USA · scph5501.bin · in place",
        "Europe · scph5502.bin · missing",
      ]);
      expect(lines.map((line) => line.querySelector<HTMLElement>(".romm-status-dot")?.style.backgroundColor)).toEqual([
        "#d94126",
        "#5ba32b",
        "#8f98a0",
      ]);
      // Above the table, and the table keeps every row.
      const table = container.querySelector('[data-testid="bios-table-header"]');
      expect(block?.compareDocumentPosition(table as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(container.querySelectorAll('[data-testid="bios-file-row"]')).toHaveLength(3);
    });

    it("words an image that is there but not met by what it is, in the block and on its row", () => {
      const status = forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]);
      status.files = (status.files ?? []).map((row) =>
        row.file_name === "scph5500.bin" ? { ...row, downloaded: true, checked: "mismatch" as const } : row,
      );
      const container = renderFor(status, "missing");

      const block = container.querySelector<HTMLElement>('[data-testid="bios-group"]')?.textContent ?? "";
      expect(block).toContain("Japan · scph5500.bin · wrong file");
      // The row says what the file is in its Contents cell; its line under the
      // name is its need, which no verdict changes.
      const row = [...container.querySelectorAll<HTMLElement>('[data-testid="bios-file-row"]')].find(
        (el) => el.querySelector('[data-testid="bios-file-name"]')?.textContent === "scph5500.bin",
      );
      expect(row?.textContent).toContain("wrong file — not the image");
      expect(row?.querySelector('[data-testid="disk-mark"]')?.textContent).toBe("✗");
      expect(container.textContent).toContain("one of these · for Japan discs");
      expect(container.textContent).not.toContain("· ✗");
      expect(block).not.toContain("scph5500.bin · missing");
    });

    it("heads the group with a subheading in the section label's style, the table following it", () => {
      const container = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");

      // Written in sentence case; the class upper-cases it, as it does "BIOS".
      expect(sectionTitles(container)).toEqual(["BIOS", "Beetle PSX · one image per disc region"]);
      const block = container.querySelector<HTMLElement>('[data-testid="bios-group"]');
      const table = container.querySelector('[data-testid="bios-table-header"]');
      expect(block?.compareDocumentPosition(table as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      // The intro sentence the subheading replaced is gone.
      expect(container.textContent).not.toContain("needs one BIOS image per disc region");
    });

    it("indents the group's lines under their subheading", () => {
      const container = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");

      const lines = [...container.querySelectorAll<HTMLElement>(".romm-panel-group-line")];
      expect(lines).toHaveLength(3);
      for (const line of lines) expect(line.style.paddingLeft).toBe("8px");
    });

    it("lists the regions in the group's order and marks the game's where it stands", () => {
      const container = renderFor(forTheGame("unmet", [], ["pal"], ["pal"]), "missing");

      expect([...container.querySelectorAll(".romm-panel-group-line")].map((line) => line.textContent)).toEqual([
        "Japan · scph5500.bin · missing",
        "USA · scph5501.bin · in place",
        "Europe · scph5502.bin · missing ← this game's region",
      ]);
    });

    it("lays out a group of any system from the payload alone", () => {
      // An invented system: its label, regions and files come off the data.
      const invented: BiosStatus = {
        ...forTheGame("met", ["east"], [], ["east"]),
        active_core_label: "Arcadia",
        files: [],
        one_of_groups: [
          {
            state: "met",
            covered: ["east"],
            missing: [],
            unchecked: [],
            game_regions: ["east"],
            regions: ["north", "east"],
            options: [
              { file_name: "north.rom", regions: ["north"], satisfied: false },
              { file_name: "east.rom", regions: ["east"], satisfied: true },
            ],
          },
        ],
      };
      const container = renderFor(invented, "ok");

      expect(sectionTitles(container)).toContain("Arcadia · one image per disc region");
      expect([...container.querySelectorAll(".romm-panel-group-line")].map((line) => line.textContent)).toEqual([
        "NORTH · north.rom · missing",
        "EAST · east.rom · in place ← this game's region",
      ]);
    });

    it("says which image starts the game where one found in the folder serves every region", () => {
      const swanstation: BiosStatus = {
        ...forTheGame("met", ["ntsc-u"], [], ["ntsc-u"]),
        active_core_label: "SwanStation",
        // The folder search identified the image: its row says so.
        files: [
          {
            ...option("scph1001.bin", "ntsc-u", true),
            one_of: { regions: ["ntsc-j", "ntsc-u", "pal"], every_region: true },
            caveats: ["firmware-image-identified"],
          },
        ],
        one_of_groups: [
          {
            state: "met",
            covered: ["ntsc-u"],
            missing: [],
            unchecked: [],
            game_regions: ["ntsc-u"],
            regions: ["ntsc-j", "ntsc-u", "pal"],
            options: [{ file_name: "scph1001.bin", regions: ["ntsc-j", "ntsc-u", "pal"], satisfied: true }],
          },
        ],
      };
      const container = renderFor(swanstation, "ok");

      expect(container.textContent).toContain(
        "SwanStation starts this game with scph1001.bin — found in the BIOS folder, it serves every region",
      );
      const lines = [...container.querySelectorAll(".romm-panel-group-line")].map((line) => line.textContent);
      expect(lines).toEqual(["every region · scph1001.bin · in place ← this game's region"]);
      expect(sectionTitles(container)).toContain("SwanStation · one image per disc region");
    });

    it("lists every group the launching emulator states, each under its own subheading", () => {
      const status = forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]);
      const [beetleGroup] = status.one_of_groups ?? [];
      // A second group of its own, unmet for the game's region like the first.
      const second = {
        ...beetleGroup!,
        regions: ["ntsc-j", "pal"],
        options: [
          { file_name: "extra.rom", regions: ["ntsc-j"], satisfied: false },
          { file_name: "extra-pal.rom", regions: ["pal"], satisfied: false },
        ],
      };
      const container = renderFor({ ...status, one_of_groups: [beetleGroup!, second] }, "missing");

      const block = container.querySelector<HTMLElement>('[data-testid="bios-group"]');
      expect(
        sectionTitles(container).filter((title) => title === "Beetle PSX · one image per disc region"),
      ).toHaveLength(2);
      expect([...(block?.querySelectorAll(".romm-panel-group-line") ?? [])].map((line) => line.textContent)).toContain(
        "Japan · extra.rom · missing ← this game's region",
      );
    });

    it("shows no group block and no subheadings where the launching emulator states no group", () => {
      const container = renderFor({ ...forTheGame("met", ["ntsc-u"], [], ["ntsc-u"]), one_of_groups: [] }, "ok");

      expect(container.querySelector('[data-testid="bios-group"]')).toBeNull();
      expect(sectionTitles(container)).toEqual(["BIOS"]);
    });
  });

  describe("a stale configured image name", () => {
    // LRPS2 set to open a file its folder does not hold lists the folder
    // instead: a neutral line, and the row reads exactly as its verdict says.
    const folder = (stale: MissingConfiguredImage | null): BiosStatus => ({
      needs_bios: true,
      server_count: 0,
      local_count: 0,
      ratio_server_count: 0,
      ratio_local_count: 0,
      all_downloaded: true,
      required_count: 1,
      required_downloaded: 1,
      required_withheld: 0,
      files: [
        {
          file_name: "bios",
          downloaded: true,
          local_path: "",
          declared_path: "pcsx2/bios",
          description: "",
          wanted: "needed",
          required_by_active: true,
          on_server: false,
          declared_kind: "directory",
          satisfied: true,
          images: ["USA     v02.00(14/06/2004)  Console"],
          caveats: stale ? ["firmware-configured-image-missing"] : [],
          missing_configured_image: stale,
        },
      ],
    });
    const lrps2: CoreInfo = { ...coreInfo, active_core: "pcsx2_libretro.so", active_core_label: "LRPS2" };

    it("names the setting under the folder and leaves its mark and sentence alone", () => {
      const withLine = render(
        <BiosTabWithState
          biosStatus={folder({ emulator_label: "LRPS2", file_name: "scph10000.bin" })}
          biosLevel="ok"
          coreInfo={lrps2}
        />,
      ).container;
      const without = render(<BiosTabWithState biosStatus={folder(null)} biosLevel="ok" coreInfo={lrps2} />).container;

      expect(withLine.textContent).toContain(
        "LRPS2's settings name scph10000.bin, which is not here — it uses another BIOS from this folder instead.",
      );
      expect(without.textContent).not.toContain("LRPS2's settings");
      const mark = (container: HTMLElement) =>
        container.querySelector<HTMLElement>('[data-testid="bios-file-row"] [data-testid="disk-mark"]');
      expect(mark(withLine)?.textContent).toBe(mark(without)?.textContent);
      expect(mark(withLine)?.style.color).toBe(mark(without)?.style.color);
      expect(mark(withLine)?.textContent).toBe("✓");
      expect(mark(withLine)?.style.color).toBe("#5ba32b");
      expect(withLine.querySelector(".romm-panel-value")?.textContent).toBe(
        without.querySelector(".romm-panel-value")?.textContent,
      );
    });

    it("names the emulator by every label the selector gives it, whichever label launches", () => {
      const status: BiosStatus = {
        ...folder({ emulator_label: "LRPS2", file_name: "scph10000.bin" }),
        active_core_label: "PCSX2",
        emulator_tabs: [
          { emulator: "pcsx2_libretro.so", label: "LRPS2 / PCSX2", launching: true, requirements_met: true },
        ],
      };
      const { container } = render(
        <BiosTabWithState biosStatus={status} biosLevel="ok" coreInfo={{ ...lrps2, active_core_label: "PCSX2" }} />,
      );

      expect(container.textContent).toContain(
        "ℹ LRPS2 / PCSX2's settings name scph10000.bin, which is not here — it uses another BIOS from this folder instead.",
      );
      expect(container.textContent).not.toContain("ℹ LRPS2's settings");
    });
  });

  describe("one emulator shown at a time", () => {
    const own = (
      file_name: string,
      declared_by: string | null,
      over: Partial<BiosFileStatus> = {},
    ): BiosFileStatus => ({
      file_name,
      downloaded: false,
      local_path: "",
      declared_path: file_name,
      description: "",
      wanted: "needed",
      required_by_active: true,
      declared_kind: "file",
      satisfied: false,
      declared_by,
      ...over,
    });

    const saturn = (): BiosStatus => ({
      needs_bios: true,
      server_count: 1,
      local_count: 0,
      all_downloaded: false,
      required_count: 1,
      required_downloaded: 0,
      active_core_label: "YabaSanshiro",
      files: [own("saturn_bios.bin", "yabasanshiro_libretro.so")],
      emulator_tabs: [
        { emulator: "yabasanshiro_libretro.so", label: "YabaSanshiro", launching: true, requirements_met: false },
        {
          emulator: "kronos_libretro.so",
          label: "Kronos",
          launching: false,
          requirements_met: true,
          rows: [
            own("saturn_bios.bin", "kronos_libretro.so", {
              declared_path: "kronos/saturn_bios.bin",
              satisfied: true,
              downloaded: true,
            }),
          ],
        },
      ],
    });

    const renderSaturn = () =>
      render(<BiosTabWithState biosStatus={saturn()} biosLevel="missing" coreInfo={coreInfo} />);
    const entries = (container: HTMLElement) =>
      [...container.querySelectorAll<HTMLElement>('[data-testid="emulator-entry"]')].map((entry) => entry.textContent);
    const shown = (container: HTMLElement) =>
      `${container.querySelector('[data-testid="emulator-shown"]')?.textContent} ` +
      `${container.querySelector('[data-testid="emulator-position"]')?.textContent}`;
    const entryButton = (container: HTMLElement, label: string) =>
      [...container.querySelectorAll<HTMLElement>('[data-testid="emulator-entry"]')]
        .find((entry) => entry.textContent === label)
        ?.closest("button") as HTMLElement;
    const chooseKronos = (container: HTMLElement) => fireEvent.click(entryButton(container, "Kronos"));
    const arrow = (container: HTMLElement, which: "Previous emulator" | "Next emulator") =>
      container.querySelector(`button[aria-label="${which}"]`) as HTMLElement;
    const names = (container: Element) =>
      [...container.querySelectorAll('[data-testid="bios-file-name"]')].map((el) => el.textContent);

    it("shows the launching emulator first, marked as the one that launches, and places every emulator in the carousel", () => {
      const { container } = renderSaturn();
      expect(shown(container)).toBe("YabaSanshiro 1 / 2");
      expect(entries(container)).toEqual(["YabaSanshiro", "Kronos"]);
      const outlined = container.querySelectorAll('[data-testid="emulator-entry"][data-shown="true"]');
      expect([...outlined].map((entry) => entry.textContent)).toEqual(["YabaSanshiro"]);
    });

    it("centres the shown emulator's dot and name on the row, with nothing beside them in that column", () => {
      const { container } = renderSaturn();
      const shownName = container.querySelector<HTMLElement>('[data-testid="emulator-shown"]');
      expect(shownName?.textContent).toBe("YabaSanshiro");
      const row = shownName?.parentElement as HTMLElement;
      expect(row.style.display).toBe("grid");
      // Two equal sides around the centred column.
      const [left, centre, right] = row.style.gridTemplateColumns.split(/ (?![^(]*\))/);
      expect(left).toBe(right);
      expect(centre).not.toBe(left);
      expect(row.children[1]).toBe(shownName);
      expect(row.children[2]?.textContent).toBe("1 / 2▶R2");
      expect(container.textContent).not.toContain("launches");
    });

    it("shows L2 beside the previous arrow and R2 beside the next one", () => {
      const { container } = renderSaturn();
      const glyphs = [...container.querySelectorAll('[data-testid="trigger-glyph"]')];
      expect(glyphs.map((glyph) => glyph.textContent)).toEqual(["L2", "R2"]);
      expect(glyphs[0]?.nextElementSibling).toBe(arrow(container, "Previous emulator"));
      expect(glyphs[1]?.previousElementSibling).toBe(arrow(container, "Next emulator"));
    });

    it("spans the left column, aligned with the sentence above it and the table below it", () => {
      const { container, getByTestId } = renderSaturn();
      const selector = container.querySelector<HTMLElement>('[data-testid="emulator-selector"]');
      expect(getByTestId("bios-main-column").contains(selector)).toBe(true);
      expect(selector?.style.maxWidth).toBe("");
      expect(selector?.style.margin).toBe("");
      // One gutter for the three: the sentence's block, the selector and the table's rows.
      const sentence = container.querySelector(".romm-panel-status-inline")?.parentElement as HTMLElement;
      const row = container.querySelector<HTMLElement>('[data-testid="bios-file-row"]');
      expect(sentence.style.padding).toBe("0px 16px");
      expect(selector?.style.padding).toBe("4px 16px 6px");
      expect(row?.style.padding).toBe("2px 16px");
    });

    it("sets the launching emulator's name in the accent and a not-installed one's in red, red over blue", () => {
      const status = saturn();
      status.emulator_tabs = (status.emulator_tabs ?? []).map((entry) =>
        entry.launching ? entry : { ...entry, installed: false },
      );
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      const topName = () => container.querySelector<HTMLElement>('[data-testid="emulator-name"]')?.style.color;
      const entryNames = () =>
        [...container.querySelectorAll<HTMLElement>('[data-testid="emulator-entry-name"]')].map((el) => el.style.color);
      // YabaSanshiro launches and is installed; Kronos is not installed.
      expect(topName()).toBe("#1a9fff");
      expect(entryNames()).toEqual(["#1a9fff", "#d94126"]);
      chooseKronos(container);
      expect(topName()).toBe("#d94126");
      expect(container.textContent).not.toContain("not installed");
    });

    it("sets a launching emulator that is not installed in red, and leaves a plain one uncoloured", () => {
      const status = saturn();
      status.emulator_tabs = (status.emulator_tabs ?? []).map((entry) =>
        entry.launching ? { ...entry, installed: false } : entry,
      );
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      const entryNames = [...container.querySelectorAll<HTMLElement>('[data-testid="emulator-entry-name"]')].map(
        (el) => el.style.color,
      );
      expect(entryNames).toEqual(["#d94126", ""]);
    });

    it("outlines the launching emulator's entry in the accent, shown or not, and the shown one otherwise in grey", () => {
      const { container } = renderSaturn();
      const outline = (label: string) => {
        const style = entryButton(container, label).parentElement?.style;
        return style?.outlineStyle === "none" ? "none" : style?.outlineColor;
      };
      expect([outline("YabaSanshiro"), outline("Kronos")]).toEqual(["#1a9fff", "none"]);
      chooseKronos(container);
      expect([outline("YabaSanshiro"), outline("Kronos")]).toEqual(["#1a9fff", "#dcdedf"]);
    });

    it("carries no entry for a launching emulator nothing identifies, and opens on the first one there is", () => {
      const status = saturn();
      status.emulator_tabs = [
        {
          emulator: "eden",
          label: "Eden (Standalone)",
          launching: false,
          requirements_met: false,
          installed: false,
          rows: [],
        },
        {
          emulator: "ryubing",
          label: "Ryubing (Standalone)",
          launching: false,
          requirements_met: null,
          installed: false,
          rows: [],
        },
      ];
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      expect(entries(container)).toEqual(["Eden (Standalone)", "Ryubing (Standalone)"]);
      expect(shown(container)).toBe("Eden (Standalone) 1 / 2");
      expect(container.textContent).not.toContain("launches");
    });

    it("dots each emulator with its own verdict, in the row and in the carousel", () => {
      const { container } = renderSaturn();
      const dots = [
        ...container.querySelectorAll<HTMLElement>('[data-testid="emulator-entry"] [data-testid="emulator-dot"]'),
      ];
      expect(dots.map((dot) => dot.style.backgroundColor)).toEqual(["#d94126", "#5ba32b"]);
      chooseKronos(container);
      const rowDot = container.querySelector<HTMLElement>(
        '[data-testid="emulator-shown"] [data-testid="emulator-dot"]',
      );
      expect(rowDot?.style.backgroundColor).toBe("#5ba32b");
    });

    it("steps with the arrows and wraps around at either end", () => {
      const { container } = renderSaturn();
      fireEvent.click(arrow(container, "Next emulator"));
      expect(shown(container)).toBe("Kronos 2 / 2");
      expect(names(container)).toEqual(["kronos/saturn_bios.bin"]);
      fireEvent.click(arrow(container, "Next emulator"));
      expect(shown(container)).toBe("YabaSanshiro 1 / 2");
      fireEvent.click(arrow(container, "Previous emulator"));
      expect(shown(container)).toBe("Kronos 2 / 2");
    });

    // Steam hands a gamepad button to the focused node and on up through its
    // ancestors; the stub's Focusable listens for the event the same way.
    const pull = (from: Element, button: number) =>
      fireEvent(from, new CustomEvent("decky-button-down", { detail: { button }, bubbles: true }));
    const L2 = 7;
    const R2 = 8;

    // Tender's section on a game page as `rommOverviewBlock` builds it: the scope
    // around it, the BIOS pane in the game page's tabs.
    const gamePage = (isActive: boolean) =>
      render(
        <EmulatorStepScope>
          {() => (
            <GamePageTabs
              tabs={[
                { id: "info", title: "GAME INFO", content: <div>game info</div> },
                {
                  id: "bios",
                  title: "BIOS",
                  content: <BiosTabWithState biosStatus={saturn()} biosLevel="missing" coreInfo={coreInfo} />,
                },
              ]}
              activeTab={isActive ? "bios" : "info"}
              onShowTab={() => {}}
            />
          )}
        </EmulatorStepScope>,
      );
    const tabButton = (container: HTMLElement, label: string) =>
      [...container.querySelectorAll("button")].find((b) => b.textContent === label) as HTMLElement;

    it("steps with L2 and R2 from the tab bar while the BIOS tab is open", () => {
      const { container } = gamePage(true);
      pull(tabButton(container, "GAME INFO"), R2);
      expect(shown(container)).toBe("Kronos 2 / 2");
      pull(tabButton(container, "BIOS"), L2);
      expect(shown(container)).toBe("YabaSanshiro 1 / 2");
    });

    it("lets L2 and R2 go on to Steam while the BIOS tab is closed", () => {
      const { container } = gamePage(false);
      let reachedSteam = false;
      const listener = () => {
        reachedSteam = true;
      };
      container.ownerDocument.body.addEventListener("decky-button-down", listener);
      pull(tabButton(container, "GAME INFO"), R2);
      container.ownerDocument.body.removeEventListener("decky-button-down", listener);
      expect(reachedSteam).toBe(true);
      expect(container.querySelector('[data-testid="emulator-selector"]')).toBeNull();
    });

    describe("where focus goes after a step", () => {
      const settle = () =>
        act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 80));
        });
      /** The focus stop of the table's first row: the row itself, or its button. */
      const firstRowStop = (container: HTMLElement) =>
        (container.querySelector(".romm-bios-row-stop") ?? null) as HTMLElement | null;
      const stepFromList = async (status: BiosStatus) => {
        const view = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
        firstRowStop(view.container)?.focus();
        await act(async () => {
          fireEvent.click(arrow(view.container, "Next emulator"));
        });
        await settle();
        return view.container;
      };

      it("stays on the list where the new emulator lists files", async () => {
        const container = await stepFromList(saturn());
        expect(container.ownerDocument.activeElement).toBe(firstRowStop(container));
      });

      it("goes to the carousel's shown entry where the new emulator lists no file", async () => {
        const status = saturn();
        const kronos = status.emulator_tabs?.[1];
        if (kronos === undefined) throw new Error("fixture has no Kronos entry");
        kronos.rows = [];
        const container = await stepFromList(status);
        expect(firstRowStop(container)).toBeNull();
        const active = container.ownerDocument.activeElement as HTMLElement | null;
        expect(active?.querySelector('[data-testid="emulator-entry"]')?.textContent).toBe("Kronos");
      });
    });

    describe("Replace… on a row", () => {
      const settle = () =>
        act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 80));
        });
      const replaceable = (offered = true): BiosStatus => {
        const status = saturn();
        status.backup_root = "/data/backups/bios";
        status.files = [
          own("cart.bin", "yabasanshiro_libretro.so", { local_path: "/bios/cart.bin" }),
          own("saturn_bios.bin", "yabasanshiro_libretro.so", {
            local_path: "/bios/saturn_bios.bin",
            downloaded: true,
            replace_offered: offered,
            ...(offered ? {} : { satisfied: true, place_link: "linked" as const }),
          }),
          own("mpr.bin", "yabasanshiro_libretro.so", { local_path: "/bios/mpr.bin" }),
        ];
        return status;
      };
      const rowNamed = (container: HTMLElement, name: string) =>
        [...container.querySelectorAll<HTMLElement>('[data-testid="bios-file-row"]')].find(
          (row) => row.querySelector('[data-testid="bios-file-name"]')?.textContent === name,
        ) as HTMLElement;
      const replaceButton = (container: HTMLElement) =>
        [...container.querySelectorAll("button")].find((b) => b.textContent === "Replace…") as HTMLElement | undefined;
      type ConfirmProps = {
        strTitle: string;
        strDescription: string;
        strOKButtonText: string;
        onOK: () => void;
      };
      const confirmation = () => {
        const calls = vi.mocked(showModal).mock.calls;
        return (calls[calls.length - 1]?.[0] as ReactElement<ConfirmProps> | undefined)?.props;
      };
      const renderReplaceable = (status = replaceable()) =>
        render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} platformSlug="saturn" />);

      it("makes every row a focus stop, the one offering it reached as its own Replace… button", () => {
        const { container } = renderReplaceable();
        for (const name of ["cart.bin", "mpr.bin"]) {
          expect(rowNamed(container, name).closest(".romm-bios-row-stop")).not.toBeNull();
        }
        const offering = rowNamed(container, "saturn_bios.bin");
        expect(offering.closest(".romm-bios-row-stop")).toBeNull();
        expect(offering.contains(replaceButton(container) ?? null)).toBe(true);
      });

      it("asks the platform page's confirmation, and replaces only once it is answered", async () => {
        vi.mocked(showModal).mockClear();
        vi.mocked(backend.replaceBiosFile).mockResolvedValue({
          success: true,
          placed: 1,
          left_in_place: 0,
          removed: 0,
          link_failures: [],
          backup_path: "/data/backups/bios/x/saturn_bios.bin",
        });
        const { container } = renderReplaceable();

        fireEvent.click(replaceButton(container) as HTMLElement);

        expect(confirmation()?.strTitle).toBe("Replace saturn_bios.bin?");
        expect(confirmation()?.strDescription).toBe(
          "The file at /bios/saturn_bios.bin goes to /data/backups/bios, and Tender's copy is linked in its place.",
        );
        expect(confirmation()?.strOKButtonText).toBe("Replace");
        expect(backend.replaceBiosFile).not.toHaveBeenCalled();
        await act(async () => {
          confirmation()?.onOK();
        });
        expect(backend.replaceBiosFile).toHaveBeenCalledWith("saturn", "/bios/saturn_bios.bin", null);
        expect(rowNamed(container, "saturn_bios.bin").textContent).toContain(
          "Replaced. The old file is in /data/backups/bios/x/saturn_bios.bin.",
        );
      });

      it("says under the buttons why its replacement failed", async () => {
        vi.mocked(showModal).mockClear();
        vi.mocked(backend.replaceBiosFile).mockResolvedValue({
          success: false,
          reason: "replace_failed",
          message: "the BIOS folder is read-only",
        });
        const { container, getByTestId } = renderReplaceable();
        fireEvent.click(replaceButton(container) as HTMLElement);
        await act(async () => {
          confirmation()?.onOK();
        });

        expect(getByTestId("status-bios").textContent).toBe("Failed to replace the file: the BIOS folder is read-only");
        expect(rowNamed(container, "saturn_bios.bin").textContent).not.toContain("Failed to replace");
      });

      it("leaves the list with a step from the Replace… row, as with a step from its rows", async () => {
        const status = replaceable();
        const kronos = status.emulator_tabs?.[1];
        if (kronos === undefined) throw new Error("fixture has no Kronos entry");
        kronos.rows = [];
        const { container } = renderReplaceable(status);
        replaceButton(container)?.focus();
        await act(async () => {
          fireEvent.click(arrow(container, "Next emulator"));
        });
        await settle();

        expect(container.querySelector('[data-testid="bios-file-row"]')).toBeNull();
        const active = container.ownerDocument.activeElement as HTMLElement | null;
        expect(active?.querySelector('[data-testid="emulator-entry"]')?.textContent).toBe("Kronos");
      });

      it("puts focus back on the row once the re-read shows it linked", async () => {
        vi.mocked(showModal).mockClear();
        vi.mocked(backend.replaceBiosFile).mockResolvedValue({
          success: true,
          placed: 1,
          left_in_place: 0,
          removed: 0,
          link_failures: [],
          backup_path: "/data/backups/bios/x/saturn_bios.bin",
        });
        const { container, rerender } = renderReplaceable();
        replaceButton(container)?.focus();
        fireEvent.click(replaceButton(container) as HTMLElement);
        await act(async () => {
          confirmation()?.onOK();
        });

        rerender(
          <BiosTabWithState biosStatus={replaceable(false)} biosLevel="ok" coreInfo={coreInfo} platformSlug="saturn" />,
        );
        await settle();

        expect(replaceButton(container)).toBeUndefined();
        expect(container.ownerDocument.activeElement).toBe(
          rowNamed(container, "saturn_bios.bin").closest(".romm-bios-row-stop"),
        );
      });
    });

    it("chooses an emulator only when its entry is pressed, never when focus lands on it", () => {
      const { container } = renderSaturn();
      fireEvent.focus(entryButton(container, "Kronos"));
      expect(shown(container)).toBe("YabaSanshiro 1 / 2");
      chooseKronos(container);
      expect(shown(container)).toBe("Kronos 2 / 2");
      const outlined = container.querySelectorAll('[data-testid="emulator-entry"][data-shown="true"]');
      expect([...outlined].map((entry) => entry.textContent)).toEqual(["Kronos"]);
    });

    it("takes every dot from the answer it is given, so a fresh answer after a download recolours it", () => {
      const before = saturn();
      const { container, rerender } = render(
        <BiosTabWithState biosStatus={before} biosLevel="missing" coreInfo={coreInfo} />,
      );
      const after = saturn();
      after.emulator_tabs = (after.emulator_tabs ?? []).map((entry) =>
        entry.launching ? { ...entry, requirements_met: true } : entry,
      );
      rerender(<BiosTabWithState biosStatus={after} biosLevel="ok" coreInfo={coreInfo} />);

      const rowDot = container.querySelector<HTMLElement>(
        '[data-testid="emulator-shown"] [data-testid="emulator-dot"]',
      );
      expect(rowDot?.style.backgroundColor).toBe("#5ba32b");
      const entryDot = container.querySelector<HTMLElement>(
        '[data-testid="emulator-entry"][data-shown="true"] [data-testid="emulator-dot"]',
      );
      expect(entryDot?.style.backgroundColor).toBe("#5ba32b");
      expect(container.querySelector(".romm-status-dot")?.getAttribute("style")).toContain("#5ba32b");
    });

    it("opens on the launching emulator's rows", () => {
      expect(names(renderSaturn().container)).toEqual(["saturn_bios.bin"]);
    });

    it("shows another emulator's own rows unmuted and leaves the header the launching emulator's", () => {
      const { container } = renderSaturn();
      const header = container.querySelector(".romm-panel-value")?.textContent;
      chooseKronos(container);

      expect(names(container)).toEqual(["kronos/saturn_bios.bin"]);
      const row = container.querySelector<HTMLElement>('[data-testid="bios-file-row"]');
      expect(row?.style.opacity).toBe("");
      expect(row?.querySelector<HTMLElement>('[data-testid="disk-mark"]')?.style.color).toBe("#5ba32b");
      expect(container.querySelector(".romm-panel-value")?.textContent).toBe(header);
    });

    it("keeps a row another emulator declares off the launching emulator's list", () => {
      const status = saturn();
      status.files = [...(status.files ?? []), own("mpr-17933.bin", "kronos_libretro.so")];
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      expect(names(container)).toEqual(["saturn_bios.bin"]);
    });

    it("draws no selector where only one emulator answered", () => {
      const status = { ...saturn(), emulator_tabs: (saturn().emulator_tabs ?? []).slice(0, 1) };
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      expect(container.querySelectorAll('[data-testid="emulator-selector"]')).toHaveLength(0);
    });

    it("keeps the library's own section whichever emulator is shown", () => {
      const status = saturn();
      status.files = [
        ...(status.files ?? []),
        own("hisaturn.bin", null, { wanted: "not_needed", required_by_active: false }),
      ];
      const { container } = render(<BiosTabWithState biosStatus={status} biosLevel="missing" coreInfo={coreInfo} />);
      expect(names(container)).toEqual(["saturn_bios.bin", "hisaturn.bin"]);

      chooseKronos(container);

      expect(names(container)).toEqual(["kronos/saturn_bios.bin", "hisaturn.bin"]);
      expect(container.textContent).toContain("IN YOUR ROMM LIBRARY, ASKED FOR BY NO EMULATOR HERE");
    });
  });

  describe("the platform page's table and buttons, on a game page", () => {
    const psxFile = (file_name: string, over: Partial<BiosFileStatus> = {}): BiosFileStatus => ({
      file_name,
      downloaded: false,
      local_path: `/bios/${file_name}`,
      declared_path: file_name,
      description: "",
      wanted: "needed",
      required_by_active: true,
      on_server: true,
      declared_kind: "file",
      satisfied: false,
      declared_by: "duckstation",
      ...over,
    });
    /** A download, a replace and a delete in one table, and a row with nothing to press. */
    const psx = (over: Partial<BiosStatus> = {}): BiosStatus => ({
      needs_bios: true,
      server_count: 3,
      local_count: 0,
      all_downloaded: false,
      required_count: 1,
      required_downloaded: 0,
      active_core_label: "DuckStation",
      backup_root: "/data/backups/bios",
      deletable_count: 2,
      files: [
        psxFile("scph5501.bin", { fetch_for_required: true }),
        psxFile("scph5500.bin", {
          required_by_active: false,
          wanted: "optional",
          downloaded: true,
          replace_offered: true,
        }),
        psxFile("scph5502.bin", {
          required_by_active: false,
          wanted: "optional",
          satisfied: true,
          downloaded: true,
          store_file: "scph5502.bin",
          delete_name: "scph5502.bin",
          deletable_count: 1,
        }),
        psxFile("scph1001.bin", { required_by_active: false, wanted: "optional", on_server: false }),
      ],
      emulator_tabs: [
        { emulator: "duckstation", label: "DuckStation", launching: true, requirements_met: false },
        { emulator: "swanstation_libretro.so", label: "SwanStation", launching: false, requirements_met: null },
      ],
      ...over,
    });
    const renderPsx = (status = psx()) =>
      render(
        <BiosTabWithState
          biosStatus={status}
          biosLevel="missing"
          coreInfo={coreInfo}
          platformName="PlayStation"
          platformSlug="psx"
          romId={42}
        />,
      );
    const button = (container: HTMLElement, label: string) =>
      [...container.querySelectorAll("button")].find((b) => b.textContent === label) as HTMLElement | undefined;
    const rowNamed = (container: HTMLElement, name: string) =>
      [...container.querySelectorAll<HTMLElement>('[data-testid="bios-file-row"]')].find(
        (row) => row.querySelector('[data-testid="bios-file-name"]')?.textContent === name,
      ) as HTMLElement;
    const confirm = async () => {
      const calls = vi.mocked(showModal).mock.calls;
      const props = (calls[calls.length - 1]?.[0] as ReactElement<{ onOK: () => void }> | undefined)?.props;
      await act(async () => {
        props?.onOK();
      });
    };

    it("names the launching emulator only in its sentence and its selector, with no column of its own", () => {
      const { container } = renderPsx();
      const titles = [...container.querySelectorAll(".romm-panel-section-title")].map((el) => el.textContent);
      expect(titles).not.toContain("Emulator");
      // The sentence says how the emulator stands; the selector which emulator's
      // files are shown. Nothing else carries the bare name.
      const selector = container.querySelector('[data-testid="emulator-selector"]');
      const bare = [...container.querySelectorAll("*")].filter(
        (el) => el.children.length === 0 && el.textContent === "DuckStation",
      );
      expect(bare.length).toBeGreaterThan(0);
      for (const el of bare) expect(selector?.contains(el)).toBe(true);
    });

    it("carries each row's action as a button of its own — Download, Replace… and Delete", () => {
      const { container } = renderPsx();
      const pressOn = (name: string) =>
        [...rowNamed(container, name).querySelectorAll("button")].map((b) => b.textContent);
      expect(pressOn("scph5501.bin")).toEqual(["Download"]);
      expect(pressOn("scph5500.bin")).toEqual(["Replace…"]);
      expect(pressOn("scph5502.bin")).toEqual(["Delete"]);
      expect(pressOn("scph1001.bin")).toEqual([]);
    });

    it("makes a row without a button a focus stop that does nothing when pressed", () => {
      vi.mocked(showModal).mockClear();
      vi.mocked(backend.downloadPlatformFirmwareFile).mockClear();
      vi.mocked(backend.deleteBiosFile).mockClear();
      vi.mocked(backend.replaceBiosFile).mockClear();
      const { container } = renderPsx();
      const stop = rowNamed(container, "scph1001.bin").closest<HTMLElement>(".romm-bios-row-stop");
      expect(stop?.tagName).toBe("BUTTON");
      fireEvent.click(stop as HTMLElement);
      expect(showModal).not.toHaveBeenCalled();
      expect(backend.downloadPlatformFirmwareFile).not.toHaveBeenCalled();
      expect(backend.deleteBiosFile).not.toHaveBeenCalled();
      expect(backend.replaceBiosFile).not.toHaveBeenCalled();
    });

    it("downloads the shown emulator's required files for that emulator, the launching one included", async () => {
      vi.mocked(backend.downloadRequiredFirmware).mockResolvedValue({ success: true, message: "", downloaded: 1 });
      const { container } = renderPsx();
      const required = button(container, "Download required for DuckStation (1)");
      await act(async () => {
        fireEvent.click(required as HTMLElement);
      });
      expect(backend.downloadRequiredFirmware).toHaveBeenCalledWith("psx", "duckstation", 42);
    });

    it("sends the game's ROM id with a row's Download and with Replace…", async () => {
      vi.mocked(showModal).mockClear();
      vi.mocked(backend.downloadPlatformFirmwareFile).mockResolvedValue({ success: true, message: "", downloaded: 1 });
      vi.mocked(backend.replaceBiosFile).mockResolvedValue({
        success: true,
        placed: 1,
        left_in_place: 0,
        removed: 0,
        link_failures: [],
        backup_path: "/data/backups/bios/x/scph5500.bin",
      });
      const { container } = renderPsx();
      await act(async () => {
        fireEvent.click(button(container, "Download") as HTMLElement);
      });
      expect(backend.downloadPlatformFirmwareFile).toHaveBeenCalledWith("psx", "scph5501.bin", null, 42);

      fireEvent.click(button(container, "Replace…") as HTMLElement);
      await confirm();
      expect(backend.replaceBiosFile).toHaveBeenCalledWith("psx", "/bios/scph5500.bin", 42);
    });

    it("downloads every library file of the platform for this game with Download all", async () => {
      vi.mocked(backend.downloadAllFirmware).mockResolvedValue({ success: true, message: "", downloaded: 3 });
      const onReread = vi.fn().mockResolvedValue(undefined);
      const { container } = render(
        <BiosTabWithState
          biosStatus={psx()}
          biosLevel="missing"
          coreInfo={coreInfo}
          platformName="PlayStation"
          platformSlug="psx"
          romId={42}
          onReread={onReread}
        />,
      );
      expect(container.textContent).toContain("FOR ALL OF PLAYSTATION");
      await act(async () => {
        fireEvent.click(button(container, "Download all") as HTMLElement);
      });
      expect(backend.downloadAllFirmware).toHaveBeenCalledWith("psx", 42);
      expect(onReread).toHaveBeenCalled();
    });

    it("deletes Tender's downloads for the platform once the platform page's confirmation is answered", async () => {
      vi.mocked(showModal).mockClear();
      vi.mocked(backend.deletePlatformBios).mockResolvedValue({
        success: true,
        message: "Deleted 2 BIOS files",
        deleted_count: 2,
      } as Awaited<ReturnType<typeof backend.deletePlatformBios>>);
      const { container, getByTestId } = renderPsx();
      fireEvent.click(button(container, "Delete downloads (2)") as HTMLElement);
      const calls = vi.mocked(showModal).mock.calls;
      const props = (calls[calls.length - 1]?.[0] as ReactElement<{ strTitle: string }> | undefined)?.props;
      expect(props?.strTitle).toBe("Delete Tender's BIOS downloads for PlayStation?");
      expect(backend.deletePlatformBios).not.toHaveBeenCalled();
      await confirm();
      expect(backend.deletePlatformBios).toHaveBeenCalledWith("psx");
      expect(getByTestId("status-bios").textContent).toBe("Deleted 2 BIOS files");
    });

    it("withdraws the downloads while RomM's listing could not be read, and keeps the deletes", () => {
      const { container } = renderPsx(psx({ server_offline: true }));
      expect(button(container, "Download")).toBeUndefined();
      expect(button(container, "Delete")).toBeDefined();
      expect(button(container, "Download all")?.hasAttribute("disabled")).toBe(true);
      expect(button(container, "Download required for DuckStation (1)")?.hasAttribute("disabled")).toBe(true);
    });

    it("stands the column of RomM library files below the left one where the page is narrower than both need", () => {
      // happy-dom lays nothing out, so what is pinned is the rule the browser
      // wraps by: a left column of a fixed 760 CSS px, a right one of at least
      // 400, and a row that wraps.
      const { getByTestId } = renderPsx();
      expect(getByTestId("bios-columns").style.flexWrap).toBe("wrap");
      expect(getByTestId("bios-main-column").style.flex).toBe("0 0 760px");
      expect(getByTestId("bios-library-column").style.flex).toBe("1 1 400px");
      expect(getByTestId("bios-main-column").nextElementSibling).toBe(getByTestId("bios-library-column"));
    });

    it("moves between the two columns by where they stand, and up and down inside each", () => {
      // Steam's nav is not in happy-dom: what is pinned is the flow each level
      // declares. How Steam steps by them: docs/architecture/qam-panel.md, "The
      // game page's BIOS tab stands in two columns".
      const focusable = vi.spyOn(deckyUi, "Focusable");
      const { container, getByTestId } = renderPsx();
      const flows = new Set(
        focusable.mock.calls
          .map(([props]) => props as { className?: string; "flow-children"?: string })
          .filter((props) => props.className?.startsWith("romm-bios-column"))
          .map((props) => `${props.className}:${props["flow-children"]}`),
      );
      focusable.mockRestore();
      expect([...flows]).toEqual(["romm-bios-columns:grid", "romm-bios-column:column"]);
      expect(container.querySelectorAll(".romm-bios-column")).toHaveLength(2);
      const columns = container.querySelector<HTMLElement>(".romm-bios-columns");
      expect(columns?.contains(getByTestId("bios-columns"))).toBe(true);
      const main = getByTestId("bios-main-column").querySelector<HTMLElement>(".romm-bios-column");
      const library = getByTestId("bios-library-column").querySelector<HTMLElement>(".romm-bios-column");
      expect(main?.contains(button(container, "Download all") as HTMLElement)).toBe(true);
      expect(main?.textContent).toBe(getByTestId("bios-main-column").textContent);
      expect(library?.textContent).toBe(getByTestId("bios-library-column").textContent);
      expect(library?.textContent).not.toBe("");
    });

    it("stretches both columns, and their focus groups, to the row's height", () => {
      // Why grid flow needs the columns to overlap from top to bottom:
      // docs/architecture/qam-panel.md, "The game page's BIOS tab stands in two
      // columns".
      const { getByTestId } = renderPsx();
      expect(getByTestId("bios-columns").style.alignItems).toBe("stretch");
      for (const id of ["bios-main-column", "bios-library-column"]) {
        const column = getByTestId(id);
        expect(column.style.display).toBe("flex");
        expect(column.style.flexDirection).toBe("column");
        expect(column.querySelector<HTMLElement>(".romm-bios-column")?.style.flexGrow).toBe("1");
      }
    });

    it("draws a row stop in the row's own text colour, which its focus rule keeps", () => {
      // What Steam paints on focus is device-only; the rule that keeps the
      // colour then is pinned in `bios/rowStopFocus.test.ts`.
      const dialogButton = vi.spyOn(deckyUi, "DialogButton");
      renderPsx();
      const stops = dialogButton.mock.calls
        .map(([props]) => props as { className?: string; style?: Record<string, unknown> })
        .filter((props) => props.className === "romm-bios-row-stop");
      dialogButton.mockRestore();
      expect(stops.length).toBeGreaterThan(0);
      for (const props of stops) expect(props.style).toMatchObject({ color: "inherit", background: "transparent" });
    });

    it("offers the platform's delete and its emulators on an answer nothing could establish", () => {
      const unknown: BiosStatus = { ...psx(), needs_bios: false, bios_status_unknown: true, files: [] };
      const { container } = render(
        <BiosTabWithState
          biosStatus={unknown}
          biosLevel="unknown"
          coreInfo={coreInfo}
          platformName="PlayStation"
          platformSlug="psx"
          romId={42}
        />,
      );
      expect(button(container, "Delete downloads (2)")?.hasAttribute("disabled")).toBe(false);
      expect(container.querySelector('[data-testid="emulator-selector"]')).not.toBeNull();
      expect(container.querySelector('[data-testid="bios-file-row"]')).toBeNull();
    });

    it("draws a bulk button as wide as its label, never stretched across its row", () => {
      const dialogButton = vi.spyOn(deckyUi, "DialogButton");
      renderPsx();
      const bulk = bulkButtonStyles(dialogButton.mock.calls);
      dialogButton.mockRestore();
      expect(Object.keys(bulk).sort()).toEqual(["Delete downloads", "Download all", "Download required"]);
      for (const style of Object.values(bulk)) expect(style).toMatchObject({ width: "auto", flex: "0 1 auto" });
    });
  });
});

/** The style each bulk button was drawn with, by the start of its label. */
function bulkButtonStyles(calls: readonly (readonly unknown[])[]): Record<string, unknown> {
  const textOf = (node: unknown): string => {
    if (typeof node === "string") return node;
    if (node && typeof node === "object" && "props" in node) {
      return textOf((node as { props: { children?: unknown } }).props.children);
    }
    return "";
  };
  const styles: Record<string, unknown> = {};
  for (const [props] of calls) {
    const { children, style } = props as { children?: unknown; style?: unknown };
    const label = ["Download required", "Download all", "Delete downloads"].find((start) =>
      textOf(children).startsWith(start),
    );
    if (label !== undefined) styles[label] = style;
  }
  return styles;
}
