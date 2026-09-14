// The pane's content is asserted through the panel that owns every field it
// renders (RomMGameInfoPanel.test.tsx). What this file exists for is the
// mounting contract: the panel mounts this tab for every ROM and leaves it
// mounted, so rendering has to be gated on `isActive` — a panel test cannot
// tell "the pane rendered nothing" apart from "the pane was never mounted".

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { BiosTab } from "./BiosTab";
import { libretroEmu, standaloneEmu } from "../test-utils/coreFixtures";
import type { BiosFileStatus, BiosStatus, CoreInfo, EmulatorOption } from "../types";

const coreInfo: CoreInfo = {
  active_core: "snes9x_libretro.so",
  active_core_label: "Snes9x",
  platform_core_label: null,
  has_game_override: false,
  emulator_data_available: true,
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
  all_downloaded: false,
  required_count: 1,
  required_downloaded: 0,
};

describe("BiosTab", () => {
  it("renders the requirement and its active core when it is the active tab", () => {
    const { container } = render(
      <BiosTab biosStatus={biosStatus} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
    );
    expect(container.textContent).toContain(
      "The one file the launching emulator requires is not in place (0/1 files held)",
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

  it("drops the ratio when the library holds none of the platform's files", () => {
    // A "(0/0 files held)" beside the sentence counts a set that does not exist.
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: true, server_count: 0, local_count: 0, all_downloaded: false, required_count: 0 }}
        biosLevel="ok"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain("The launching emulator marks none of its BIOS files as required");
    expect(container.textContent).not.toContain("files held");
  });

  it("says the console needs at least one file where the counts would say nothing is required", () => {
    // The PlayStation state, and the whole reason the axis exists: SwanStation
    // marks every image it declares optional — a libretro `.info` cannot say the
    // console needs one of them — so `required_count` is 0 and the pane read a
    // green "Nothing required (0/20 files held)" while no game on the platform
    // would start. The sentence has to say the requirement is ONE file, and to
    // point at no set while doing it: only the images the launching core
    // declares can answer it, and the twenty in the ratio is the library's
    // inventory rather than that set.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          system_image: "absent",
        }}
        biosLevel="missing"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain(
      "The launching emulator cannot start this system without a BIOS image (0/20 files held)",
    );
    expect(container.textContent).not.toContain("marks none of its BIOS files as required");
    expect(container.textContent).not.toContain("requires are in place");
    expect(container.innerHTML).toContain("#d94126");
  });

  it("says the console needs at least one file even where the level declines", () => {
    // Today the backend never sends this pair — `absent` lands on `missing` —
    // and the order now lives once, in `biosSummary`, which both wording
    // surfaces read. This asserts it end to end from THIS one: were a decline
    // added ahead of the `absent` test in `compute_bios_level`, a surface
    // reading the level first would print an ignorance over a requirement that
    // was demonstrated.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          required_withheld: 0,
          system_image: "absent",
        }}
        biosLevel="unknown"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain(
      "The launching emulator cannot start this system without a BIOS image (0/20 files held)",
    );
    // The two sentences the decline would have printed instead — named as
    // sentences, because the page shows the sentence and a status never reaches
    // it, so asserting the short forms absent would assert nothing.
    expect(container.textContent).not.toContain("could not be established");
    expect(container.textContent).not.toContain("could not be checked");
  });

  it("names the readiness as the unknown where the console's own image is unsettled", () => {
    // The requirement IS known here — this console needs an image — and it is
    // whether one is in place that could not be established. The
    // requirement-unknown sentence would be the wrong half.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 0,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          required_withheld: 0,
          system_image: "unsettled",
        }}
        biosLevel="unknown"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain(
      "Whether the BIOS image the launching emulator needs is in place could not be established",
    );
    // The requirement-unknown sentence is the wrong half, and it is the one this
    // state would fall through to.
    expect(container.textContent).not.toContain("Nothing could be established about what");
  });

  it("says nothing of its own for the two quiet answers", () => {
    // `not_demanded` covers a console nothing is recorded about, and it must
    // change nothing — its own sentence would be a claim about an unasked
    // question.
    for (const systemImage of ["not_demanded", "held"] as const) {
      const { container } = render(
        <BiosTab
          biosStatus={{
            needs_bios: true,
            server_count: 20,
            local_count: 1,
            all_downloaded: false,
            required_count: 0,
            required_downloaded: 0,
            system_image: systemImage,
          }}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      expect(container.textContent).toContain(
        "The launching emulator marks none of its BIOS files as required (1/20 files held)",
      );
      expect(container.textContent).not.toContain("cannot start this system");
    }
  });

  it("says whose requirement the empty count is, and never that the console needs nothing", () => {
    // `required_count: 0` is one emulator's declaration and nothing else: the
    // active core marks none of the files it names required. It says nothing
    // about the CONSOLE, which is a separate axis (`system_image`) and answers
    // `not_demanded` for a console nobody has asked about as readily as for one
    // shown to start with nothing — so a headline that dropped the subject was
    // read as an all-clear the reading never gave.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 3,
          local_count: 1,
          all_downloaded: false,
          required_count: 0,
          required_downloaded: 0,
          system_image: "not_demanded",
        }}
        biosLevel="ok"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain(
      "The launching emulator marks none of its BIOS files as required (1/3 files held)",
    );
    // The subject, spelled out: the sentence may not stand without it. "Nothing
    // required" is the state's short form, which this surface never shows —
    // printing it here would be the subjectless headline the sentence replaced.
    expect(container.textContent).not.toContain("Nothing required");
  });

  it("names the emulator the answer was scoped to, where the answer names one", () => {
    // The sentence sat two inches from an `Active Core` row and said less than
    // everything around it. The name is the label half of the one pick the
    // backend filtered these counts by, carried on the answer itself — reading
    // it off the core payload beside it would be a second resolution of the
    // same question.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 20,
          local_count: 1,
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

    expect(container.textContent).toContain("mGBA marks none of its BIOS files as required (1/20 files held)");
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
      "The launching emulator marks none of its BIOS files as required (1/20 files held)",
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
              cores: {},
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
              cores: {},
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
              cores: {},
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
              cores: { swanstation_libretro: { required: true } },
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
              cores: {},
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
              cores: {},
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

  it("draws a folder row whose contents could not be read amber, never green", () => {
    // The colours are the pane's own, so they are pinned here rather than
    // through the panel: `downloaded` is true for a folder — something is at the
    // destination — and green over a verdict nothing established is the false
    // all-clear this row exists to avoid. No core matches `coreInfo`'s active
    // one, so the amber cannot have come from the active-core highlight.
    const { container } = render(
      <BiosTab
        biosStatus={{
          needs_bios: true,
          server_count: 0,
          local_count: 0,
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
              cores: {},
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
    expect(container.innerHTML).toContain("#d4a72c");
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
              cores: {},
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

  it("falls back to 'Default' when no active core is resolved", () => {
    const { container } = render(
      <BiosTab
        biosStatus={biosStatus}
        biosLevel="missing"
        coreInfo={{ ...coreInfo, active_core: null, active_core_label: null }}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain("Default");
  });

  describe("a core's line", () => {
    // Two speakers, and the line prints whichever of them has something to say.
    // What a core marks the file is its own `.info` and is never rewritten;
    // what its CONSOLE needs comes from the packaged table beside it. A libretro
    // declaration has only "needed" and "optional" to reach for, so an author
    // who knows the console will not start without one of these images writes
    // "optional" and the row read as a flat contradiction of the headline above
    // it.
    const psxRow = (cores: Record<string, { required: boolean; needs_one_of?: number | null }>) => ({
      needs_bios: true,
      server_count: 1,
      local_count: 0,
      all_downloaded: false,
      required_count: 0,
      required_downloaded: 0,
      required_withheld: 0,
      system_image: "absent" as const,
      files: [
        {
          file_name: "scph5501.bin",
          downloaded: false,
          local_path: "",
          description: "PlayStation BIOS (SCPH-5501)",
          wanted: "optional" as const,
          required_by_active: false,
          cores,
          on_server: true,
          declared_kind: "file" as const,
          satisfied: false,
        },
      ],
    });

    const lineFor = (
      cores: Record<string, { required: boolean; needs_one_of?: number | null }>,
      label: string,
    ): string | undefined => {
      const { container } = render(
        <BiosTab biosStatus={psxRow(cores)} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
      );
      // Leaf divs only: the block wrapping the core lines is a div too, and its
      // text is every line run together.
      return [...container.querySelectorAll("div")]
        .filter((div) => div.children.length === 0)
        .map((div) => div.textContent)
        .find((text) => text.startsWith(label));
    };

    it("states the whole requirement where the core marks each of its files optional", () => {
      // The count is what makes this line the only one that can: the headline
      // above says "at least one" without a number, and each row below
      // describes one file.
      expect(lineFor({ "swanstation_libretro.so": { required: false, needs_one_of: 5 } }, "swanstation")).toBe(
        "swanstation (needs one of its 5 BIOS files)",
      );
    });

    it("keeps the core's own word — the declaration is never rewritten", () => {
      // "required" is what this core's `.info` says, and the console's demand
      // adds nothing a reader would act on differently, so the line is
      // unchanged. Reading the pair as licence to print "required" over an
      // "optional" declaration is the misreading this shape exists to prevent.
      expect(lineFor({ "beetle_psx_libretro.so": { required: true, needs_one_of: null } }, "beetle_psx")).toBe(
        "beetle_psx (required)",
      );
    });

    it("says nothing extra for a core whose console the table has no entry for", () => {
      // Absent is an unasked question, not "this console needs nothing" — so the
      // line says only what the declaration said.
      expect(lineFor({ "mgba_libretro.so": { required: false } }, "mgba")).toBe("mgba (optional)");
    });

    it("leaves a file a demanding core marks optional beside required ones plain", () => {
      // `ps1_rom.bin` under Beetle PSX, and the line the annotation this
      // replaced got wrong. That console does not start without an image, and
      // what the core says about it is three OTHER files marked required — so
      // this row carries no disjunction and the line is the declaration alone.
      expect(lineFor({ "beetle_psx_libretro.so": { required: false } }, "beetle_psx")).toBe("beetle_psx (optional)");
    });

    it("answers each core on its own, over one file two of them declare", () => {
      // One row, two emulators, two different consoles' answers: the pane lists
      // both lines and neither may take the other's.
      const cores = {
        "swanstation_libretro.so": { required: false, needs_one_of: 5 },
        "pcsx_rearmed_libretro.so": { required: false, needs_one_of: null },
      };
      expect(lineFor(cores, "swanstation")).toBe("swanstation (needs one of its 5 BIOS files)");
      expect(lineFor(cores, "pcsx_rearmed")).toBe("pcsx_rearmed (optional)");
    });
  });

  describe("which emulator a line names", () => {
    // A row's `cores` map is keyed on the resolver's emulator IDENTITY, and each
    // picker row carries the same string beside its label — that pair is the
    // whole join, and there is no other. `core_so` cannot serve it: it is null
    // for every standalone emulator and omits the core file's extension for a
    // libretro one, so a page matching on it named no standalone emulator at all
    // and printed `dolphin_libretro.so` where the label said Dolphin.
    type CoreEntry = { required: boolean; needs_one_of?: number | null };

    const statusFor = (cores: Record<string, CoreEntry>): BiosStatus => ({
      needs_bios: true,
      server_count: 1,
      local_count: 0,
      all_downloaded: false,
      required_count: 1,
      required_downloaded: 0,
      required_withheld: 0,
      files: [
        {
          file_name: "scph5501.bin",
          downloaded: false,
          local_path: "",
          description: "PlayStation BIOS (SCPH-5501)",
          wanted: "needed",
          required_by_active: true,
          cores,
          on_server: true,
          declared_kind: "file",
          satisfied: false,
        },
      ],
    });

    const info = (emulators: EmulatorOption[], activeCore: string | null): CoreInfo => ({
      active_core: activeCore,
      active_core_label: null,
      platform_core_label: null,
      has_game_override: false,
      emulator_data_available: true,
      emulators,
    });

    /** The per-emulator lines under the row, as elements. Leaf divs only: the
     *  block wrapping them is a div too, and its text is every line run
     *  together. */
    const linesOf = (cores: Record<string, CoreEntry>, coreInfo: CoreInfo): HTMLElement[] => {
      const { container } = render(
        <BiosTab biosStatus={statusFor(cores)} biosLevel="missing" coreInfo={coreInfo} isActive={true} />,
      );
      return [...container.querySelectorAll<HTMLElement>("div")].filter((div) => div.children.length === 0);
    };

    const textsOf = (cores: Record<string, CoreEntry>, coreInfo: CoreInfo): (string | null)[] =>
      linesOf(cores, coreInfo).map((div) => div.textContent);

    const lineNamed = (cores: Record<string, CoreEntry>, coreInfo: CoreInfo, text: string): HTMLElement | undefined =>
      linesOf(cores, coreInfo).find((div) => div.textContent === text);

    it("takes a libretro core's label off the picker row carrying its identity", () => {
      const cores = { "swanstation_libretro.so": { required: true } };
      expect(textsOf(cores, info([libretroEmu("swanstation_libretro", "SwanStation")], null))).toContain(
        "SwanStation (required)",
      );
    });

    it("names a standalone emulator, which has no core file to be named after", () => {
      // The whole reason the join is the identity: DuckStation declares firmware
      // and carries no `core_so` at all, so nothing could put its own name on
      // the line and the row read the resolver's spelling, DUCKSTATION.
      expect(textsOf({ DUCKSTATION: { required: true } }, info([standaloneEmu("DuckStation")], null))).toContain(
        "DuckStation (required)",
      );
    });

    it("strips the core file's spelling off an identity the picker cannot name", () => {
      // Both strips in one identity. The key is the core FILE, so taking only
      // `_libretro` off leaves the line naming a file with an extension, and
      // taking only `.so` off leaves the marker no reader typed.
      expect(textsOf({ "some_obscure_libretro.so": { required: true } }, info([], null))).toContain(
        "some_obscure (required)",
      );
    });

    it("prints an identity that is nothing but those two parts whole", () => {
      // Stripping both would leave an empty line, which names no emulator at all.
      expect(textsOf({ "_libretro.so": { required: true } }, info([], null))).toContain("_libretro.so (required)");
    });

    it("takes the first declared label where two picker rows are one emulator", () => {
      // ES-DE lists one `pcsx2_libretro.so` as both LRPS2 and PCSX2, so a label
      // identifies a row and not an emulator. Declared order is preference order
      // — it is the order the system default is chosen in — so the first row
      // names the line and the second does not rename it.
      const emulators = [libretroEmu("pcsx2_libretro", "LRPS2", true), libretroEmu("pcsx2_libretro", "PCSX2")];
      const texts = textsOf({ "pcsx2_libretro.so": { required: true } }, info(emulators, null));
      expect(texts).toContain("LRPS2 (required)");
      expect(texts).not.toContain("PCSX2 (required)");
    });

    it("highlights the line of the standalone emulator the platform launches with (#955)", () => {
      // The highlight is an identity match, so it reaches a standalone pick.
      // `active_core` used to be the libretro `.so`, which is null for one — no
      // line was highlighted on any platform that launches a standalone
      // emulator, which on a stock RetroDECK is PS2, GameCube and PSP.
      const emulators = [standaloneEmu("DuckStation", true), libretroEmu("swanstation_libretro", "SwanStation")];
      const cores = { DUCKSTATION: { required: true }, "swanstation_libretro.so": { required: false } };
      const active = lineNamed(cores, info(emulators, "DUCKSTATION"), "DuckStation (required)");
      const other = lineNamed(cores, info(emulators, "DUCKSTATION"), "SwanStation (optional)");

      expect(active?.style.color).toBe("#d4a72c");
      expect(active?.style.fontWeight).toBe("bold");
      expect(other?.style.color).toBe("rgba(255, 255, 255, 0.5)");
      expect(other?.style.fontWeight).toBe("normal");
    });

    it("highlights a libretro core on its identity and not on its bare name", () => {
      const emulators = [libretroEmu("swanstation_libretro", "SwanStation", true)];
      const cores = { "swanstation_libretro.so": { required: true } };
      const line = (activeCore: string) => lineNamed(cores, info(emulators, activeCore), "SwanStation (required)");

      expect(line("swanstation_libretro.so")?.style.fontWeight).toBe("bold");
      // The bare `core_so` is the spelling the payload used to carry. It matches
      // no key here, so a payload still sending it highlights nothing — which is
      // the regression this pins, not a state the backend can reach.
      expect(line("swanstation_libretro")?.style.fontWeight).toBe("normal");
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
      cores: { "flycast_libretro.so": { required: false } },
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
      // The same requirement in the only other spelling an emulator has for it:
      // a libretro declaration cannot say "one of these", so SwanStation marks
      // all five PlayStation images optional and the demand arrives here. Drop
      // this answer and the header states the console's own demand
      // (`system_image: "absent"`) over a list with no image in it.
      ["the console's own image rests on it", { system_image_candidate: true }],
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
          biosStatus={statusWith([row("dc_boot.bin", { required_by_active: true }), ...arcadeRows])}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      // Both claims hold of every row counted, and the last clause is the whole
      // point: a summary that does not say where the summarised rows are hides
      // them.
      expect(container.textContent).toContain(
        "6 more files an installed emulator asks for — none required for this launch, none to download; " +
          "the Library page's Platforms tab lists them",
      );
    });

    it("counts a single left-out row in the singular", () => {
      const { container } = render(
        <BiosTab
          biosStatus={statusWith([row("dc_boot.bin", { required_by_active: true }), row("naomi.zip")])}
          biosLevel="ok"
          coreInfo={coreInfo}
          isActive={true}
        />,
      );
      // Every noun and verb, not just the count: "none required" over a set of
      // one reads as a slip, so the singular is its own sentence.
      expect(container.textContent).toContain(
        "1 more file an installed emulator asks for — not required for this launch, nothing to download; " +
          "the Library page's Platforms tab lists it",
      );
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
          hasNote: container.textContent.includes("more files an installed emulator asks for —"),
        };
      };

      const kept = row("dc_boot.bin", { satisfied: true, downloaded: true });
      const withLeftOut = headerOf([kept, ...arcadeRows]);
      const withoutThem = headerOf([kept]);

      expect(withLeftOut.label).toBe(
        "The launching emulator marks none of its BIOS files as required (1/20 files held)",
      );
      expect(withLeftOut.label).toBe(withoutThem.label);
      // Non-vacuous: the first render really did leave rows out, so the equality
      // above is over two different lists rather than two identical ones.
      expect(withLeftOut.hasNote).toBe(true);
      expect(withoutThem.hasNote).toBe(false);
    });
  });
});
