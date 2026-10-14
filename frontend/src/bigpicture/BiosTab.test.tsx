// Besides the pane's rendering, this file pins the mounting contract: the panel
// mounts this tab for every ROM and leaves it mounted, so rendering has to be
// gated on `isActive` — a panel test cannot tell "the pane rendered nothing"
// apart from "the pane was never mounted".

import { describe, it, expect } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { BiosTab } from "./BiosTab";
import { libretroEmu, standaloneEmu } from "../test-utils/coreFixtures";
import { BIOS_MISSING_RED } from "../utils/biosColor";
import type { BiosFileStatus, BiosStatus, CoreInfo, EmulatorOption, MissingConfiguredImage } from "../types";

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
};

describe("BiosTab", () => {
  it("renders the requirement and the emulator it is scoped to when it is the active tab", () => {
    const { container } = render(
      <BiosTab biosStatus={biosStatus} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
    );
    expect(container.textContent).toContain(
      "The one file the launching emulator requires is not in place (0/1 RomM library files)",
    );
    expect(container.textContent).toContain("Snes9x");
  });

  it("renders nothing while another tab is showing", () => {
    const { container } = render(
      <BiosTab biosStatus={biosStatus} biosLevel="missing" coreInfo={coreInfo} isActive={false} />,
    );
    expect(container.textContent).toBe("");
  });

  it("renders nothing when nothing needs BIOS", () => {
    const { container } = render(<BiosTab biosStatus={null} biosLevel={null} coreInfo={coreInfo} isActive={true} />);
    expect(container.textContent).toBe("");
  });

  it("renders the unknown reading off a status with no counts at all", () => {
    // What a platform whose emulators cannot be asked hands the tab: the wire
    // payload for "nothing could establish it", with no file rows and no
    // aggregates to read. The pane still has to say so rather than fall through
    // to the no-requirement sentence, whose counts would both be zero.
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain("Nothing could be established about what the launching emulator needs");
    expect(container.textContent).not.toContain("marks none of its BIOS files as required");
    expect(container.innerHTML).toContain("#8f98a0");
  });

  it("gives no reason line where the answering source gives an emulator list", () => {
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.querySelector('[data-testid="bios-reason"]')).toBeNull();
  });

  it("says on a game page that the source has no system for the game's platform", () => {
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={{
          ...coreInfo,
          emulators: [],
          emulator_data_available: false,
          emulator_data_reason: "no_platform_system",
          platform_system: { state: "no_system", source: "retrodeck", system: null, platform: "Commodore VIC-20" },
        }}
        isActive={true}
      />,
    );
    expect(container.querySelector('[data-testid="bios-reason"]')?.textContent).toBe(
      "RetroDECK has no system for Commodore VIC-20, so Tender cannot download its games.",
    );
  });

  it("puts the reason line between the BIOS heading and the state", () => {
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: false, bios_status_unknown: true }}
        biosLevel="unknown"
        coreInfo={{
          ...coreInfo,
          emulator_data_available: false,
          emulator_data_reason: "switched_off",
          emulator_source: null,
          platform_system: null,
        }}
        isActive={true}
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
    [2, "All 2 files the launching emulator requires are in place (5/20 RomM library files)"],
    [1, "The one file the launching emulator requires is in place (5/20 RomM library files)"],
  ])("states a ready requirement of %i beside a library ratio that counts a different set", (required, sentence) => {
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 5,
          ratio_server_count: 20,
          ratio_local_count: 5,
          all_downloaded: false,
          required_count: required,
          required_downloaded: required,
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain(sentence);
  });

  it("drops the ratio when the library holds none of the platform's files", () => {
    // A "(0/0 RomM library files)" beside the sentence counts a set that does not exist.
    const { container } = render(
      <BiosTab
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
        isActive={true}
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
        <BiosTab
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
          isActive={true}
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
      <BiosTab
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
        isActive={true}
      />,
    );
    expect(container.textContent).not.toContain("does not start without a BIOS image");
  });

  it("says no BIOS is chosen in the emulator's settings, in the resolver's words", () => {
    // PCSX2 (Standalone) with `[Filenames] BIOS` empty.
    const { container } = render(
      <BiosTab
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
        isActive={true}
      />,
    );
    expect(container.textContent).toContain("No BIOS is chosen in PCSX2's settings, and no game boots until one is");
  });

  it("says nothing of the console where the resolver records no need for an image", () => {
    // A console nothing is recorded about must change nothing — its own sentence
    // would be a claim about an unasked question.
    for (const consoleNeedsImage of [{ console_needs_image: false }, {}]) {
      const { container } = render(
        <BiosTab
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
          isActive={true}
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
      <BiosTab
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
        isActive={true}
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
      <BiosTab
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
        isActive={true}
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
      <BiosTab
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
        isActive={true}
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
      <BiosTab
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
        isActive={true}
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
      <BiosTab
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
        isActive={true}
      />,
    );

    const names = [...container.querySelectorAll(".romm-panel-file-name")].map((el) => el.textContent);
    // The declared path heads the row — the folder is the one thing a reader
    // placing the file by hand needs, and `file_name` is only its basename —
    // and what the description still says follows it on the same line, in the
    // packager's own punctuation. That is the form the packager wrote
    // (`firmware1_desc`), with our declared path in place of the bare basename.
    expect(names).toContain("dc/dc_boot.bin (Dreamcast BIOS)");
    // A description that is nothing but the name adds nothing, so that row is
    // the name alone rather than the name twice.
    expect(names).toContain("macventure.dat");
    expect(container.textContent).not.toContain("macventure.dat — ");
  });

  it("puts the packager's label beside the name, muted, and never under the row", () => {
    // Under the row it read as a sixth entry in the list of emulators that want
    // the file — the one thing it is not. Beside the name it is one statement
    // with the name: what this file IS. Muted like those emulator lines rather
    // than like the name, because the name is what the eye lands on.
    const { container } = render(
      <BiosTab
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
              file_name: "scph5500.bin",
              downloaded: false,
              local_path: "",
              declared_path: "scph5500.bin",
              description: "scph5500.bin (PS1 JP BIOS)",
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
        isActive={true}
      />,
    );

    const name = container.querySelector(".romm-panel-file-name")!;
    expect(name.textContent).toBe("scph5500.bin (PS1 JP BIOS)");
    // A span of its own inside the name, in the muted colour: the label is
    // beside the name rather than part of it.
    const label = [...name.querySelectorAll("span")].find((el) => el.textContent.includes("(PS1 JP BIOS)"));
    expect(label).toBeTruthy();
    expect(label!.getAttribute("style")).toContain("rgba(255, 255, 255, 0.5)");
    // Nothing under the row carries it — that block is what the read found and
    // who wants the file.
    const under = [...container.querySelectorAll("div")].map((div) => div.textContent);
    expect(under).not.toContain("(PS1 JP BIOS)");
  });

  it("puts a satisfied folder's images on their own lines, under a name short enough to keep its dot", () => {
    // The row's name and its status dot share one flex line. Folding three
    // image descriptions into that name ran it to ~150 characters, wrapped the
    // line and left the dot stranded above the text — so the images go in the
    // indented block below, beside the per-core lines.
    const images = [
      "USA     v02.00(14/06/2004)  Console 20040614-100909",
      "Europe  v02.00(14/06/2004)  Console 20040614-100914",
    ];
    const { container } = render(
      <BiosTab
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
        isActive={true}
      />,
    );

    const name = container.querySelector(".romm-panel-file-name");
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
      <BiosTab
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
              local_path: "",
              description: "Dreamcast boot ROM",
              declaration: "read",
              wanted: "needed",
              required_by_active: true,
              on_server: true,
              declared_kind: "file",
              satisfied: false,
              caveats: ["firmware-path-inaccessible"],
            },
          ],
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );

    // Three parts on one line, left to right: the name, what the file IS, then
    // how it STANDS. The note is the last and keeps the em dash, which is what
    // marks it as a statement about the state rather than about the file (this
    // description names nothing the row's name already shows, so it is printed
    // whole and carries no parentheses of its own).
    expect(container.textContent).toContain("dc_boot.bin Dreamcast boot ROM — its location could not be read");
  });

  it("draws a missing optional file's dot grey, never amber, where another emulator requires it", () => {
    // Amber means only a one-of group whose images cover some regions (#2190
    // D7). What another emulator demands is on that emulator's own tab, so a
    // file this launch does not need is grey here whoever else needs it.
    const { container } = render(
      <BiosTab
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
        isActive={true}
      />,
    );
    const dot = container.querySelector(".romm-panel-file-row .romm-status-dot") as HTMLElement;
    expect(dot.style.backgroundColor).toBe("#8f98a0");
  });

  it("draws a folder row whose contents could not be read grey, never green", () => {
    // The colours are the pane's own, so they are pinned here rather than
    // through the panel: `downloaded` is true for a folder — something is at the
    // destination — and green over a verdict nothing established is the false
    // all-clear this row exists to avoid. Grey is the colour of "not
    // established"; amber is no mark of a row's.
    const { container } = render(
      <BiosTab
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
        isActive={true}
      />,
    );
    const dot = container.querySelector(".romm-panel-file-row .romm-status-dot") as HTMLElement;
    expect(dot.style.backgroundColor).toBe("#8f98a0");
    expect(container.innerHTML).not.toContain("#d4a72c");
    expect(container.innerHTML).not.toContain("#5ba32b");
  });

  it.each([
    ["green over a folder holding an image", true, "#5ba32b", "#d94126", ["firmware-image-identified"], ["Europe"]],
    ["red over a folder holding none", false, "#d94126", "#5ba32b", ["firmware-directory-holds-no-image"], []],
  ] as const)("draws %s", (_name, satisfied, colour, otherColour, caveats, images) => {
    // The folder's verdict is what it HOLDS, so it takes the same two colours a
    // declared file does — the amber above is for a verdict, not for a folder.
    //
    // Asserted on the file row alone, because the header dot beside it emits the
    // same hex for these two levels: over the whole container the presence check
    // would pass on the header without the row's dot ever being drawn.
    const { container } = render(
      <BiosTab
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
        isActive={true}
      />,
    );
    const row = container.querySelector(".romm-panel-file-row");
    expect(row?.innerHTML).toContain(colour);
    expect(row?.innerHTML).not.toContain(otherColour);
    expect(row?.innerHTML).not.toContain("#d4a72c");
  });

  it("states the launching emulator under the Emulator heading and gives it no label of its own", () => {
    // A standalone emulator is not a core, and the column's own heading already
    // names the subject — so the value stands alone under it, the way the BIOS
    // column's sentence stands under `BIOS`. A label here would be a second name
    // for the same thing, in a narrow column.
    const { container } = render(
      <BiosTab
        biosStatus={biosStatus}
        biosLevel="missing"
        coreInfo={{
          ...coreInfo,
          active_core: "DUCKSTATION",
          active_core_label: "DuckStation (Legacy) (Standalone)",
          emulators: [standaloneEmu("DuckStation (Legacy) (Standalone)", true, { emulator: "DUCKSTATION" })],
        }}
        isActive={true}
      />,
    );
    const titles = [...container.querySelectorAll(".romm-panel-section-title")].map((el) => el.textContent);
    expect(titles).toEqual(["BIOS", "Emulator"]);
    const emulatorHeading = [...container.querySelectorAll(".romm-panel-section-title")].find(
      (el) => el.textContent === "Emulator",
    );
    const underTheHeading = emulatorHeading?.nextElementSibling;
    expect(underTheHeading?.className).toBe("romm-panel-value");
    expect(underTheHeading?.textContent).toBe("DuckStation (Legacy) (Standalone)");
    expect(container.querySelector(".romm-panel-label")).toBeNull();
    expect(container.textContent).not.toContain("Active Core");
  });

  describe("the Emulator column where no emulator is resolved", () => {
    // The column takes the Platforms header's words for the same payload
    // (`emulatorClause`), so each of the three unpinnable states reads its own
    // way, and the two the games cannot start in are red.
    const underTheEmulatorHeading = (core: CoreInfo | null) => {
      const { container } = render(
        <BiosTab biosStatus={biosStatus} biosLevel="missing" coreInfo={core} isActive={true} />,
      );
      const heading = [...container.querySelectorAll(".romm-panel-section-title")].find(
        (el) => el.textContent === "Emulator",
      );
      expect(heading).toBeTruthy();
      return { container, value: heading?.nextElementSibling as HTMLElement | null };
    };
    const unresolved = (emulators: EmulatorOption[]): CoreInfo => ({
      ...coreInfo,
      active_core: null,
      active_core_label: null,
      emulators,
    });

    it("reads 'RetroDECK decides', not in red, where the fallback can run", () => {
      const { container, value } = underTheEmulatorHeading({ ...coreInfo, active_core: null, active_core_label: null });
      expect(value?.textContent).toBe("RetroDECK decides");
      expect(value?.style.color).toBe("");
      expect(container.textContent).not.toContain("Default");
    });

    it("reads 'no emulator installed' in red where the fallback is not installed", () => {
      const { value } = underTheEmulatorHeading(
        unresolved([standaloneEmu("mGBA (Standalone)", false, { bakeable: false, reason: "not_installed" })]),
      );
      expect(value?.textContent).toBe("no emulator installed");
      expect(value?.style.color).toBe(BIOS_MISSING_RED);
    });

    it("reads 'no emulator' in red where the platform lists none", () => {
      const { value } = underTheEmulatorHeading(unresolved([]));
      expect(value?.textContent).toBe("no emulator");
      expect(value?.style.color).toBe(BIOS_MISSING_RED);
    });

    it("renders no value under the heading while the core answer is not there", () => {
      const { value } = underTheEmulatorHeading(null);
      expect(value).toBeNull();
    });

    it("renders no value under the heading where the emulator list could not be read", () => {
      const { value } = underTheEmulatorHeading({
        ...unresolved([]),
        emulator_data_available: false,
        emulator_data_reason: "no_platform_system",
      });
      expect(value).toBeNull();
    });
  });

  describe("which rows the pane shows", () => {
    // The Dreamcast this was written for listed eight rows, six of them arcade
    // BIOSes Flycast declares because it also emulates Naomi and AtomisWave.
    // Not required for the launch, not present, not in the RomM library —
    // nothing a reader of a Dreamcast game's page could do with any of them,
    // and they pushed the two rows that mattered off the top. The platform page
    // keeps listing every one of them; it is the management surface.
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

    /** The six arcade rows, in the spelling the device showed them in. */
    const arcadeRows = ["airlbios.zip", "f355bios.zip", "f355dlx.zip", "hod2bios.zip", "naomi.zip", "naomi2.zip"].map(
      (name) => row(name, { declared_path: `dc/${name}` }),
    );

    /** Header aggregates held fixed, so a row's fate can never move a number. */
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

    /** The declared path each row is headed with — `biosFileNote`'s note rides
     *  on the same span behind a dash, and which rows are drawn is the question
     *  here, not what each of them says about itself. */
    const namesOf = (files: BiosFileStatus[]): string[] => {
      const { container } = render(
        <BiosTab biosStatus={statusWith(files)} biosLevel="ok" coreInfo={coreInfo} isActive={true} />,
      );
      return [...container.querySelectorAll(".romm-panel-file-name")].map((el) => el.textContent.split(" — ")[0] ?? "");
    };

    it.each([
      ["the launching emulator requires it", { required_by_active: true }],
      // The same requirement in its other shape: one option of the launching
      // emulator's one-of group, which no emulator marks required. Drop this
      // answer and the header states the group's verdict over a list with none
      // of its files in it.
      [
        "it is an option of the launching emulator's group",
        { one_of: { regions: ["ntsc-u"] as string[], every_region: false } },
      ],
      ["it is there", { satisfied: true, downloaded: true }],
      // The condition the platform page's download buttons are built from.
      ["the platform page can fetch it", { on_server: true }],
      ["nothing could judge it", { satisfied: null }],
    ] as const)("keeps a row where %s", (_why, over) => {
      expect(namesOf([row("dc_boot.bin", over)])).toContain("dc_boot.bin");
    });

    it("leaves the rest to the platform page, counted and pointed at", () => {
      const names = namesOf([row("dc_boot.bin", { required_by_active: true }), ...arcadeRows]);
      expect(names).toEqual(["dc_boot.bin"]);

      const { container } = render(
        <BiosTab
          biosStatus={{
            ...statusWith([row("dc_boot.bin", { required_by_active: true }), ...arcadeRows]),
            active_core_label: "Flycast",
          }}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      // Both claims hold of every row counted, and the last clause is the whole
      // point: a summary that does not say where the summarised rows are hides
      // them. The emulator named is the one whose rows were counted.
      expect(container.textContent).toContain(
        "6 more files Flycast lists — not required for this launch, nothing to download. " +
          "All files are on the platform page.",
      );
    });

    it("counts a single left-out row in the singular", () => {
      const { container } = render(
        <BiosTab
          biosStatus={{
            ...statusWith([row("dc_boot.bin", { required_by_active: true }), row("naomi.zip")]),
            active_core_label: "Flycast",
          }}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      expect(container.textContent).toContain(
        "1 more file Flycast lists — not required for this launch, nothing to download. " +
          "All files are on the platform page.",
      );
    });

    it.each([
      [2, "2 files on server no installed emulator asks for"],
      [1, "1 file on server no installed emulator asks for"],
    ])("counts server files nothing asks for in a note of their own (%i)", (count, note) => {
      const nobodys = Array.from({ length: count }, (_, i) =>
        row(`extra${i}.bin`, { wanted: "not_needed", on_server: true }),
      );
      const { container } = render(
        <BiosTab
          biosStatus={statusWith([row("dc_boot.bin", { required_by_active: true }), ...nobodys])}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      expect(container.textContent).toContain(note);
      // A note, not rows: none of them is drawn with the files an emulator wants.
      const names = [...container.querySelectorAll(".romm-panel-file-name")].map((el) => el.textContent);
      expect(names.some((name) => name.startsWith("extra"))).toBe(false);
    });

    it("moves no number in the header", () => {
      // This is a display decision and nothing else: every count on the header
      // line is the backend's, over the set it counted, and none of them may
      // follow what this page chose to draw. One payload, rendered with the six
      // left-out rows and again with them taken out of `files` — the aggregates
      // are identical in both, so the header has to be too.
      const headerOf = (files: BiosFileStatus[]): { label: string | null; hasNote: boolean } => {
        const { container } = render(
          <BiosTab biosStatus={statusWith(files)} biosLevel="ok" coreInfo={coreInfo} isActive={true} />,
        );
        return {
          label: container.querySelector(".romm-panel-value")?.textContent ?? null,
          hasNote: container.textContent.includes("lists — not required for this launch"),
        };
      };

      const kept = row("dc_boot.bin", { satisfied: true, downloaded: true });
      const withLeftOut = headerOf([kept, ...arcadeRows]);
      const withoutThem = headerOf([kept]);

      expect(withLeftOut.label).toBe(
        "The launching emulator marks none of its BIOS files as required (1/20 RomM library files)",
      );
      expect(withLeftOut.label).toBe(withoutThem.label);
      // Non-vacuous: the first render really did leave rows out, so the equality
      // above is over two different lists rather than two identical ones.
      expect(withLeftOut.hasNote).toBe(true);
      expect(withoutThem.hasNote).toBe(false);
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
      render(<BiosTab biosStatus={status} biosLevel={level} coreInfo={beetle} isActive={true} />).container;
    const sectionTitles = (container: HTMLElement): string[] =>
      [...container.querySelectorAll<HTMLElement>(".romm-panel-section-title")].map((title) => title.textContent);
    const dotOf = (container: HTMLElement, name: string): string | undefined =>
      [...container.querySelectorAll<HTMLElement>(".romm-panel-file-row")]
        .find((row) => row.textContent.startsWith(name))
        ?.querySelector<HTMLElement>(".romm-status-dot")?.style.backgroundColor;

    it("names the regions the group states nothing for on the page of a game with no clear region", () => {
      // A World release: no game region narrows the group, so the platform's
      // verdict stands — and with it the clause naming Europe.
      const status = forTheGame("met", ["ntsc-u"], ["ntsc-j"], []);
      const [group] = status.one_of_groups ?? [];
      const open = { ...group!, state: "partial" as const, unchecked: ["pal"], unstated: ["pal"] };
      const container = render(
        <BiosTab
          biosStatus={{ ...status, required_downloaded: 0, one_of_groups: [open] }}
          biosLevel="unknown"
          coreInfo={beetle}
          isActive={true}
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
      // Above the file list, and the list keeps every row.
      const list = container.querySelector(".romm-panel-file-list");
      expect(block?.compareDocumentPosition(list as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(container.querySelectorAll(".romm-panel-file-list .romm-panel-file-row")).toHaveLength(3);
    });

    it("heads the group with a subheading in the section label's style, the file list following it", () => {
      const container = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");

      // Written in sentence case; the class upper-cases it, as it does "BIOS".
      expect(sectionTitles(container)).toEqual(["BIOS", "Beetle PSX · one image per disc region", "Emulator"]);
      const block = container.querySelector<HTMLElement>('[data-testid="bios-group"]');
      const list = container.querySelector(".romm-panel-file-list");
      expect(block?.compareDocumentPosition(list as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
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
      expect(sectionTitles(container)).toEqual(["BIOS", "Emulator"]);
    });

    it("draws the missing image of the game's region red and another region's grey", () => {
      const japanese = renderFor(forTheGame("unmet", [], ["ntsc-j"], ["ntsc-j"]), "missing");
      const american = renderFor(forTheGame("met", ["ntsc-u"], [], ["ntsc-u"]), "ok");

      expect(dotOf(japanese, "scph5500.bin")).toBe("#d94126");
      expect(dotOf(japanese, "scph5502.bin")).toBe("#8f98a0");
      expect(dotOf(american, "scph5500.bin")).toBe("#8f98a0");
      expect(dotOf(american, "scph5501.bin")).toBe("#5ba32b");
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

    it("names the setting under the folder and leaves its dot and sentence alone", () => {
      const withLine = render(
        <BiosTab
          biosStatus={folder({ emulator_label: "LRPS2", file_name: "scph10000.bin" })}
          biosLevel="ok"
          coreInfo={lrps2}
          isActive={true}
        />,
      ).container;
      const without = render(
        <BiosTab biosStatus={folder(null)} biosLevel="ok" coreInfo={lrps2} isActive={true} />,
      ).container;

      expect(withLine.textContent).toContain(
        "LRPS2's settings name scph10000.bin, which is not here — it uses another BIOS from this folder instead.",
      );
      expect(without.textContent).not.toContain("LRPS2's settings");
      const dot = (container: HTMLElement) =>
        container.querySelector<HTMLElement>(".romm-panel-file-row .romm-status-dot")?.style.backgroundColor;
      expect(dot(withLine)).toBe(dot(without));
      expect(dot(withLine)).toBe("#5ba32b");
      expect(withLine.querySelector(".romm-panel-value")?.textContent).toBe(
        without.querySelector(".romm-panel-value")?.textContent,
      );
    });
  });

  describe("one tab per emulator (#2190)", () => {
    const own = (file_name: string, declared_by: string, over: Partial<BiosFileStatus> = {}): BiosFileStatus => ({
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
      render(<BiosTab biosStatus={saturn()} biosLevel="missing" coreInfo={coreInfo} isActive={true} />);
    const tabs = (container: HTMLElement) =>
      [...container.querySelectorAll<HTMLElement>('[data-testid="emulator-tab"]')].map((tab) => tab.textContent);
    const names = (container: HTMLElement) =>
      [...container.querySelectorAll(".romm-panel-file-name")].map((el) => el.textContent.split(" ")[0]);

    it("puts the launching emulator's tab first, marked as the one that launches", () => {
      expect(tabs(renderSaturn().container)).toEqual(["YabaSanshiro· launches", "Kronos"]);
    });

    it("dots each tab with its own emulator's verdict", () => {
      const dots = [...renderSaturn().container.querySelectorAll<HTMLElement>('[data-testid="emulator-tab-dot"]')];
      expect(dots.map((dot) => dot.style.backgroundColor)).toEqual(["#d94126", "#5ba32b"]);
    });

    it("opens on the launching emulator's rows", () => {
      expect(names(renderSaturn().container)).toEqual(["saturn_bios.bin"]);
    });

    it("shows another emulator's own rows, muted, and leaves the header the launching emulator's", () => {
      const { container } = renderSaturn();
      const header = container.querySelector(".romm-panel-value")?.textContent;
      fireEvent.click([...container.querySelectorAll('[data-testid="emulator-tab"]')][1] as HTMLElement);

      expect(names(container)).toEqual(["kronos/saturn_bios.bin"]);
      const row = container.querySelector<HTMLElement>(".romm-panel-file-row");
      expect(row?.style.opacity).toBe("0.6");
      expect(container.querySelector(".romm-panel-value")?.textContent).toBe(header);
    });

    it("narrows another emulator's tab by that emulator's own need and names it in the note", () => {
      const status = saturn();
      const kronos = status.emulator_tabs?.[1];
      if (kronos === undefined) throw new Error("fixture has no Kronos tab");
      kronos.rows = [
        ...(kronos.rows ?? []),
        // Kronos's own optional cartridge image: not required by it, not there,
        // not in the library — nothing this page could act on.
        own("mpr-19367-mx.ic1", "kronos_libretro.so", {
          declared_path: "kronos/mpr-19367-mx.ic1",
          wanted: "optional",
          required_by_active: false,
          on_server: false,
        }),
      ];
      const { container } = render(
        <BiosTab biosStatus={status} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
      );
      fireEvent.click([...container.querySelectorAll('[data-testid="emulator-tab"]')][1] as HTMLElement);

      expect(names(container)).toEqual(["kronos/saturn_bios.bin"]);
      expect(container.textContent).toContain(
        "1 more file Kronos lists — not required by Kronos, nothing to download. " +
          "All files are on the platform page.",
      );
    });

    it("keeps a row another emulator declares off the launching tab", () => {
      const status = saturn();
      status.files = [...(status.files ?? []), own("mpr-17933.bin", "kronos_libretro.so")];
      const { container } = render(
        <BiosTab biosStatus={status} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
      );
      expect(names(container)).toEqual(["saturn_bios.bin"]);
    });

    it("draws no strip where only one emulator answered", () => {
      const status = { ...saturn(), emulator_tabs: (saturn().emulator_tabs ?? []).slice(0, 1) };
      const { container } = render(
        <BiosTab biosStatus={status} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
      );
      expect(container.querySelectorAll('[data-testid="emulator-tab"]')).toHaveLength(0);
    });
  });
});
