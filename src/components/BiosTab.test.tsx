// The pane's content is asserted through the panel that owns every field it
// renders (RomMGameInfoPanel.test.tsx). What this file exists for is the
// mounting contract: the panel mounts this tab for every ROM and leaves it
// mounted, so rendering has to be gated on `isActive` — a panel test cannot
// tell "the pane rendered nothing" apart from "the pane was never mounted".

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { BiosTab } from "./BiosTab";
import type { BiosStatus, CoreInfo } from "../types";

const coreInfo: CoreInfo = {
  active_core: "snes9x_libretro",
  active_core_label: "Snes9x",
  platform_core_label: null,
  has_game_override: false,
  emulator_data_available: true,
  emulators: [
    { label: "Snes9x", kind: "libretro", core_so: "snes9x_libretro", is_default: true, bakeable: true, reason: null },
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
    expect(container.textContent).toContain("0/1 required files ready");
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
    expect(container.textContent).toContain("BIOS requirement unknown");
    expect(container.textContent).not.toContain("Nothing required");
    expect(container.innerHTML).toContain("#8f98a0");
  });

  it("drops the ratio when the library holds none of the platform's files", () => {
    // "Nothing required (0/0 files held)" counts a set that does not exist.
    const { container } = render(
      <BiosTab
        biosStatus={{ needs_bios: true, server_count: 0, local_count: 0, all_downloaded: false, required_count: 0 }}
        biosLevel="ok"
        coreInfo={coreInfo}
        isActive={true}
      />,
    );
    expect(container.textContent).toContain("Nothing required");
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
    expect(container.textContent).toContain("Needs at least one BIOS file (0/20 files held)");
    expect(container.textContent).not.toContain("Nothing required");
    expect(container.textContent).not.toContain("required files ready");
    expect(container.innerHTML).toContain("#d94126");
  });

  it("says the console needs at least one file even where the level declines", () => {
    // The order the three surfaces have to share. Today the backend never sends
    // this pair — `absent` lands on `missing` — but nothing joins the three, so
    // each pins its own: were a decline added ahead of the `absent` test in
    // `compute_bios_level`, a surface reading the level first would print an
    // ignorance over a requirement that was demonstrated.
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
    expect(container.textContent).toContain("Needs at least one BIOS file (0/20 files held)");
    expect(container.textContent).not.toContain("BIOS readiness unknown");
    expect(container.textContent).not.toContain("BIOS requirement unknown");
  });

  it("names the readiness as the unknown where the console's own image is unsettled", () => {
    // The requirement IS known here — this console needs an image — and it is
    // whether one is in place that could not be established. "BIOS requirement
    // unknown" would be the wrong half.
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
    expect(container.textContent).toContain("BIOS readiness unknown");
    expect(container.textContent).not.toContain("Nothing required");
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
      expect(container.textContent).toContain("Nothing required (1/20 files held)");
      expect(container.textContent).not.toContain("Needs at least one");
    }
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
    // no "holds" heading over a list that is its own sentence.
    expect(name?.textContent).toBe("'pcsx2/bios' folder");
    expect(container.textContent).not.toContain(images.join(", "));
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

    expect(container.textContent).toContain("Dreamcast boot ROM — its location could not be read");
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
      expect(lineFor({ swanstation_libretro: { required: false, needs_one_of: 5 } }, "swanstation")).toBe(
        "swanstation (needs one of its 5 BIOS files)",
      );
    });

    it("keeps the core's own word — the declaration is never rewritten", () => {
      // "required" is what this core's `.info` says, and the console's demand
      // adds nothing a reader would act on differently, so the line is
      // unchanged. Reading the pair as licence to print "required" over an
      // "optional" declaration is the misreading this shape exists to prevent.
      expect(lineFor({ beetle_psx_libretro: { required: true, needs_one_of: null } }, "beetle_psx")).toBe(
        "beetle_psx (required)",
      );
    });

    it("says nothing extra for a core whose console the table has no entry for", () => {
      // Absent is an unasked question, not "this console needs nothing" — so the
      // line says only what the declaration said.
      expect(lineFor({ mgba_libretro: { required: false } }, "mgba")).toBe("mgba (optional)");
    });

    it("leaves a file a demanding core marks optional beside required ones plain", () => {
      // `ps1_rom.bin` under Beetle PSX, and the line the annotation this
      // replaced got wrong. That console does not start without an image, and
      // what the core says about it is three OTHER files marked required — so
      // this row carries no disjunction and the line is the declaration alone.
      expect(lineFor({ beetle_psx_libretro: { required: false } }, "beetle_psx")).toBe("beetle_psx (optional)");
    });

    it("answers each core on its own, over one file two of them declare", () => {
      // One row, two emulators, two different consoles' answers: the pane lists
      // both lines and neither may take the other's.
      const cores = {
        swanstation_libretro: { required: false, needs_one_of: 5 },
        pcsx_rearmed_libretro: { required: false, needs_one_of: null },
      };
      expect(lineFor(cores, "swanstation")).toBe("swanstation (needs one of its 5 BIOS files)");
      expect(lineFor(cores, "pcsx_rearmed")).toBe("pcsx_rearmed (optional)");
    });
  });
});
